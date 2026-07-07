from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from app.automation.existing_chrome import ChromeTabInfo, ChromeTabRef
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
        self.best_effort_stop_calls = 0
        self.last_max_images: int | None = None
        self.result = "سلام! من خوبم."
        self.image_paths: list[Path] = []
        self.rebind_result = RebindResult(
            tab=ChromeTabRef(window_id=11, tab_id=22),
            info=ChromeTabInfo(window_id=11, tab_id=22, url="https://example.com/c/1", title="tab", active=True),
        )
        self.login_state = "ready"
        self.upload_state = {
            "attachment": True,
            "hasPreview": True,
            "loading": False,
            "submitReady": True,
        }

    def open_tab(self, *, target_url: str | None = None) -> ChromeTabInfo:
        self.opened_url = target_url
        return ChromeTabInfo(window_id=1, tab_id=2, url=target_url or "https://example.com", title="new", active=True)

    def rebind_tab(self, *, conversation_url: str | None, tab_ref: ChromeTabRef | None) -> RebindResult:
        return self.rebind_result

    def detect_login_state(self, tab: ChromeTabRef) -> str:
        return self.login_state

    def detect_busy_state(self, tab: ChromeTabRef) -> bool:
        return False

    def find_prompt_input(self, tab: ChromeTabRef, timeout_ms: int) -> None:
        return None

    def verify_upload_complete(self, tab: ChromeTabRef) -> dict[str, object]:
        return self.upload_state

    def submit_prompt(self, tab: ChromeTabRef) -> None:
        self.submit_calls += 1

    def wait_for_response(
        self,
        tab: ChromeTabRef,
        *,
        timeout_ms: int,
        stable_seconds: int,
        excluded_text: str,
        expect_images: bool,
        should_cancel=None,
    ) -> str:
        if should_cancel and should_cancel():
            raise TimeoutError("__ORD_CANCELLED__")
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


def test_chat_job_fails_when_google_chrome_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: False)

    with pytest.raises(GeminiAutomationError, match="Google Chrome is not open"):
        run_gemini_job(
            "job-no-chrome",
            GeminiJobRequest(question="سلام", start_new_chat=True),
            runtime=runtime,
            app_settings=settings,
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
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    answer = run_gemini_job(
        "job-existing-chrome",
        GeminiJobRequest(question="سلام gemini خوبی ؟", start_new_chat=True),
        runtime=runtime,
        app_settings=settings,
    )

    assert answer == "سلام! من خوبم."
    assert adapter.inserted == ["سلام gemini خوبی ؟"]
    assert runtime.answer == "سلام! من خوبم."
    assert adapter.opened_url == settings.provider_new_chat_url("gemini")
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


def test_chat_job_reuses_existing_conversation_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
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
        app_settings=settings,
    )

    assert answer == "سلام! من خوبم."
    assert adapter.opened_url is None
    assert runtime.remembered_tabs


def test_image_job_uses_existing_google_chrome(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
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
        app_settings=settings,
    )

    assert answer == "سلام! من خوبم."
    assert adapter.inserted == ["Analyze the uploaded image and answer this request:\nاین تصویر را توضیح بده."]


def test_image_generate_job_collects_output_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
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
        app_settings=settings,
    )

    assert answer == "Gemini generated image output in the current Chrome tab. Saved images: 1."
    assert runtime.output_images == [output_path]
    assert adapter.last_max_images == settings.max_output_images_per_job


def test_chatgpt_job_uses_existing_google_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    answer = run_gemini_job(
        "job-chatgpt-existing-chrome",
        GeminiJobRequest(question="سلام", provider="chatgpt", start_new_chat=True),
        runtime=runtime,
        app_settings=settings,
    )

    assert answer == "سلام! من خوبم."
    assert adapter.opened_url == settings.provider_new_chat_url("chatgpt")


def test_chatgpt_job_requires_project_url_for_new_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = RuntimeSpy()
    adapter = FakeAdapter()
    local_settings = replace(settings, chatgpt_project_url=None)
    install_fake_adapter(monkeypatch, adapter)
    monkeypatch.setattr("app.automation.gemini_worker.is_google_chrome_running", lambda: True)

    with pytest.raises(
        GeminiAutomationError,
        match="A ChatGPT project URL is required",
    ):
        run_gemini_job(
            "job-chatgpt-project-missing",
            GeminiJobRequest(question="سلام", provider="chatgpt", start_new_chat=True),
            runtime=runtime,
            app_settings=local_settings,
        )

    assert runtime.statuses == ["checking_browser"]
