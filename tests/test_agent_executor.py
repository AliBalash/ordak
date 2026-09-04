from __future__ import annotations

import sys

from app.agent.executor import AgentExecutor
from app.agent.protocol import ApplyPatchAction, ExecAction, ReadFileAction, WriteFileAction
from app.agent.types import AgentResolvedConfig
from app.config import settings


def _resolved_config(tmp_path) -> AgentResolvedConfig:
    return AgentResolvedConfig(
        workspace=tmp_path.resolve(),
        workspace_display=str(tmp_path.resolve()),
        max_steps=10,
        command_timeout_seconds=10,
        execution_backend="host",
        network_enabled=False,
    )


def test_executor_can_write_read_and_patch_files(tmp_path) -> None:
    executor = AgentExecutor(
        settings=settings,
        job_id="job-agent",
        storage_dir=tmp_path / ".artifacts",
    )
    config = _resolved_config(tmp_path)

    write_result = executor.execute(
        WriteFileAction(
            version=1,
            id="step-write",
            tool="write_file",
            path="src/example.txt",
            content="hello\nworld\n",
            create_parents=True,
            overwrite=False,
        ),
        resolved_config=config,
        step_index=1,
        should_cancel=None,
    )
    assert write_result.ok is True

    read_result = executor.execute(
        ReadFileAction(
            version=1,
            id="step-read",
            tool="read_file",
            path="src/example.txt",
        ),
        resolved_config=config,
        step_index=2,
        should_cancel=None,
    )
    assert read_result.payload["content"] == "hello\nworld\n"

    patch_result = executor.execute(
        ApplyPatchAction(
            version=1,
            id="step-patch",
            tool="apply_patch",
            patch=(
                "*** Begin Patch\n"
                "*** Update File: src/example.txt\n"
                "@@\n"
                " hello\n"
                "-world\n"
                "+agent\n"
                "*** End Patch"
            ),
        ),
        resolved_config=config,
        step_index=3,
        should_cancel=None,
    )
    assert patch_result.ok is True
    assert (tmp_path / "src" / "example.txt").read_text(encoding="utf-8") == "hello\nagent\n"


def test_executor_can_write_a_file_in_the_workspace_root(tmp_path) -> None:
    executor = AgentExecutor(
        settings=settings,
        job_id="job-agent-root-write",
        storage_dir=tmp_path / ".artifacts",
    )

    result = executor.execute(
        WriteFileAction(
            version=1,
            id="step-root-write",
            tool="write_file",
            path="index.html",
            content="<!doctype html><title>Calculator</title>",
            create_parents=False,
            overwrite=False,
        ),
        resolved_config=_resolved_config(tmp_path),
        step_index=1,
        should_cancel=None,
    )

    assert result.ok is True
    assert (tmp_path / "index.html").read_text(encoding="utf-8").startswith("<!doctype html>")


def test_executor_runs_host_command(tmp_path) -> None:
    executor = AgentExecutor(
        settings=settings,
        job_id="job-exec",
        storage_dir=tmp_path / ".artifacts",
    )
    config = _resolved_config(tmp_path)

    result = executor.execute(
        ExecAction(
            version=1,
            id="step-exec",
            tool="exec",
            cwd=".",
            argv=[sys.executable, "-c", "print('ok-from-agent')"],
            timeout_seconds=5,
        ),
        resolved_config=config,
        step_index=1,
        should_cancel=None,
    )

    assert result.ok is True
    assert "ok-from-agent" in str(result.payload["stdout"])
