"""Things left behind by apps that are already gone:
- menu entries whose program no longer exists (broken shortcuts)
- ~/.var/app/<id> folders from Flatpaks you uninstalled without deleting data
"""
from __future__ import annotations

from pathlib import Path

from .. import desktop
from ..core import (describe_icons, HOME, App, Plan, deletion_problem, format_size, needs_root,
                    path_size, root_delete_command, short_path)

VAR_APP = HOME / ".var/app"


def scan(inv) -> list[App]:
    apps = []
    for e in inv.unclaimed(inv.user_entries) + inv.unclaimed(inv.system_entries):
        if not e.program_missing:
            continue
        inv.claim(e)
        missing = e.argv[0] if e.argv else "?"
        apps.append(App(
            source="leftover", key=str(e.path), name=e.name, icon=e.icon,
            size=None, detail=f"Broken shortcut · {short_path(missing)} no longer exists",
            data={"kind": "broken", "entry": str(e.path)},
        ))

    known_ids = inv.flatpak_ids | {e.path.stem for e in inv.all_entries}
    try:
        folders = sorted(p for p in VAR_APP.iterdir() if p.is_dir())
    except OSError:
        folders = []
    for folder in folders:
        if folder.name in known_ids:
            continue
        apps.append(App(
            source="leftover", key=str(folder), name=folder.name, icon="folder",
            size=path_size(folder),
            detail=f"Data from an uninstalled Flatpak · {short_path(folder)}",
            data={"kind": "flatpak-data", "path": str(folder)},
        ))
    return apps


def plan(app: App, inv) -> Plan:
    if app.data["kind"] == "broken":
        entry = inv.entry(Path(app.data["entry"]))
        entries = [entry] if entry else []
        icons = desktop.icon_files(entries, inv.entries_except(entries))
        paths = [e.path for e in entries] + icons
        items = [f"Menu entry {short_path(p)}" for p in [e.path for e in entries]]
        items += describe_icons(icons)
        summary = f"The program behind “{app.name}” is gone. This removes the leftover shortcut."
    else:
        folder = Path(app.data["path"])
        paths = [folder]
        items = [f"Folder {short_path(folder)} ({format_size(app.size)})"]
        summary = (f"This permanently deletes the settings and files {app.name} left behind"
                   f"{', freeing ' + format_size(app.size) if app.size else ''}.")

    problems = [msg for p in paths if (msg := deletion_problem(p))]
    if problems:
        return Plan(summary="", blocked="\n".join(problems))
    commands = [root_delete_command(p) for p in paths if needs_root(p)]
    user_paths = [p for p in paths if not needs_root(p)]
    if commands:
        summary += " You'll be asked for your password."
    return Plan(summary=summary, items=items, commands=commands,
                delete_paths=user_paths, freed=app.size)
