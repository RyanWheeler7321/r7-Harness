# Rollback and uninstall, both limited to the paths recorded in state.json.

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from install import HarnessPaths, InstallError, is_within, load_state, save_state


class RollbackError(RuntimeError):
    pass


@dataclass(frozen=True)
class UninstallPlan:
    build_paths: tuple[Path, ...]
    profile_paths: tuple[Path, ...]
    external_paths: tuple[Path, ...]
    state_path: Path
    current_path: Path

    @property
    def paths(self) -> tuple[Path, ...]:
        return (*self.build_paths, *self.profile_paths, *self.external_paths, self.current_path, self.state_path)

    def as_dict(self) -> dict[str, object]:
        return {
            "build_paths": [str(path) for path in self.build_paths],
            "profile_paths": [str(path) for path in self.profile_paths],
            "external_paths": [str(path) for path in self.external_paths],
            "state_path": str(self.state_path),
            "current_path": str(self.current_path),
        }


def _build_record_path(paths: HarnessPaths, build_id: str, record: Mapping[str, object]) -> Path:
    # a recorded path must be exactly where install puts builds, anything else is refused
    raw_path = record.get("path")
    if not isinstance(raw_path, str):
        raise RollbackError(f"owned build {build_id} has no path")
    path = Path(raw_path)
    if path.resolve() != (paths.build_root / build_id).resolve() or not is_within(path, paths.build_root):
        raise RollbackError(f"owned build {build_id} has an unsafe path")
    return path


def _profile_record_root(paths: HarnessPaths, slug: str, record: Mapping[str, object]) -> Path:
    raw_path = record.get("path")
    if not isinstance(raw_path, str):
        raise RollbackError(f"owned profile {slug} has no path")
    path = Path(raw_path)
    if path.resolve() != (paths.profile_root / slug / "agent").resolve() or not is_within(path, paths.profile_root):
        raise RollbackError(f"owned profile {slug} has an unsafe path")
    return path


def _record_owned_paths(record: Mapping[str, object]) -> tuple[Path, ...]:
    raw = record.get("owned_paths", [])
    if not isinstance(raw, list):
        return ()
    return tuple(Path(value) for value in raw if isinstance(value, str))


def rollback(paths: HarnessPaths, target_build: str | None = None) -> dict[str, object]:
    # only switches current_build, build folders are left alone
    try:
        state = load_state(paths)
    except InstallError as error:
        raise RollbackError(str(error)) from error
    builds = state["builds"]
    if not builds:
        raise RollbackError("no owned r7Harness build is installed; use stock OMP directly")
    current = state.get("current_build")
    if target_build is None:
        candidates = [build_id for build_id in builds if build_id != current]
        if not candidates:
            raise RollbackError("no previous owned build is available; use stock OMP directly")
        target_build = max(
            candidates,
            key=lambda build_id: str(builds[build_id].get("installed_at", "")) if isinstance(builds[build_id], Mapping) else "",
        )
    record = builds.get(target_build)
    if not isinstance(target_build, str) or not isinstance(record, Mapping):
        raise RollbackError(f"requested build is not owned: {target_build}")
    target_path = _build_record_path(paths, target_build, record)
    if not target_path.is_dir() or target_path.is_symlink():
        raise RollbackError(f"requested owned build is missing or unsafe: {target_path}")
    state["current_build"] = target_build
    save_state(paths, state)
    return {
        "current_build": target_build,
        "build_path": str(target_path),
        "previous_build": current,
    }


def uninstall_plan(paths: HarnessPaths) -> UninstallPlan:
    # preview only, nothing is removed here
    try:
        state = load_state(paths)
    except InstallError as error:
        raise RollbackError(str(error)) from error
    builds = state["builds"]
    profiles = state["profiles"]

    build_paths: list[Path] = []
    profile_paths: list[Path] = []
    external_paths: list[Path] = []
    for build_id, record in builds.items():
        if not isinstance(build_id, str) or not isinstance(record, Mapping):
            raise RollbackError("owned state has an invalid build record")
        build_paths.append(_build_record_path(paths, build_id, record))

    for slug, record in profiles.items():
        if not isinstance(slug, str) or not isinstance(record, Mapping):
            raise RollbackError("owned state has an invalid profile record")
        root = _profile_record_root(paths, slug, record)
        for candidate in _record_owned_paths(record):
            if candidate.resolve() == root.resolve():
                continue
            if is_within(candidate, root):
                profile_paths.append(candidate)
        global_template = record.get("global_template_path")
        if isinstance(global_template, str):
            candidate = Path(global_template)
            if candidate.name not in {"AGENTS.md", "AGENTS.r7harness.template.md"}:
                raise RollbackError(f"owned profile {slug} has an unsafe external template path")
            if candidate not in _record_owned_paths(record):
                raise RollbackError(f"owned profile {slug} external template is not listed as owned")
            external_paths.append(candidate)

    unique = lambda values: tuple(dict.fromkeys(path.resolve() for path in values))
    return UninstallPlan(
        build_paths=unique(build_paths),
        profile_paths=unique(profile_paths),
        external_paths=unique(external_paths),
        state_path=paths.state_path,
        current_path=paths.current_path,
    )


def _remove_path(path: Path, *, directory: bool = False) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    if path.is_symlink() or path.is_file():
        path.unlink()
        return True
    if directory:
        shutil.rmtree(path)
        return True
    return False


def _remove_empty_parent(path: Path, stop_at: Path) -> None:
    current = path
    while current != stop_at and is_within(current, stop_at):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def uninstall(paths: HarnessPaths) -> list[Path]:
    plan = uninstall_plan(paths)
    removed: list[Path] = []
    for build_path in plan.build_paths:
        if _remove_path(build_path, directory=True):
            removed.append(build_path)

    # deepest files first, a folder with unrecorded files in it (sessions, auth) stays
    for profile_path in sorted(plan.profile_paths, key=lambda item: len(item.parts), reverse=True):
        if _remove_path(profile_path):
            removed.append(profile_path)
            _remove_empty_parent(profile_path.parent, paths.profile_root)
    for external_path in plan.external_paths:
        if _remove_path(external_path):
            removed.append(external_path)
    if _remove_path(plan.current_path):
        removed.append(plan.current_path)
    if _remove_path(plan.state_path):
        removed.append(plan.state_path)
    return removed
