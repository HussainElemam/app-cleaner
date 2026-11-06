#!/bin/sh
# Remove what install.sh added. The project folder itself is left alone.
set -eu
id="io.github.hussainelemam.AppCleaner"
rm -f "$HOME/.local/bin/app-cleaner" \
      "$HOME/.local/share/applications/$id.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/$id.svg"
command -v update-desktop-database >/dev/null && \
    update-desktop-database "$HOME/.local/share/applications" || true
echo "App Cleaner was removed from your app menu."
