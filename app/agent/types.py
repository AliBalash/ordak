from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


ExecutionBackend = Literal["host", "docker", "podman"]


@dataclass(slots=True)
class AgentResolvedConfig:
    workspace: Path
    workspace_display: str
    max_steps: int
    command_timeout_seconds: int
    execution_backend: ExecutionBackend
    network_enabled: bool


@dataclass(slots=True)
class AgentArtifact:
    path: str
    kind: str


@dataclass(slots=True)
class ToolExecutionResult:
    id: str
    tool: str
    ok: bool
    payload: dict[str, object] = field(default_factory=dict)
