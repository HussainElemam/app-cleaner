# App Cleaner

One window that lists every app on your computer, shows where it came from,
and removes it with one click, using the right method for each kind of app.

| Source | How it's found | How it's removed |
|---|---|---|
| **RPM package** (dnf, GNOME Software, downloaded `.rpm`) | menu entries owned by a package | `pkexec dnf remove -y <pkg>` (after a dry-run preview) |
| **Flatpak** | `flatpak list --app` | `flatpak uninstall -y --delete-data` |
| **Snap** | snapd API (`/run/snapd.socket`) | `pkexec snap remove --purge <name>` |
| **AppImage** | `~/Applications`, `~/Downloads`, `~`… and anything a menu entry starts | deletes the file, its menu entries, icons and launcher script |
| **Web app** | Chrome/Brave/Edge `--app-id=` shortcuts, PWAsForFirefox | deletes the shortcut / `firefoxpwa site uninstall` |
| **Manual install** | menu entries no package manager owns (e.g. Android Studio) | deletes the install folder (with `pkexec` if it's in `/opt` or `/usr/local`) |
| **Leftover** | broken shortcuts, `~/.var/app/<id>` from removed Flatpaks | deletes them |

Your own copies of menu entries (e.g. `~/.local/share/applications/Zoom.desktop`)
are recognized and removed together with the app they belong to.

## Use it

```sh
./install.sh            # adds "App Cleaner" to your app menu and `app-cleaner` to ~/.local/bin
app-cleaner             # open the window
app-cleaner --dry-run   # try everything without removing anything
app-cleaner --list      # print all apps in the terminal (--all includes Fedora's own)
app-cleaner --plan zoom # print exactly what removing an app would do, then exit
./uninstall.sh          # remove the menu entry and command again
```

In the window: type to search, pick a source in the first drop-down, sort by
size to find the big ones. Apps that came with Fedora are hidden; turn them on
from the ☰ menu.

## Safety

- **Nothing happens without a confirmation** that lists everything that will be
  removed, plus the exact commands (click *Show exact commands*).
- **dnf removals are previewed first** (`dnf remove --assumeno`). If removing an app
  would also remove something the desktop needs (GNOME Shell, Files, the
  terminal, dnf…), App Cleaner refuses.
- **Protected folders are never deleted:** your home folder, Projects, Documents,
  Downloads (the folder itself), `~/.config`, `/usr`, `/opt`, and so on. Folders
  with a `.git` folder inside are treated as code projects and left alone too.
- **For manual installs it only deletes the app's own folder**, for example
  `/usr/local/android-studio`. If the program lives somewhere shared, like
  `~/.cargo/bin` or one of your projects, only the menu entry is removed.
- **It runs as you, not as root.** When root is needed you get the normal GNOME
  password prompt (`pkexec`).
- **Deletion is permanent** (no Trash). AppImage settings in `~/.config` are not
  touched.

## Develop

```sh
/usr/bin/python3 -m unittest discover -s tests -v
```

The code is plain Python and uses the system's GTK 4 / libadwaita bindings, so
there is nothing to pip-install. Each source lives in `app_cleaner/sources/`
and has two functions: `scan()` finds the apps and `plan()` describes how to
remove one. `core.execute()` is the only code that runs commands or deletes files.
