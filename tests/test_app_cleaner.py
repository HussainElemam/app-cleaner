"""Tests for the parts that decide what gets deleted. Run with:

    /usr/bin/python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_cleaner import core, desktop  # noqa: E402
from app_cleaner.sources import appimage, manual, rpm, webapp  # noqa: E402

HOME = core.HOME


class SizeTests(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(core.parse_size("233.3 MB"), 233_300_000)
        self.assertEqual(core.parse_size("947 MiB"), 947 * 1024**2)
        self.assertEqual(core.parse_size("1,5 GB"), 1_500_000_000)
        self.assertEqual(core.parse_size("12 bytes"), 12)
        self.assertIsNone(core.parse_size("lots"))

    def test_format_size(self):
        self.assertEqual(core.format_size(None), "")
        self.assertEqual(core.format_size(999), "999 bytes")
        self.assertEqual(core.format_size(144_400_000), "144.4 MB")
        self.assertEqual(core.format_size(3_700_000_000), "3.7 GB")


class ExecParsingTests(unittest.TestCase):
    def test_field_codes_and_quotes(self):
        self.assertEqual(desktop.parse_exec('"/home/u/My App/run" --x %U'),
                         ["/home/u/My App/run", "--x"])

    def test_wrappers_are_removed(self):
        self.assertEqual(desktop.parse_exec("/home/u/.local/bin/app-scope /usr/bin/zoom %U"),
                         ["/usr/bin/zoom"])
        self.assertEqual(desktop.parse_exec("env GDK_BACKEND=x11 FOO=1 /opt/x/x"), ["/opt/x/x"])
        self.assertEqual(desktop.parse_exec("systemd-run --user --scope -- /opt/y/y"), ["/opt/y/y"])


def entry(exec_line, name="X", path="/tmp/x.desktop"):
    return desktop.Entry(path=Path(path), name=name, icon=None, exec_line=exec_line,
                         all_exec=exec_line, argv=desktop.parse_exec(exec_line), visible=True)


class WebAppTests(unittest.TestCase):
    def test_chrome(self):
        info = webapp.detect(entry("app-scope /opt/google/chrome/google-chrome "
                                   "--profile-directory=Default --app-id=abc %U"))
        self.assertEqual(info, {"kind": "chromium", "id": "abc", "browser": "Chrome"})

    def test_firefoxpwa(self):
        info = webapp.detect(entry("/usr/bin/firefoxpwa site launch 01ABC --protocol %u"))
        self.assertEqual(info["kind"], "firefoxpwa")
        self.assertEqual(info["id"], "01ABC")

    def test_normal_app_is_not_a_web_app(self):
        self.assertIsNone(webapp.detect(entry("/usr/bin/firefox %u")))


class DnfPreviewTests(unittest.TestCase):
    OUTPUT = """\
Package               Arch   Version     Repository   Size
Removing:
 wireshark            x86_64 1:4.4.0-1   updates     16.3 MiB
Removing dependent packages:
 wireshark-plugins    x86_64 1:4.4.0-1   updates      1.0 MiB
Removing unused dependencies:
 libsmi               x86_64 0.4.8-1     fedora       2.0 MiB

Transaction Summary:
 Removing:           3 packages

