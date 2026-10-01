# Side-by-side install of the patched OMP build and an isolated profile.
# Everything it creates is recorded in state.json, rollback and uninstall only touch those paths.

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from profile import ProfileInstall, ProfileSpec, install_profile, profile_path

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
BUILD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STATE_SCHEMA = 1

# OMP build: workspace install from the lockfile, then the omp binary in packages/coding-agent
BUILD_STEPS = (
    (".", ("bun", "install", "--frozen-lockfile")),
    ("packages/coding-agent", ("bun", "run", "build")),
)
ENTRYPOINT = "packages/coding-agent/dist/omp"
NATIVES_DIR = "packages/natives/native"


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class HarnessPaths:
    home: Path
    data: Path
    profile_root: Path

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "HarnessPaths":
        env = os.environ if environment is None else environment
        return cls(
            home=Path(env.get("R7HARNESS_HOME", "~/.config/r7harness")).expanduser(),
            data=Path(env.get("R7HARNESS_DATA", "~/.local/share/r7harness")).expanduser(),
            profile_root=Path(env.get("OMP_PROFILE_ROOT", "~/.omp/profiles")).expanduser(),
        )

    @property
    def state_path(self) -> Path:
        return self.home / "state.json"

    @property
    def current_path(self) -> Path:
        return self.data / "current.json"

    @property
    def build_root(self) -> Path:
        return self.data / "omp"


@dataclass(frozen=True)
class RepositoryManifest:
    path: Path
    raw: Mapping[str, Any]
    draft: bool
    build_id: str
    omp_version: str
    source_sha256: str | None
    patch_path: Path | None
    patch_sha256: str | None
    natives: Mapping[str, Any]


@dataclass(frozen=True)
class BuildInstall:
    build_id: str
    path: Path
    source_sha256: str
    entrypoint: str

    def as_state(self) -> dict[str, object]:
        return {
            "build_id": self.build_id,
            "path": str(self.path),
            "source_sha256": self.source_sha256,
            "entrypoint": self.entrypoint,
            "installed_at": utc_timestamp(),
        }


@dataclass(frozen=True)
class InstallResult:
    build: BuildInstall
    profile: ProfileInstall
    state_path: Path
    current_path: Path

    def as_dict(self) -> dict[str, object]:
        return {
            "build": {"build_id": self.build.build_id, "path": str(self.build.path), "entrypoint": self.build.entrypoint},
            "profile": self.profile.as_state(),
            "state_path": str(self.state_path),
            "current_path": str(self.current_path),
        }


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_within(candidate: Path, parent: Path) -> bool:
    return candidate.resolve().is_relative_to(parent.resolve())


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def load_repository_manifest(repository_root: Path) -> RepositoryManifest:
    path = repository_root / "manifest.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise InstallError(f"repository manifest is missing: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"repository manifest is invalid: {error}") from error
    if not isinstance(raw, dict):
        raise InstallError("repository manifest must contain a JSON object")

    omp = raw["omp"] if isinstance(raw.get("omp"), dict) else {}
    version = _text(omp.get("version"))
    if not version:
        raise InstallError("repository manifest does not declare an OMP version")
    # build id is the upstream version plus the patch version, e.g. 18.4.4-r7h3
    patch_version = _text(omp.get("patchVersion"))
    build_id = f"{version}-{patch_version}" if patch_version else version
    if not BUILD_ID_RE.fullmatch(build_id):
        raise InstallError("manifest build identifier contains unsupported path characters")
    patch = _text(omp.get("patch"))
    if patch and (Path(patch).is_absolute() or ".." in Path(patch).parts):
        raise InstallError("manifest patch path must stay inside the repository")
    draft = raw.get("draft", True)
    if not isinstance(draft, bool):
        raise InstallError("manifest draft value must be true or false")
    return RepositoryManifest(
        path=path,
        raw=raw,
        draft=draft,
        build_id=build_id,
        omp_version=version,
        source_sha256=_text(omp.get("sourceSha256")),
        patch_path=repository_root / patch if patch else None,
        patch_sha256=_text(omp.get("patchSha256")),
        natives=omp["natives"] if isinstance(omp.get("natives"), dict) else {},
    )


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def empty_state() -> dict[str, Any]:
    return {"schema": STATE_SCHEMA, "builds": {}, "profiles": {}, "current_build": None, "current_profile": None}


def load_state(paths: HarnessPaths) -> dict[str, Any]:
    # no state file means nothing is installed yet
    try:
        state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state()
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"owned state is invalid: {error}") from error
    if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA:
        raise InstallError("owned state has an unsupported schema")
    for key in ("builds", "profiles"):
        if not isinstance(state.get(key), dict):
            raise InstallError(f"owned state has an invalid {key} section")
    return state


