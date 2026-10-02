# r7harness check: reports what this machine supports, it only looks and never creates anything

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


_STATUS_ORDER = {
    "supported": 0,
    "available": 1,
    "experimental": 2,
    "limited": 3,
    "optional": 4,
    "unavailable": 5,
    "unsupported": 6,
}
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class ComponentStatus:
    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class CompatibilityReport:
    environment: str
    operating_system: str
    architecture: str
    components: tuple[ComponentStatus, ...]
    recommendation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "operating_system": self.operating_system,
            "architecture": self.architecture,
            "components": [component.as_dict() for component in self.components],
            "component_map": {
                component.name: {"status": component.status, "detail": component.detail}
                for component in self.components
            },
            "recommended_install": self.recommendation,
        }

    def render(self) -> str:
        width = max((len(component.name) for component in self.components), default=0)
        rows = ["r7-Harness compatibility", "", f"Environment: {self.environment} ({self.architecture})", ""]
        rows.extend(
            f"{component.name:<{width}}  {component.status:<12} {component.detail}"
            for component in self.components
        )
        rows.extend(("", f"Recommended install: {self.recommendation}"))
        return "\n".join(rows)


class SystemProbe:
    # everything the check reads from the machine goes through here so tests can fake it
    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        system: str | None = None,
        machine: str | None = None,
        which: Callable[[str], str | None] | None = None,
        run: Callable[[Sequence[str]], object] | None = None,
        proc_version: str | None = None,
    ) -> None:
        self.environment = dict(os.environ if environment is None else environment)
        self._system = system or platform.system()
        self._machine = machine or platform.machine()
        self._which = which or shutil.which
        self._run = run or self._run_command
        self._proc_version = proc_version

    @staticmethod
    def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                list(command),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    @property
    def system(self) -> str:
        return self._system

    @property
    def machine(self) -> str:
        return self._machine

    def which(self, command: str) -> str | None:
        return self._which(command)

    def proc_version(self) -> str:
        if self._proc_version is not None:
            return self._proc_version
        try:
            return Path("/proc/version").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def version(self, command: str) -> str | None:
        executable = self.which(command)
        if not executable:
            return None
        result = self._run((executable, "--version"))
        if result is None:
            return None
        returncode = getattr(result, "returncode", 0)
        if returncode not in (0, None):
            return None
        text = str(getattr(result, "stdout", "") or getattr(result, "stderr", "")).strip()
        if not text:
            return "present"
        return text.splitlines()[0][:160]


def default_roots(environment: Mapping[str, str] | None = None) -> dict[str, Path]:
    env = os.environ if environment is None else environment
    home = Path(env.get("R7HARNESS_HOME", "~/.config/r7harness")).expanduser()
    data = Path(env.get("R7HARNESS_DATA", "~/.local/share/r7harness")).expanduser()
    profiles = Path(env.get("OMP_PROFILE_ROOT", "~/.omp/profiles")).expanduser()
    return {"home": home, "data": data, "profiles": profiles}


def classify_environment(probe: SystemProbe) -> str:
    system = probe.system.lower()
    if system == "windows":
        return "native Windows"
    if system == "darwin":
        return "macOS"
    if system == "linux":
        proc_version = probe.proc_version().lower()
        if (
            probe.environment.get("WSL_INTEROP")
            or probe.environment.get("WSL_DISTRO_NAME")
            or "microsoft" in proc_version
            or "wsl" in proc_version
        ):
            return "WSL2"
        return "Linux"
    return probe.system or "unknown"


def _is_writable_destination(path: Path) -> tuple[bool, Path]:
    # checks the nearest folder that exists, nothing is created
    candidate = path.expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    writable = candidate.exists() and os.access(candidate, os.W_OK | os.X_OK)
    return writable, candidate


def _component_for_command(probe: SystemProbe, label: str, commands: Sequence[str]) -> ComponentStatus:
    for command in commands:
        version = probe.version(command)
        if version:
            return ComponentStatus(label, "available", version)
    return ComponentStatus(label, "unavailable", "not found on PATH")


