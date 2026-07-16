from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.agent.types import ExecutionBackend
from app.config import Settings


IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "dist",
    "build",
}

_DANGEROUS_ARGV_PREFIXES = {
    ("rm", "-rf", "/"),
    ("rm", "-fr", "/"),
    ("sudo", "rm", "-rf", "/"),
    ("sudo", "rm", "-fr", "/"),
    ("reboot",),
    ("shutdown",),
    ("poweroff",),
    ("halt",),
    ("mkfs",),
    ("dd",),
}


def validate_execution_backend(
    requested: str | None,
    settings: Settings,
) -> ExecutionBackend:
    resolved = (requested or settings.agent_execution_backend).strip().lower()
    if resolved not in {"host", "docker", "podman"}:
        raise ValueError("Unsupported agent execution backend.")
    if resolved != settings.agent_execution_backend:
        raise ValueError(
            f"Execution backend {resolved!r} is not enabled on this ordak instance."
        )
    return resolved  # type: ignore[return-value]


def container_runtime_available(backend: str) -> bool:
    if backend not in {"docker", "podman"}:
        return True
    return shutil.which(backend) is not None


def build_agent_environment(
    settings: Settings,
    *,
    workspace: Path,
    step_home_root: Path,
) -> dict[str, str]:
    environment: dict[str, str] = {
        "CI": "1",
        "ORDAK_AGENT": "1",
    }
    for name in settings.agent_env_allowlist:
        value = os.environ.get(name)
        if value:
            environment[name] = value
    agent_home = step_home_root / ".ordak-agent-home"
    agent_home.mkdir(parents=True, exist_ok=True)
    environment["HOME"] = str(agent_home)
    environment["XDG_CACHE_HOME"] = str(agent_home / ".cache")
    environment["XDG_CONFIG_HOME"] = str(agent_home / ".config")
    environment["XDG_DATA_HOME"] = str(agent_home / ".local" / "share")
    environment["PWD"] = str(workspace)
    return environment


def validate_exec_argv(argv: list[str], settings: Settings) -> None:
    if not argv:
        raise ValueError("argv must not be empty.")
    if len(argv) > 256:
        raise ValueError("argv contains too many arguments.")
    for argument in argv:
        if "\x00" in argument:
            raise ValueError("Command arguments must not contain NUL bytes.")
        if len(argument) > 8192:
            raise ValueError("A command argument exceeds the allowed length.")
    if not settings.agent_reject_dangerous_commands:
        return
    lower_prefix = tuple(argument.strip().lower() for argument in argv[:4])
    for denied in _DANGEROUS_ARGV_PREFIXES:
        if lower_prefix[: len(denied)] == denied:
            raise ValueError("This command is blocked by the agent safety policy.")
    if argv[0] in {"bash", "sh", "zsh"} and "-lc" in argv:
        script = argv[argv.index("-lc") + 1] if len(argv) > argv.index("-lc") + 1 else ""
        lowered = script.lower()
        if "rm -rf /" in lowered or "shutdown" in lowered or "reboot" in lowered:
            raise ValueError("This shell command is blocked by the agent safety policy.")