After this operation, 19 MiB will be freed (install 0 B, remove 19 MiB).
Operation aborted by the user.
"""

    def test_parse(self):
        packages, freed = rpm.parse_dnf_preview(self.OUTPUT)
        self.assertEqual(packages, [
            ("wireshark", "Removing"),
            ("wireshark-plugins", "Removing dependent packages"),
            ("libsmi", "Removing unused dependencies"),
        ])
        self.assertEqual(freed, "19 MiB")

    def test_problem_has_no_packages(self):
        packages, _ = rpm.parse_dnf_preview(
            "Failed to resolve the transaction:\nProblem: The operation would result in "
            "removing the following protected packages: gnome-shell\n")
        self.assertEqual(packages, [])


class AppImageNameTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(appimage.name_from_file(Path("arduino-ide_2.3.10_Linux_64bit.AppImage")),
                         "Arduino Ide")
        self.assertEqual(appimage.name_from_file(Path("FontForge-2025-10-09-Linux-x86_64.AppImage")),
                         "FontForge")
        self.assertEqual(appimage.name_from_file(Path("pCloud.AppImage")), "pCloud")


class SafetyTests(unittest.TestCase):
    def test_protected_folders_are_refused(self):
        for p in [HOME, HOME / "Projects", HOME / "Downloads", HOME / ".config",
                  HOME / ".local/share/applications", Path("/usr"), Path("/opt"),
                  Path("/usr/local"), Path("/")]:
            self.assertIsNotNone(core.deletion_problem(p), p)

    def test_outside_allowed_roots(self):
        self.assertIsNotNone(core.deletion_problem(Path("/etc/foo/bar")))
        self.assertIsNotNone(core.deletion_problem(Path("/usr/lib/thing")))

    def test_package_files_are_refused(self):
        owned = Path("/usr/share/applications/org.mozilla.firefox.desktop")
        if not owned.exists():
            self.skipTest("Firefox RPM not installed")
        self.assertIn("package", core.deletion_problem(owned))

    def test_git_projects_are_refused(self):
        with tempfile.TemporaryDirectory(dir=HOME / ".cache") as tmp:
            project = Path(tmp) / "proj"
            (project / ".git").mkdir(parents=True)
            self.assertIn(".git", core.deletion_problem(project))

    def test_relative_path(self):
        self.assertIsNotNone(core.deletion_problem(Path("foo/bar")))


class InstallFolderTests(unittest.TestCase):
    """find_install decides how much of the disk a manual app owns."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.containers = {self.home, self.home / "Downloads", self.root / "opt"}
        self.bins = {self.home / ".local/bin"}
        for d in self.containers | self.bins:
            d.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def find(self, target):
        return manual.find_install(target, containers=self.containers, bin_dirs=self.bins,
                                   home=self.home, check=lambda p: None)

    def make(self, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        return p

    def test_folder_in_opt(self):
        exe = self.make("opt/android-studio/bin/studio")
        inst = self.find(exe)
        self.assertEqual((inst.mode, inst.delete), ("folder", [self.root / "opt/android-studio"]))

    def test_tarball_in_downloads(self):
        exe = self.make("home/Downloads/tsetup.6.3.6/Telegram/Telegram")
        inst = self.find(exe)
        self.assertEqual(inst.delete, [self.home / "Downloads/tsetup.6.3.6"])

    def test_single_binary_in_bin(self):
        exe = self.make("home/.local/bin/screen-recorder")
        self.assertEqual(self.find(exe).delete, [exe])

    def test_symlink_in_bin_deletes_link_and_folder(self):
        real = self.make("opt/tool/tool")
        link = self.home / ".local/bin/tool"
        link.symlink_to(real)
        inst = self.find(link)
        self.assertEqual(inst.delete, [link, self.root / "opt/tool"])

    def test_hidden_home_folder_is_left_alone(self):
        exe = self.make("home/.cargo/bin/thing")
        inst = self.find(exe)
        self.assertEqual((inst.mode, inst.delete), ("launcher-only", []))

    def test_shared_home_folder_is_left_alone(self):
        exe = self.make("home/go/bin/thing")
        self.assertEqual(self.find(exe).mode, "launcher-only")

    def test_unsafe_root_is_left_alone(self):
        exe = self.make("home/Projects/app/run.sh")
        inst = manual.find_install(exe, containers=self.containers, bin_dirs=self.bins,
                                   home=self.home,
                                   check=lambda p: "protected" if p == self.home / "Projects" else None)
        self.assertEqual(inst.mode, "launcher-only")


class ExecuteTests(unittest.TestCase):
    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory(dir=HOME / ".cache") as tmp:
            f = Path(tmp) / "fake.AppImage"
            f.write_text("x")
            plan = core.Plan(summary="", commands=[["false"]], delete_paths=[f])
            result = core.execute(plan, dry_run=True)
            self.assertTrue(result.ok)
            self.assertTrue(f.exists())

    def test_deletes_files_after_commands(self):
        with tempfile.TemporaryDirectory(dir=HOME / ".cache") as tmp:
            f = Path(tmp) / "fake.AppImage"
            f.write_text("x")
            result = core.execute(core.Plan(summary="", commands=[["true"]], delete_paths=[f]))
            self.assertTrue(result.ok, result.output)
            self.assertFalse(f.exists())

    def test_failed_command_keeps_files(self):
        with tempfile.TemporaryDirectory(dir=HOME / ".cache") as tmp:
            f = Path(tmp) / "fake.AppImage"
            f.write_text("x")
            result = core.execute(core.Plan(summary="", commands=[["false"]], delete_paths=[f]))
            self.assertFalse(result.ok)
            self.assertTrue(f.exists())

    def test_blocked_plan_never_runs(self):
        with mock.patch("subprocess.run") as run:
            result = core.execute(core.Plan(summary="", commands=[["true"]], blocked="no"))
            self.assertFalse(result.ok)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
