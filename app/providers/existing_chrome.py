from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from app.automation.existing_chrome import (
    ChromeTabInfo,
    ChromeTabRef,
    best_effort_stop,
    detect_busy_state,
    detect_login_or_verification,
    inspect_generated_image_state,
    export_generated_images,
    get_tab_info,
    inspect_upload_state,
    insert_prompt,
    list_google_chrome_tabs,
    open_provider_tab_in_existing_chrome,
    submit_prompt,
    wait_for_generated_image_ready,
    wait_for_prompt_input,
    wait_for_response_stable,
)
from app.automation.extraction import clean_answer_text
from app.errors import ErrorCode
from app.providers.base import (
    ImageExtractionResult,
    LoginState,
    ProviderAdapter,
    ProviderDiagnostics,
    RebindResult,
)


class ExistingChromeProviderAdapter:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def _wait_for_generated_image_ready(
        self,
        tab: ChromeTabRef,
        *,
        timeout_ms: int,
    ) -> bool:
        return wait_for_generated_image_ready(
            tab,
            provider=self.provider,
            timeout_ms=timeout_ms,
        )

    def _inspect_generated_image_state(self, tab: ChromeTabRef) -> dict[str, object]:
        return inspect_generated_image_state(tab, provider=self.provider)

    def _export_generated_images(
        self,
        tab: ChromeTabRef,
        *,
        output_dir: Path,
        job_id: str,
        timeout_ms: int,
        max_images: int,
        strategy: str,
    ) -> list[Path]:
        return export_generated_images(
            tab,
            output_dir=output_dir,
            job_id=job_id,
            provider=self.provider,
            max_images=max_images,
            timeout_ms=timeout_ms,
            strategy=strategy,
        )

    def open_tab(self, *, target_url: str | None = None) -> ChromeTabInfo:
        target = target_url or self.default_url
        tab = open_provider_tab_in_existing_chrome(self.provider, target)
        return get_tab_info(tab) or ChromeTabInfo(
            window_id=tab.window_id,
            tab_id=tab.tab_id,
            url=target,
            title=f"{self.provider.title()}",
            active=True,
        )

    def rebind_tab(
        self,
        *,
        conversation_url: str | None,
        tab_ref: ChromeTabRef | None,
    ) -> RebindResult:
        if tab_ref is not None:
            info = get_tab_info(tab_ref)
            if info is not None and self._matches_provider_url(info.url):
                return RebindResult(tab=tab_ref, info=info)

        tabs = list_google_chrome_tabs()
        if conversation_url:
            for tab in tabs:
                if tab.url == conversation_url:
                    return RebindResult(tab=tab.ref, info=tab)

        domain_matches = [tab for tab in tabs if self._matches_provider_url(tab.url)]
        if domain_matches:
            preferred = next((tab for tab in domain_matches if tab.active), domain_matches[-1])
            return RebindResult(tab=preferred.ref, info=preferred)
        return RebindResult(tab=None, info=None, error_code=ErrorCode.TAB_LOST)

    def detect_login_state(self, tab: ChromeTabRef) -> LoginState:
        state = detect_login_or_verification(tab, provider=self.provider)
        if state == "login_required":
            return "login_required"
        if state == "manual_verification_required":
            return "manual_verification_required"
        return "ready"

    def detect_busy_state(self, tab: ChromeTabRef) -> bool:
        return detect_busy_state(tab, provider=self.provider)

    def find_prompt_input(self, tab: ChromeTabRef, timeout_ms: int) -> None:
        wait_for_prompt_input(tab, timeout_ms=timeout_ms, provider=self.provider)

    def insert_prompt(self, tab: ChromeTabRef, prompt: str) -> None:
        insert_prompt(tab, prompt, provider=self.provider)

    def verify_upload_complete(self, tab: ChromeTabRef) -> dict[str, object]:
        return inspect_upload_state(tab, provider=self.provider)

    def submit_prompt(self, tab: ChromeTabRef) -> None:
        submit_prompt(tab, provider=self.provider)

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
        try:
            return wait_for_response_stable(
                tab,
                timeout_ms=timeout_ms,
                stable_seconds=stable_seconds,
                excluded_text=excluded_text,
                expect_images=expect_images,
                provider=self.provider,
                should_cancel=should_cancel,
            )
        except TimeoutError as exc:
            if str(exc) == "__ORD_CANCELLED__":
                self.best_effort_stop(tab)
            raise

    def extract_text_result(self, raw_text: str) -> str:
        return clean_answer_text(raw_text)

    def extract_image_result(
        self,
        tab: ChromeTabRef,
        *,
        output_dir: Path,
        job_id: str,
        timeout_ms: int,
        max_images: int,
    ) -> ImageExtractionResult:
        notes = ["provider download control: checking same-turn download/open-image affordances first"]
        if not self._wait_for_generated_image_ready(tab, timeout_ms=timeout_ms):
            notes.append("generated image extraction timed out while the provider was still rendering")
            return ImageExtractionResult(
                artifacts=[],
                source="dom",
                confidence="low",
                technical_notes=notes,
            )

        state = self._inspect_generated_image_state(tab)
        download_paths = self._export_generated_images(
            tab,
            output_dir=output_dir,
            job_id=job_id,
            max_images=max_images,
            timeout_ms=timeout_ms,
            strategy="download",
        )
        if download_paths:
            notes.append("provider download control: captured same-turn download/open-image asset")
            return ImageExtractionResult(
                artifacts=download_paths,
                source="download",
                confidence="high",
                technical_notes=notes,
            )

        notes.append("provider download control: no downloadable same-turn asset controls were usable")
        asset_paths = self._export_generated_images(
            tab,
            output_dir=output_dir,
            job_id=job_id,
            max_images=max_images,
            timeout_ms=timeout_ms,
            strategy="asset_url",
        )
        if asset_paths:
            notes.append("same-turn asset URL discovery: captured from the latest assistant turn")
            return ImageExtractionResult(
                artifacts=asset_paths,
                source="asset_url",
                confidence="high" if state.get("generatedMarker") or state.get("downloadAffordance") else "medium",
                technical_notes=notes,
            )

        notes.append("same-turn asset URL discovery: no durable image URLs were exportable")
        dom_paths = self._export_generated_images(
            tab,
            output_dir=output_dir,
            job_id=job_id,
            max_images=max_images,
            timeout_ms=timeout_ms,
            strategy="dom",
        )
        if not dom_paths:
            notes.append("dom fallback: no trusted artifacts found in the latest assistant turn")
            return ImageExtractionResult(
                artifacts=[],
                source="dom",
                confidence="low",
                technical_notes=notes,
            )

        dom_confidence = (
            "medium"
            if state.get("generatedMarker") or state.get("downloadAffordance")
            else "low"
        )
        notes.append("dom fallback: exported only from the latest assistant turn and excluded user-turn images")
        return ImageExtractionResult(
            artifacts=dom_paths,
            source="dom",
            confidence=dom_confidence,
            technical_notes=notes,
        )

    def best_effort_stop(self, tab: ChromeTabRef) -> bool:
        return best_effort_stop(tab, provider=self.provider)

    def collect_diagnostics(self) -> ProviderDiagnostics:
        tabs = [tab for tab in list_google_chrome_tabs() if self._matches_provider_url(tab.url)]
        active = next((tab for tab in tabs if tab.active), tabs[-1] if tabs else None)
        login_state: LoginState = "ready"
        busy = False
        notes: list[str] = []
        if active is not None:
            try:
                login_state = self.detect_login_state(active.ref)
                busy = self.detect_busy_state(active.ref)
            except Exception as exc:
                notes.append(str(exc))
        return ProviderDiagnostics(
            logged_in=login_state == "ready" and bool(tabs),
            login_state=login_state,
            busy=busy,
            open_tabs=[asdict(tab) for tab in tabs],
            active_tab=asdict(active) if active is not None else None,
            notes=notes,
        )

    def _matches_provider_url(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        if self.provider == "chatgpt":
            return "chatgpt.com" in host
        return "gemini.google.com" in host or "bard.google.com" in host

    @property
    def default_url(self) -> str:
        return "https://chatgpt.com/" if self.provider == "chatgpt" else "https://gemini.google.com/app"


class GeminiAdapter(ExistingChromeProviderAdapter):
    def __init__(self) -> None:
        super().__init__("gemini")


class ChatGPTAdapter(ExistingChromeProviderAdapter):
    def __init__(self) -> None:
        super().__init__("chatgpt")


def get_provider_adapter(provider: str) -> ProviderAdapter:
    if provider == "chatgpt":
        return ChatGPTAdapter()
    return GeminiAdapter()
