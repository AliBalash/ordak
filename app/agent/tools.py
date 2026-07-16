from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent.workspace import resolve_workspace_path


@dataclass(slots=True)
class PatchOperation:
    kind: str
    path: str
    lines: list[str]


def parse_begin_patch(patch: str) -> list[PatchOperation]:
    lines = patch.splitlines()
    if not lines or lines[0] != "*** Begin Patch":
        raise ValueError("Patch must start with *** Begin Patch.")
    if lines[-1] != "*** End Patch":
        raise ValueError("Patch must end with *** End Patch.")
    operations: list[PatchOperation] = []
    index = 1
    while index < len(lines) - 1:
        line = lines[index]
        if line.startswith("*** Add File: "):
            path = line.removeprefix("*** Add File: ").strip()
            index += 1
            payload: list[str] = []
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                if not lines[index].startswith("+"):
                    raise ValueError("Add File hunks may contain only added lines.")
                payload.append(lines[index][1:])
                index += 1
            operations.append(PatchOperation(kind="add", path=path, lines=payload))
            continue
        if line.startswith("*** Delete File: "):
            path = line.removeprefix("*** Delete File: ").strip()
            operations.append(PatchOperation(kind="delete", path=path, lines=[]))
            index += 1
            continue
        if line.startswith("*** Update File: "):
            path = line.removeprefix("*** Update File: ").strip()
            index += 1
            payload = []
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                payload.append(lines[index])
                index += 1
            operations.append(PatchOperation(kind="update", path=path, lines=payload))
            continue
        raise ValueError(f"Unsupported patch directive: {line}")
    return operations


def _apply_update_lines(original: str, patch_lines: list[str]) -> str:
    source = original.splitlines()
    result: list[str] = []
    pointer = 0
    for raw_line in patch_lines:
        if raw_line == "@@" or raw_line.startswith("@@ "):
            continue
        if not raw_line:
            raise ValueError("Patch lines must begin with a diff prefix.")
        prefix = raw_line[0]
        content = raw_line[1:]
        if prefix == " ":
            if pointer >= len(source) or source[pointer] != content:
                raise ValueError("Patch context did not match the target file.")
            result.append(source[pointer])
            pointer += 1
            continue
        if prefix == "-":
            if pointer >= len(source) or source[pointer] != content:
                raise ValueError("Patch removal did not match the target file.")
            pointer += 1
            continue
        if prefix == "+":
            result.append(content)
            continue
        raise ValueError("Unsupported patch diff prefix.")
    result.extend(source[pointer:])
    rebuilt = "\n".join(result)
    if original.endswith("\n") or any(line.startswith("+") for line in patch_lines):
        rebuilt += "\n"
    return rebuilt


def apply_begin_patch(
    workspace: Path,
    patch: str,
) -> tuple[dict[Path, str | None], list[Path]]:
    operations = parse_begin_patch(patch)
    pending_contents: dict[Path, str | None] = {}
    modified_paths: list[Path] = []
    for operation in operations:
        target = resolve_workspace_path(
            workspace,
            operation.path,
            allow_nonexistent=operation.kind in {"add", "update"},
        )
        if operation.kind == "add":
            if target.exists():
                raise ValueError(f"Cannot add {operation.path}: file already exists.")
            pending_contents[target] = "\n".join(operation.lines) + ("\n" if operation.lines else "")
        elif operation.kind == "delete":
            if not target.exists() or not target.is_file():
                raise ValueError(f"Cannot delete {operation.path}: file does not exist.")
            pending_contents[target] = None
        elif operation.kind == "update":
            if not target.exists() or not target.is_file():
                raise ValueError(f"Cannot update {operation.path}: file does not exist.")
            original = target.read_text(encoding="utf-8")
            pending_contents[target] = _apply_update_lines(original, operation.lines)
        else:
            raise ValueError(f"Unsupported patch operation {operation.kind}.")
        modified_paths.append(target)
    return pending_contents, modified_paths