def save_state(paths: HarnessPaths, state: Mapping[str, Any]) -> None:
    write_json(paths.state_path, state)
    # current.json points other tools at the selected build and profile
    build = state["builds"].get(state.get("current_build") or "") or {}
    profile = state["profiles"].get(state.get("current_profile") or "") or {}
    write_json(
        paths.current_path,
        {
            "schema": STATE_SCHEMA,
            "current_build": state.get("current_build"),
            "current_profile": state.get("current_profile"),
            "build_path": build.get("path"),
            "profile_path": profile.get("path"),
        },
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_directory(path: Path) -> str:
    # stable hash of relative paths and file contents, symlinks are refused
    digest = hashlib.sha256()
    for member in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        relative = member.relative_to(path).as_posix()
        if member.is_symlink():
            raise InstallError(f"source directory contains a symlink: {relative}")
        if member.is_dir():
            continue
        if not member.is_file():
            raise InstallError(f"source directory contains an unsupported entry: {relative}")
        digest.update(relative.encode("utf-8") + b"\0")
        with member.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def source_checksum(path: Path) -> str:
    if path.is_file():
        return sha256_file(path)
    if path.is_dir():
        return sha256_directory(path)
    raise InstallError(f"source payload does not exist: {path}")


def _expected_sha256(label: str, value: str | None) -> str:
    if not value or not SHA256_RE.fullmatch(value):
        raise InstallError(f"{label} checksum is missing or invalid; refusing to install an unverified build")
    return value.lower()


def verify_install_inputs(manifest: RepositoryManifest, source: Path, *, allow_draft: bool) -> str:
    # runs before anything is written
    if manifest.draft and not allow_draft:
        raise InstallError("this repository manifest is marked draft; pass --allow-draft to install it")
    expected = _expected_sha256("source", manifest.source_sha256)
    actual = source_checksum(source.expanduser())
    if actual != expected:
        raise InstallError("source checksum does not match the repository manifest")
    if manifest.patch_path is not None:
        if not manifest.patch_path.is_file():
            raise InstallError(f"manifest patch is missing: {manifest.patch_path}")
        if manifest.patch_sha256 is not None:
            if sha256_file(manifest.patch_path) != _expected_sha256("patch", manifest.patch_sha256):
                raise InstallError("patch checksum does not match the repository manifest")
    return actual


def _safe_member(name: str) -> bool:
    member = Path(name)
    return bool(name) and not member.is_absolute() and ".." not in member.parts


def _extract_archive(source: Path, destination: Path) -> Path:
    name = source.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                if not _safe_member(member.filename):
                    raise InstallError(f"archive contains an unsafe path: {member.filename}")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise InstallError(f"archive contains a symlink: {member.filename}")
            archive.extractall(destination)
    elif name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
        with tarfile.open(source, "r:*") as archive:
            for member in archive.getmembers():
                if not _safe_member(member.name):
                    raise InstallError(f"archive contains an unsafe path: {member.name}")
                if member.issym() or member.islnk() or member.isdev():
                    raise InstallError(f"archive contains an unsupported link or device: {member.name}")
            archive.extractall(destination, filter="data")
    else:
        raise InstallError("source must be a directory or a supported tar/zip archive")

    # GitHub archives unpack into a single top-level folder
    entries = list(destination.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return destination


def _copy_source(source: Path, destination: Path) -> None:
    source = source.expanduser()
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False)
        return
    unpacked = destination.parent / "unpacked"
    unpacked.mkdir(parents=True)
    root = _extract_archive(source, unpacked)
    if root == unpacked:
        unpacked.rename(destination)
    else:
        shutil.move(str(root), destination)
        shutil.rmtree(unpacked, ignore_errors=True)


def _apply_patch(source_root: Path, patch_path: Path | None) -> None:
    if patch_path is None:
        return
    # stop git from finding a parent repo (dotfiles in ~ etc.), it would skip every patch path
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(source_root.parent)}
    patch = str(patch_path.resolve())
    for command in (("git", "apply", "--check", patch), ("git", "apply", patch)):
        try:
            completed = subprocess.run(command, cwd=source_root, env=env, capture_output=True, text=True, check=False)
        except OSError as error:
            raise InstallError(f"could not apply repository patch: {error}") from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise InstallError(f"repository patch could not be applied: {detail or 'git apply failed'}")


def host_platform() -> str:
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(), platform.machine())
    return f"{platform.system().lower()}-{arch}"


