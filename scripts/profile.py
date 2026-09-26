# Builds an isolated OMP profile from the preset and template files in this repo.
# Auth, sessions and browser data are never copied, OMP creates those itself on first run.

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

# files every profile needs, doctor checks for these
REQUIRED_FILES = ("SYSTEM.md", "PERSONALITY.md", "APPEND_SYSTEM.md", "AGENTS.md", "config.yml", "lsp.json", "profile.json")
# repo folder -> folder inside the profile
PROFILE_DIRECTORIES = {"extension": "extension", "fonts": "fonts", "preset/skills": "skills", "themes": "themes"}


class ProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProfileSpec:
    slug: str
    user_name: str = ""
    agent_name: str = ""
    provider: str = ""
    model: str = ""
    identity_image: Path | None = None
    global_agents: Path | None = None


@dataclass(frozen=True)
class ProfileInstall:
    slug: str
    path: Path
    owned_paths: tuple[Path, ...]
    global_template_path: Path | None = None

    def as_state(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "path": str(self.path),
            "owned_paths": [str(path) for path in self.owned_paths],
            "global_template_path": str(self.global_template_path) if self.global_template_path else None,
        }


def normalize_slug(value: str) -> str:
    # "My Agent" -> "my-agent", this is also the OMP profile name
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ProfileError("profile name must contain at least one letter or number")
    if len(slug) > 64:
        raise ProfileError("profile name must be 64 characters or less")
    return slug


def profile_path(profile_root: Path, slug: str) -> Path:
    return profile_root.expanduser() / normalize_slug(slug) / "agent"


def missing_profile_artifacts(destination: Path) -> tuple[Path, ...]:
    return tuple(destination / name for name in REQUIRED_FILES if not (destination / name).is_file())


def _render(text: str, values: Mapping[str, str]) -> str:
    # plain {{key}} replacement, everything else in the file is left as is
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _profile_files(repository_root: Path) -> dict[str, Path]:
    # every file in preset/ plus templates/PERSONALITY.md, AGENTS.template.md is installed as AGENTS.md
    files: dict[str, Path] = {}
    preset = repository_root / "preset"
    if preset.is_dir():
        for source in sorted(preset.iterdir()):
            if source.is_file() and not source.name.startswith("."):
                files["AGENTS.md" if source.name == "AGENTS.template.md" else source.name] = source
    personality = repository_root / "templates" / "PERSONALITY.md"
    if personality.is_file():
        files["PERSONALITY.md"] = personality
    missing = [name for name in REQUIRED_FILES if name != "profile.json" and name not in files]
    if missing:
        raise ProfileError("required profile templates are missing: " + ", ".join(missing))
    return files


def _global_template_target(global_agents: Path, force: bool) -> Path:
    # an existing global AGENTS.md is never overwritten, the template goes next to it
    if global_agents.is_dir():
        raise ProfileError(f"global AGENTS path is a directory: {global_agents}")
    if not global_agents.parent.is_dir():
        raise ProfileError(f"global AGENTS parent does not exist: {global_agents.parent}")
    target = global_agents.with_name("AGENTS.r7harness.template.md") if global_agents.exists() else global_agents
    if target.exists() and not force:
        raise ProfileError(f"global AGENTS template already exists: {target}; use --force to replace it")
    return target


def install_profile(*, repository_root: Path, profile_root: Path, spec: ProfileSpec, force: bool = False) -> ProfileInstall:
    slug = normalize_slug(spec.slug)
    destination = profile_path(profile_root, slug)
    if destination.is_symlink():
        raise ProfileError(f"refusing to write through a profile symlink: {destination}")
    if destination.exists() and not force:
        raise ProfileError(f"profile already exists: {destination}; use --force to replace its artifacts")
    files = _profile_files(repository_root)
    for folder in PROFILE_DIRECTORIES:
        if not (repository_root / folder).is_dir():
            raise ProfileError(f"required profile directory is missing: {folder}")
    identity = spec.identity_image.expanduser() if spec.identity_image else None
    if identity is not None and not identity.is_file():
        raise ProfileError(f"identity image is not a readable file: {identity}")
    global_target = _global_template_target(spec.global_agents.expanduser(), force) if spec.global_agents else None

    # everything is checked, now write the profile
    values = {
        "profile_slug": slug,
        "profile_path": str(destination),
        "user_name": spec.user_name,
        "agent_name": spec.agent_name or slug,
        "provider": spec.provider,
        "model": spec.model,
        "identity_image": str(identity) if identity else "none",
    }
    destination.mkdir(parents=True, exist_ok=True)
    owned = [destination]
    for name, source in files.items():
        (destination / name).write_text(_render(source.read_text(encoding="utf-8"), values), encoding="utf-8")
        owned.append(destination / name)
    for folder, name in PROFILE_DIRECTORIES.items():
        shutil.copytree(repository_root / folder, destination / name, dirs_exist_ok=True)
        # record each copied file, uninstall leaves anything the user added
        for copied in sorted((repository_root / folder).rglob("*")):
            if copied.is_file():
                owned.append(destination / name / copied.relative_to(repository_root / folder))

    metadata = {
        "schema": 1,
        "slug": slug,
        "user_name": spec.user_name,
        "agent_name": values["agent_name"],
        "provider": spec.provider,
        "model": spec.model,
        "profile_path": str(destination),
    }
    (destination / "profile.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    owned.append(destination / "profile.json")
    if identity is not None:
        (destination / "identity").mkdir(exist_ok=True)
        shutil.copy2(identity, destination / "identity" / identity.name)
        owned.append(destination / "identity" / identity.name)
    if global_target is not None:
        global_target.write_text(files["AGENTS.md"].read_text(encoding="utf-8"), encoding="utf-8")
        owned.append(global_target)
    return ProfileInstall(slug=slug, path=destination, owned_paths=tuple(owned), global_template_path=global_target)