def _manifest_checksum_component(repository_root: Path) -> ComponentStatus:
    manifest_path = repository_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ComponentStatus("Pinned source checksum", "optional", "manifest.json is not present")
    except (OSError, json.JSONDecodeError) as error:
        return ComponentStatus("Pinned source checksum", "unavailable", f"manifest cannot be read: {error}")

    candidates: list[object] = [manifest.get("source_sha256"), manifest.get("sourceSha256")]
    for key in ("source", "omp", "upstream"):
        section = manifest.get(key)
        if isinstance(section, dict):
            candidates.extend((section.get("sha256"), section.get("source_sha256"), section.get("sourceSha256")))
            nested_source = section.get("source")
            if isinstance(nested_source, dict):
                candidates.append(nested_source.get("sha256"))
    checksum = next((value for value in candidates if isinstance(value, str) and value.strip()), None)
    if checksum is None:
        return ComponentStatus("Pinned source checksum", "optional", "not declared; install will refuse a build")
    if not _SHA256_RE.fullmatch(checksum.strip()):
        return ComponentStatus("Pinned source checksum", "unavailable", "manifest checksum is not a SHA-256 value")
    return ComponentStatus("Pinned source checksum", "available", "manifest declares a SHA-256 source checksum")


def _environment_components(environment: str, architecture: str, probe: SystemProbe) -> list[ComponentStatus]:
    x64 = architecture.lower() in {"x86_64", "amd64"}
    if environment == "WSL2":
        core = ComponentStatus(
            "Core OMP modification",
            "supported" if x64 else "limited",
            "WSL2 x64 side-by-side build" if x64 else "WSL2 architecture has not been smoke tested",
        )
        themes = ComponentStatus("OMP themes", "supported", "available through the isolated OMP profile")
        terminal_found = bool(probe.environment.get("WT_SESSION") or probe.which("wt.exe") or probe.which("wt"))
        terminal = ComponentStatus(
            "Windows Terminal colors",
            "supported" if terminal_found else "limited",
            "Windows Terminal detected" if terminal_found else "Windows Terminal was not detected from WSL",
        )
        ahk_found = any(probe.which(command) for command in ("AutoHotkey.exe", "AutoHotkey64.exe", "autohotkey", "ahk"))
        ahk = ComponentStatus(
            "AutoHotkey companion",
            "available" if ahk_found else "optional",
            "AutoHotkey v2 detected" if ahk_found else "install separately on Windows to enable it",
        )
        return [core, ComponentStatus("Isolated profile", "supported", "separate profile root and authentication"), themes, terminal, ahk]

    if environment == "Linux":
        core = ComponentStatus(
            "Core OMP modification",
            "supported" if x64 else "limited",
            "Linux x64 side-by-side build" if x64 else "architecture has not been smoke tested",
        )
        return [
            core,
            ComponentStatus("Isolated profile", "supported", "separate profile root and authentication"),
            ComponentStatus("OMP themes", "supported", "available through the isolated OMP profile"),
            ComponentStatus("Windows Terminal colors", "unavailable", "Windows Terminal integration is WSL/Windows only"),
            ComponentStatus("AutoHotkey companion", "unavailable", "Windows only"),
        ]

    if environment == "macOS":
        return [
            ComponentStatus("Core OMP modification", "experimental", "macOS support requires an explicit experimental install"),
            ComponentStatus("Isolated profile", "supported", "separate profile root and authentication"),
            ComponentStatus("OMP themes", "supported", "available through the isolated OMP profile"),
            ComponentStatus("Windows Terminal colors", "unavailable", "Windows Terminal integration is WSL/Windows only"),
            ComponentStatus("AutoHotkey companion", "unavailable", "Windows only"),
        ]

    if environment == "native Windows":
        return [
            ComponentStatus("Core OMP modification", "unsupported", "native Windows requires WSL2 for this release"),
            ComponentStatus("Isolated profile", "unavailable", "install is not offered on native Windows"),
            ComponentStatus("OMP themes", "unavailable", "install is not offered on native Windows"),
            ComponentStatus("Windows Terminal colors", "optional", "available after a supported WSL2 install"),
            ComponentStatus("AutoHotkey companion", "optional", "available after a supported WSL2 install"),
        ]

    return [
        ComponentStatus("Core OMP modification", "unsupported", f"{environment} is not a supported host"),
        ComponentStatus("Isolated profile", "unavailable", "install is not offered on this host"),
        ComponentStatus("OMP themes", "unavailable", "install is not offered on this host"),
        ComponentStatus("Windows Terminal colors", "unavailable", "Windows Terminal integration is WSL/Windows only"),
        ComponentStatus("AutoHotkey companion", "unavailable", "Windows only"),
    ]


