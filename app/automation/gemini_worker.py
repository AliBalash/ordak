from __future__ import annotations

from dataclasses import dataclass
import mimetypes
from pathlib import Path
import time
from typing import Callable
from urllib.parse import urlparse
import uuid

from app.agent import run_agent_job
from app.agent.executor import AgentExecutor
from app.agent.types import AgentResolvedConfig
from app.automation.browser import ensure_linux_remote_debugging_session
from app.automation.existing_chrome import (
    ChromeTabInfo,
    ChromeTabRef,
    activate_create_image_mode,
    execute_javascript,
    get_tab_info,
    wait_for_chatgpt_workspace_ready,
    insert_prompt as insert_prompt_existing,
    is_google_chrome_running,
    upload_local_file,
)
from app.config import Settings, settings
from app.errors import ErrorCode, JobCancelled, OrdaKError
from app.providers import get_provider_adapter
from app.schemas import JobMode, Provider


class GeminiAutomationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: str = "failed",
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.error_code = error_code


class ManualVerificationRequired(GeminiAutomationError):
    def __init__(self, message: str = "Manual verification required. Automation paused.") -> None:
        super().__init__(
            message,
            status="manual_verification_required",
            error_code=ErrorCode.MANUAL_VERIFICATION_REQUIRED.value,
        )


@dataclass(slots=True)
class WorkerRuntime:
    update_status: Callable[[str], None]
    append_log: Callable[[str, str], None]
    attach_screenshot: Callable[[Path], None]
    attach_output_image: Callable[[Path], None]
    remember_conversation_state: Callable[[ChromeTabInfo], None]
    set_trace_path: Callable[[Path], None]
    save_answer: Callable[[str], None]
    save_error: Callable[[str, str, str | None], None]
    should_cancel: Callable[[], bool] | None = None
    start_agent_step: Callable[[int, str, str, str], str] | None = None
    finish_agent_step: Callable[[str, str, str | None, str | None], None] | None = None
    agent_max_protocol_errors: int = 5
    agent_seen_command_ids: tuple[str, ...] = ()

    def checkpoint(self) -> None:
        if self.should_cancel and self.should_cancel():
            raise JobCancelled()


def _runtime_checkpoint(runtime: WorkerRuntime | None) -> None:
    if runtime is not None and hasattr(runtime, "checkpoint"):
        runtime.checkpoint()


@dataclass(slots=True)
class AutomationJobRequest:
    question: str
    provider: Provider = "gemini"
    conversation_id: str | None = None
    start_new_chat: bool = False
    target_tab: ChromeTabRef | None = None
    conversation_url: str | None = None
    mode: JobMode = "chat"
    upload_paths: list[Path] | None = None
    run_strategy: str | None = None
    agent_config: AgentResolvedConfig | None = None

    @property
    def uploads(self) -> list[Path]:
        return list(self.upload_paths or [])


GeminiJobRequest = AutomationJobRequest


def _provider_name(provider: Provider) -> str:
    return "ChatGPT" if provider == "chatgpt" else "Gemini"


def _verify_chatgpt_project_tab(
    tab: ChromeTabRef,
    *,
    app_settings: Settings,
    runtime: WorkerRuntime | None,
) -> None:
    """Refuse a new project job if Chrome did not stay on its configured project URL."""
    project_url = app_settings.chatgpt_project_url or ""
    if not project_url:
        return
    info = get_tab_info(tab)
    current_url = info.url if info is not None else ""
    configured = urlparse(project_url)
    current = urlparse(current_url)
    configured_parts = [part for part in configured.path.split("/") if part]
    current_parts = [part for part in current.path.split("/") if part]
    # ChatGPT may immediately restore a project conversation as
    # /g/<project-id>-<workspace>/c/<conversation-id>.  This remains inside
    # the configured project; only a generic /c/... route is unsafe here.
    project_slug = configured_parts[1] if len(configured_parts) >= 2 and configured_parts[0] == "g" else ""
    project_scoped = (
        current.scheme == configured.scheme
        and current.netloc == configured.netloc
        and len(current_parts) >= 2
        and current_parts[0] == "g"
        and project_slug
        and (
            current_parts[1] == project_slug
            or current_parts[1].startswith(f"{project_slug}-")
        )
    )
    if not project_scoped:
        raise GeminiAutomationError(
            "ChatGPT did not open the configured project URL; refusing to submit into a normal chat."
        )
    if runtime is not None:
        runtime.append_log("Verified the configured ChatGPT Project URL before creating the new chat.")


