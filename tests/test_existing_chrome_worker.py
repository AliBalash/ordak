from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from app.agent.types import AgentResolvedConfig
from app.automation.existing_chrome import ChromeTabInfo, ChromeTabRef, ResponseBaseline
from app.automation.gemini_worker import GeminiAutomationError, GeminiJobRequest, run_gemini_job
from app.config import settings
from app.providers.base import ImageExtractionResult, ProviderDiagnostics, RebindResult


@dataclass
class RuntimeSpy:
    statuses: list[str] = field(default_factory=list)
    logs: list[tuple[str, str]] = field(default_factory=list)
    screenshots: list[Path] = field(default_factory=list)
    output_images: list[Path] = field(default_factory=list)
    traces: list[Path] = field(default_factory=list)
    answer: str | None = None
    errors: list[tuple[str, str, str | None]] = field(default_factory=list)
    remembered_tabs: list[ChromeTabInfo] = field(default_factory=list)
    agent_max_protocol_errors: int = 5
    agent_seen_command_ids: tuple[str, ...] = ()
    should_cancel = None

    def checkpoint(self) -> None:
        return None

    def update_status(self, status: str) -> None:
        self.statuses.append(status)

    def append_log(self, message: str, level: str = "info") -> None:
        self.logs.append((level, message))

    def attach_screenshot(self, path: Path) -> None:
        self.screenshots.append(path)

    def attach_output_image(self, path: Path) -> None:
        self.output_images.append(path)

    def remember_conversation_state(self, tab_info: ChromeTabInfo) -> None:
        self.remembered_tabs.append(tab_info)

    def set_trace_path(self, path: Path) -> None:
        self.traces.append(path)

    def save_answer(self, answer: str) -> None:
        self.answer = answer

    def save_error(self, message: str, status: str = "failed", error_code: str | None = None) -> None:
        self.errors.append((status, message, error_code))


class FakeAdapter:
    def __init__(self) -> None:
        self.opened_url: str | None = None
        self.inserted: list[str] = []
        self.submit_calls = 0
        self.submit_failures_remaining = 0
        self.best_effort_stop_calls = 0
        self.last_max_images: int | None = None
        self.rebind_calls: list[tuple[str | None, ChromeTabRef | None]] = []
        self.result = "سلام! من خوبم."
        self.previous_response = ""
        self.previous_assistant_turn_count = 0
        self.wait_previous_responses: list[str] = []
        self.wait_previous_turn_counts: list[int | None] = []
        self.wait_failures_remaining = 0
        self.image_paths: list[Path] = []
        self.rebind_result = RebindResult(
            tab=ChromeTabRef(window_id=11, tab_id=22),
            info=ChromeTabInfo(window_id=11, tab_id=22, url="https://example.com/c/1", title="tab", active=True),
        )
        self.login_state = "ready"
        self.find_prompt_calls = 0
        self.find_prompt_failures_remaining = 0
        self.upload_state = {
            "attachment": True,
            "attachmentCount": 3,
            "hasPreview": True,
            "loading": False,
            "submitReady": True,
        }

    def open_tab(self, *, target_url: str | None = None) -> ChromeTabInfo:
        self.opened_url = target_url
        return ChromeTabInfo(window_id=1, tab_id=2, url=target_url or "https://example.com", title="new", active=True)

    def rebind_tab(self, *, conversation_url: str | None, tab_ref: ChromeTabRef | None) -> RebindResult:
        self.rebind_calls.append((conversation_url, tab_ref))
        return self.rebind_result

    def detect_login_state(self, tab: ChromeTabRef) -> str:
        return self.login_state

    def detect_busy_state(self, tab: ChromeTabRef) -> bool:
        return False

    def find_prompt_input(self, tab: ChromeTabRef, timeout_ms: int) -> None:
        self.find_prompt_calls += 1
        if self.find_prompt_failures_remaining:
            self.find_prompt_failures_remaining -= 1
            raise RuntimeError("prompt input is still loading")
        return None

    def verify_upload_complete(self, tab: ChromeTabRef) -> dict[str, object]:
        return self.upload_state

    def submit_prompt(self, tab: ChromeTabRef) -> None:
        self.submit_calls += 1
        if self.submit_failures_remaining:
            self.submit_failures_remaining -= 1
            raise RuntimeError("temporary submit failure")

    def read_latest_response_text(self, tab: ChromeTabRef) -> str:
        return self.previous_response

    def read_latest_response_baseline(self, tab: ChromeTabRef) -> ResponseBaseline:
        return ResponseBaseline(
            text=self.previous_response,
            assistant_turn_count=self.previous_assistant_turn_count,
        )

    def wait_for_response(
        self,
        tab: ChromeTabRef,
        *,
        timeout_ms: int,
        stable_seconds: int,
        excluded_text: str,
        expect_images: bool,
        previous_response: str = "",
        previous_assistant_turn_count: int | None = None,
        should_cancel=None,
        stall_refresh_seconds: int = 0,
        max_stall_refreshes: int = 0,
        recovery_callback=None,
    ) -> str:
        self.wait_previous_responses.append(previous_response)
        self.wait_previous_turn_counts.append(previous_assistant_turn_count)
        if should_cancel and should_cancel():
            raise TimeoutError("__ORD_CANCELLED__")
        if self.wait_failures_remaining:
            self.wait_failures_remaining -= 1
            raise TimeoutError("temporary response stall")
        return self.result

    def extract_text_result(self, raw_text: str) -> str:
        return raw_text

    def extract_image_result(
        self,
        tab: ChromeTabRef,
        *,
        output_dir: Path,
        job_id: str,
        timeout_ms: int,
        max_images: int,
    ) -> ImageExtractionResult:
        self.last_max_images = max_images
        return ImageExtractionResult(
            artifacts=list(self.image_paths),
            source="dom",
            confidence="high" if self.image_paths else "low",
            technical_notes=[],
        )

    def best_effort_stop(self, tab: ChromeTabRef) -> bool:
        self.best_effort_stop_calls += 1
        return True

    def collect_diagnostics(self) -> ProviderDiagnostics:
        return ProviderDiagnostics(
            logged_in=True,
            login_state="ready",
            busy=False,
            open_tabs=[],
            active_tab=None,
            notes=[],
        )


