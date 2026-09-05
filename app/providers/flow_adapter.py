from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from app.providers.base import ProviderAdapter, ProviderDiagnostics, RebindResult, ImageExtractionResult
from app.automation.existing_chrome import ChromeTabRef, ChromeTabInfo

@dataclass
class FlowAdapter(ProviderAdapter):
    provider: str = "flow"

    def open_tab(self, *, target_url: str | None = None) -> ChromeTabInfo:
        from app.automation.existing_chrome import open_provider_tab_in_existing_chrome, get_tab_info
        target = target_url or "https://flow.google.com/"
        tab = open_provider_tab_in_existing_chrome(self.provider, target)
        info = get_tab_info(tab)
        return info or ChromeTabInfo(window_id=tab.window_id, tab_id=tab.tab_id, url=target, title="Flow", active=True, window_key=tab.window_key, target_id=tab.target_id)

    def rebind_tab(self, *, conversation_url: str | None, tab_ref: ChromeTabRef | None) -> RebindResult:
        from app.providers.existing_chrome import ExistingChromeProviderAdapter
        # Use existing chrome logic but with flow-specific URL matching
        adapter = ExistingChromeProviderAdapter("flow")
        return adapter.rebind_tab(conversation_url=conversation_url, tab_ref=tab_ref)

    def detect_login_state(self, tab: ChromeTabRef):
        from app.automation.existing_chrome import detect_login_or_verification
        # Flow uses Google auth, detect via generic login detection
        return detect_login_or_verification(tab, provider="flow")

    def detect_busy_state(self, tab: ChromeTabRef) -> bool:
        from app.automation.existing_chrome import detect_busy_state
        return detect_busy_state(tab, provider="flow")

    def find_prompt_input(self, tab: ChromeTabRef, timeout_ms: int) -> None:
        from app.automation.existing_chrome import wait_for_prompt_input
        return wait_for_prompt_input(tab, provider="flow", timeout_ms=timeout_ms)

    def verify_upload_complete(self, tab: ChromeTabRef) -> dict:
        from app.automation.existing_chrome import inspect_upload_state
        return inspect_upload_state(tab, provider="flow")

    def submit_prompt(self, tab: ChromeTabRef) -> None:
        from app.automation.existing_chrome import submit_prompt
        return submit_prompt(tab, provider="flow")

    def read_latest_response_text(self, tab: ChromeTabRef) -> str:
        from app.automation.existing_chrome import read_latest_response_text
        return read_latest_response_text(tab, provider="flow")

    def read_latest_response_baseline(self, tab: ChromeTabRef):
        from app.automation.existing_chrome import read_latest_response_baseline
        return read_latest_response_baseline(tab, provider="flow")

    def wait_for_response(self, tab: ChromeTabRef, *, timeout_ms: int, stable_seconds: int, excluded_text: str, expect_images: bool, previous_response: str = "", previous_assistant_turn_count: int | None = None, should_cancel=None, stall_refresh_seconds: int = 0, max_stall_refreshes: int = 0, recovery_callback=None, observation_callback=None) -> str:
        from app.automation.existing_chrome import wait_for_response_stable
        return wait_for_response_stable(tab, provider="flow", timeout_ms=timeout_ms, stable_seconds=stable_seconds, excluded_text=excluded_text, expect_images=expect_images)

    def extract_text_result(self, raw_text: str) -> str:
        from app.automation.extraction import clean_answer_text
        return clean_answer_text(raw_text)

    def extract_image_result(self, tab: ChromeTabRef, *, output_dir: Path, job_id: str, timeout_ms: int, max_images: int) -> ImageExtractionResult:
        # For video_generate, extract video artifacts differently; keep image extraction as fallback
        from app.automation.existing_chrome import export_generated_images
        return ImageExtractionResult(artifacts=[], source="dom", confidence="low", technical_notes=["Flow video extraction not via image path"])

    def best_effort_stop(self, tab: ChromeTabRef) -> bool:
        from app.automation.existing_chrome import best_effort_stop
        return best_effort_stop(tab, provider="flow")

    def collect_diagnostics(self) -> ProviderDiagnostics:
        from app.providers.existing_chrome import ExistingChromeProviderAdapter
        adapter = ExistingChromeProviderAdapter("flow")
        diag = adapter.collect_diagnostics()
        diag.notes.append("FlowAdapter: GOOGLE Flow Web - isolated provider")
        return diag
