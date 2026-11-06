"""Shared data types, size helpers, command running and deletion safety checks.

Nothing in this module imports GTK, so it can be used from the command line
and from tests.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
USER_APPS_DIR = HOME / ".local/share/applications"
USER_ICONS_DIR = HOME / ".local/share/icons"

# Order here is the order sources appear in the filter drop-down.
SOURCE_LABELS = {
    "rpm": "RPM package",
    "flatpak": "Flatpak",
    "snap": "Snap",
    "appimage": "AppImage",
    "webapp": "Web app",
    "manual": "Manual install",
    "leftover": "Leftover",
}

# pkexec exits with 126 when the password dialog is dismissed and 127 when
# authentication fails.
PKEXEC_CANCELLED = {126, 127}


@dataclass
class App:
    source: str               # a key of SOURCE_LABELS
    key: str                  # unique inside its source (package, app id, path…)
    name: str
    icon: str | None = None   # themed icon name or absolute path
    size: int | None = None   # bytes on disk, None when unknown
    detail: str = ""          # package name / app id / path shown under the name
    builtin: bool = False     # came with Fedora
    data: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.key}"


@dataclass
class Plan:
    """Everything that will happen when an app is removed, shown before it runs."""
    summary: str
    items: list[str] = field(default_factory=list)            # what will be removed
    commands: list[list[str]] = field(default_factory=list)   # run first, in order
    delete_paths: list[Path] = field(default_factory=list)    # user files, deleted after
    warnings: list[str] = field(default_factory=list)
    blocked: str | None = None                                # removal refused, and why
    freed: int | None = None


@dataclass
class Result:
    ok: bool
    cancelled: bool = False
    message: str = ""
    output: str = ""


# ---------------------------------------------------------------- sizes

_UNITS = {
    "b": 1, "bytes": 1,
    "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
    "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
}


def parse_size(text: str) -> int | None:
    """Turn '233.3 MB' or '1,2 GiB' into bytes."""
    m = re.match(r"\s*([\d.,]+)\s*([A-Za-z]+)?", text or "")
    if not m:
        return None
    try:
        number = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    unit = (m.group(2) or "b").lower()
    if unit not in _UNITS:
        return None
    return int(number * _UNITS[unit])


def format_size(n: int | None) -> str:
    """Format bytes the way GNOME does (decimal units)."""
    if n is None:
        return ""
    if n < 1000:
        return f"{n} bytes"
    for unit in ("kB", "MB", "GB", "TB"):
        n /= 1000
        if n < 1000 or unit == "TB":
            return f"{n:.1f} {unit}"
    return ""


def path_size(path: Path) -> int:
    """Disk usage of a file or folder. Does not follow symlinks."""
    try:
        st = path.lstat()
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    total = 0
    seen: set[tuple[int, int]] = set()
    stack = [str(path)]
    while stack:
        try:
            it = os.scandir(stack.pop())
        except OSError:
            continue
        with it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    s = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if s.st_nlink > 1:
                    ident = (s.st_dev, s.st_ino)
                    if ident in seen:
                        continue
                    seen.add(ident)
                total += s.st_size
    return total


def short_path(path: Path | str) -> str:
    p = str(path)
    home = str(HOME)
    if p == home:
        return "~"
    if p.startswith(home + "/"):
        return "~" + p[len(home):]
    return p


def describe_icons(icons: list[Path]) -> list[str]:
    """'8 icon files named appimagekit-pcloud' instead of one line per size."""
    by_name: dict[str, list[Path]] = {}
    for i in icons:
        by_name.setdefault(i.stem, []).append(i)
    lines = []
    for name, files in by_name.items():
        if len(files) == 1:
            lines.append(f"Icon {short_path(files[0])}")
        else:
            lines.append(f"{len(files)} icon files named {name} (all sizes)")
    return lines


# ---------------------------------------------------------------- commands

def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a command with English output so it can be parsed."""
    env = dict(os.environ, LC_ALL="C", LANG="C")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, env=env)


def command_text(cmd: list[str]) -> str:
    import shlex
    return " ".join(shlex.quote(c) for c in cmd)


# ---------------------------------------------------------------- safety

# Folders that hold many apps or the user's own files. They are never deleted,
# and an app's "install folder" is the folder directly inside one of these.
CONTAINER_DIRS = {
    HOME, HOME / "Applications", HOME / "applications", HOME / "Downloads",
    HOME / "Desktop", HOME / "apps", HOME / "Apps", HOME / "opt",
    HOME / "Programs", HOME / ".local", HOME / ".local/share",
    HOME / ".local/opt", HOME / ".local/lib",
    Path("/opt"), Path("/usr/local"), Path("/usr/local/lib"),
    Path("/usr/local/share"),
}

BIN_DIRS = {HOME / ".local/bin", HOME / "bin", Path("/usr/local/bin")}


def _xdg_user_dirs() -> set[Path]:
    dirs = set()
    try:
        text = (HOME / ".config/user-dirs.dirs").read_text()
    except OSError:
        return dirs
    for m in re.finditer(r'^XDG_\w+_DIR="(.*)"', text, re.M):
        dirs.add(Path(m.group(1).replace("$HOME", str(HOME))))
    return dirs