def install_fake_adapter(monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter) -> None:
    monkeypatch.setattr("app.automation.gemini_worker.get_provider_adapter", lambda provider: adapter)
    monkeypatch.setattr("app.automation.gemini_worker.get_tab_info", lambda tab: ChromeTabInfo(tab.window_id, tab.tab_id, "https://example.com/c/1", "Tab", True))
    monkeypatch.setattr("app.automation.gemini_worker.insert_prompt_existing", lambda tab, prompt, provider="gemini": adapter.inserted.append(prompt))
    monkeypatch.setattr("app.automation.gemini_worker.activate_create_image_mode", lambda tab, provider="gemini": True)
    monkeypatch.setattr("app.automation.gemini_worker.upload_local_file", lambda *args, **kwargs: None)


def generic_worker_settings():
    return replace(settings, browser_platform="auto")


def test_chat_job_fails_when_google_chrome_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    local_settings = generic_worker_settings()
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: False)

    with pytest.raises(GeminiAutomationError, match="Google Chrome is not open"):
        run_gemini_job(
            "job-no-chrome",
            GeminiJobRequest(question="سلام", start_new_chat=True),
            runtime=runtime,
            app_settings=local_settings,
        )

    assert runtime.statuses == ["checking_browser"]
    assert runtime.errors == [
        (
            "failed",
            "Google Chrome is not open. Open your regular Chrome with Gemini already logged in, then retry.",
            "chrome_not_open",
        )
    ]


