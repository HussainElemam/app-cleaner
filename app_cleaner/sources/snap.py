"""Snap packages. Read through the snapd API, which gives exact sizes and types."""
from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path

from .. import desktop
from ..core import HOME, App, Plan, format_size, path_size, short_path

SNAPD_SOCKET = "/run/snapd.socket"


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost", timeout=15)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect(self._path)
        self.sock = sock


def _snapd_get(path: str):
    conn = _UnixHTTPConnection(SNAPD_SOCKET)
    try:
        conn.request("GET", path)
        return json.loads(conn.getresponse().read())["result"]
    finally:
        conn.close()


def scan(inv) -> list[App]:
    if not Path(SNAPD_SOCKET).exists():
        return []
    apps = []
    for s in _snapd_get("/v2/snaps"):
        name = s["name"]
        snap_entries = [e for e in inv.other_entries
                        if e.path.parent == desktop.SNAP_APP_DIR
                        and e.path.name.startswith(name + "_")]
        for e in snap_entries:
            inv.claim(e)
        mine = [e for e in inv.unclaimed(inv.user_entries)
                if e.argv and (e.argv[0].startswith(f"/snap/bin/{name}")
                               or e.argv[:3] == ["snap", "run", name])]
        for e in mine:
            inv.claim(e)

        # Skip helper snaps (bases, themes, libraries) that you never use directly.
        cmds = s.get("apps") or []
        is_app = s.get("type") == "app" and any(
            a.get("desktop-file") or a.get("name") == name for a in cmds)
        if not is_app:
            continue
        shown = next((e for e in snap_entries if e.visible), None)
        apps.append(App(
            source="snap", key=name, name=s.get("title") or name,
            icon=shown.icon if shown and shown.icon else "utilities-terminal",
            size=s.get("installed-size"),
            detail=f"{name} · {s.get('channel') or s.get('tracking-channel') or ''}".rstrip(" ·"),
            data={"name": name, "user_entries": [str(e.path) for e in mine]},
        ))
    return apps


def plan(app: App, inv) -> Plan:
    name = app.data["name"]
    data_dir = HOME / "snap" / name
    data_size = path_size(data_dir) if data_dir.exists() else 0
    items = [f"Snap {name} (all versions)"]
    if data_size:
        items.append(f"Its settings and files in {short_path(data_dir)} ({format_size(data_size)})")
    copies = [Path(p) for p in app.data.get("user_entries", [])]
    items += [f"Your menu entry {short_path(p)}" for p in copies]
    total = (app.size or 0) + data_size
    summary = f"This removes {app.name} and its data"
    summary += f", freeing about {format_size(total)}." if total else "."
    summary += " You'll be asked for your password."
    return Plan(
        summary=summary, items=items,
        # --purge: don't keep a backup snapshot, delete it for good
        commands=[["pkexec", "/usr/bin/snap", "remove", "--purge", name]],
        delete_paths=copies, freed=total or None,
    )
