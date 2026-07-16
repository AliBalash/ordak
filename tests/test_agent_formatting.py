from __future__ import annotations

from pathlib import Path

from app.agent.formatting import build_initial_task_message
from app.agent.types import AgentResolvedConfig


def test_initial_task_requires_fenced_action_json() -> None:
    message = build_initial_task_message(
        question="Fix the calculator.",
        resolved_config=AgentResolvedConfig(
            workspace=Path("/tmp/workspace"),
            workspace_display="/tmp/workspace",
            max_steps=20,
            command_timeout_seconds=60,
            execution_backend="host",
            network_enabled=False,
        ),
    )

    assert "inside a ```json code fence" in message
    assert "Never emit action JSON as ordinary Markdown text" in message
    assert 'RUN\n```json\n{"version":1' in message
    assert "\n```\n\nAfter each action" in message
