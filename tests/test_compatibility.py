from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from compatibility import SystemProbe, inspect_compatibility


class CompatibilityTests(unittest.TestCase):
    def _probe(self) -> SystemProbe:
        return SystemProbe(
            environment={"WSL_INTEROP": "1", "WT_SESSION": "test-terminal"},
            system="Linux",
            machine="x86_64",
            proc_version="Linux version test microsoft-standard-WSL2",
            which=lambda command: f"/fake/{command}",
            run=lambda command: subprocess.CompletedProcess(command, 0, "test 1.0\n", ""),
        )

    def test_wsl_components_are_reported_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = inspect_compatibility(
                repository_root=root / "repository",
                roots={"home": root / "config", "data": root / "data", "profiles": root / "profiles"},
                provider="openai-codex",
                model="test-model",
                probe=self._probe(),
            )

        components = report.as_dict()["component_map"]
        self.assertEqual(report.environment, "WSL2")
        self.assertEqual(components["Core OMP modification"]["status"], "supported")
        self.assertEqual(components["Windows Terminal colors"]["status"], "supported")
        self.assertEqual(components["AutoHotkey companion"]["status"], "available")
        self.assertEqual(components["Provider/model selection"]["status"], "available")

    def test_inspection_never_creates_destination_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            report = inspect_compatibility(
                repository_root=root / "repository",
                roots={"home": root / "config", "data": root / "data", "profiles": root / "profiles"},
                probe=self._probe(),
            )
            after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

        self.assertEqual(before, after)
        self.assertIn("Recommended install:", report.render())


if __name__ == "__main__":
    unittest.main()
