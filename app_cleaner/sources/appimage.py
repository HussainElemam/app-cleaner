"""AppImages: single files that run on their own. Removing one = deleting the
file, its menu entries, its icons and any small launcher script."""
from __future__ import annotations

import re
from pathlib import Path

from .. import desktop
from ..core import describe_icons, HOME, App, Plan, deletion_problem, format_size, short_path

# (folder, how many levels deep to look)
SEARCH_DIRS = [
    (HOME / "Applications", 3), (HOME / "applications", 3), (HOME / "AppImages", 2),
    (HOME / "Downloads", 2), (HOME / "Desktop", 1), (HOME, 1), (HOME / ".local/bin", 1),
    (HOME / "bin", 1), (HOME / "opt", 2), (HOME / "apps", 2), (HOME / "Apps", 2),
]


def _is_appimage(p: Path) -> bool:
    return p.name.lower().endswith(".appimage")


def _find(directory: Path, depth: int) -> list[Path]:
    found = []
    try:
        children = list(directory.iterdir())
    except OSError:
        return found
    for child in children:
        try:
            if child.is_symlink():
                continue
            if child.is_file() and _is_appimage(child):
                found.append(child)
            elif depth > 1 and child.is_dir() and not child.name.startswith("."):
                found.extend(_find(child, depth - 1))
        except OSError:
            continue
    return found


def name_from_file(path: Path) -> str:
    """'arduino-ide_2.3.10_Linux_64bit.AppImage' -> 'Arduino Ide'"""
    stem = re.sub(r"\.appimage$", "", path.name, flags=re.I)
    stem = re.split(r"[-_ .]v?\d", stem, maxsplit=1)[0] or stem
    stem = re.sub(r"[-_]+(linux|x86_64|amd64|x64)$", "", stem, flags=re.I)
    words = re.sub(r"[-_]+", " ", stem).strip()
    return words.title() if words.islower() else words


def scan(inv) -> list[App]:
    images: dict[Path, None] = {}
    for d, depth in SEARCH_DIRS:
        for p in _find(d, depth):
            images[p] = None
    # AppImages started from a menu entry, wherever they live
    for e in inv.all_entries:
        for token in e.all_exec.replace('"', " ").split():
            if _is_appimage(Path(token)):
                p = Path(token).expanduser()
                if p.is_absolute() and p.is_file():
                    images[p] = None

    apps = []
    for image in sorted(images):
        entries = [e for e in inv.unclaimed(inv.user_entries) if desktop.mentions(e, image)]
        for e in entries:
            inv.claim(e)
        shown = next((e for e in entries if e.visible), None)
        try:
            size = image.stat().st_size
        except OSError:
            size = None
        apps.append(App(
            source="appimage", key=str(image),
            name=shown.name if shown else name_from_file(image),
            icon=shown.icon if shown and shown.icon else "application-x-executable",
            size=size, detail=short_path(image),
            data={"path": str(image), "entries": [str(e.path) for e in entries]},
        ))
    # A copy without a menu entry (e.g. the original download) borrows the
    # proper name of an integrated copy: "Arduino Ide" -> "Arduino IDE".
    proper = {a.name.lower(): a for a in apps if a.data["entries"]}
    for a in apps:
        twin = proper.get(a.name.lower())
        if twin and not a.data["entries"]:
            a.name, a.icon = twin.name, twin.icon
    return apps


def plan(app: App, inv) -> Plan:
    image = Path(app.data["path"])
    entries = [e for p in app.data["entries"] if (e := inv.entry(Path(p)))]
    scripts = [s for e in entries for s in desktop.launcher_scripts(e, image)]
    icons = desktop.icon_files(entries, inv.entries_except(entries))

    paths = [image] + [e.path for e in entries] + scripts + icons
    problems = [msg for p in paths if (msg := deletion_problem(p))]
    if problems:
        return Plan(summary="", blocked="\n".join(problems))

    items = [f"The AppImage {short_path(image)} ({format_size(app.size)})"]
    items += [f"Menu entry {short_path(e.path)}" for e in entries]
    items += [f"Launcher script {short_path(s)}" for s in scripts]
    items += describe_icons(icons)
    summary = (f"This permanently deletes {app.name}"
               f"{', freeing ' + format_size(app.size) if app.size else ''}. "
               "Its settings (usually in ~/.config) are left alone.")
    return Plan(summary=summary, items=items, delete_paths=paths, freed=app.size)
