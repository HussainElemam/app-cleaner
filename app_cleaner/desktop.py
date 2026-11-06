"""Reading .desktop menu entries and working out which program they start."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from gi.repository import GLib

from .core import HOME, USER_APPS_DIR, USER_ICONS_DIR

SYSTEM_APP_DIRS = [Path("/usr/share/applications"), Path("/usr/local/share/applications")]
FLATPAK_EXPORT_DIRS = {
    "system": Path("/var/lib/flatpak/exports/share/applications"),
    "user": HOME / ".local/share/flatpak/exports/share/applications",
}
SNAP_APP_DIR = Path("/var/lib/snapd/desktop/applications")

# Apps started from the GNOME menu may not have ~/.local/bin etc. in $PATH,
# so look in the usual places too before calling a program "missing".
SEARCH_PATH = os.pathsep.join(dict.fromkeys(
    os.environ.get("PATH", "").split(os.pathsep) + [
        str(HOME / ".local/bin"), str(HOME / "bin"), str(HOME / ".cargo/bin"),
        "/usr/local/bin", "/usr/local/sbin", "/usr/bin", "/usr/sbin", "/bin", "/sbin",
        "/snap/bin", "/var/lib/flatpak/exports/bin",
        str(HOME / ".local/share/flatpak/exports/bin"),
    ]))

# Programs that only start another program. The real program comes after them.
WRAPPERS = {"app-scope", "env", "nohup", "setsid", "systemd-run"}

_FIELD_CODE = re.compile(r"%[fFuUdDnNickvm]")
_CURRENT_DESKTOPS = {d.lower() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d}


@dataclass
class Entry:
    path: Path
    name: str
    icon: str | None
    exec_line: str
    all_exec: str          # every Exec/TryExec line in the file, for searching
    argv: list[str]        # the real command, wrappers and %-codes removed
    visible: bool          # shown in the app menu

    @property
    def id(self) -> str:
        return self.path.name

    @property
    def target(self) -> Path | None:
        """The program this entry runs, or None if it can't be found."""
        if not self.argv:
            return None
        prog = os.path.expanduser(self.argv[0])
        if "/" in prog:
            return Path(os.path.abspath(prog))
        found = shutil.which(prog, path=SEARCH_PATH)
        return Path(found) if found else None

    @property
    def program_missing(self) -> bool:
        """True when the program clearly no longer exists (a broken shortcut)."""
        if not self.argv:
            return False
        prog = os.path.expanduser(self.argv[0])
        if "/" in prog:
            p = Path(prog)
            return not (p.exists() or p.is_symlink())
        return shutil.which(prog, path=SEARCH_PATH) is None


def parse_exec(exec_line: str) -> list[str]:
    """Split an Exec= line into arguments, dropping %f-style codes and wrappers."""
    try:
        ok, argv = GLib.shell_parse_argv(exec_line)
    except GLib.Error:
        return []
    argv = [_FIELD_CODE.sub("", a).replace("%%", "%") for a in argv]
    argv = [a for a in argv if a]
    return strip_wrappers(argv)


def strip_wrappers(argv: list[str]) -> list[str]:
    argv = list(argv)
    while argv:
        base = os.path.basename(argv[0])
        if base not in WRAPPERS:
            break
        argv = argv[1:]
        # skip the wrapper's own options: env VAR=1, systemd-run --user --scope …
        while argv and (argv[0].startswith("-") or (base == "env" and "=" in argv[0])):
            if argv[0] == "--":
                argv = argv[1:]
                break
            argv = argv[1:]
    return argv


def _visible(kf: GLib.KeyFile) -> bool:
    def get_bool(key: str) -> bool:
        try:
            return kf.get_boolean("Desktop Entry", key)
        except GLib.Error:
            return False

    def get_list(key: str) -> set[str]:
        try:
            return {x.lower() for x in kf.get_string_list("Desktop Entry", key)}
        except GLib.Error:
            return set()

    if get_bool("NoDisplay") or get_bool("Hidden"):
        return False
    only = get_list("OnlyShowIn")
    if only and not (only & _CURRENT_DESKTOPS):
        return False
    if get_list("NotShowIn") & _CURRENT_DESKTOPS:
        return False
    return True


