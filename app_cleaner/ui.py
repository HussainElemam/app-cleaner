"""The App Cleaner window (GTK 4 + libadwaita)."""
from __future__ import annotations

import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from .core import (SOURCE_LABELS, App, Plan, Result, command_text, execute,  # noqa: E402
                   format_size, short_path)
from .sources import Inventory  # noqa: E402

FILTERS = [
    ("All sources", None), ("RPM packages", "rpm"), ("Flatpaks", "flatpak"),
    ("Snaps", "snap"), ("AppImages", "appimage"), ("Web apps", "webapp"),
    ("Manual installs", "manual"), ("Leftovers", "leftover"),
]
SORTS = ["Sort by name", "Sort by size", "Sort by source"]
SOURCE_ORDER = list(SOURCE_LABELS)


def verb_for(app: App) -> str:
    if app.source == "leftover":
        return "Clean Up"
    if app.source in ("appimage", "manual"):
        return "Delete"
    if app.source == "webapp":
        return "Remove"
    return "Uninstall"


def make_icon(name: str | None) -> Gtk.Image:
    """An image for a themed icon name or an icon file (png, svg, ico, xpm…)."""
    fallback = "application-x-executable"
    if name and name.startswith("/"):
        if os.path.isfile(name):
            try:
                # GdkPixbuf reads more formats (e.g. .ico) than GTK does on its own.
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(name, 72, 72, True)
                fmt = (Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha()
                       else Gdk.MemoryFormat.R8G8B8)
                texture = Gdk.MemoryTexture.new(pixbuf.get_width(), pixbuf.get_height(), fmt,
                                                pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())
                return Gtk.Image.new_from_paintable(texture)
            except GLib.Error:
                pass
        return Gtk.Image.new_from_icon_name(fallback)
    if name:
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        for candidate in (name, os.path.splitext(name)[0]):
            if theme.has_icon(candidate):
                return Gtk.Image.new_from_icon_name(candidate)
    return Gtk.Image.new_from_icon_name(fallback)


class AppRow(Adw.ActionRow):
    def __init__(self, app: App, on_remove) -> None:
        parts = [SOURCE_LABELS[app.source], format_size(app.size), app.detail]
        super().__init__(title=app.name, subtitle=" · ".join(p for p in parts if p),
                         use_markup=False, title_lines=1, subtitle_lines=1)
        self.app = app
        self.set_tooltip_text(app.detail)

        icon = make_icon(app.icon)
        icon.set_pixel_size(36)
        icon.add_css_class("icon-dropshadow")
        self.add_prefix(icon)

        badge_text = app.data.get("badge")
        if app.builtin and badge_text:
            badge = Gtk.Label(label=badge_text, valign=Gtk.Align.CENTER)
            badge.add_css_class("caption")
            badge.add_css_class("dim-label")
            self.add_suffix(badge)

        self.spinner = Adw.Spinner(visible=False, valign=Gtk.Align.CENTER)
        self.button = Gtk.Button(label=verb_for(app), valign=Gtk.Align.CENTER)
        self.button.connect("clicked", lambda *_: on_remove(self))
        self.add_suffix(self.spinner)
        self.add_suffix(self.button)

    def set_busy(self, busy: bool) -> None:
        self.spinner.set_visible(busy)
        self.button.set_visible(not busy)


