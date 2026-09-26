#!/usr/bin/env python3
# r7harness command line: check, install, launch, status, doctor, update-check, rollback, uninstall

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

# keep check read-only, no __pycache__ next to the scripts
sys.dont_write_bytecode = True
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from compatibility import inspect_compatibility
from doctor import run_doctor
from install import (
    HarnessPaths,
    InstallError,
    install_harness,
    load_repository_manifest,
    load_state,
    state_status,
)
from profile import ProfileError, ProfileSpec
from rollback import RollbackError, rollback, uninstall, uninstall_plan


class CommandError(RuntimeError):
    pass


def _repository_root() -> Path:
    return SCRIPT_DIRECTORY.parent


def _location_options() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo-root", type=Path, default=_repository_root(), help="repository root (default: script parent)")
    parser.add_argument("--home", type=Path, help="override R7HARNESS_HOME")
    parser.add_argument("--data-dir", type=Path, help="override r7Harness install data root")
    parser.add_argument("--profile-root", type=Path, help="override isolated OMP profiles root")
    return parser


def build_parser() -> argparse.ArgumentParser:
    locations = _location_options()
    parser = argparse.ArgumentParser(prog="r7harness", description="r7Harness side-by-side profile manager")
    subcommands = parser.add_subparsers(dest="command", required=True)

    check = subcommands.add_parser("check", parents=[locations], help="inspect compatibility without changing anything")
    check.add_argument("--json", action="store_true", help="emit structured JSON")
    check.add_argument("--provider", help="provider shortcut to inspect")
    check.add_argument("--model", help="model identifier to inspect")

    install = subcommands.add_parser("install", parents=[locations], help="install a verified local source payload")
    install.add_argument("--source", type=Path, required=True, help="local source archive or directory to verify and stage")
    install.add_argument("--profile", default="r7", help="isolated profile name")
    install.add_argument("--user-name", default="", help="local profile user name")
    install.add_argument("--agent-name", default="", help="local agent name")
    install.add_argument("--provider", default="", help="OMP provider identifier")
    install.add_argument("--model", default="", help="OMP model identifier")
    install.add_argument("--identity", type=Path, help="optional local identity image")
    install.add_argument("--global-agents", type=Path, help="explicit global AGENTS.md path; never auto-discovered")
    install.add_argument("--force", action="store_true", help="replace only recorded build/profile artifacts")
    install.add_argument("--allow-draft", action="store_true", help="allow an explicitly draft manifest to mutate local roots")
    install.add_argument("--experimental", action="store_true", help="allow the macOS core-only path")
    install.add_argument("--json", action="store_true", help="emit structured JSON")

    launch = subcommands.add_parser("launch", parents=[locations], help="launch the current owned build with its isolated profile")
    launch.add_argument("--dry-run", action="store_true", help="show the isolated command and environment without starting it")
    launch.add_argument("--json", action="store_true", help="emit structured JSON")
    launch.add_argument("arguments", nargs=argparse.REMAINDER, help="arguments forwarded to the owned launcher")

    status = subcommands.add_parser("status", parents=[locations], help="show owned installation state")
    status.add_argument("--json", action="store_true", help="emit structured JSON")

    doctor = subcommands.add_parser("doctor", parents=[locations], help="check owned artifacts without repairing them")
    doctor.add_argument("--json", action="store_true", help="emit structured JSON")

    update = subcommands.add_parser("update-check", parents=[locations], help="compare the installed build with this repo's build")
    update.add_argument("--json", action="store_true", help="emit structured JSON")

    roll = subcommands.add_parser("rollback", parents=[locations], help="select a previous owned build")
    roll.add_argument("--to", dest="target_build", help="owned build identifier to select")
    roll.add_argument("--json", action="store_true", help="emit structured JSON")

    remove = subcommands.add_parser("uninstall", parents=[locations], help="preview or remove exact owned paths")
    remove.add_argument("--yes", action="store_true", help="remove the displayed owned paths")
    remove.add_argument("--json", action="store_true", help="emit structured JSON")
    return parser


