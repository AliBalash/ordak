from __future__ import annotations

import json
from typing import Callable

from app.agent.executor import AgentExecutor
from app.agent.formatting import (
    build_initial_task_message,
    build_protocol_error_message,
    build_resume_task_message,
    build_tool_result_message,
)
from app.agent.protocol import FinalResponse, ProtocolValidationError, parse_ordex_response
from app.agent.types import AgentResolvedConfig, ToolExecutionResult
from app.errors import ErrorCode, OrdaKError


def _raise_if_access_blocked(answer: str) -> None:
    lowered = answer.lower()
    blocked_markers = (
        "you do not have access to gpt interactions",
        "log in or sign up to get smarter responses",
        "log in to get answers based on saved chats",
    )
    if any(marker in lowered for marker in blocked_markers):
        raise OrdaKError(
            code=ErrorCode.LOGIN_REQUIRED,
            message=(
                "ChatGPT reported that this Chrome session does not currently have access "
                "to GPT interactions. Log in to ChatGPT in the same Chrome profile and retry."
            ),
        )


def run_agent_job(
    *,
    question: str,
    resolved_config: AgentResolvedConfig,
    executor: AgentExecutor,
    runtime,
    exchange: Callable[[str], str],
    resume: bool = False,
) -> str:
    seen_command_ids = set(getattr(runtime, "agent_seen_command_ids", ()))
    step_number = 0
    protocol_error_count = 0

    runtime.update_status("preparing_agent")
    runtime.append_log(
        f"Preparing Ordex Agent Mode for workspace {resolved_config.workspace_display}."
    )
    initial_prompt = (
        build_resume_task_message(
            question=question,
            resolved_config=resolved_config,
            seen_command_ids=seen_command_ids,
        )
        if resume
        else build_initial_task_message(
            question=question,
            resolved_config=resolved_config,
        )
    )
    runtime.update_status("waiting_for_agent")
    runtime.append_log("Sending the initial Ordex task message.")
    answer = exchange(initial_prompt)
    _raise_if_access_blocked(answer)

    while True:
        runtime.checkpoint()
        runtime.update_status("parsing_agent_action")
        runtime.append_log("Parsing the latest Ordex response.")
        try:
            parsed = parse_ordex_response(answer)
        except ProtocolValidationError as exc:
            protocol_error_count += 1
            runtime.append_log(f"Protocol error from Ordex: {exc}", level="warning")
            invalid_excerpt = answer.strip()
            if len(invalid_excerpt) > 2_000:
                invalid_excerpt = invalid_excerpt[:1_000] + "\n...[truncated]...\n" + invalid_excerpt[-1_000:]
            runtime.append_log(
                f"Invalid Ordex response excerpt: {invalid_excerpt}",
                level="warning",
            )
            if protocol_error_count > runtime.agent_max_protocol_errors:
                raise OrdaKError(
                    code=ErrorCode.AGENT_PROTOCOL_LIMIT_EXCEEDED,
                    message="Ordex exceeded the maximum number of protocol errors.",
                )
            runtime.update_status("waiting_for_agent")
            answer = exchange(build_protocol_error_message(str(exc)))
            continue

        if isinstance(parsed, FinalResponse):
            runtime.update_status("finalizing_agent")
            runtime.append_log("Ordex emitted FINAL. Saving the final answer.")
            runtime.save_answer(parsed.message)
            runtime.update_status("completed")
            runtime.append_log("Agent job completed successfully.")
            return parsed.message

        if parsed.id in seen_command_ids:
            protocol_error_count += 1
            runtime.append_log(
                f"Ordex repeated the command ID {parsed.id}. Sending a protocol error.",
                level="warning",
            )
            if protocol_error_count > runtime.agent_max_protocol_errors:
                raise OrdaKError(
                    code=ErrorCode.AGENT_PROTOCOL_LIMIT_EXCEEDED,
                    message="Ordex exceeded the maximum number of protocol errors.",
                )
            runtime.update_status("waiting_for_agent")
            answer = exchange(
                build_protocol_error_message(
                    f"Duplicate command ID {parsed.id!r}. Use a unique id for every RUN action."
                )
            )
            continue
        protocol_error_count = 0
        if step_number >= resolved_config.max_steps:
            raise OrdaKError(
                code=ErrorCode.AGENT_MAX_STEPS_EXCEEDED,
                message="The agent reached the configured maximum number of steps.",
            )

        seen_command_ids.add(parsed.id)
        step_number += 1
        request_json = json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        step_id = runtime.start_agent_step(
            sequence=step_number,
            command_id=parsed.id,
            tool=parsed.tool,
            request_json=request_json,
        )
        runtime.update_status("executing_agent_action")
        runtime.append_log(
            f"Executing agent step {step_number}: {parsed.tool} ({parsed.id})."
        )
        result: ToolExecutionResult | None = None
        error_message: str | None = None
        try:
            result = executor.execute(
                parsed,
                resolved_config=resolved_config,
                step_index=step_number,
                should_cancel=runtime.should_cancel,
            )
            runtime.finish_agent_step(
                step_id=step_id,
                status="completed" if result.ok else "failed",
                result_json=json.dumps(result.payload, ensure_ascii=False, sort_keys=True),
                error_message=None if result.ok else str(result.payload.get("stderr") or result.payload.get("error") or ""),
            )
        except Exception as exc:
            error_message = str(exc) or "Agent tool execution failed."
            runtime.finish_agent_step(
                step_id=step_id,
                status="failed",
                result_json=json.dumps({"ok": False, "error": error_message}, ensure_ascii=False, sort_keys=True),
                error_message=error_message,
            )
            result = ToolExecutionResult(
                id=parsed.id,
                tool=parsed.tool,
                ok=False,
                payload={"ok": False, "error": error_message},
            )

        runtime.checkpoint()
        runtime.update_status("sending_agent_result")
        runtime.append_log(
            f"Sending TOOL_RESULT for {parsed.id} back to Ordex."
        )
        runtime.update_status("waiting_for_agent")
        answer = exchange(build_tool_result_message(result))
        _raise_if_access_blocked(answer)