class Window(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, dry_run: bool) -> None:
        super().__init__(application=application, title="App Cleaner",
                         default_width=820, default_height=760)
        self.dry_run = dry_run
        self.inventory: Inventory | None = None
        self.rows: list[AppRow] = []
        self.show_builtin = False
        self.busy = False

        # header
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="App Cleaner")
        header.set_title_widget(self.window_title)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic",
                             tooltip_text="Scan again (Ctrl+R)", action_name="win.reload")
        header.pack_start(refresh)
        menu = Gio.Menu()
        menu.append("Show Apps That Came With Fedora", "win.show-builtin")
        menu.append("About App Cleaner", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menu", primary=True))

        # search + filters
        self.search = Gtk.SearchEntry(placeholder_text="Search apps", hexpand=True)
        self.search.set_key_capture_widget(self)
        self.source_dd = Gtk.DropDown.new_from_strings([f[0] for f in FILTERS])
        self.sort_dd = Gtk.DropDown.new_from_strings(SORTS)
        self.source_dd.set_tooltip_text("Show apps from one source")
        self.sort_dd.set_tooltip_text("Sort order")
        bar = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        for w in (self.search, self.source_dd, self.sort_dd):
            bar.append(w)
        bar_clamp = Adw.Clamp(maximum_size=900, child=bar)

        banner = Adw.Banner(title="Dry run: nothing will actually be removed",
                            revealed=dry_run)

        # list
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_filter_func(self._filter)
        self.listbox.set_sort_func(self._sort)
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=6,
                           margin_bottom=24, margin_start=12, margin_end=12)
        list_box.append(self.listbox)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=Adw.Clamp(maximum_size=900, child=list_box))

        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        spinner = Adw.Spinner(width_request=48, height_request=48)
        loading_label = Gtk.Label(label="Looking for installed apps…")
        loading_label.add_css_class("dim-label")
        loading.append(spinner)
        loading.append(loading_label)

        empty = Adw.StatusPage(icon_name="edit-find-symbolic", title="No Apps Found",
                               description="Try a different search or source.")

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(loading, "loading")
        self.stack.add_named(scroller, "list")
        self.stack.add_named(empty, "empty")
        self.toasts = Adw.ToastOverlay(child=self.stack)

        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        view.add_top_bar(banner)
        view.add_top_bar(bar_clamp)
        self.set_content(view)

        # actions
        self._add_action("reload", lambda *_: self.reload())
        self._add_action("search", lambda *_: self.search.grab_focus())
        self._add_action("about", lambda *_: self._about())
        builtin = Gio.SimpleAction.new_stateful("show-builtin", None,
                                                GLib.Variant.new_boolean(False))
        builtin.connect("change-state", self._on_show_builtin)
        self.add_action(builtin)

        self.search.connect("search-changed", lambda *_: self._refilter())
        self.source_dd.connect("notify::selected", lambda *_: self._refilter())
        self.sort_dd.connect("notify::selected", lambda *_: self._resort())

        self.reload()

    def _add_action(self, name, callback) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    # ------------------------------------------------------------ loading
    def reload(self) -> None:
        if self.busy:
            self.toast("Wait for the current removal to finish")
            return
        self.stack.set_visible_child_name("loading")
        self.window_title.set_subtitle("")

        def work():
            inv = Inventory()
            inv.scan()
            GLib.idle_add(self._populate, inv)

        threading.Thread(target=work, daemon=True).start()

    def _populate(self, inv: Inventory) -> bool:
        self.inventory = inv
        self.listbox.remove_all()
        self.rows = []
        for app in inv.apps:
            row = AppRow(app, self._on_remove_clicked)
            self.rows.append(row)
            self.listbox.append(row)
        for source, error in inv.errors:
            print(f"App Cleaner: could not read {source}:\n{error}", file=sys.stderr)
            self.toast(f"Couldn't read {SOURCE_LABELS.get(source, source)} apps")
        self._refilter()
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------ filter / sort
    def _filter(self, row: AppRow) -> bool:
        app = row.app
        if app.builtin and not self.show_builtin:
            return False
        source = FILTERS[self.source_dd.get_selected()][1]
        if source and app.source != source:
            return False
        q = self.search.get_text().strip().lower()
        if q and not any(q in s.lower() for s in (app.name, app.detail, SOURCE_LABELS[app.source])):
            return False
        return True

    def _sort(self, a: AppRow, b: AppRow) -> int:
        mode = self.sort_dd.get_selected()
        x, y = a.app, b.app
        if mode == 1:
            key_x, key_y = (-(x.size or -1), x.name.casefold()), (-(y.size or -1), y.name.casefold())
        elif mode == 2:
            key_x = (SOURCE_ORDER.index(x.source), x.name.casefold())
            key_y = (SOURCE_ORDER.index(y.source), y.name.casefold())
        else:
            key_x, key_y = x.name.casefold(), y.name.casefold()
        return (key_x > key_y) - (key_x < key_y)

    def _refilter(self) -> None:
        self.listbox.invalidate_filter()
        self._update_status()

    def _resort(self) -> None:
        self.listbox.invalidate_sort()

    def _update_status(self) -> None:
        if self.inventory is None:
            return
        visible = [r for r in self.rows if self._filter(r)]
        total = sum(r.app.size or 0 for r in visible)
        text = f"{len(visible)} app{'s' if len(visible) != 1 else ''}"
        if total:
            text += f" · {format_size(total)}"
        hidden = sum(r.app.builtin for r in self.rows)
        if hidden and not self.show_builtin:
            text += f" · {hidden} built-in hidden"
        self.window_title.set_subtitle(text)
        self.stack.set_visible_child_name("list" if visible else "empty")

    def _on_show_builtin(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self.show_builtin = value.get_boolean()
        self._refilter()

    # ------------------------------------------------------------ removing
    def _on_remove_clicked(self, row: AppRow) -> None:
        if self.busy:
            self.toast("Wait for the current removal to finish")
            return
        self.busy = True
        row.set_busy(True)

        def work():
            try:
                plan, error = self.inventory.plan(row.app), None
            except Exception as e:  # show the error instead of crashing
                plan, error = None, e
            GLib.idle_add(self._show_plan, row, plan, error)

        threading.Thread(target=work, daemon=True).start()

    def _show_plan(self, row: AppRow, plan: Plan | None, error: Exception | None) -> bool:
        row.set_busy(False)
        self.busy = False
        app = row.app
        if error is not None or plan is None:
            self._error_dialog(f"Couldn't check {app.name}", str(error), "")
            return GLib.SOURCE_REMOVE
        if plan.blocked:
            d = Adw.AlertDialog(heading=f"Can't Remove {app.name}", body=plan.blocked)
            d.add_response("close", "Close")
            d.present(self)
            return GLib.SOURCE_REMOVE

        verb = verb_for(app)
        d = Adw.AlertDialog(heading=f"{verb} {app.name}?", body=plan.summary,
                            prefer_wide_layout=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for w in plan.warnings:
            label = Gtk.Label(label=f"⚠ {w}", wrap=True, xalign=0,
                              wrap_mode=Pango.WrapMode.WORD_CHAR)
            label.add_css_class("warning")
            box.append(label)
        if plan.items:
            heading = Gtk.Label(label="This will remove:", xalign=0)
            heading.add_css_class("heading")
            items = Gtk.Label(label="\n".join(f"• {i}" for i in plan.items), xalign=0,
                              wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            box.append(heading)
            box.append(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          propagate_natural_height=True,
                                          max_content_height=220, child=items))
        lines = [command_text(c) for c in plan.commands]
        lines += [f"delete {short_path(p)}" for p in plan.delete_paths]
        if lines:
            details = Gtk.Label(label="\n".join(lines), xalign=0, wrap=True,
                                wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
            details.add_css_class("monospace")
            details.add_css_class("caption")
            box.append(Gtk.Expander(label="Show exact commands", child=details))
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("remove", verb)
        d.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: r == "remove" and self._run(row, plan))
        d.present(self)
        return GLib.SOURCE_REMOVE

    def _run(self, row: AppRow, plan: Plan) -> None:
        self.busy = True
        row.set_busy(True)

        def work():
            try:
                result = execute(plan, dry_run=self.dry_run)
            except Exception as e:
                result = Result(False, message=str(e))
            GLib.idle_add(self._finished, row, plan, result)

        threading.Thread(target=work, daemon=True).start()

    def _finished(self, row: AppRow, plan: Plan, result: Result) -> bool:
        self.busy = False
        row.set_busy(False)
        app = row.app
        if result.ok and self.dry_run:
            print(f"[dry run] {app.name}:\n{result.output}\n")
            self.toast(f"Dry run: {app.name} was not removed (commands printed in the terminal)")
        elif result.ok:
            self.listbox.remove(row)
            self.rows.remove(row)
            freed = f" · {format_size(plan.freed)} freed" if plan.freed else ""
            self.toast(f"{app.name} was removed{freed}")
            self._update_status()
        elif result.cancelled:
            self.toast("Cancelled, nothing was removed")
        else:
            self._error_dialog(f"Couldn't Remove {app.name}", result.message, result.output)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------ helpers
    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def _error_dialog(self, heading: str, message: str, output: str) -> None:
        d = Adw.AlertDialog(heading=heading, body=message)
        if output:
            label = Gtk.Label(label=output, xalign=0, yalign=0, selectable=True, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR)
            label.add_css_class("monospace")
            label.add_css_class("caption")
            d.set_extra_child(Gtk.ScrolledWindow(child=label, min_content_height=160,
                                                 max_content_height=320,
                                                 propagate_natural_height=True))
        d.add_response("close", "Close")
        d.present(self)

    def _about(self) -> None:
        Adw.AboutDialog(
            application_name="App Cleaner", application_icon=APP_ID, version=__version__,
            comments="See every app on your computer, where it came from, "
                     "and remove it with one click.",
        ).present(self)


def run_gui(dry_run: bool = False) -> int:
    GLib.set_application_name("App Cleaner")
    app = Adw.Application(application_id=APP_ID)

    def on_activate(application):
        win = application.get_active_window() or Window(application, dry_run)
        win.present()

    app.connect("activate", on_activate)
    quit_action = Gio.SimpleAction.new("quit", None)
    quit_action.connect("activate", lambda *_: app.quit())
    app.add_action(quit_action)
    app.set_accels_for_action("app.quit", ["<Ctrl>q"])
    app.set_accels_for_action("win.reload", ["<Ctrl>r", "F5"])
    app.set_accels_for_action("win.search", ["<Ctrl>f"])
    return app.run([sys.argv[0]])