PROTECTED_DIRS = CONTAINER_DIRS | BIN_DIRS | _xdg_user_dirs() | {
    Path(p) for p in (
        "/", "/usr", "/usr/bin", "/usr/sbin", "/usr/lib", "/usr/lib64",
        "/usr/share", "/usr/share/applications", "/usr/share/icons",
        "/usr/local/share/applications", "/etc", "/var", "/home", "/root",
        "/boot", "/tmp", "/srv", "/mnt", "/media", "/run", "/proc", "/sys",
    )
} | {
    HOME / p for p in (
        "Documents", "Music", "Pictures", "Videos", "Templates", "Public",
        "Projects", ".config", ".cache", ".ssh", ".gnupg", ".var", ".var/app",
        ".local/share/applications", ".local/share/icons", ".local/state",
        "snap", ".mozilla", ".pki",
    )
}

# Places App Cleaner is ever allowed to delete from.
ALLOWED_ROOTS = (HOME, Path("/opt"), Path("/usr/local"), Path("/usr/share/applications"))


def is_rpm_owned(path: Path) -> bool:
    try:
        import rpm  # type: ignore
        ts = rpm.TransactionSet()
        return any(True for _ in ts.dbMatch("basenames", str(path)))
    except ImportError:
        r = run(["rpm", "-qf", str(path)])
        return r.returncode == 0


def deletion_problem(path: Path) -> str | None:
    """Return why `path` must not be deleted, or None when it is safe."""
    if not path.is_absolute():
        return f"{path} is not an absolute path"
    norm = Path(os.path.normpath(path))
    real = Path(os.path.realpath(norm.parent)) / norm.name
    for p in (norm, real):
        if p in PROTECTED_DIRS:
            return f"{short_path(p)} is a protected folder"
    if len(norm.parts) < 3:
        return f"{norm} is too close to the top of the file system"
    if not any(norm.is_relative_to(root) for root in ALLOWED_ROOTS):
        return f"{norm} is outside the folders App Cleaner may change"
    if not norm.is_relative_to(HOME) and is_rpm_owned(norm):
        return f"{norm} belongs to an installed package"
    if norm.is_dir() and not norm.is_symlink() and (norm / ".git").exists():
        return f"{short_path(norm)} looks like a code project (it has a .git folder)"
    return None


def needs_root(path: Path) -> bool:
    """True when the current user can't delete `path` on their own."""
    parent = path.parent
    if not os.access(parent, os.W_OK | os.X_OK):
        return True
    if path.is_dir() and not path.is_symlink():
        for dirpath, dirnames, _ in os.walk(path):
            if not os.access(dirpath, os.W_OK | os.X_OK):
                return True
    return False


def delete_path(path: Path) -> None:
    problem = deletion_problem(path)
    if problem:
        raise PermissionError(problem)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def root_delete_command(path: Path) -> list[str]:
    """A pkexec command deleting `path`. Callers must check deletion_problem first."""
    return ["pkexec", "/usr/bin/rm", "-rf", "--", str(path)]


# ---------------------------------------------------------------- execute

def execute(plan: Plan, dry_run: bool = False) -> Result:
    if plan.blocked:
        return Result(False, message=plan.blocked)
    log: list[str] = []
    if dry_run:
        for cmd in plan.commands:
            log.append("would run: " + command_text(cmd))
        for p in plan.delete_paths:
            log.append("would delete: " + str(p))
        return Result(True, message="Dry run: nothing was removed", output="\n".join(log))

    for cmd in plan.commands:
        log.append("$ " + command_text(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.append(str(e))
            return Result(False, message=str(e), output="\n".join(log))
        log.append((r.stdout + r.stderr).strip())
        if cmd[0] == "pkexec" and r.returncode in PKEXEC_CANCELLED:
            return Result(False, cancelled=True, message="Cancelled",
                          output="\n".join(log))
        if r.returncode != 0:
            return Result(False, message=f"{cmd[0] if cmd[0] != 'pkexec' else cmd[1]} "
                                         f"failed (exit code {r.returncode})",
                          output="\n".join(log))

    errors = []
    for p in plan.delete_paths:
        try:
            if p.exists() or p.is_symlink():
                delete_path(p)
                log.append("deleted " + str(p))
        except OSError as e:
            errors.append(f"{p}: {e}")
    refresh_desktop_caches(plan.delete_paths)
    if errors:
        log.extend(errors)
        return Result(False, message="Some files could not be deleted",
                      output="\n".join(log))
    return Result(True, message="Removed", output="\n".join(log))


def refresh_desktop_caches(paths: list[Path]) -> None:
    """Tell the desktop that menu entries / icons changed."""
    if any(p.is_relative_to(USER_APPS_DIR) for p in paths) and shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(USER_APPS_DIR)],
                       capture_output=True, stdin=subprocess.DEVNULL)
    hicolor = USER_ICONS_DIR / "hicolor"
    if any(p.is_relative_to(hicolor) for p in paths) and (hicolor / "index.theme").exists() \
            and shutil.which("gtk-update-icon-cache"):
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(hicolor)],
                       capture_output=True, stdin=subprocess.DEVNULL)
