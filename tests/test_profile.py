from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from profile import ProfileError, ProfileSpec, install_profile


def write_profile_templates(repository: Path) -> None:
    preset = repository / "preset"
    templates = repository / "templates"
    preset.mkdir(parents=True)
    templates.mkdir()
    (preset / "SYSTEM.md").write_text("System for {{agent_name}}\n", encoding="utf-8")
    (templates / "PERSONALITY.md").write_text("Personality for {{user_name}}\n", encoding="utf-8")
    (preset / "APPEND_SYSTEM.md").write_text("Provider {{provider}} / {{model}}\n", encoding="utf-8")
    (preset / "AGENTS.template.md").write_text("Local instructions\n", encoding="utf-8")
    (preset / "config.yml").write_text("profile: {{profile_slug}}\n", encoding="utf-8")
    (preset / "lsp.json").write_text('{"enabled": true}\n', encoding="utf-8")
    (preset / "skills" / "r7harness").mkdir(parents=True)
    (preset / "skills" / "r7harness" / "SKILL.md").write_text("Skill\n", encoding="utf-8")
    for directory, filename in (("extension", "index.ts"), ("fonts", "catalog.json"), ("themes", "catalog.json")):
        target = repository / directory
        target.mkdir()
        (target / filename).write_text("{}\n", encoding="utf-8")


class ProfileTests(unittest.TestCase):
    def test_profile_isolated_and_existing_global_agents_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            write_profile_templates(repository)
            profiles = root / "profiles"
            global_agents = root / "workspace" / "AGENTS.md"
            global_agents.parent.mkdir()
            global_agents.write_text("Existing owner instructions\n", encoding="utf-8")
            identity = root / "identity.png"
            identity.write_bytes(b"not-a-session")

            result = install_profile(
                repository_root=repository,
                profile_root=profiles,
                spec=ProfileSpec(
                    slug="My Agent",
                    user_name="User",
                    agent_name="Agent",
                    provider="openai-codex",
                    model="test-model",
                    identity_image=identity,
                    global_agents=global_agents,
                ),
            )

            self.assertEqual(result.path, profiles / "my-agent" / "agent")
            self.assertEqual(global_agents.read_text(encoding="utf-8"), "Existing owner instructions\n")
            self.assertEqual(
                (global_agents.parent / "AGENTS.r7harness.template.md").read_text(encoding="utf-8"),
                "Local instructions\n",
            )
            self.assertTrue((result.path / "SYSTEM.md").is_file())
            self.assertTrue((result.path / "PERSONALITY.md").is_file())
            self.assertTrue((result.path / "APPEND_SYSTEM.md").is_file())
            self.assertTrue((result.path / "AGENTS.md").is_file())
            self.assertTrue((result.path / "config.yml").is_file())
            self.assertTrue((result.path / "lsp.json").is_file())
            self.assertTrue((result.path / "extension" / "index.ts").is_file())
            self.assertTrue((result.path / "skills" / "r7harness" / "SKILL.md").is_file())
            self.assertTrue((result.path / "themes" / "catalog.json").is_file())
            self.assertTrue((result.path / "fonts" / "catalog.json").is_file())
            self.assertTrue((result.path / "identity" / "identity.png").is_file())
            self.assertFalse((result.path / "auth").exists())
            self.assertFalse((result.path / "sessions").exists())

    def test_existing_profile_is_not_overwritten_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            write_profile_templates(repository)
            first = install_profile(
                repository_root=repository,
                profile_root=root / "profiles",
                spec=ProfileSpec(slug="agent"),
            )
            original = (first.path / "SYSTEM.md").read_text(encoding="utf-8")

            with self.assertRaises(ProfileError):
                install_profile(
                    repository_root=repository,
                    profile_root=root / "profiles",
                    spec=ProfileSpec(slug="agent", agent_name="Replacement"),
                )

            self.assertEqual((first.path / "SYSTEM.md").read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
