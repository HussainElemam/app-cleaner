#!/bin/sh
# Install App Cleaner for the current user (no root needed):
#   ~/.local/bin/app-cleaner                         -> link to this folder's launcher
#   ~/.local/share/applications/<id>.desktop         -> app menu entry
#   ~/.local/share/icons/hicolor/scalable/apps/<id>.svg
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
id="io.github.hussainelemam.AppCleaner"
bin="$HOME/.local/bin"
apps="$HOME/.local/share/applications"
icons="$HOME/.local/share/icons/hicolor/scalable/apps"

mkdir -p "$bin" "$apps" "$icons"
ln -sf "$here/app-cleaner" "$bin/app-cleaner"
cp "$here/data/$id.svg" "$icons/$id.svg"

# Start through app-scope when it exists, like your other menu entries.
exec_line="$bin/app-cleaner"
[ -x "$bin/app-scope" ] && exec_line="$bin/app-scope $bin/app-cleaner"
sed "s|@EXEC@|$exec_line|" "$here/data/$id.desktop.in" > "$apps/$id.desktop"

command -v update-desktop-database >/dev/null && update-desktop-database "$apps" || true
command -v gtk-update-icon-cache >/dev/null && \
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
echo "Installed. Open \"App Cleaner\" from Activities, or run: app-cleaner"