def _browser_platform_label(app_settings: Settings) -> str:
    platform_name = (app_settings.browser_platform or "").strip().lower()
    if platform_name in {"darwin", "mac", "macos"}:
        return "mac"
    if platform_name in {"linux", "lin"}:
        return "linux"
    return platform_name or "auto"


def _chrome_not_ready_message(app_settings: Settings, provider: Provider) -> str:
    if _browser_platform_label(app_settings) == "linux":
        return (
            "Google Chrome remote debugging is not reachable. Start your regular Chrome on Linux "
            "with --remote-debugging-port=9222, keep "
            f"{_provider_name(provider)} already logged in there, then retry."
        )
    return (
        f"Google Chrome is not open. Open your regular Chrome with {_provider_name(provider)} already "
        "logged in, then retry."
    )


def _ensure_linux_browser_ready(
    app_settings: Settings,
    provider: Provider,
) -> None:
    if _browser_platform_label(app_settings) != "linux":
        return
    ensure_linux_remote_debugging_session(
        app_settings,
        target_url=app_settings.provider_url(provider),
    )


def _refresh_linux_provider_tab_for_images(
    app_settings: Settings,
    adapter,
    tab: ChromeTabRef,
    provider: Provider,
) -> ChromeTabRef:
    if _browser_platform_label(app_settings) != "linux" or provider != "chatgpt":
        return tab
    rebound = adapter.rebind_tab(
        conversation_url=None,
        tab_ref=None,
    )
    return rebound.tab or tab


def _provider_new_chat_url(app_settings: Settings, provider: Provider) -> str:
    return app_settings.provider_new_chat_url(provider)


def _provider_response_timeout_ms(app_settings: Settings, provider: Provider) -> int:
    return app_settings.provider_response_timeout_ms(provider)


def _provider_stable_seconds(app_settings: Settings, provider: Provider) -> int:
    return app_settings.provider_stable_response_seconds(provider)


def _effective_prompt(job: AutomationJobRequest) -> str:
    if job.mode == "image_generate" and job.uploads:
        return (
            "Using the uploaded reference image, generate or edit an image that matches this prompt:\n"
            f"{job.question}"
        )
    if job.mode == "image_generate":
        return f"Generate an image based on this prompt:\n{job.question}"
    if job.mode == "image_analyze" and job.uploads:
        return f"Analyze the uploaded image and answer this request:\n{job.question}"
    return job.question


def _prepare_and_submit_prompt(
    *,
    tab: ChromeTabRef,
    adapter,
    provider: Provider,
    prompt: str,
    runtime: WorkerRuntime | None,
    app_settings: Settings,
) -> None:
    last_error: RuntimeError | None = None
    initial_tab_info = get_tab_info(tab)
    recovery_url = (
        initial_tab_info.url
        if initial_tab_info is not None and "/c/" in initial_tab_info.url
        else None
    )
    for attempt in range(3):
        submit_started = time.perf_counter()
        if attempt:
            if runtime is not None:
                recovery_action = (
                    "Reopening the current conversation and retrying"
                    if attempt == 2 and recovery_url
                    else "Retrying without leaving the current conversation"
                )
                runtime.append_log(
                    f"Prompt submission did not complete. {recovery_action} "
                    f"({attempt + 1}/3).",
                    level="warning",
                )
                _runtime_checkpoint(runtime)
            if attempt == 2:
                try:
                    if recovery_url:
                        execute_javascript(
                            tab,
                            f"window.location.href = {recovery_url!r}; 'reopening'",
                        )
                    else:
                        execute_javascript(tab, "window.location.reload(); 'reloading'")
                except RuntimeError:
                    pass
                time.sleep(4)
            else:
                time.sleep(1)

        if runtime is not None:
            runtime.update_status("finding_input")
            runtime.append_log(
                f"Looking for the {_provider_name(provider)} prompt input in the current Google Chrome tab."
            )
            _runtime_checkpoint(runtime)
        adapter.find_prompt_input(tab, timeout_ms=min(app_settings.browser_timeout_ms, 60_000))
        _map_login_error(provider, adapter.detect_login_state(tab))
        if provider == "chatgpt":
            wait_for_chatgpt_workspace_ready(
                tab,
                timeout_ms=min(app_settings.browser_timeout_ms, 60_000),
            )
        insert_prompt_existing(tab, prompt, provider=provider)

        if runtime is not None:
            runtime.update_status("submitting_prompt")
            runtime.append_log("Submitting prompt through the existing Google Chrome tab.")
            _runtime_checkpoint(runtime)
        try:
            adapter.submit_prompt(tab)
            if runtime is not None:
                runtime.append_log(
                    "TIMING operation=prompt_submit "
                    f"attempt={attempt + 1} elapsed_seconds={time.perf_counter() - submit_started:.3f}"
                )
            return
        except RuntimeError as exc:
            last_error = exc

    _raise_structured_error(
        OrdaKError(
            code=ErrorCode.SUBMIT_FAILED,
            message=str(last_error) if last_error else "Prompt submission failed.",
        )
    )


