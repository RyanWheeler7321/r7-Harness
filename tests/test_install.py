from __future__ import annotations

import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import install
from install import HarnessPaths, InstallError, install_harness, load_state, sha256_directory
from profile import ProfileSpec
from rollback import rollback, uninstall, uninstall_plan


def write_profile_templates(repository: Path) -> None:
    preset = repository / "preset"
    templates = repository / "templates"
    preset.mkdir(parents=True)
    templates.mkdir()
    (preset / "SYSTEM.md").write_text("System\n", encoding="utf-8")
    (templates / "PERSONALITY.md").write_text("Personality\n", encoding="utf-8")
    (preset / "APPEND_SYSTEM.md").write_text("Append\n", encoding="utf-8")
    (preset / "AGENTS.template.md").write_text("Instructions\n", encoding="utf-8")
    (preset / "config.yml").write_text("profile: test\n", encoding="utf-8")
    (preset / "lsp.json").write_text("{}\n", encoding="utf-8")
    (preset / "skills" / "r7harness").mkdir(parents=True)
    (preset / "skills" / "r7harness" / "SKILL.md").write_text("Skill\n", encoding="utf-8")
    for directory, filename in (("extension", "index.ts"), ("fonts", "catalog.json"), ("themes", "catalog.json")):
        target = repository / directory
        target.mkdir()
        (target / filename).write_text("{}\n", encoding="utf-8")


def write_manifest(repository: Path, checksum: str, *, patch_version: str, draft: bool) -> None:
    omp = {"version": "18.3.2", "patchVersion": patch_version, "sourceSha256": checksum}
    (repository / "manifest.json").write_text(json.dumps({"draft": draft, "omp": omp}), encoding="utf-8")


def write_source(root: Path) -> Path:
    # stands in for an already built OMP tree, the bun build steps are skipped in these tests
    source = root / "source"
    binary = source / install.ENTRYPOINT
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    native = source / install.NATIVES_DIR / "pi_natives.test.node"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"")
    return source


class InstallTests(unittest.TestCase):
    def test_draft_refusal_happens_before_any_install_root_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = write_source(root)
            write_manifest(repository, sha256_directory(source), patch_version="r7h2", draft=True)
            paths = HarnessPaths(home=root / "config", data=root / "data", profile_root=root / "profiles")

            with self.assertRaises(InstallError):
                install_harness(repository_root=repository, paths=paths, source=source, profile_spec=ProfileSpec(slug="agent"))

            self.assertFalse(paths.home.exists())
            self.assertFalse(paths.data.exists())
            self.assertFalse(paths.profile_root.exists())

    def test_wrong_source_checksum_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = write_source(root)
            write_manifest(repository, "0" * 64, patch_version="r7h2", draft=False)
            paths = HarnessPaths(home=root / "config", data=root / "data", profile_root=root / "profiles")

            with self.assertRaises(InstallError):
                install_harness(repository_root=repository, paths=paths, source=source, profile_spec=ProfileSpec(slug="agent"))
            self.assertFalse(paths.data.exists())

    @mock.patch.object(install, "BUILD_STEPS", ())
    def test_rollback_and_uninstall_only_touch_recorded_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            write_profile_templates(repository)
            source = write_source(root)
            checksum = sha256_directory(source)
            paths = HarnessPaths(home=root / "config", data=root / "data", profile_root=root / "profiles")
            stock_build = paths.build_root / "stock-omp"
            stock_build.mkdir(parents=True)
            (stock_build / "keep.txt").write_text("stock", encoding="utf-8")
            unrelated_profile = paths.profile_root / "unrelated" / "agent"
            unrelated_profile.mkdir(parents=True)
            (unrelated_profile / "keep.txt").write_text("independent", encoding="utf-8")

            write_manifest(repository, checksum, patch_version="r7h1", draft=False)
            first = install_harness(repository_root=repository, paths=paths, source=source, profile_spec=ProfileSpec(slug="first"))
            write_manifest(repository, checksum, patch_version="r7h2", draft=False)
            second = install_harness(repository_root=repository, paths=paths, source=source, profile_spec=ProfileSpec(slug="second"))

            self.assertEqual(first.build.build_id, "18.3.2-r7h1")
            self.assertEqual(second.build.build_id, "18.3.2-r7h2")
            self.assertTrue((second.build.path / second.build.entrypoint).is_file())
            selected = rollback(paths)
            self.assertEqual(selected["current_build"], first.build.build_id)
            self.assertEqual(load_state(paths)["current_build"], first.build.build_id)
            plan = uninstall_plan(paths)
            self.assertNotIn(stock_build.resolve(), plan.build_paths)
            self.assertNotIn(unrelated_profile.resolve(), plan.profile_paths)
            removed = uninstall(paths)

            self.assertIn(first.build.path.resolve(), removed)
            self.assertIn(second.build.path.resolve(), removed)
            self.assertTrue((stock_build / "keep.txt").is_file())
            self.assertTrue((unrelated_profile / "keep.txt").is_file())
            self.assertFalse(first.build.path.exists())
            self.assertFalse(second.build.path.exists())
            self.assertFalse(paths.state_path.exists())



class NativeAddonTests(unittest.TestCase):
    def test_missing_platform_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            manifest = mock.Mock(natives={})
            with self.assertRaisesRegex(install.InstallError, "no prebuilt OMP native addon"):
                install._add_natives(source, manifest, Path(temporary))

    def test_wrong_checksum_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle.tgz"
            node = root / "pi_natives.linux-x64-modern.node"
            node.write_bytes(b"addon")
            with tarfile.open(bundle, "w:gz") as archive:
                archive.add(node, arcname="package/pi_natives.linux-x64-modern.node")
            source = root / "source"
            source.mkdir()
            manifest = mock.Mock(natives={install.host_platform(): {"url": bundle.as_uri(), "sha256": "0" * 64}})
            with self.assertRaisesRegex(install.InstallError, "checksum does not match"):
                install._add_natives(source, manifest, root)
            manifest.natives[install.host_platform()]["sha256"] = install.sha256_file(bundle)
            install._add_natives(source, manifest, root)
            self.assertEqual((source / install.NATIVES_DIR / node.name).read_bytes(), b"addon")


if __name__ == "__main__":
    unittest.main()