def test_chat_job_uses_adapter_open_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    local_settings = generic_worker_settings()
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    answer = run_gemini_job(
        "job-existing-chrome",
        GeminiJobRequest(question="سلام gemini خوبی ؟", start_new_chat=True),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "سلام! من خوبم."
    assert adapter.inserted == ["سلام gemini خوبی ؟"]
    assert runtime.answer == "سلام! من خوبم."
    assert adapter.opened_url == local_settings.provider_new_chat_url("gemini")
    assert runtime.statuses == [
        "checking_browser",
        "opening_provider_tab",
        "checking_login",
        "finding_input",
        "submitting_prompt",
        "waiting_for_response",
        "extracting_answer",
        "completed",
    ]


def test_chat_job_recovers_from_transient_submit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    adapter.submit_failures_remaining = 1
    local_settings = generic_worker_settings()
    reloads: list[str] = []
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)
    monkeypatch.setattr(
        "app.automation.gemini_worker.execute_javascript",
        lambda tab, script: reloads.append(script) or "reloading",
    )
    monkeypatch.setattr("app.automation.gemini_worker.time.sleep", lambda _: None)

    answer = run_gemini_job(
        "job-submit-recovery",
        GeminiJobRequest(question="recover this prompt", start_new_chat=True),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "سلام! من خوبم."
    assert adapter.submit_calls == 2
    assert adapter.inserted == ["recover this prompt", "recover this prompt"]
    assert reloads == []


def test_agent_job_recovers_when_prompt_input_is_still_loading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    adapter.find_prompt_failures_remaining = 1
    adapter.result = "FINAL\nRecovered after input reload."
    reloads: list[str] = []
    local_settings = replace(
        generic_worker_settings(),
        agent_allowed_workspace_roots=(tmp_path.resolve(),),
    )
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)
    monkeypatch.setattr(
        "app.automation.gemini_worker.execute_javascript",
        lambda tab, script: reloads.append(script) or "reopening",
    )
    monkeypatch.setattr("app.automation.gemini_worker.time.sleep", lambda _: None)

    answer = run_gemini_job(
        "job-input-recovery",
        GeminiJobRequest(
            question="continue",
            provider="chatgpt",
            mode="agent",
            start_new_chat=False,
            conversation_url="https://chatgpt.com/g/custom/c/conversation-123",
            agent_config=AgentResolvedConfig(
                workspace=tmp_path.resolve(),
                workspace_display=str(tmp_path.resolve()),
                max_steps=5,
                command_timeout_seconds=60,
                execution_backend="host",
                network_enabled=False,
            ),
        ),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "Recovered after input reload."
    assert adapter.find_prompt_calls >= 2
    assert any("conversation-123" in script for script in reloads)
    assert any("input is not ready" in message for _, message in runtime.logs)


def test_chat_job_excludes_the_response_visible_before_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    adapter.previous_response = "the previous assistant response"
    adapter.previous_assistant_turn_count = 5
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    run_gemini_job(
        "job-response-baseline",
        GeminiJobRequest(question="continue", start_new_chat=True),
        runtime=runtime,
        app_settings=generic_worker_settings(),
    )

    assert adapter.wait_previous_responses == ["the previous assistant response"]
    assert adapter.wait_previous_turn_counts == [5]


def test_chatgpt_exchange_adds_a_stable_correlation_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    run_gemini_job(
        "job-chatgpt-correlation",
        GeminiJobRequest(
            question="متن دارای `backtick` و literal_*_value",
            provider="chatgpt",
            start_new_chat=True,
        ),
        runtime=runtime,
        app_settings=generic_worker_settings(),
    )

    assert len(adapter.inserted) == 1
    assert adapter.inserted[0].startswith("متن دارای `backtick` و literal_*_value\n\n")
    assert adapter.inserted[0].rsplit("\n", 1)[-1].startswith("ORDAK_EXCHANGE_ID_")


def test_agent_exchange_recovers_completed_response_after_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    adapter.wait_failures_remaining = 1
    adapter.result = "FINAL\nRecovered successfully."
    local_settings = replace(
        generic_worker_settings(),
        agent_allowed_workspace_roots=(tmp_path.resolve(),),
        agent_max_exchange_recoveries=1,
        agent_recovery_delay_seconds=0,
    )
    refresh_scripts = []
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)
    monkeypatch.setattr(
        "app.automation.gemini_worker.execute_javascript",
        lambda tab, script: refresh_scripts.append(script) or "recovering",
    )
    monkeypatch.setattr("app.automation.gemini_worker.time.sleep", lambda _: None)

    answer = run_gemini_job(
        "job-agent-response-recovery",
        GeminiJobRequest(
            question="کار را کامل کن",
            provider="chatgpt",
            mode="agent",
            start_new_chat=True,
            agent_config=AgentResolvedConfig(
                workspace=tmp_path.resolve(),
                workspace_display=str(tmp_path.resolve()),
                max_steps=5,
                command_timeout_seconds=60,
                execution_backend="host",
                network_enabled=False,
            ),
        ),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "Recovered successfully."
    assert adapter.submit_calls == 1
    assert len(refresh_scripts) == 1
    assert any("Recovered the completed assistant response" in message for _, message in runtime.logs)


def test_chat_job_reuses_existing_conversation_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    local_settings = generic_worker_settings()
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    answer = run_gemini_job(
        "job-existing-conversation-tab",
        GeminiJobRequest(
            question="ادامه بده",
            conversation_id="conv-1",
            start_new_chat=False,
            target_tab=ChromeTabRef(window_id=11, tab_id=22),
        ),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "سلام! من خوبم."
    assert adapter.opened_url is None
    assert runtime.remembered_tabs


def test_image_job_uses_existing_google_chrome(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    local_settings = generic_worker_settings()
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)
    upload_path = tmp_path / "sample-image-test.png"
    upload_path.write_bytes(b"fake-image")

    answer = run_gemini_job(
        "job-existing-chrome-image",
        GeminiJobRequest(
            question="این تصویر را توضیح بده.",
            mode="image_analyze",
            upload_paths=[upload_path],
            start_new_chat=True,
        ),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "سلام! من خوبم."
    assert adapter.inserted == ["Analyze the uploaded image and answer this request:\nاین تصویر را توضیح بده."]


def test_image_generate_job_collects_output_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    local_settings = generic_worker_settings()
    output_path = tmp_path / "generated.png"
    output_path.write_bytes(b"fake-output")
    adapter.result = "__GENERATED_IMAGES__:2"
    adapter.image_paths = [output_path]
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    answer = run_gemini_job(
        "job-existing-chrome-image-generate",
        GeminiJobRequest(
            question="پس زمینه را حذف کن.",
            mode="image_generate",
            start_new_chat=True,
        ),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "Gemini generated image output in the current Chrome tab. Saved images: 1."
    assert runtime.output_images == [output_path]
    assert adapter.last_max_images == local_settings.max_output_images_per_job


def test_linux_chatgpt_image_generate_refreshes_tab_before_extracting_images(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    output_path = tmp_path / "generated.png"
    output_path.write_bytes(b"fake-output")
    adapter.result = "__GENERATED_IMAGES__:1"
    adapter.image_paths = [output_path]
    local_settings = replace(settings, browser_platform="linux")
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)
    monkeypatch.setattr(
        "app.automation.gemini_worker.ensure_linux_remote_debugging_session",
        lambda app_settings, target_url=None: None,
    )

    answer = run_gemini_job(
        "job-linux-chatgpt-image-generate",
        GeminiJobRequest(
            question="پس زمینه را سفید کن.",
            provider="chatgpt",
            mode="image_generate",
            start_new_chat=True,
        ),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "ChatGPT generated image output in the current Chrome tab. Saved images: 1."
    assert adapter.rebind_calls[-1] == (None, None)
    assert runtime.output_images == [output_path]


def test_chatgpt_job_uses_existing_google_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    local_settings = generic_worker_settings()
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    answer = run_gemini_job(
        "job-chatgpt-existing-chrome",
        GeminiJobRequest(question="سلام", provider="chatgpt", start_new_chat=True),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "سلام! من خوبم."
    assert adapter.opened_url == local_settings.provider_new_chat_url("chatgpt")


def test_chatgpt_job_without_project_url_falls_back_to_default_chat_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    local_settings = replace(generic_worker_settings(), chatgpt_project_url=None)
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    answer = run_gemini_job(
        "job-chatgpt-project-missing",
        GeminiJobRequest(question="سلام", provider="chatgpt", start_new_chat=True),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "سلام! من خوبم."
    assert adapter.opened_url == local_settings.chatgpt_url


def test_linux_job_ensures_remote_debugging_before_browser_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    local_settings = replace(settings, browser_platform="linux")
    ensure_calls: list[str] = []
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr(
        "app.automation.gemini_worker.ensure_linux_remote_debugging_session",
        lambda app_settings, target_url=None: ensure_calls.append(target_url or ""),
    )
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    answer = run_gemini_job(
        "job-linux-devtools-ready",
        GeminiJobRequest(question="سلام", provider="chatgpt", start_new_chat=True),
        runtime=runtime,
        app_settings=local_settings,
    )

    assert answer == "سلام! من خوبم."
    assert ensure_calls == [local_settings.chatgpt_url]


def test_linux_job_returns_structured_error_when_remote_debugging_launch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    local_settings = replace(settings, browser_platform="linux")
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr(
        "app.automation.gemini_worker.ensure_linux_remote_debugging_session",
        lambda app_settings, target_url=None: (_ for _ in ()).throw(RuntimeError("launch failed")),
    )

    with pytest.raises(GeminiAutomationError, match="launch failed"):
        run_gemini_job(
            "job-linux-launch-failed",
            GeminiJobRequest(question="سلام", start_new_chat=True),
            runtime=runtime,
            app_settings=local_settings,
        )

    assert runtime.errors == [
        (
            "failed",
            "launch failed",
            "chrome_not_open",
        )
    ]
