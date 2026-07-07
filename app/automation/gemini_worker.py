from __future__ import annotations

from dataclasses import dataclass
import mimetypes
from pathlib import Path
from typing import Callable

from app.automation.existing_chrome import (
    ChromeTabInfo,
    ChromeTabRef,
    activate_create_image_mode,
    get_tab_info,
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

    @property
    def uploads(self) -> list[Path]:
        return list(self.upload_paths or [])


GeminiJobRequest = AutomationJobRequest


def _provider_name(provider: Provider) -> str:
    return "ChatGPT" if provider == "chatgpt" else "Gemini"


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
    if len(upload_paths) > 1:
        raise OrdaKError(
            code=ErrorCode.UPLOAD_INCOMPLETE,
            message="This MVP currently supports one uploaded image at a time.",
        )
    upload_path = upload_paths[0]
    mime_type = mimetypes.guess_type(upload_path.name)[0] or "application/octet-stream"
    runtime and runtime.append_log(
        f"Attaching uploaded image in the current Google Chrome tab from {upload_path.name}."
    )
    upload_local_file(
        tab,
        file_path=upload_path,
        file_name=upload_path.name,
        mime_type=mime_type,
        timeout_ms=min(app_settings.browser_timeout_ms, 90_000),
        provider=provider,
    )
    runtime and runtime.append_log("Image upload attached in the current Google Chrome tab.")


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
        if not is_google_chrome_running():
            _raise_structured_error(
                OrdaKError(
                    code=ErrorCode.CHROME_NOT_OPEN,
                    message=f"Google Chrome is not open. Open your regular Chrome with {_provider_name(job.provider)} already logged in, then retry.",
                )
            )

        target_url = None
        should_open_new_tab = bool(job.start_new_chat or job.run_strategy == "new_chat")
        if job.run_strategy == "new_tab_same_conversation" and job.conversation_url:
            should_open_new_tab = True
            target_url = job.conversation_url

        if should_open_new_tab:
            if job.provider == "chatgpt" and not resolved.chatgpt_project_url and not target_url:
                _raise_structured_error(
                    OrdaKError(code=ErrorCode.PROJECT_URL_MISSING)
                )
            if runtime is not None:
                runtime.update_status("opening_provider_tab")
                runtime.append_log(
                    f"Opening {_provider_name(job.provider)} in a new tab inside the existing Google Chrome window."
                )
                _runtime_checkpoint(runtime)
            opened = adapter.open_tab(target_url=target_url or _provider_new_chat_url(resolved, job.provider))
            tab = opened.ref
            _remember_tab(runtime, tab)
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
        adapter.find_prompt_input(tab, timeout_ms=min(resolved.browser_timeout_ms, 60_000))

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

        if runtime is not None:
            runtime.update_status("finding_input")
            runtime.append_log(
                f"Looking for the {_provider_name(job.provider)} prompt input in the current Google Chrome tab."
            )
            _runtime_checkpoint(runtime)
        insert_prompt_existing(tab, effective_prompt, provider=job.provider)

        if runtime is not None:
            runtime.update_status("submitting_prompt")
            runtime.append_log("Submitting prompt through the existing Google Chrome tab.")
            _runtime_checkpoint(runtime)
        try:
            adapter.submit_prompt(tab)
        except RuntimeError as exc:
            _raise_structured_error(
                OrdaKError(code=ErrorCode.SUBMIT_FAILED, message=str(exc))
            )

        if runtime is not None:
            runtime.update_status("waiting_for_response")
            runtime.append_log(
                f"Waiting for {_provider_name(job.provider)} to finish generating a stable response."
            )
        try:
                answer = adapter.wait_for_response(
                    tab,
                    timeout_ms=_provider_response_timeout_ms(resolved, job.provider),
                    stable_seconds=_provider_stable_seconds(resolved, job.provider),
                    excluded_text=effective_prompt,
                    expect_images=job.mode == "image_generate",
                    should_cancel=getattr(runtime, "should_cancel", None) if runtime is not None else None,
                )
        except TimeoutError as exc:
            if str(exc) == "__ORD_CANCELLED__":
                raise JobCancelled() from exc
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
        if answer.startswith("__GENERATED_IMAGES__:"):
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
