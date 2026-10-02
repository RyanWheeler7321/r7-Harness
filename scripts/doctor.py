# Read-only health check of the repo files and the recorded install, it never repairs anything.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from install import HarnessPaths, InstallError, load_repository_manifest, load_state, sha256_file
from install import is_within as _is_within
from profile import missing_profile_artifacts


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def healthy(self) -> bool:
        return all(check.status not in {"unavailable", "invalid"} for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {"healthy": self.healthy, "checks": [check.as_dict() for check in self.checks]}

    def render(self) -> str:
        width = max((len(check.name) for check in self.checks), default=0)
        rows = ["r7-Harness doctor", ""]
        rows.extend(f"{check.name:<{width}}  {check.status:<12} {check.detail}" for check in self.checks)
        rows.extend(("", f"Overall: {'healthy' if self.healthy else 'attention required'}"))
        return "\n".join(rows)


def _manifest_checks(repository_root: Path) -> tuple[DoctorCheck, list[DoctorCheck], Mapping[str, Any] | None]:
    try:
        manifest = load_repository_manifest(repository_root)
    except InstallError as error:
        return DoctorCheck("Repository manifest", "invalid", str(error)), [], None
    checks = [
        DoctorCheck(
            "Repository manifest",
            "available",
            f"OMP {manifest.omp_version}; {'draft' if manifest.draft else 'release'} metadata",
        )
    ]
    if manifest.patch_path is None:
        checks.append(DoctorCheck("Pinned patch", "optional", "no patch is declared"))
    elif not manifest.patch_path.is_file():
        checks.append(DoctorCheck("Pinned patch", "unavailable", f"missing: {manifest.patch_path}"))
    elif manifest.patch_sha256:
        actual = sha256_file(manifest.patch_path)
        status = "available" if actual.lower() == manifest.patch_sha256.lower() else "invalid"
        detail = "checksum matches manifest" if status == "available" else "checksum differs from manifest"
        checks.append(DoctorCheck("Pinned patch", status, detail))
    else:
        checks.append(DoctorCheck("Pinned patch", "available", "present; no patch checksum declared"))
    return checks[0], checks[1:], manifest.raw


def _build_checks(paths: HarnessPaths, state: Mapping[str, object]) -> list[DoctorCheck]:
    builds = state.get("builds")
    current_build = state.get("current_build")
    if not isinstance(builds, Mapping) or not builds:
        return [DoctorCheck("Side-by-side build", "unavailable", "no owned build is installed")]
    if not isinstance(current_build, str) or not isinstance(builds.get(current_build), Mapping):
        return [DoctorCheck("Side-by-side build", "invalid", "current build is not recorded as owned")]
    record = builds[current_build]
    raw_path = record.get("path")
    if not isinstance(raw_path, str):
        return [DoctorCheck("Side-by-side build", "invalid", "owned build has no path")]
    build_path = Path(raw_path)
    expected = paths.build_root / current_build
    if build_path.resolve(strict=False) != expected.resolve(strict=False) or not _is_within(build_path, paths.build_root):
        return [DoctorCheck("Side-by-side build", "invalid", "owned build path escapes the data root")]
    if not build_path.is_dir() or build_path.is_symlink():
        return [DoctorCheck("Side-by-side build", "unavailable", f"missing or unsafe: {build_path}")]
    metadata_path = build_path / "build.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [DoctorCheck("Side-by-side build", "invalid", "build metadata is missing")]
    except (OSError, json.JSONDecodeError) as error:
        return [DoctorCheck("Side-by-side build", "invalid", f"build metadata is unreadable: {error}")]
    if not isinstance(metadata, Mapping) or metadata.get("source_sha256") != record.get("source_sha256"):
        return [DoctorCheck("Side-by-side build", "invalid", "build source checksum does not match owned state")]
    checks = [DoctorCheck("Side-by-side build", "available", f"{current_build} at {build_path}")]
    entrypoint = record.get("entrypoint")
    if isinstance(entrypoint, str) and entrypoint:
        executable = build_path / entrypoint
        if executable.is_file() and _is_within(executable, build_path):
            checks.append(DoctorCheck("Launch entrypoint", "available", str(executable)))
        else:
            checks.append(DoctorCheck("Launch entrypoint", "unavailable", f"missing: {executable}"))
    else:
        checks.append(DoctorCheck("Launch entrypoint", "optional", "this verified source build has no declared launcher"))
    return checks


def _profile_checks(paths: HarnessPaths, state: Mapping[str, object]) -> list[DoctorCheck]:
    profiles = state.get("profiles")
    current_profile = state.get("current_profile")
    if not isinstance(profiles, Mapping) or not profiles:
        return [DoctorCheck("Isolated profile", "unavailable", "no owned profile is installed")]
    if not isinstance(current_profile, str) or not isinstance(profiles.get(current_profile), Mapping):
        return [DoctorCheck("Isolated profile", "invalid", "current profile is not recorded as owned")]
    record = profiles[current_profile]
    raw_path = record.get("path")
    if not isinstance(raw_path, str):
        return [DoctorCheck("Isolated profile", "invalid", "owned profile has no path")]
    profile_path = Path(raw_path)
    expected = paths.profile_root / current_profile / "agent"
    if profile_path.resolve(strict=False) != expected.resolve(strict=False) or not _is_within(profile_path, paths.profile_root):
        return [DoctorCheck("Isolated profile", "invalid", "owned profile path escapes the profile root")]
    if profile_path.is_symlink() or not profile_path.is_dir():
        return [DoctorCheck("Isolated profile", "unavailable", f"missing or unsafe: {profile_path}")]
    missing = missing_profile_artifacts(profile_path)
    if missing:
        return [DoctorCheck("Isolated profile", "unavailable", "missing: " + ", ".join(path.name for path in missing))]
    return [DoctorCheck("Isolated profile", "available", str(profile_path))]


def run_doctor(repository_root: Path, paths: HarnessPaths) -> DoctorReport:
    manifest_check, patch_checks, _ = _manifest_checks(repository_root)
    checks: list[DoctorCheck] = [manifest_check, *patch_checks]
    try:
        state = load_state(paths)
    except InstallError as error:
        checks.append(DoctorCheck("Owned state", "invalid", str(error)))
    else:
        if paths.state_path.exists():
            checks.append(DoctorCheck("Owned state", "available", str(paths.state_path)))
        else:
            checks.append(DoctorCheck("Owned state", "optional", "no installation state exists"))
        checks.extend(_build_checks(paths, state))
        checks.extend(_profile_checks(paths, state))

    extension = repository_root / "extension" / "index.ts"
    checks.append(
        DoctorCheck("Extension", "available", str(extension))
        if extension.is_file()
        else DoctorCheck("Extension", "unavailable", f"missing: {extension}")
    )
    theme_catalog = repository_root / "themes" / "catalog.json"
    theme_selection = repository_root / "themes" / "selection.json"
    if theme_catalog.is_file() and theme_selection.is_file():
        checks.append(DoctorCheck("Themes", "available", "catalog and selection are present"))
    else:
        missing = [str(path.name) for path in (theme_catalog, theme_selection) if not path.is_file()]
        checks.append(DoctorCheck("Themes", "unavailable", "missing: " + ", ".join(missing)))
    return DoctorReport(checks=tuple(checks))