def _add_natives(source_root: Path, manifest: RepositoryManifest, work: Path) -> None:
    # a clean clone has no native addon, so use OMP's own prebuilt from npm (same one their release ships)
    native_dir = source_root / NATIVES_DIR
    if any(native_dir.glob("*.node")):
        return
    key = host_platform()
    entry = manifest.natives.get(key)
    if not isinstance(entry, Mapping) or not _text(entry.get("url")):
        raise InstallError(f"manifest lists no prebuilt OMP native addon for {key}")
    expected = _expected_sha256(f"native addon ({key})", _text(entry.get("sha256")))
    archive = work / "natives.tgz"
    try:
        with urllib.request.urlopen(entry["url"], timeout=120) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
    except OSError as error:
        raise InstallError(f"could not download the native addon: {error}") from error
    if sha256_file(archive) != expected:
        raise InstallError("native addon checksum does not match the manifest")
    native_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            name = Path(member.name).name
            if member.isfile() and _safe_member(member.name) and name.endswith(".node"):
                (native_dir / name).write_bytes(bundle.extractfile(member).read())
    if not any(native_dir.glob("*.node")):
        raise InstallError("native addon archive had no .node files")


def _run_build(source_root: Path) -> None:
    for folder, command in BUILD_STEPS:
        try:
            completed = subprocess.run(list(command), cwd=source_root / folder, check=False)
        except OSError as error:
            raise InstallError(f"build command could not start: {error}") from error
        if completed.returncode != 0:
            raise InstallError(f"{' '.join(command)} failed with exit code {completed.returncode}")


def _stage_build(paths: HarnessPaths, manifest: RepositoryManifest, source: Path, source_hash: str) -> Path:
    # build in a hidden temp folder next to the builds, it only moves into place once it works
    paths.data.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".r7harness-build-", dir=paths.data))
    try:
        _copy_source(source, stage / "source")
        _apply_patch(stage / "source", manifest.patch_path)
        _add_natives(stage / "source", manifest, stage)
        _run_build(stage / "source")
        if not (stage / "source" / ENTRYPOINT).is_file():
            raise InstallError(f"build did not produce {ENTRYPOINT}")
        write_json(
            stage / "build.json",
            {
                "schema": STATE_SCHEMA,
                "build_id": manifest.build_id,
                "omp_version": manifest.omp_version,
                "source_sha256": source_hash,
                "patch": str(manifest.patch_path) if manifest.patch_path else None,
                "entrypoint": f"source/{ENTRYPOINT}",
                "installed_at": utc_timestamp(),
            },
        )
        return stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def install_harness(
    *,
    repository_root: Path,
    paths: HarnessPaths,
    source: Path,
    profile_spec: ProfileSpec,
    allow_draft: bool = False,
    force: bool = False,
) -> InstallResult:
    manifest = load_repository_manifest(repository_root)
    source_hash = verify_install_inputs(manifest, source, allow_draft=allow_draft)
    state = load_state(paths)
    target = paths.build_root / manifest.build_id
    profile_dir = profile_path(paths.profile_root, profile_spec.slug)
    if profile_dir.exists() and not force:
        raise InstallError(f"profile already exists: {profile_dir}; use --force to replace its artifacts")
    if target.exists() and not force:
        raise InstallError(f"build already exists: {target}; use --force to replace this owned build")
    if target.exists() and manifest.build_id not in state["builds"]:
        raise InstallError(f"refusing to replace a build not recorded as owned: {target}")

    stage = _stage_build(paths, manifest, source, source_hash)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(stage, target)
    build = BuildInstall(build_id=manifest.build_id, path=target, source_sha256=source_hash, entrypoint=f"source/{ENTRYPOINT}")
    # record the build right away so a failed profile step still leaves it owned
    state["builds"][build.build_id] = build.as_state()
    save_state(paths, state)

    profile = install_profile(repository_root=repository_root, profile_root=paths.profile_root, spec=profile_spec, force=force)
    state["profiles"][profile.slug] = profile.as_state()
    state["current_build"] = build.build_id
    state["current_profile"] = profile.slug
    save_state(paths, state)
    return InstallResult(build=build, profile=profile, state_path=paths.state_path, current_path=paths.current_path)


def state_status(paths: HarnessPaths) -> dict[str, object]:
    state = load_state(paths)
    builds, profiles = state["builds"], state["profiles"]
    return {
        "installed": bool(builds or profiles),
        "current_build": state.get("current_build"),
        "current_profile": state.get("current_profile"),
        "builds": [
            {"build_id": key, "path": value.get("path"), "installed_at": value.get("installed_at")}
            for key, value in builds.items()
            if isinstance(value, Mapping)
        ],
        "profiles": [{"slug": key, "path": value.get("path")} for key, value in profiles.items() if isinstance(value, Mapping)],
        "state_path": str(paths.state_path),
        "current_path": str(paths.current_path),
    }
