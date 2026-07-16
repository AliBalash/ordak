from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.loop import run_agent_job
from app.agent.types import AgentResolvedConfig, ToolExecutionResult
from app.errors import ErrorCode, OrdaKError


class _Runtime:
    def __init__(self) -> None:
        self.agent_max_protocol_errors = 5
        self.should_cancel = None

    def checkpoint(self) -> None:
        return None

    def update_status(self, status: str) -> None:
        self.status = status

    def append_log(self, message: str, level: str = "info") -> None:
        self.last_log = (level, message)

    def save_answer(self, answer: str) -> None:
        self.answer = answer

    def start_agent_step(self, **kwargs):
        raise AssertionError("No tool execution should start for access-blocked responses.")

    def finish_agent_step(self, **kwargs) -> None:
        raise AssertionError("No tool execution should finish for access-blocked responses.")


class _Executor:
    def execute(self, *args, **kwargs):
        raise AssertionError("Executor should not run for access-blocked responses.")


def test_run_agent_job_fails_fast_when_chatgpt_denies_gpt_access() -> None:
    runtime = _Runtime()
    config = AgentResolvedConfig(
        workspace=Path("/tmp"),
        workspace_display="/tmp",
        max_steps=5,
        command_timeout_seconds=60,
        execution_backend="host",
        network_enabled=False,
    )

    with pytest.raises(OrdaKError) as exc_info:
        run_agent_job(
            question="Inspect the repository.",
            resolved_config=config,
            executor=_Executor(),
            runtime=runtime,
            exchange=lambda prompt: (
                "We are sorry, but you do not have access to GPT interactions.\n"
                "Log in or sign up to get smarter responses."
            ),
        )

    assert exc_info.value.code == ErrorCode.LOGIN_REQUIRED


def test_run_agent_job_limits_duplicate_command_id_protocol_errors() -> None:
    class Runtime(_Runtime):
        def __init__(self) -> None:
            super().__init__()
            self.agent_max_protocol_errors = 2
            self.steps_started = 0

        def start_agent_step(self, **kwargs):
            self.steps_started += 1
            return f"step-{self.steps_started}"

        def finish_agent_step(self, **kwargs) -> None:
            return None

    class Executor:
        def execute(self, action, **kwargs):
            return ToolExecutionResult(
                id=action.id,
                tool=action.tool,
                ok=True,
                payload={"ok": True, "entries": []},
            )

    runtime = Runtime()
    repeated_action = (
        'RUN\n{"version":1,"id":"step-1","tool":"list_directory",'
        '"path":".","max_depth":1,"max_entries":20}'
    )

    with pytest.raises(OrdaKError) as exc_info:
        run_agent_job(
            question="Inspect the repository.",
            resolved_config=AgentResolvedConfig(
                workspace=Path("/tmp"),
                workspace_display="/tmp",
                max_steps=5,
                command_timeout_seconds=60,
                execution_backend="host",
                network_enabled=False,
            ),
            executor=Executor(),
            runtime=runtime,
            exchange=lambda prompt: repeated_action,
        )

    assert exc_info.value.code == ErrorCode.AGENT_PROTOCOL_LIMIT_EXCEEDED
    assert runtime.steps_started == 1
