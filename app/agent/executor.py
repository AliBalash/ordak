from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import time
from pathlib import Path

from app.agent.formatting import safe_step_artifact_path
from app.agent.policy import (
    IGNORED_DIRECTORY_NAMES,
    build_agent_environment,
    container_runtime_available,
    validate_exec_argv,
)
from app.agent.protocol import (
    ApplyPatchAction,
    ExecAction,
    ListDirectoryAction,
    ReadFileAction,
    WriteFileAction,
)
from app.agent.tools import apply_begin_patch
from app.agent.types import AgentResolvedConfig, ToolExecutionResult
from app.agent.workspace import resolve_workspace_path
from app.config import Settings


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _truncate_middle(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    head = limit // 2
    tail = max(limit - head - 24, 0)
    return (
        value[:head] + "\n...[truncated]...\n" + value[-tail:],
        True,
    )


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


class AgentExecutor:
    def __init__(
        self,
        *,
        settings: Settings,
        job_id: str,
        storage_dir: Path,
    ) -> None:
        self.settings = settings
        self.job_id = job_id
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def execute(
        self,
        action,
        *,
        resolved_config: AgentResolvedConfig,
        step_index: int,
        should_cancel,
    ) -> ToolExecutionResult:
        if isinstance(action, ExecAction):
            return self._exec(action, resolved_config=resolved_config, step_index=step_index, should_cancel=should_cancel)
        if isinstance(action, ReadFileAction):
            return self._read_file(action, resolved_config=resolved_config)
        if isinstance(action, ListDirectoryAction):
            return self._list_directory(action, resolved_config=resolved_config)
        if isinstance(action, WriteFileAction):
            return self._write_file(action, resolved_config=resolved_config)
        if isinstance(action, ApplyPatchAction):
            return self._apply_patch(action, resolved_config=resolved_config)
        raise ValueError(f"Unsupported agent tool: {action.tool}")

    def _artifact_dir(self, step_index: int) -> Path:
        target = self.storage_dir / f"step-{step_index:04d}"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _exec(self, action: ExecAction, *, resolved_config: AgentResolvedConfig, step_index: int, should_cancel) -> ToolExecutionResult:
        validate_exec_argv(action.argv, self.settings)
        if resolved_config.execution_backend in {"docker", "podman"} and not container_runtime_available(resolved_config.execution_backend):
            raise RuntimeError(
                f"{resolved_config.execution_backend} is not available on this machine."
            )
        cwd = resolve_workspace_path(
            resolved_config.workspace,
            action.cwd,
            allow_nonexistent=False,
        )
        if not cwd.is_dir():
            raise ValueError("exec cwd must resolve to a directory inside the workspace.")
        timeout_seconds = action.timeout_seconds or resolved_config.command_timeout_seconds
        environment = build_agent_environment(
            self.settings,
            workspace=resolved_config.workspace,
            step_home_root=self._artifact_dir(step_index),
        )
        command = list(action.argv)
        if resolved_config.execution_backend in {"docker", "podman"}:
            command = self._container_command(
                backend=resolved_config.execution_backend,
                workspace=resolved_config.workspace,
                cwd=cwd,
                argv=command,
                network_enabled=resolved_config.network_enabled,
            )
            environment = {}

        started_at = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout_bytes = b""
        stderr_bytes = b""
        cancelled = False
        timed_out = False
        while True:
            if should_cancel and should_cancel():
                cancelled = True
                _kill_process_group(process)
                break
            try:
                stdout_bytes, stderr_bytes = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - started_at >= timeout_seconds:
                    timed_out = True
                    _kill_process_group(process)
                    stdout_bytes, stderr_bytes = process.communicate()
                    break
                continue
        duration_ms = int((time.monotonic() - started_at) * 1000)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        stdout, stdout_truncated = _truncate_middle(stdout, self.settings.agent_max_stdout_chars)
        stderr, stderr_truncated = _truncate_middle(stderr, self.settings.agent_max_stderr_chars)
        artifacts: list[dict[str, str]] = []
        artifact_dir = self._artifact_dir(step_index)
        if len(stdout_bytes.decode("utf-8", errors="replace")) > self.settings.agent_max_stdout_chars:
            stdout_artifact = safe_step_artifact_path(artifact_dir, command_id=action.id, suffix="-stdout.txt")
            stdout_artifact.write_text(stdout_bytes.decode("utf-8", errors="replace"), encoding="utf-8")
            artifacts.append({"kind": "stdout", "path": str(stdout_artifact)})
        if len(stderr_bytes.decode("utf-8", errors="replace")) > self.settings.agent_max_stderr_chars:
            stderr_artifact = safe_step_artifact_path(artifact_dir, command_id=action.id, suffix="-stderr.txt")
            stderr_artifact.write_text(stderr_bytes.decode("utf-8", errors="replace"), encoding="utf-8")
            artifacts.append({"kind": "stderr", "path": str(stderr_artifact)})
        exit_code = process.returncode if process.returncode is not None else -9
        return ToolExecutionResult(
            id=action.id,
            tool=action.tool,
            ok=exit_code == 0 and not timed_out and not cancelled,
            payload={
                "ok": exit_code == 0 and not timed_out and not cancelled,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "cancelled": cancelled,
                "duration_ms": duration_ms,
                "stdout": stdout,
                "stderr": stderr,
                "artifacts": artifacts,
                "truncated": {
                    "stdout": stdout_truncated,
                    "stderr": stderr_truncated,
                },
            },
        )

    def _container_command(
        self,
        *,
        backend: str,
        workspace: Path,
        cwd: Path,
        argv: list[str],
        network_enabled: bool,
    ) -> list[str]:
        workspace_cwd = "/" + str(cwd.relative_to(workspace)).replace(os.sep, "/")
        if workspace_cwd == "/.":
            workspace_cwd = "/workspace"
        else:
            workspace_cwd = f"/workspace{workspace_cwd}"
        command = [
            backend,
            "run",
            "--rm",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:rw,size=256m",
            "-v",
            f"{workspace}:/workspace:rw",
            "-w",
            workspace_cwd,
        ]
        if not network_enabled:
            command.extend(["--network", "none"])
        command.extend([self.settings.agent_container_image or "ordak-agent:latest", *argv])
        return command

    def _read_file(self, action: ReadFileAction, *, resolved_config: AgentResolvedConfig) -> ToolExecutionResult:
        target = resolve_workspace_path(resolved_config.workspace, action.path)
        if target.is_dir():
            raise ValueError("read_file path must refer to a file, not a directory.")
        content = target.read_text(encoding="utf-8")
        limit = min(action.limit or self.settings.agent_max_file_read_chars, self.settings.agent_max_file_read_chars)
        sliced = content[action.offset : action.offset + limit]
        truncated = action.offset + limit < len(content)
        return ToolExecutionResult(
            id=action.id,
            tool=action.tool,
            ok=True,
            payload={
                "ok": True,
                "path": action.path,
                "content": sliced,
                "encoding": "utf-8",
                "offset": action.offset,
                "limit": limit,
                "size_chars": len(content),
                "truncated": truncated,
            },
        )

    def _list_directory(self, action: ListDirectoryAction, *, resolved_config: AgentResolvedConfig) -> ToolExecutionResult:
        root = resolve_workspace_path(resolved_config.workspace, action.path)
        if not root.is_dir():
            raise ValueError("list_directory path must refer to a directory.")
        entries: list[dict[str, object]] = []

        def walk(directory: Path, relative_prefix: Path, depth: int) -> None:
            if len(entries) >= action.max_entries or depth > action.max_depth:
                return
            for entry in sorted(directory.iterdir(), key=lambda item: item.name):
                if len(entries) >= action.max_entries:
                    return
                relative_path = relative_prefix / entry.name
                is_symlink = entry.is_symlink()
                entry_type = "symlink" if is_symlink else "directory" if entry.is_dir() else "file"
                entries.append(
                    {
                        "path": str(relative_path) or ".",
                        "type": entry_type,
                    }
                )
                if (
                    entry.is_dir()
                    and not is_symlink
                    and depth < action.max_depth
                    and entry.name not in IGNORED_DIRECTORY_NAMES
                ):
                    walk(entry, relative_path, depth + 1)

        walk(root, Path("."), 0)
        return ToolExecutionResult(
            id=action.id,
            tool=action.tool,
            ok=True,
            payload={
                "ok": True,
                "path": action.path,
                "entries": entries,
                "max_depth": action.max_depth,
                "max_entries": action.max_entries,
                "truncated": len(entries) >= action.max_entries,
            },
        )

    def _write_file(self, action: WriteFileAction, *, resolved_config: AgentResolvedConfig) -> ToolExecutionResult:
        content = action.content
        if content is None:
            raise ValueError("write_file content was not decoded.")
        if len(content) > self.settings.agent_max_file_write_chars:
            raise ValueError("write_file content exceeds the configured size limit.")
        target = resolve_workspace_path(
            resolved_config.workspace,
            action.path,
            allow_nonexistent=True,
        )
        if not target.parent.exists():
            if not action.create_parents:
                raise ValueError(
                    "write_file parent directory does not exist; set create_parents to true."
                )
            target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_dir():
            raise ValueError("write_file target must be a file path.")
        if target.exists() and not action.overwrite:
            raise ValueError("write_file refused to overwrite an existing file.")
        before_hash = None
        before_mode = None
        if target.exists():
            before_hash = _sha256_text(target.read_text(encoding="utf-8"))
            before_mode = stat.S_IMODE(target.stat().st_mode)
        temporary = target.with_name(f".{target.name}.ordak-tmp")
        temporary.write_text(content, encoding="utf-8")
        if before_mode is not None:
            os.chmod(temporary, before_mode)
        os.replace(temporary, target)
        after_hash = _sha256_text(content)
        return ToolExecutionResult(
            id=action.id,
            tool=action.tool,
            ok=True,
            payload={
                "ok": True,
                "path": action.path,
                "before_hash": before_hash,
                "after_hash": after_hash,
                "bytes_written": len(content.encode("utf-8")),
            },
        )

    def _apply_patch(self, action: ApplyPatchAction, *, resolved_config: AgentResolvedConfig) -> ToolExecutionResult:
        patch = action.patch
        if patch is None:
            raise ValueError("apply_patch payload was not decoded.")
        pending_contents, modified_paths = apply_begin_patch(
            resolved_config.workspace,
            patch,
        )
        for target, new_content in pending_contents.items():
            if new_content is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.ordak-patch")
            before_mode = None
            if target.exists():
                before_mode = stat.S_IMODE(target.stat().st_mode)
            temporary.write_text(new_content, encoding="utf-8")
            if before_mode is not None:
                os.chmod(temporary, before_mode)
            os.replace(temporary, target)
        return ToolExecutionResult(
            id=action.id,
            tool=action.tool,
            ok=True,
            payload={
                "ok": True,
                "modified_paths": [
                    str(path.relative_to(resolved_config.workspace))
                    for path in modified_paths
                ],
            },
        )