def load_entry(path: Path) -> Entry | None:
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(str(path), GLib.KeyFileFlags.NONE)
        if kf.get_string("Desktop Entry", "Type") != "Application":
            return None
    except GLib.Error:
        return None

    def get(key: str, localized: bool = False) -> str | None:
        try:
            if localized:
                return kf.get_locale_string("Desktop Entry", key, None).strip()
            return kf.get_string("Desktop Entry", key).strip()
        except GLib.Error:
            return None

    exec_line = get("Exec") or ""
    all_exec = []
    groups, _ = kf.get_groups()
    for group in groups:
        for key in ("Exec", "TryExec"):
            try:
                all_exec.append(kf.get_string(group, key))
            except GLib.Error:
                pass
    return Entry(
        path=path,
        name=get("Name", localized=True) or path.stem,
        icon=get("Icon") or None,
        exec_line=exec_line,
        all_exec="\n".join(all_exec),
        argv=parse_exec(exec_line),
        visible=_visible(kf),
    )


def load_dir(directory: Path) -> list[Entry]:
    entries = []
    try:
        files = sorted(directory.glob("*.desktop"))
    except OSError:
        return entries
    for f in files:
        e = load_entry(f)
        if e:
            entries.append(e)
    return entries


def mentions(entry: Entry, path: Path) -> bool:
    """Does this menu entry start `path`, directly or through a small launcher script?"""
    p = str(path)
    if p in entry.all_exec:
        return True
    target = entry.target
    if target is None:
        return False
    try:
        if os.path.realpath(target) == os.path.realpath(path):
            return True
        # e.g. pCloud: Exec=~/.config/pcloud/pcloud-launcher.sh, which runs the AppImage
        if target.is_relative_to(HOME) and target.is_file() and target.stat().st_size < 64_000:
            head = target.read_bytes()
            if not head.startswith(b"\x7fELF") and p.encode() in head:
                return True
    except OSError:
        pass
    return False


def launcher_scripts(entry: Entry, path: Path) -> list[Path]:
    """Small user-owned scripts that this entry runs only to start `path`."""
    target = entry.target
    if target and target != path and target.is_relative_to(HOME) and mentions(entry, path) \
            and str(path) not in entry.all_exec and target.suffix in (".sh", ""):
        try:
            if target.is_file() and target.stat().st_size < 64_000:
                return [target]
        except OSError:
            pass
    return []


_ICON_EXTS = (".png", ".svg", ".svgz", ".xpm", ".ico")


def icon_files(remove: list[Entry], keep: list[Entry]) -> list[Path]:
    """User-installed icon files used only by the entries being removed."""
    still_used = {e.icon for e in keep if e.icon}
    still_used_names = {Path(i).stem if i.startswith("/") else i for i in still_used}
    found: list[Path] = []
    for e in remove:
        icon = e.icon
        if not icon or icon in still_used:
            continue
        names: set[str] = set()
        if icon.startswith("/"):
            p = Path(icon)
            if p.is_relative_to(HOME) and p.is_file():
                found.append(p)
            if p.is_relative_to(USER_ICONS_DIR):
                names.add(p.stem)       # other sizes of the same icon
        else:
            names.add(icon)
        names -= still_used_names
        for name in names:
            # Apps put their icons in the hicolor theme; never touch other themes.
            for base in (USER_ICONS_DIR / "hicolor", HOME / ".local/share/pixmaps"):
                if not base.is_dir():
                    continue
                for ext in _ICON_EXTS:
                    found.extend(base.rglob(name + ext))
    out, seen = [], set()
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def user_entries() -> list[Entry]:
    return load_dir(USER_APPS_DIR)


def system_entries() -> list[Entry]:
    out = []
    for d in SYSTEM_APP_DIRS:
        out.extend(load_dir(d))
    return out