def _find_prompt_input_with_recovery(
    *,
    tab: ChromeTabRef,
    adapter,
    provider: Provider,
    timeout_ms: int,
    runtime: WorkerRuntime | None,
    recovery_url: str | None,
) -> None:
    last_error: RuntimeError | TimeoutError | None = None
    for attempt in range(2):
        try:
            adapter.find_prompt_input(tab, timeout_ms=timeout_ms)
            return
        except (RuntimeError, TimeoutError) as exc:
            last_error = exc
            if attempt:
                break
            if runtime is not None:
                runtime.append_log(
                    f"{_provider_name(provider)} input is not ready. Reopening the exact conversation and retrying.",
                    level="warning",
                )
                _runtime_checkpoint(runtime)
            try:
                if recovery_url:
                    execute_javascript(
                        tab,
                        f"window.location.href = {recovery_url!r}; 'reopening'",
                    )
                else:
                    execute_javascript(tab, "window.location.reload(); 'reloading'")
            except RuntimeError:
                pass
            time.sleep(4)
            _map_login_error(provider, adapter.detect_login_state(tab))
    if last_error is not None:
        raise last_error


def send_prompt_and_wait_for_text(
    *,
    tab: ChromeTabRef,
    adapter,
    provider: Provider,
    prompt: str,
    runtime: WorkerRuntime | None,
    app_settings: Settings,
    max_recoveries: int = 1,
) -> str:
    exchange_prompt = (
        f"{prompt}\n\nORDAK_EXCHANGE_ID_{uuid.uuid4().hex}"
        if provider == "chatgpt"
        else prompt
    )
    recovery_attempt = 0
    while True:
        baseline_reader = getattr(adapter, "read_latest_response_baseline", None)
        if baseline_reader is not None:
            baseline = baseline_reader(tab)
            previous_response = baseline.text
            previous_assistant_turn_count = baseline.assistant_turn_count
        else:
            previous_response = adapter.read_latest_response_text(tab)
            previous_assistant_turn_count = None
        _prepare_and_submit_prompt(
            tab=tab,
            adapter=adapter,
            provider=provider,
            prompt=exchange_prompt,
            runtime=runtime,
            app_settings=app_settings,
        )
        if runtime is not None:
            runtime.update_status("waiting_for_response")
            runtime.append_log(
                f"Waiting for {_provider_name(provider)} to finish generating a stable response."
            )
        wait_options = {
            "timeout_ms": _provider_response_timeout_ms(app_settings, provider),
            "stable_seconds": _provider_stable_seconds(app_settings, provider),
            "excluded_text": exchange_prompt,
            "previous_response": previous_response,
            "expect_images": False,
            "should_cancel": (
                getattr(runtime, "should_cancel", None) if runtime is not None else None
            ),
            "stall_refresh_seconds": (
                app_settings.chatgpt_stall_refresh_seconds
                if provider == "chatgpt"
                else 0
            ),
            "max_stall_refreshes": (
                app_settings.chatgpt_max_stall_refreshes
                if provider == "chatgpt"
                else 0
            ),
            "recovery_callback": (
                (
                    lambda message: runtime.append_log(message, level="warning")
                )
                if runtime is not None
                else None
            ),
            "observation_callback": (
                (lambda message: runtime.append_log(message, level="info"))
                if runtime is not None
                else None
            ),
        }
        if previous_assistant_turn_count is not None:
            wait_options["previous_assistant_turn_count"] = previous_assistant_turn_count
        try:
            answer = adapter.wait_for_response(tab, **wait_options)
            break
        except TimeoutError as exc:
            if str(exc) == "__ORD_CANCELLED__":
                raise JobCancelled() from exc
            adapter.best_effort_stop(tab)
            if recovery_attempt >= max_recoveries:
                _raise_structured_error(
                    OrdaKError(
                        code=ErrorCode.RESPONSE_TIMEOUT,
                        message=(
                            f"{_provider_name(provider)} did not finish response within "
                            "the timeout after automatic recovery."
                        ),
                    )
                )
            recovery_attempt += 1
            if runtime is not None:
                runtime.append_log(
                    "The provider response remained stuck after refresh. "
                    f"Reopening the same conversation and retrying this exchange "
                    f"({recovery_attempt}/{max_recoveries}).",
                    level="warning",
                )
                _runtime_checkpoint(runtime)
            info = get_tab_info(tab)
            recovery_url = info.url if info is not None else ""
            try:
                if recovery_url:
                    execute_javascript(
                        tab,
                        f"window.location.href = {recovery_url!r}; 'recovering'",
                    )
                else:
                    execute_javascript(tab, "window.location.reload(); 'recovering'")
            except RuntimeError:
                pass
            time.sleep(app_settings.agent_recovery_delay_seconds)
            try:
                adapter.find_prompt_input(
                    tab,
                    timeout_ms=min(app_settings.browser_timeout_ms, 60_000),
                )
                _map_login_error(provider, adapter.detect_login_state(tab))
            except (RuntimeError, TimeoutError):
                continue

            # A refresh can reveal a response that finished server-side while the
            # local page was stale. Reconcile it before resubmitting the prompt.
            try:
                answer = adapter.wait_for_response(
                    tab,
                    timeout_ms=20_000,
                    stable_seconds=min(
                        2,
                        _provider_stable_seconds(app_settings, provider),
                    ),
                    excluded_text=prompt,
                    previous_response=previous_response,
                    previous_assistant_turn_count=previous_assistant_turn_count,
                    expect_images=False,
                    should_cancel=(
                        getattr(runtime, "should_cancel", None)
                        if runtime is not None
                        else None
                    ),
                )
                if answer:
                    if runtime is not None:
                        runtime.append_log(
                            "Recovered the completed assistant response after refreshing "
                            "the same conversation.",
                            level="warning",
                        )
                    break
            except TimeoutError as reconcile_error:
                if str(reconcile_error) == "__ORD_CANCELLED__":
                    raise JobCancelled() from reconcile_error
                continue
    if runtime is not None:
        runtime.update_status("extracting_answer")
        runtime.append_log("Extracting the final response from the current Google Chrome tab.")
        _runtime_checkpoint(runtime)
    answer = adapter.extract_text_result(answer)
    if not answer:
        _raise_structured_error(
            OrdaKError(
                code=ErrorCode.RESULT_NOT_EXTRACTABLE,
                message=f"Could not extract final answer from {_provider_name(provider)} UI.",
            )
        )
    return answer


