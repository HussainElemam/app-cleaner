"""Apps installed as RPM packages: dnf, GNOME Software, or a downloaded .rpm file."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import desktop
from ..core import HOME, App, Plan, parse_size, run, short_path
from . import webapp

# Removing any of these would break the desktop, the package manager or
# App Cleaner itself, so a removal that touches them is refused.
CRITICAL = {
    "bash", "coreutils", "dbus", "dbus-broker", "dnf", "dnf5", "fedora-release",
    "firewalld", "flatpak", "gdm", "glibc", "gnome-control-center", "gnome-session",
    "gnome-settings-daemon", "gnome-shell", "gnome-software", "gtk4", "libadwaita",
    "mutter", "nautilus", "NetworkManager", "pipewire", "polkit", "ptyxis", "python3",
    "python3-gobject", "python3-libs", "rpm", "selinux-policy", "shadow-utils",
    "snapd", "sudo", "systemd", "util-linux", "wireplumber", "xdg-desktop-portal",
    "xdg-desktop-portal-gnome",
}

_SECTION_LABELS = {
    "Removing": "the app",
    "Removing dependent packages": "depends on it",
    "Removing unused dependencies": "no longer needed",
}


def _owner_lookup():
    """Return a function: path -> (package name, installed size) or None."""
    try:
        import rpm as rpmlib  # type: ignore
        ts = rpmlib.TransactionSet()

        def lookup(path: Path):
            for h in ts.dbMatch("basenames", str(path)):
                return h["name"], int(h["size"] or 0)
            return None
    except ImportError:
        def lookup(path: Path):
            r = run(["rpm", "-qf", "--qf", "%{NAME}\t%{SIZE}\n", str(path)])
            if r.returncode != 0 or "\t" not in r.stdout:
                return None
            name, size = r.stdout.splitlines()[0].split("\t")
            return name, int(size)
    return lookup


def install_reasons(packages: set[str]) -> dict[str, str]:
    """Why each package is installed: 'User', 'Group' (came with Fedora), 'Dependency'…"""
    if not packages:
        return {}
    r = run(["dnf", "-q", "-C", "repoquery", "--installed",
             "--qf", "%{name}\t%{reason}\n", *sorted(packages)], timeout=90)
    reasons = {}
    for line in r.stdout.splitlines():
        if "\t" in line:
            name, reason = line.split("\t", 1)
            reasons[name.strip()] = reason.strip()
    return reasons


def _best_entry(entries: list[desktop.Entry], pkg: str) -> desktop.Entry:
    p = pkg.lower()
    stems = [(e, e.path.stem.lower()) for e in entries]
    for e, stem in stems:
        if stem == p or stem.rsplit(".", 1)[-1] == p:
            return e
    for e, stem in stems:
        if p in stem or stem.rsplit(".", 1)[-1] in p:
            return e
    return entries[0]


def scan(inv) -> list[App]:
    lookup = _owner_lookup()
    sizes: dict[str, int] = {}
    pkg_entries: dict[str, list[desktop.Entry]] = {}
    id_owner: dict[str, str] = {}

    for e in inv.system_entries:
        owner = lookup(e.path)
        if not owner:
            continue
        pkg, size = owner
        sizes[pkg] = size
        pkg_entries.setdefault(pkg, []).append(e)
        id_owner[e.id] = pkg
        inv.claim(e)

    # Your own copies of menu entries (e.g. ~/.local/share/applications/Zoom.desktop)
    # belong to the package they start, so they get removed with it.
    user_copies: dict[str, list[desktop.Entry]] = {}
    for e in inv.unclaimed(inv.user_entries):
        if webapp.detect(e):
            continue
        pkg = id_owner.get(e.id)
        if pkg is None:
            t = e.target
            if t is not None and not t.is_relative_to(HOME) and t.exists():
                owner = lookup(t) or lookup(Path(os.path.realpath(t)))
                if owner:
                    pkg = owner[0]
                    sizes.setdefault(pkg, owner[1])
        if pkg:
            user_copies.setdefault(pkg, []).append(e)
            inv.claim(e)

    packages = set(pkg_entries) | set(user_copies)
    reasons = install_reasons(packages)
    apps = []
    for pkg in sorted(packages):
        system = pkg_entries.get(pkg, [])
        mine = user_copies.get(pkg, [])
        my_ids = {e.id for e in mine}
        # A user copy hides the system entry with the same file name.
        effective = mine + [e for e in system if e.id not in my_ids]
        shown = [e for e in effective if e.visible]
        if not shown:
            continue
        best = _best_entry(shown, pkg)
        others = sorted({e.name for e in shown} - {best.name})
        detail = pkg + (" · also: " + ", ".join(others) if others else "")
        reason = reasons.get(pkg, "")
        if reason.startswith("Group"):
            badge = "Came with Fedora"
        elif reason in ("Dependency", "Weak Dependency"):
            badge = "Installed automatically"
        else:
            badge = None
        apps.append(App(
            source="rpm", key=pkg, name=best.name, icon=best.icon,
            size=sizes.get(pkg), detail=detail, builtin=badge is not None,
            data={"package": pkg, "user_entries": [str(e.path) for e in mine],
                  "badge": badge},
        ))
    return apps


def parse_dnf_preview(text: str) -> tuple[list[tuple[str, str]], str | None]:
    """Read `dnf remove --assumeno` output: [(package, section)], freed space."""
    packages: list[tuple[str, str]] = []
    section = None
    freed = None
    for line in text.splitlines():
        stripped = line.strip()
        m = re.search(r"After this operation, (.+?) will be freed", stripped)
        if m:
            freed = m.group(1)
        if stripped.startswith("Transaction Summary"):
            section = None
            continue
        if line and not line[0].isspace() and stripped.endswith(":"):
            title = stripped[:-1]
            section = title if title.startswith("Removing") else None
            continue
        if section and line.startswith(" ") and stripped:
            packages.append((stripped.split()[0], section))
    return packages, freed


def _problem_text(output: str) -> str | None:
    lines = output.strip().splitlines()
    for i, line in enumerate(lines):
        if "protected packages" in line:
            return "Fedora protects this package because the system needs it."
        if line.lstrip().startswith("Problem"):
            return "\n".join(lines[i:i + 6])
    return output.strip()[-600:] or None


def plan(app: App, inv) -> Plan:
    pkg = app.data["package"]
    r = run(["dnf", "remove", "--assumeno", pkg], timeout=300)
    packages, freed = parse_dnf_preview(r.stdout + "\n" + r.stderr)
    if not packages:
        return Plan(summary="", blocked=_problem_text(r.stdout + r.stderr)
                    or "dnf could not work out how to remove this package.")

    names = [n for n, _ in packages]
    critical = [n for n in names if n in CRITICAL or n.startswith("kernel")]
    blocked = None
    if pkg in critical:
        blocked = f"{app.name} ({pkg}) is a core part of your system. App Cleaner won't remove it."
    elif critical:
        blocked = (f"Removing {app.name} would also remove {', '.join(critical)}, "
                   "which your system needs. App Cleaner won't do that.")

    items = [f"Package {n} ({_SECTION_LABELS.get(s, s.lower())})" for n, s in packages]
    copies = [Path(p) for p in app.data.get("user_entries", [])]
    items += [f"Your menu entry {short_path(p)}" for p in copies]

    warnings = []
    if app.builtin:
        how = ("came with Fedora" if app.data.get("badge") == "Came with Fedora"
               else "was installed automatically with Fedora's default apps")
        warnings.append(f"This app {how}. Other parts of the system may expect it.")
    dependents = [n for n, s in packages if s == "Removing dependent packages"]
    if dependents:
        warnings.append(f"{len(dependents)} other package(s) that need {pkg} will be removed "
                        f"too: {', '.join(dependents[:8])}{'…' if len(dependents) > 8 else ''}")

    extra = len(packages) - 1
    summary = f"This removes the {pkg} package"
    summary += f" and {extra} related package{'s' if extra != 1 else ''}" if extra else ""
    summary += f", freeing about {freed}." if freed else "."
    summary += " You'll be asked for your password."
    return Plan(
        summary=summary, items=items,
        commands=[["pkexec", "/usr/bin/dnf", "remove", "-y", pkg]],
        delete_paths=copies, warnings=warnings, blocked=blocked,
        freed=parse_size(freed) if freed else app.size,
    )
