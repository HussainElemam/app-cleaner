"""Command line entry point.

    app-cleaner              open the window
    app-cleaner --dry-run    open the window, but never really remove anything
    app-cleaner --list       print every app found and exit
    app-cleaner --plan NAME  print what removing matching apps would do (removes nothing)
"""
from __future__ import annotations

import argparse
import sys

import gi

gi.require_version("GLib", "2.0")


def main() -> int:
    parser = argparse.ArgumentParser(prog="app-cleaner", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be removed without removing anything")
    parser.add_argument("--list", action="store_true", help="print the apps found and exit")
    parser.add_argument("--all", action="store_true",
                        help="with --list: include apps that came with Fedora")
    parser.add_argument("--plan", metavar="NAME",
                        help="print the removal plan for apps whose name contains NAME")
    args = parser.parse_args()

    if args.list or args.plan:
        return cli(args)

    from .ui import run_gui
    return run_gui(dry_run=args.dry_run)


def cli(args) -> int:
    from .core import SOURCE_LABELS, command_text, format_size, short_path
    from .sources import Inventory

    inv = Inventory()
    apps = inv.scan()
    for source, error in inv.errors:
        print(f"!! could not read {source}: {error}", file=sys.stderr)

    if args.list:
        shown = [a for a in apps if args.all or not a.builtin]
        order = list(SOURCE_LABELS)
        for a in sorted(shown, key=lambda a: (order.index(a.source), a.name.lower())):
            flag = f" [{a.data.get('badge') or 'built-in'}]" if a.builtin else ""
            print(f"{SOURCE_LABELS[a.source]:<15} {format_size(a.size):>10}  {a.name}{flag}  — {a.detail}")
        hidden = sum(a.builtin for a in apps) if not args.all else 0
        print(f"\n{len(shown)} apps" + (f" ({hidden} built-in hidden, use --all)" if hidden else ""))
        return 0

    needle = args.plan.lower()
    matches = [a for a in apps if needle in a.name.lower() or needle in a.detail.lower()]
    if not matches:
        print("no app matches", args.plan)
        return 1
    for a in matches:
        p = inv.plan(a)
        print(f"=== {a.name}  [{SOURCE_LABELS[a.source]}]")
        if p.blocked:
            print("BLOCKED:", p.blocked)
        else:
            print(p.summary)
        for w in p.warnings:
            print("  warning:", w)
        for i in p.items:
            print("  -", i)
        for c in p.commands:
            print("  $", command_text(c))
        for d in p.delete_paths:
            print("  delete", short_path(d))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
