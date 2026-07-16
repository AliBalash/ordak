from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class WorkspaceResolution:
    workspace: Path
    display_path: str


def _require_no_nul(value: str, *, label: str) -> None:
    if "\x00" in value:
        raise ValueError(f"{label} must not contain NUL bytes.")


def _ensure_inside(parent: Path, child: Path, *, error_message: str) -> None:
    try:
        child.relative_to(parent)
    except ValueError as exc:
        raise ValueError(error_message) from exc


def resolve_workspace(workspace: str, allowed_roots: tuple[Path, ...]) -> WorkspaceResolution:
    _require_no_nul(workspace, label="workspace")
    expanded = Path(workspace).expanduser()
    resolved_workspace = expanded.resolve()
    if not resolved_workspace.exists():
        raise ValueError("Agent workspace does not exist.")
    if not resolved_workspace.is_dir():
        raise ValueError("Agent workspace must be a directory.")
    if not allowed_roots:
        raise ValueError("No allowed workspace roots are configured.")
    for root in allowed_roots:
        try:
            resolved_workspace.relative_to(root)
            return WorkspaceResolution(
                workspace=resolved_workspace,
                display_path=str(resolved_workspace),
            )
        except ValueError:
            continue
    raise ValueError("Agent workspace is outside the allowed roots.")


def resolve_workspace_path(
    workspace: Path,
    relative_path: str,
    *,
    allow_nonexistent: bool = False,
) -> Path:
    _require_no_nul(relative_path, label="path")
    requested = Path(relative_path)
    if requested.is_absolute():
        raise ValueError("Paths must be relative to the workspace.")
    if any(part == ".." for part in requested.parts):
        raise ValueError("Paths must not escape the workspace.")
    candidate = workspace / requested
    if candidate.exists():
        resolved_candidate = candidate.resolve()
        _ensure_inside(
            workspace,
            resolved_candidate,
            error_message="Resolved path escapes the workspace.",
        )
        return resolved_candidate

    if not allow_nonexistent:
        raise ValueError("Path does not exist.")

    parent = candidate.parent
    while not parent.exists():
        parent = parent.parent
    resolved_parent = parent.resolve()
    _ensure_inside(
        workspace,
        resolved_parent,
        error_message="Resolved parent escapes the workspace.",
    )
    final_path = resolved_parent / candidate.relative_to(parent)
    _ensure_inside(
        workspace,
        final_path,
        error_message="Resolved path escapes the workspace.",
    )
    return final_path