def _font_component(repository_root: Path) -> ComponentStatus:
    # the catalog only links official downloads, fonts are installed by hand
    if (repository_root / "fonts" / "catalog.json").is_file():
        return ComponentStatus("Font catalog", "available", "fonts/catalog.json links official downloads")
    return ComponentStatus("Font catalog", "unavailable", "fonts/catalog.json is missing")


def _provider_component(provider: str | None, model: str | None) -> ComponentStatus:
    if not provider and not model:
        return ComponentStatus("Provider/model selection", "optional", "select a provider and model during profile setup")
    if not provider or not model:
        return ComponentStatus("Provider/model selection", "limited", "both provider and model are required for a checked selection")
    known_providers = {"openai-codex", "anthropic"}
    if provider.lower() in known_providers:
        return ComponentStatus("Provider/model selection", "available", f"{provider}/{model} is a built-in shortcut")
    return ComponentStatus("Provider/model selection", "limited", f"{provider}/{model} will be validated by the installed OMP")


def inspect_compatibility(
    *,
    repository_root: Path,
    roots: Mapping[str, Path] | None = None,
    provider: str | None = None,
    model: str | None = None,
    probe: SystemProbe | None = None,
) -> CompatibilityReport:
    system_probe = probe or SystemProbe()
    selected_roots = dict(default_roots(system_probe.environment) if roots is None else roots)
    environment = classify_environment(system_probe)
    architecture = system_probe.machine or "unknown"
    components = _environment_components(environment, architecture, system_probe)
    components.extend(
        (
            _component_for_command(system_probe, "OMP", ("omp", "oh-my-pi")),
            _component_for_command(system_probe, "Git", ("git",)),
            ComponentStatus("Python", "available", sys.version.split()[0]),
            _component_for_command(system_probe, "Bash", ("bash",)),
            _component_for_command(system_probe, "Bun", ("bun",)),
            _component_for_command(system_probe, "RTK integration", ("rtk",)),
            _font_component(repository_root),
            _provider_component(provider, model),
            _manifest_checksum_component(repository_root),
        )
    )

    destination_details: list[str] = []
    all_writable = True
    for label, path in ("config", selected_roots["home"]), ("data", selected_roots["data"]), ("profiles", selected_roots["profiles"]):
        writable, ancestor = _is_writable_destination(path)
        all_writable = all_writable and writable
        qualifier = "writable via" if writable else "not writable via"
        destination_details.append(f"{label} {qualifier} {ancestor}")
    components.append(
        ComponentStatus(
            "Destination writability",
            "supported" if all_writable else "unavailable",
            "; ".join(destination_details),
        )
    )

    core = next(component for component in components if component.name == "Core OMP modification")
    required_commands = {component.name: component.status for component in components}
    requirements_ready = all(
        required_commands[name] in {"available", "supported"}
        for name in ("Git", "Python", "Bash", "Bun", "Destination writability")
    )
    if core.status == "unsupported":
        recommendation = "not supported"
    elif environment == "macOS":
        recommendation = "experimental core only"
    elif environment == "WSL2" and requirements_ready and required_commands["Windows Terminal colors"] == "supported":
        recommendation = "full"
    elif core.status in {"supported", "experimental"} and requirements_ready:
        recommendation = "core only"
    else:
        recommendation = "resolve unavailable required components"

    return CompatibilityReport(
        environment=environment,
        operating_system=system_probe.system,
        architecture=architecture,
        components=tuple(components),
        recommendation=recommendation,
    )
