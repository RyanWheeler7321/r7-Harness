from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "extras" / "windows" / "r7Harness.ahk"


class WindowsCompanionStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_expected_hotkeys_are_declared(self) -> None:
        for declaration in ("!CapsLock::", "^!CapsLock::", "!+CapsLock::"):
            self.assertIn(declaration, self.source)

    def test_is_standalone_and_has_no_private_machine_path(self) -> None:
        self.assertNotRegex(self.source, r"(?im)^\s*#include\b")
        self.assertNotRegex(self.source, r"(?i)(?:[a-z]:\\users\\|/mnt/[a-z]/|/home/[a-z0-9_-]+/)")

    def test_launches_titled_windows_terminal_with_wsl(self) -> None:
        self.assertIn("--title ", self.source)
        # task titles have to reach the window title
        self.assertNotIn("--suppressApplicationTitle", self.source)
        self.assertIn('WindowTitle := "r7Harness"', self.source)
        self.assertIn('"wsl.exe -e bash -lc " Chr(34) "r7harness launch" Chr(34)', self.source)
        self.assertIn('"R7HARNESS_LAUNCH_COMMAND"', self.source)

    def test_recall_matches_windows_terminal_windows(self) -> None:
        self.assertIn('"WindowsTerminal.exe"', self.source)
        self.assertIn('WinGetList("ahk_exe " ExpectedTerminalProcess)', self.source)


if __name__ == "__main__":
    unittest.main()