def _paths(args: argparse.Namespace) -> HarnessPaths:
    defaults = HarnessPaths.from_environment()
    return HarnessPaths(
        home=(args.home or defaults.home).expanduser(),
        data=(args.data_dir or defaults.data).expanduser(),
        profile_root=(args.profile_root or defaults.profile_root).expanduser(),
    )


def _json_or_text(value: dict[str, Any], text: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        print(text)


def _render_status(status: dict[str, object]) -> str:
    if not status["installed"]:
        return "r7Harness status\n\nNo owned r7Harness installation is recorded."
    rows = ["r7Harness status", "", f"Current build: {status['current_build']}", f"Current profile: {status['current_profile']}", "", "Owned builds:"]
    rows.extend(f"- {build['build_id']}: {build['path']}" for build in status["builds"])
    rows.append("Owned profiles:")
    rows.extend(f"- {profile['slug']}: {profile['path']}" for profile in status["profiles"])
    return "\n".join(rows)


def _current_launch(paths: HarnessPaths) -> tuple[list[str], dict[str, str], dict[str, object]]:
    state = load_state(paths)
    builds = state.get("builds")
    profiles = state.get("profiles")
    current_build = state.get("current_build")
    current_profile = state.get("current_profile")
    if not isinstance(builds, dict) or not isinstance(profiles, dict):
        raise CommandError("owned state is invalid")
    build = builds.get(current_build) if isinstance(current_build, str) else None
    profile = profiles.get(current_profile) if isinstance(current_profile, str) else None
    if not isinstance(build, dict) or not isinstance(profile, dict):
        raise CommandError("no current owned build and isolated profile are installed")
    build_path = Path(str(build.get("path", "")))
    profile_path = Path(str(profile.get("path", "")))
    slug = str(current_profile)
    if not build_path.is_dir() or build_path.is_symlink():
        raise CommandError(f"current owned build is missing or unsafe: {build_path}")
    if not profile_path.is_dir() or profile_path.is_symlink():
        raise CommandError(f"current isolated profile is missing or unsafe: {profile_path}")
    entrypoint = build.get("entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint:
        raise CommandError("current build has no declared launcher")
    executable = build_path / entrypoint
    try:
        executable.resolve(strict=False).relative_to(build_path.resolve(strict=False))
    except ValueError as error:
        raise CommandError("current launch entrypoint escapes the owned build") from error
    if not executable.is_file():
        raise CommandError(f"current launch entrypoint is missing: {executable}")
    if not os.access(executable, os.X_OK):
        raise CommandError(f"current launch entrypoint is not executable: {executable}")
    command = [str(executable)]
    environment = dict(os.environ)
    environment.update(
        {
            "R7HARNESS_HOME": str(paths.home),
            "R7HARNESS_DATA": str(paths.data),
            "R7HARNESS_PROFILE": str(profile_path),
            "R7HARNESS_PROFILE_SLUG": slug,
        }
    )
    # OMP_PROFILE takes a profile name and OMP looks in ~/.omp/profiles/<name>/agent,
    # a profile somewhere else goes through PI_CODING_AGENT_DIR with the default profile
    if profile_path.resolve() == (Path.home() / ".omp" / "profiles" / slug / "agent").resolve():
        environment["OMP_PROFILE"] = slug
    else:
        environment["OMP_PROFILE"] = ""
        environment["PI_CODING_AGENT_DIR"] = str(profile_path)
    return command, environment, {"build_path": str(build_path), "profile_path": str(profile_path), "entrypoint": str(executable)}


def _run_check(args: argparse.Namespace, paths: HarnessPaths) -> int:
    roots = {"home": paths.home, "data": paths.data, "profiles": paths.profile_root}
    report = inspect_compatibility(
        repository_root=args.repo_root.expanduser(),
        roots=roots,
        provider=args.provider,
        model=args.model,
    )
    _json_or_text(report.as_dict(), report.render(), args.json)
    return 0


def _run_install(args: argparse.Namespace, paths: HarnessPaths) -> int:
    report = inspect_compatibility(
        repository_root=args.repo_root.expanduser(),
        roots={"home": paths.home, "data": paths.data, "profiles": paths.profile_root},
    )
    if report.environment == "native Windows":
        raise CommandError("native Windows is not supported; run from WSL2 instead")
    if report.environment == "macOS" and not args.experimental:
        raise CommandError("macOS is experimental; pass --experimental to attempt a core-only install")
    spec = ProfileSpec(
        slug=args.profile,
        user_name=args.user_name,
        agent_name=args.agent_name,
        provider=args.provider,
        model=args.model,
        identity_image=args.identity,
        global_agents=args.global_agents,
    )
    result = install_harness(
        repository_root=args.repo_root.expanduser(),
        paths=paths,
        source=args.source.expanduser(),
        profile_spec=spec,
        allow_draft=args.allow_draft,
        force=args.force,
    )
    _json_or_text(result.as_dict(), f"Installed {result.build.build_id} with isolated profile {result.profile.slug}.", args.json)
    return 0


def _run_launch(args: argparse.Namespace, paths: HarnessPaths) -> int:
    command, environment, details = _current_launch(paths)
    forwarded = list(args.arguments)
    if forwarded[:1] == ["--"]:
        forwarded.pop(0)
    command.extend(forwarded)
    keys = ("R7HARNESS_HOME", "R7HARNESS_DATA", "R7HARNESS_PROFILE", "R7HARNESS_PROFILE_SLUG", "OMP_PROFILE", "PI_CODING_AGENT_DIR")
    payload = {"command": command, "environment": {key: environment[key] for key in keys if key in environment}, **details}
    if args.dry_run:
        profile_line = f"OMP_PROFILE: {environment['OMP_PROFILE']}" if environment["OMP_PROFILE"] else f"PI_CODING_AGENT_DIR: {details['profile_path']}"
        _json_or_text(payload, "\n".join(("r7Harness launch (dry run)", "", "Command: " + " ".join(command), profile_line)), args.json)
        return 0
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    completed = subprocess.run(command, env=environment, check=False)
    return completed.returncode


def _run_update_check(args: argparse.Namespace, paths: HarnessPaths) -> int:
    manifest = load_repository_manifest(args.repo_root.expanduser())
    state = load_state(paths)
    payload = {
        "installed_build": state.get("current_build"),
        "repository_build": manifest.build_id,
        "draft": manifest.draft,
        "network_checked": False,
        "detail": "local manifest only; no network update check is performed",
    }
    text = "\n".join(
        (
            "r7Harness update check",
            "",
            f"Installed build: {payload['installed_build'] or 'none'}",
            f"Repository build: {manifest.build_id}",
            "Network: not checked (local manifest only)",
        )
    )
    _json_or_text(payload, text, args.json)
    return 0


def _run_uninstall(args: argparse.Namespace, paths: HarnessPaths) -> int:
    plan = uninstall_plan(paths)
    preview = plan.as_dict()
    if not args.yes:
        text = "\n".join(("r7Harness uninstall preview", "", *(f"- {path}" for path in plan.paths), "", "Re-run with --yes to remove only these owned paths."))
        _json_or_text({"preview": preview, "will_remove": False}, text, args.json)
        return 0
    removed = uninstall(paths)
    text = "\n".join(("r7Harness uninstall", "", "Removed:", *(f"- {path}" for path in removed)))
    _json_or_text({"preview": preview, "will_remove": True, "result": {"removed_paths": [str(path) for path in removed]}}, text, args.json)
    return 0


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = _paths(args)
    try:
        if args.command == "check":
            return _run_check(args, paths)
        if args.command == "install":
            return _run_install(args, paths)
        if args.command == "launch":
            return _run_launch(args, paths)
        if args.command == "status":
            status = state_status(paths)
            _json_or_text(status, _render_status(status), args.json)
            return 0
        if args.command == "doctor":
            report = run_doctor(args.repo_root.expanduser(), paths)
            _json_or_text(report.as_dict(), report.render(), args.json)
            return 0 if report.healthy else 1
        if args.command == "update-check":
            return _run_update_check(args, paths)
        if args.command == "rollback":
            result = rollback(paths, args.target_build)
            _json_or_text(result, f"Selected owned build {result['current_build']}.", args.json)
            return 0
        if args.command == "uninstall":
            return _run_uninstall(args, paths)
    except (CommandError, InstallError, ProfileError, RollbackError) as error:
        print(f"r7Harness: {error}", file=sys.stderr)
        return 2
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(run())
