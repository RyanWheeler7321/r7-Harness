from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from install import load_repository_manifest


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    def test_draft_is_explicit_and_source_is_immutable(self) -> None:
        self.assertIsInstance(self.manifest["draft"], bool)
        omp = self.manifest["omp"]
        self.assertRegex(omp["commit"], r"^[0-9a-f]{40}$")
        self.assertIn(omp["commit"], omp["sourceUrl"])
        self.assertRegex(omp["sourceSha256"], r"^[0-9a-f]{64}$")

    def test_patch_exists_and_matches_manifest_hash(self) -> None:
        omp = self.manifest["omp"]
        patch = ROOT / omp["patch"]
        self.assertTrue(patch.is_file())
        digest = hashlib.sha256(patch.read_bytes()).hexdigest()
        self.assertEqual(omp["patchSha256"], digest)

    def test_install_roots_are_side_by_side_user_paths(self) -> None:
        paths = self.manifest["paths"]
        self.assertTrue(paths["configRoot"].startswith("~/.config/r7harness"))
        self.assertTrue(paths["dataRoot"].startswith("~/.local/share/r7harness"))
        self.assertIn("{agentSlug}", paths["profileRoot"])

    def test_installer_understands_manifest_checksums_and_patch(self) -> None:
        loaded = load_repository_manifest(ROOT)
        self.assertEqual(loaded.source_sha256, self.manifest["omp"]["sourceSha256"])
        self.assertEqual(loaded.patch_path, ROOT / self.manifest["omp"]["patch"])
        self.assertEqual(loaded.patch_sha256, self.manifest["omp"]["patchSha256"])

    def test_version_fields_agree(self) -> None:
        # catches a half-updated manifest after an OMP version bump
        omp = self.manifest["omp"]
        self.assertEqual(omp["tag"], "v" + omp["version"])
        self.assertEqual(omp["upstreamVersion"], omp["version"])
        self.assertEqual(Path(omp["patch"]).name, f"omp-{omp['version']}-{omp['patchVersion']}.patch")
        self.assertEqual(load_repository_manifest(ROOT).build_id, f"{omp['version']}-{omp['patchVersion']}")


if __name__ == "__main__":
    unittest.main()
