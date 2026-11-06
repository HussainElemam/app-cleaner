"""Web apps: websites installed as apps from Chrome/Chromium/Brave/Edge, or
from Firefox with the PWAsForFirefox extension (firefoxpwa)."""
from __future__ import annotations

from pathlib import Path

from .. import desktop
from ..core import describe_icons, App, Plan, deletion_problem, short_path

CHROMIUM_NAMES = {
    "google-chrome": "Chrome", "google-chrome-stable": "Chrome", "chrome": "Chrome",
    "chromium": "Chromium", "chromium-browser": "Chromium", "brave": "Brave",
    "brave-browser": "Brave", "microsoft-edge": "Edge", "vivaldi": "Vivaldi",
}


def detect(entry: desktop.Entry) -> dict | None:
    """Return details if this menu entry opens a web app, else None."""
    argv = entry.argv
    if not argv:
        return None
    prog = Path(argv[0]).name
    if prog == "firefoxpwa" and argv[1:3] == ["site", "launch"] and len(argv) > 3:
        return {"kind": "firefoxpwa", "id": argv[3], "browser": "Firefox"}
    for arg in argv[1:]:
        if arg.startswith("--app-id="):
            browser = CHROMIUM_NAMES.get(prog, prog.replace("-", " ").title())
            return {"kind": "chromium", "id": arg.split("=", 1)[1], "browser": browser}
    return None


def scan(inv) -> list[App]:
    apps = []
    for e in inv.unclaimed(inv.user_entries):
        info = detect(e)
        if not info:
            continue
        inv.claim(e)
        if not e.visible:
            continue
        apps.append(App(
            source="webapp", key=f"{info['kind']}:{info['id']}", name=e.name,
            icon=e.icon, size=None, detail=f"{info['browser']} web app",
            data={**info, "entry": str(e.path)},
        ))
    return apps


def plan(app: App, inv) -> Plan:
    entry = inv.entry(Path(app.data["entry"]))
    entries = [entry] if entry else []
    icons = desktop.icon_files(entries, inv.entries_except(entries))
    paths = [e.path for e in entries] + icons
    problems = [msg for p in paths if (msg := deletion_problem(p))]
    if problems:
        return Plan(summary="", blocked="\n".join(problems))

    items = [f"Menu entry {short_path(p)}" for p in [e.path for e in entries]]
    items += describe_icons(icons)
    commands, warnings = [], []
    if app.data["kind"] == "firefoxpwa":
        commands.append(["firefoxpwa", "site", "uninstall", "--quiet", app.data["id"]])
        items.insert(0, f"The web app from PWAsForFirefox (id {app.data['id']})")
        summary = f"This uninstalls the {app.name} web app from Firefox."
    else:
        browser = app.data["browser"]
        scheme = {"Brave": "brave", "Edge": "edge", "Vivaldi": "vivaldi"}.get(browser, "chrome")
        summary = f"This removes the {app.name} shortcut from your app menu."
        warnings.append(
            f"{browser} still keeps this web app. To remove it completely, "
            f"open {scheme}://apps in {browser}, right-click {app.name} and "
            "choose \"Remove\". Otherwise the browser may add the shortcut back.")
    return Plan(summary=summary, items=items, commands=commands,
                delete_paths=paths, warnings=warnings)