def _run_agent_job_in_existing_chrome(
    job_id: str,
    job: AutomationJobRequest,
    runtime: WorkerRuntime | None,
    resolved: Settings,
) -> str:
    if job.provider != "chatgpt":
        _raise_structured_error(
            OrdaKError(
                code=ErrorCode.AGENT_TOOL_NOT_SUPPORTED,
                message="Agent mode currently supports ChatGPT only.",
            )
        )
    if job.agent_config is None:
        _raise_structured_error(
            OrdaKError(
                code=ErrorCode.AGENT_WORKSPACE_REQUIRED,
                message="Agent mode requires a resolved workspace configuration.",
            )
        )
    adapter = get_provider_adapter(job.provider)
    tab: ChromeTabRef | None = None
    try:
        if runtime is not None:
            runtime.update_status("checking_browser")
            runtime.append_log("Checking whether Google Chrome is already open.")
            _runtime_checkpoint(runtime)
        try:
            _ensure_linux_browser_ready(resolved, job.provider)
        except (FileNotFoundError, RuntimeError) as exc:
            message = str(exc) or _chrome_not_ready_message(resolved, job.provider)
            _raise_structured_error(
                OrdaKError(
                    code=(
                        ErrorCode.CHROME_CONTROL_UNAVAILABLE
                        if "already running but DevTools" in message
                        else ErrorCode.CHROME_NOT_OPEN
                    ),
                    message=message,
                )
            )
        if not is_google_chrome_running():
            _raise_structured_error(
                OrdaKError(
                    code=ErrorCode.CHROME_NOT_OPEN,
                    message=_chrome_not_ready_message(resolved, job.provider),
                )
            )
        if runtime is not None:
            runtime.update_status("opening_provider_tab")
            runtime.append_log(
                "Opening the Ordex Custom GPT in the existing ChatGPT Chrome session."
            )
            _runtime_checkpoint(runtime)
        if job.start_new_chat:
            opened = adapter.open_tab(
                target_url=resolved.provider_new_chat_url(job.provider, mode="agent")
            )
            tab = opened.ref
            _remember_tab(runtime, tab)
        else:
            rebound = adapter.rebind_tab(
                conversation_url=job.conversation_url,
                tab_ref=job.target_tab,
            )
            if rebound.tab is not None:
                tab = rebound.tab
                _remember_tab(runtime, tab)
            elif job.conversation_url:
                if runtime is not None:
                    runtime.append_log(
                        "The saved tab is unavailable. Reopening the exact previous ChatGPT conversation URL.",
                        level="warning",
                    )
                opened = adapter.open_tab(target_url=job.conversation_url)
                tab = opened.ref
                _remember_tab(runtime, tab)
            else:
                _raise_structured_error(OrdaKError(code=ErrorCode.TAB_LOST))
        if runtime is not None:
            runtime.update_status("checking_login")
            runtime.append_log(
                "Checking whether ChatGPT is already authenticated in the current Google Chrome session."
            )
            _runtime_checkpoint(runtime)
        _map_login_error(job.provider, adapter.detect_login_state(tab))
        _find_prompt_input_with_recovery(
            tab=tab,
            adapter=adapter,
            provider=job.provider,
            timeout_ms=min(resolved.browser_timeout_ms, 60_000),
            runtime=runtime,
            recovery_url=job.conversation_url,
        )
        _remember_tab(runtime, tab)

        def exchange(prompt: str) -> str:
            nonlocal tab
            answer = send_prompt_and_wait_for_text(
                tab=tab,
                adapter=adapter,
                provider=job.provider,
                prompt=prompt,
                runtime=runtime,
                app_settings=resolved,
                max_recoveries=resolved.agent_max_exchange_recoveries,
            )
            _remember_tab(runtime, tab)
            rebound_info = get_tab_info(tab)
            if runtime is not None and rebound_info is not None:
                runtime.remember_conversation_state(rebound_info)
            return answer

        executor = AgentExecutor(
            settings=resolved,
            job_id=job_id,
            storage_dir=resolved.agent_step_log_dir / job_id,
        )
        return run_agent_job(
            question=job.question,
            resolved_config=job.agent_config,
            executor=executor,
            runtime=runtime,
            exchange=exchange,
            resume=job.run_strategy in {"same_tab", "new_tab_same_conversation"},
        )
    except JobCancelled as exc:
        if runtime is not None:
            if tab is not None:
                adapter.best_effort_stop(tab)
            runtime.save_error(exc.message, "cancelled", None)
            runtime.append_log(exc.message, level="warning")
        raise GeminiAutomationError(exc.message, status="cancelled") from exc
    except GeminiAutomationError as exc:
        if runtime is not None:
            runtime.save_error(exc.message, exc.status, exc.error_code)
            runtime.append_log(exc.message, level="error")
        raise
    except OrdaKError as exc:
        if runtime is not None:
            runtime.save_error(exc.message, "failed", exc.code.value)
            runtime.append_log(exc.message, level="error")
        raise GeminiAutomationError(exc.message, error_code=exc.code.value) from exc
    except RuntimeError as exc:
        message = str(exc) or "Unexpected ChatGPT agent automation failure."
        if runtime is not None:
            runtime.save_error(message, "failed", ErrorCode.PROVIDER_UI_CHANGED.value)
            runtime.append_log(message, level="error")
        raise GeminiAutomationError(
            message,
            error_code=ErrorCode.PROVIDER_UI_CHANGED.value,
        ) from exc


