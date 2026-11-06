"""Apps installed by hand: unpacked into a folder (Android Studio, Telegram's
tarball, …) with a menu entry that no package manager knows about."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .. import desktop
from ..core import (describe_icons, BIN_DIRS, CONTAINER_DIRS, HOME, App, Plan, deletion_problem,
                    format_size, needs_root, path_size, root_delete_command, short_path)

PROJECT_DIR = Path(__file__).resolve().parents[2]

# Folders directly in your home that hold tools for many things, not one app.
SHARED_HOME_DIRS = {"go", "Android", "Sdk", "sdk", "node_modules", "venv", "env",
                    "miniconda3", "anaconda3", "src", "code", "dev", "git", "workspace"}


@dataclass
class Install:
    delete: list[Path] = field(default_factory=list)   # what gets deleted
    mode: str = "launcher-only"                        # 'folder', 'file' or 'launcher-only'
    kept: Path | None = None                           # files we chose not to touch


def find_install(target: Path, containers=CONTAINER_DIRS, bin_dirs=BIN_DIRS,
                 home=HOME, check=deletion_problem) -> Install:
    """Work out what to delete for a program at `target`.

    /usr/local/android-studio/bin/studio      -> folder /usr/local/android-studio
    ~/Downloads/tsetup/Telegram/Telegram      -> folder ~/Downloads/tsetup
    ~/.local/bin/screen-recorder (a file)     -> just that file
    ~/Projects/thing/run.sh                   -> nothing (your project stays)
    """
    t = Path(os.path.abspath(target))
    links: list[Path] = []
    if t.parent in bin_dirs:
        if not t.is_symlink():
            return Install([t], "file")
        links = [t]            # a link in ~/.local/bin: delete it and what it points to
    real = Path(os.path.realpath(t))
    if real.parent in bin_dirs or real.parent in containers:
        if check(real):
            return Install(links, "launcher-only", kept=real)
        return Install(links + [real], "file")

    root = real.parent
    depth = 0
    while root.parent not in containers:
        if root.parent == root or depth > 5:
            return Install(links, "launcher-only", kept=real.parent)
        root = root.parent
        depth += 1
    if root.parent == home and (root.name.startswith(".") or root.name in SHARED_HOME_DIRS):
        return Install(links, "launcher-only", kept=root)
    if check(root):
        return Install(links, "launcher-only", kept=root)
    return Install(links + [root], "folder")


def scan(inv) -> list[App]:
    candidates = inv.unclaimed(inv.user_entries) + inv.unclaimed(inv.system_entries)
    groups: dict[str, dict] = {}
    for e in candidates:
        if not e.visible:
            continue
        target = e.target
        if target is None or not (target.exists() or target.is_symlink()):
            continue
        if target.resolve().is_relative_to(PROJECT_DIR):
            continue   # App Cleaner itself
        inst = find_install(target)
        key = str(inst.delete[-1] if inst.delete else e.path)
        g = groups.setdefault(key, {"install": inst, "entries": []})
        g["entries"].append(e)
        inv.claim(e)

    # Hidden helper entries (URL handlers etc.) that start something inside an install
    for e in inv.unclaimed(inv.user_entries) + inv.unclaimed(inv.system_entries):
        t = e.target
        if t is None:
            continue
        for g in groups.values():
            if any(t == p or t.is_relative_to(p) for p in g["install"].delete
                   if g["install"].mode == "folder"):
                g["entries"].append(e)
                inv.claim(e)
                break

    apps = []
    for key, g in groups.items():
        inst: Install = g["install"]
        entries = g["entries"]
        shown = next(e for e in entries if e.visible)
        main = inst.delete[-1] if inst.delete else None
        apps.append(App(
            source="manual", key=key, name=shown.name, icon=shown.icon,
            size=path_size(main) if main else None,
            detail=short_path(main) if main else f"menu entry only · {short_path(shown.path)}",
            data={"delete": [str(p) for p in inst.delete], "mode": inst.mode,
                  "kept": str(inst.kept) if inst.kept else None,
                  "entries": [str(e.path) for e in entries]},
        ))
    return apps


def plan(app: App, inv) -> Plan:
    entries = [e for p in app.data["entries"] if (e := inv.entry(Path(p)))]
    files = [Path(p) for p in app.data["delete"]]
    icons = desktop.icon_files(entries, inv.entries_except(entries))
    # Icons inside the folder being deleted go with it.
    icons = [i for i in icons if not any(i.is_relative_to(f) for f in files)]
    paths = files + [e.path for e in entries] + icons

    problems = [msg for p in paths if (msg := deletion_problem(p))]
    if problems:
        return Plan(summary="", blocked="\n".join(problems))

    commands, user_paths = [], []
    for p in paths:
        if needs_root(p):
            commands.append(root_delete_command(p))
        else:
            user_paths.append(p)

    items = []
    for p in files:
        kind = "Folder" if p.is_dir() and not p.is_symlink() else "File"
        items.append(f"{kind} {short_path(p)} ({format_size(path_size(p))})")
    items += [f"Menu entry {short_path(e.path)}" for e in entries]
    items += describe_icons(icons)

    warnings = []
    if app.data["mode"] == "launcher-only" and app.data.get("kept"):
        warnings.append(f"Only the menu entry is removed. The program's files in "
                        f"{short_path(app.data['kept'])} are left alone because that folder "
                        "may hold other things you need.")
    for p in files:
        if p.parent == HOME:
            warnings.append(f"This deletes the whole folder {short_path(p)}. "
                            "Make sure nothing else you need is inside it.")

    summary = f"This permanently deletes {app.name}"
    summary += f", freeing {format_size(app.size)}." if app.size else "."
    if commands:
        summary += " You'll be asked for your password."
    return Plan(summary=summary, items=items, commands=commands,
                delete_paths=user_paths, warnings=warnings, freed=app.size)
