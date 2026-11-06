"""Flatpak apps (usually from Flathub)."""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import desktop
from ..core import HOME, App, Plan, format_size, parse_size, path_size, run, short_path


def _runs_flatpak(entry: desktop.Entry, app_id: str) -> bool:
    argv = entry.argv
    return bool(argv) and argv[0].endswith("flatpak") and "run" in argv and app_id in argv


def scan(inv) -> list[App]:
    if not shutil.which("flatpak"):
        return []
    r = run(["flatpak", "list", "--app", "--columns=application,name,installation,size"])
    apps = []
    for line in r.stdout.splitlines():
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        app_id, name, installation, size = (c.strip() for c in cols[:4])
        inv.flatpak_ids.add(app_id)

        export_dir = desktop.FLATPAK_EXPORT_DIRS.get(installation)
        exported = [e for e in inv.other_entries
                    if export_dir and e.path.parent == export_dir
                    and e.path.name.startswith(app_id + ".")]
        for e in exported:
            inv.claim(e)
        # Your own copies of this app's menu entry, or launchers that run it.
        mine = [e for e in inv.unclaimed(inv.user_entries)
                if e.id == app_id + ".desktop" or _runs_flatpak(e, app_id)]
        for e in mine:
            inv.claim(e)

        main = next((e for e in exported if e.id == app_id + ".desktop"), None)
        icon = (main.icon if main and main.icon else app_id)
        data_dir = HOME / ".var/app" / app_id
        apps.append(App(
            source="flatpak", key=f"{installation}/{app_id}", name=name or app_id,
            icon=icon, size=parse_size(size), detail=app_id,
            data={"id": app_id, "installation": installation,
                  "user_entries": [str(e.path) for e in mine],
                  "data_dir": str(data_dir)},
        ))
    return apps


def plan(app: App, inv) -> Plan:
    app_id = app.data["id"]
    installation = app.data["installation"]
    data_dir = Path(app.data["data_dir"])
    data_size = path_size(data_dir) if data_dir.exists() else 0

    items = [f"Flatpak {app_id} ({installation} installation)"]
    if data_size:
        items.append(f"Its settings and files in {short_path(data_dir)} ({format_size(data_size)})")
    copies = [Path(p) for p in app.data.get("user_entries", [])]
    items += [f"Your menu entry {short_path(p)}" for p in copies]

    summary = f"This uninstalls {app.name} and deletes its data"
    total = (app.size or 0) + data_size
    summary += f", freeing about {format_size(total)}." if total else "."
    if installation == "system":
        summary += " You may be asked for your password."
    return Plan(
        summary=summary, items=items,
        commands=[["flatpak", "uninstall", "-y", "--delete-data", f"--{installation}", app_id]],
        delete_paths=copies, freed=total or None,
    )