def _remember_tab(runtime: WorkerRuntime | None, tab: ChromeTabRef) -> None:
    if runtime is None:
        return
    info = get_tab_info(tab)
    if info is not None:
        runtime.remember_conversation_state(info)


def _attach_uploads_in_existing_chrome(
    tab: ChromeTabRef,
    upload_paths: list[Path],
    app_settings: Settings,
    provider: Provider,
    runtime: WorkerRuntime | None = None,
) -> None:
    if not upload_paths:
        return
    for index, upload_path in enumerate(upload_paths, start=1):
        upload_started = time.perf_counter()
        mime_type = mimetypes.guess_type(upload_path.name)[0] or "application/octet-stream"
        runtime and runtime.append_log(
            f"Attaching reference {index}/{len(upload_paths)} from {upload_path.name}."
        )
        upload_local_file(
            tab,
            file_path=upload_path,
            file_name=upload_path.name,
            mime_type=mime_type,
            timeout_ms=min(app_settings.browser_timeout_ms, 90_000),
            provider=provider,
            cumulative_file_paths=upload_paths[:index],
        )
        upload_state = get_provider_adapter(provider).verify_upload_complete(tab)
        if not (
            upload_state.get("attachment")
            and int(upload_state.get("attachmentCount") or 0) >= index
            and upload_state.get("hasPreview")
            and not upload_state.get("loading")
            and upload_state.get("submitReady")
        ):
            raise OrdaKError(
                code=ErrorCode.UPLOAD_INCOMPLETE,
                message=(
                    f"Reference {index}/{len(upload_paths)} is not ready for submission "
                    f"(provider reports {upload_state.get('attachmentCount', 0)} attachments)."
                ),
            )
        runtime and runtime.append_log(
            "TIMING operation=reference_upload "
            f"reference_index={index} reference_name={upload_path.name} "
            f"elapsed_seconds={time.perf_counter() - upload_started:.3f}"
        )
    runtime and runtime.append_log(f"All {len(upload_paths)} reference attachments are ready.")


