"""Every way an app can be installed, and the inventory that runs them all.

Each source module has:
    scan(inv)        -> list[App]   find the apps installed this way
    plan(app, inv)   -> Plan        work out what removing one would do

Sources run in order. Each one "claims" the menu entries it recognises, so a
menu entry is only ever listed once (e.g. your copy of Zoom.desktop in
~/.local/share/applications belongs to the zoom RPM, not to "manual installs").
"""
from __future__ import annotations

import traceback
from pathlib import Path

from .. import desktop
from ..core import App, Plan
from . import appimage, flatpak, leftover, manual, rpm, snap, webapp

# Order matters: later sources only see entries earlier ones did not claim.
SOURCES = {
    "rpm": rpm,
    "flatpak": flatpak,
    "snap": snap,
    "appimage": appimage,
    "webapp": webapp,
    "leftover": leftover,
    "manual": manual,
}


class Inventory:
    def __init__(self) -> None:
        self.user_entries: list[desktop.Entry] = []
        self.system_entries: list[desktop.Entry] = []
        self.other_entries: list[desktop.Entry] = []   # flatpak + snap exports
        self.claimed: set[Path] = set()
        self.apps: list[App] = []
        self.errors: list[tuple[str, str]] = []
        self.flatpak_ids: set[str] = set()

    # -- entries -----------------------------------------------------------
    def claim(self, entry: desktop.Entry) -> None:
        self.claimed.add(entry.path)

    def unclaimed(self, entries: list[desktop.Entry]) -> list[desktop.Entry]:
        return [e for e in entries if e.path not in self.claimed]

    @property
    def all_entries(self) -> list[desktop.Entry]:
        return self.user_entries + self.system_entries + self.other_entries

    def entries_except(self, removing: list[desktop.Entry]) -> list[desktop.Entry]:
        gone = {e.path for e in removing}
        return [e for e in self.all_entries if e.path not in gone]

    def entry(self, path: Path) -> desktop.Entry | None:
        for e in self.all_entries:
            if e.path == path:
                return e
        return None

    # -- scanning ----------------------------------------------------------
    def scan(self) -> list[App]:
        self.user_entries = desktop.user_entries()
        self.system_entries = desktop.system_entries()
        others = []
        for d in list(desktop.FLATPAK_EXPORT_DIRS.values()) + [desktop.SNAP_APP_DIR]:
            others.extend(desktop.load_dir(d))
        self.other_entries = others

        for name, module in SOURCES.items():
            try:
                self.apps.extend(module.scan(self))
            except Exception as e:  # one broken source shouldn't hide the others
                self.errors.append((name, f"{e}\n{traceback.format_exc()}"))
        return self.apps

    def plan(self, app: App) -> Plan:
        return SOURCES[app.source].plan(app, self)