def _raise_structured_error(exc: OrdaKError) -> None:
    status = (
        "manual_verification_required"
        if exc.code == ErrorCode.MANUAL_VERIFICATION_REQUIRED
        else "failed"
    )
    raise GeminiAutomationError(
        exc.message,
        status=status,
        error_code=exc.code.value,
    ) from exc


def _map_login_error(provider: Provider, login_state: str) -> None:
    if login_state == "login_required":
        _raise_structured_error(
            OrdaKError(
                code=ErrorCode.LOGIN_REQUIRED,
                message=f"{_provider_name(provider)} login required in your regular Google Chrome session.",
            )
        )
    if login_state == "manual_verification_required":
        raise ManualVerificationRequired()


def run_gemini_job(
    job_id: str,
    job: AutomationJobRequest,
    runtime: WorkerRuntime | None = None,
    app_settings: Settings | None = None,
) -> str:
    if job.mode == "agent":
        return _run_agent_job_in_existing_chrome(
            job_id,
            job,
            runtime=runtime,
            resolved=app_settings or settings,
        )
    return _run_gemini_job_in_existing_chrome(
        job_id,
        job,
        runtime=runtime,
        app_settings=app_settings or settings,
    )


def _run_gemini_job_in_existing_chrome(
    job_id: str,
    job: AutomationJobRequest,
    runtime: WorkerRuntime | None = None,
    app_settings: Settings | None = None,
) -> str:
    resolved = app_settings or settings
    adapter = get_provider_adapter(job.provider)
    tab: ChromeTabRef | None = None
    effective_prompt = _effective_prompt(job)
    try:
        if runtime is not None:
            runtime.update_status("checking_browser")
            runtime.append_log("Checking whether Google Chrome is already open.")
            _runtime_checkpoint(runtime)
        try:
            _ensure_linux_browser_ready(resolved, job.provider)
        except (FileNotFoundError, RuntimeError) as exc:
            message = str(exc) or _chrome_not_ready_message(resolved, job.provider)
            _raise_structured_error(
                OrdaKError(
                    code=(
                        ErrorCode.CHROME_CONTROL_UNAVAILABLE
                        if "already running but DevTools" in message
                        else ErrorCode.CHROME_NOT_OPEN
                    ),
                    message=message,
                )
            )
        if not is_google_chrome_running():
            _raise_structured_error(
                OrdaKError(
                    code=ErrorCode.CHROME_NOT_OPEN,
                    message=_chrome_not_ready_message(resolved, job.provider),
                )
            )

        target_url = None
        should_open_new_tab = bool(job.start_new_chat or job.run_strategy == "new_chat")
        if job.run_strategy == "new_tab_same_conversation" and job.conversation_url:
            should_open_new_tab = True
            target_url = job.conversation_url

        if should_open_new_tab:
            if runtime is not None:
                runtime.update_status("opening_provider_tab")
                runtime.append_log(
                    f"Opening {_provider_name(job.provider)} in a new tab inside the existing Google Chrome window."
                )
                _runtime_checkpoint(runtime)
            opened = adapter.open_tab(target_url=target_url or _provider_new_chat_url(resolved, job.provider))
            tab = opened.ref
            _remember_tab(runtime, tab)
            if job.provider == "chatgpt" and target_url is None:
                _verify_chatgpt_project_tab(tab, app_settings=resolved, runtime=runtime)
        else:
            if runtime is not None:
                runtime.update_status("opening_provider_tab")
                runtime.append_log(
                    f"Rebinding to the current {_provider_name(job.provider)} conversation tab."
                )
                _runtime_checkpoint(runtime)
            rebound = adapter.rebind_tab(
                conversation_url=job.conversation_url,
                tab_ref=job.target_tab,
            )
            if rebound.tab is None:
                _raise_structured_error(OrdaKError(code=ErrorCode.TAB_LOST))
            tab = rebound.tab
            _remember_tab(runtime, tab)

        if runtime is not None:
            runtime.update_status("checking_login")
            runtime.append_log(
                f"Checking whether {_provider_name(job.provider)} is already authenticated in the current Google Chrome session."
            )
            _runtime_checkpoint(runtime)
        _map_login_error(job.provider, adapter.detect_login_state(tab))
        _find_prompt_input_with_recovery(
            tab=tab,
            adapter=adapter,
            provider=job.provider,
            timeout_ms=min(resolved.browser_timeout_ms, 60_000),
            runtime=runtime,
            recovery_url=job.conversation_url,
        )
        if job.provider == "chatgpt" and should_open_new_tab and target_url is None:
            _verify_chatgpt_project_tab(tab, app_settings=resolved, runtime=runtime)

        if job.mode == "image_generate":
            activated = activate_create_image_mode(tab, provider=job.provider)
            if runtime is not None:
                runtime.append_log(
                    f"{_provider_name(job.provider)} image mode activated in the current Google Chrome tab."
                    if activated
                    else f"{_provider_name(job.provider)} image mode button was not found, continuing with a generation prompt instead.",
                    level="info" if activated else "warning",
                )

        if job.uploads:
            _runtime_checkpoint(runtime)
            try:
                _attach_uploads_in_existing_chrome(tab, job.uploads, resolved, job.provider, runtime)
            except TimeoutError as exc:
                _raise_structured_error(
                    OrdaKError(
                        code=ErrorCode.UPLOAD_INCOMPLETE,
                        message=str(exc) or "Timed out while waiting for the uploaded image to finish attaching.",
                    )
                )
            upload_state = adapter.verify_upload_complete(tab)
            if not (
                upload_state.get("attachment")
                and upload_state.get("hasPreview")
                and not upload_state.get("loading")
                and upload_state.get("submitReady")
            ):
                _raise_structured_error(
                    OrdaKError(code=ErrorCode.UPLOAD_INCOMPLETE)
                )

        if job.mode == "image_generate":
            _prepare_and_submit_prompt(
                tab=tab,
                adapter=adapter,
                provider=job.provider,
                prompt=effective_prompt,
                runtime=runtime,
                app_settings=resolved,
            )
            if runtime is not None:
                runtime.update_status("waiting_for_response")
                runtime.append_log(
                    f"Waiting for {_provider_name(job.provider)} to finish generating a stable response."
                )
            resubmits = 0
            while True:
                try:
                    answer = adapter.wait_for_response(
                        tab,
                        timeout_ms=_provider_response_timeout_ms(resolved, job.provider),
                        stable_seconds=_provider_stable_seconds(resolved, job.provider),
                        excluded_text=effective_prompt,
                        expect_images=True,
                        should_cancel=getattr(runtime, "should_cancel", None) if runtime is not None else None,
                        stall_refresh_seconds=resolved.chatgpt_stall_refresh_seconds,
                        max_stall_refreshes=resolved.chatgpt_max_stall_refreshes,
                        recovery_callback=(
                            lambda message: runtime.append_log(message, level="warning")
                            if runtime is not None else None
                        ),
                        observation_callback=(
                            lambda message: runtime.append_log(message, level="info")
                            if runtime is not None else None
                        ),
                    )
                    break
                except TimeoutError as exc:
                    if str(exc) == "__ORD_CANCELLED__":
                        raise JobCancelled() from exc
                    # wait_for_response_stable emits this marker only after a
                    # refresh/reopen found a healthy, idle page with no new
                    # image.  It is the sole safe point for a bounded resend.
                    if (
                        str(exc) == "__ORD_RECONCILED_IDLE_INCOMPLETE__"
                        and resubmits < 1
                    ):
                        resubmits += 1
                        if runtime is not None:
                            runtime.append_log(
                                "Reconciliation found no generated image on an idle ChatGPT page; resubmitting once.",
                                level="warning",
                            )
                        if job.uploads:
                            _attach_uploads_in_existing_chrome(
                                tab, job.uploads, resolved, job.provider, runtime
                            )
                        _prepare_and_submit_prompt(
                            tab=tab,
                            adapter=adapter,
                            provider=job.provider,
                            prompt=effective_prompt,
                            runtime=runtime,
                            app_settings=resolved,
                        )
                        continue
                    adapter.best_effort_stop(tab)
                    _raise_structured_error(
                        OrdaKError(
                            code=ErrorCode.RESPONSE_TIMEOUT,
                            message=f"{_provider_name(job.provider)} did not finish response within the timeout.",
                        )
                    )
            if runtime is not None:
                runtime.update_status("extracting_answer")
                runtime.append_log("Extracting the final response from the current Google Chrome tab.")
                _runtime_checkpoint(runtime)
        else:
            answer = send_prompt_and_wait_for_text(
                tab=tab,
                adapter=adapter,
                provider=job.provider,
                prompt=effective_prompt,
                runtime=runtime,
                app_settings=resolved,
            )
        if answer.startswith("__GENERATED_IMAGES__:"):
            tab = _refresh_linux_provider_tab_for_images(
                resolved,
                adapter,
                tab,
                job.provider,
            )
            extraction = adapter.extract_image_result(
                tab,
                output_dir=resolved.browser_output_dir,
                job_id=job_id,
                timeout_ms=min(_provider_response_timeout_ms(resolved, job.provider), 90_000),
                max_images=resolved.max_output_images_per_job,
            )
            if not extraction.is_acceptable:
                _raise_structured_error(
                    OrdaKError(
                        code=ErrorCode.RESULT_NOT_EXTRACTABLE,
                        message=f"Could not extract generated images from {_provider_name(job.provider)} UI.",
                    )
                )
            if runtime is not None:
                for output_path in extraction.artifacts:
                    runtime.attach_output_image(output_path)
            answer = (
                f"{_provider_name(job.provider)} generated image output in the current Chrome tab. "
                f"Saved images: {len(extraction.artifacts)}."
            )
        else:
            answer = adapter.extract_text_result(answer)
        if not answer:
            _raise_structured_error(
                OrdaKError(
                    code=ErrorCode.RESULT_NOT_EXTRACTABLE,
                    message=f"Could not extract final answer from {_provider_name(job.provider)} UI.",
                )
            )

        _remember_tab(runtime, tab)
        if runtime is not None:
            runtime.save_answer(answer)
            runtime.update_status("completed")
            runtime.append_log("Job completed successfully.")
        return answer
    except JobCancelled as exc:
        if runtime is not None:
            if tab is not None:
                adapter.best_effort_stop(tab)
            runtime.save_error(exc.message, "cancelled", None)
            runtime.append_log(exc.message, level="warning")
        raise GeminiAutomationError(exc.message, status="cancelled") from exc
    except GeminiAutomationError as exc:
        if runtime is not None:
            runtime.save_error(exc.message, exc.status, exc.error_code)
            runtime.append_log(exc.message, level="error")
        raise
    except RuntimeError as exc:
        message = str(exc) or f"Unexpected {_provider_name(job.provider)} automation failure."
        if "JavaScript from Apple Events" in message:
            _raise_structured_error(OrdaKError(code=ErrorCode.APPLE_EVENTS_BLOCKED, message=message))
        _raise_structured_error(
            OrdaKError(code=ErrorCode.PROVIDER_UI_CHANGED, message=message)
        )
