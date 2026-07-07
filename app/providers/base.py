from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Protocol

from app.automation.existing_chrome import ChromeTabInfo, ChromeTabRef
from app.errors import ErrorCode


ImageSource = Literal["download", "asset_url", "dom"]
ImageConfidence = Literal["high", "medium", "low"]
LoginState = Literal["ready", "login_required", "manual_verification_required"]


@dataclass(slots=True)
class ImageArtifact:
    path: Path
    label: str


@dataclass(slots=True)
class ImageExtractionResult:
    artifacts: list[Path] = field(default_factory=list)
    source: ImageSource = "dom"
    confidence: ImageConfidence = "low"
    technical_notes: list[str] = field(default_factory=list)

    @property
    def is_acceptable(self) -> bool:
        return self.confidence in {"high", "medium"} and bool(self.artifacts)


@dataclass(slots=True)
class RebindResult:
    tab: ChromeTabRef | None
    info: ChromeTabInfo | None
    error_code: ErrorCode | None = None


@dataclass(slots=True)
class ProviderDiagnostics:
    logged_in: bool
    login_state: LoginState
    busy: bool
    open_tabs: list[dict[str, object]]
    active_tab: dict[str, object] | None
    notes: list[str] = field(default_factory=list)


class ProviderAdapter(Protocol):
    provider: str

    def open_tab(self, *, target_url: str | None = None) -> ChromeTabInfo: ...

    def rebind_tab(
        self,
        *,
        conversation_url: str | None,
        tab_ref: ChromeTabRef | None,
    ) -> RebindResult: ...

    def detect_login_state(self, tab: ChromeTabRef) -> LoginState: ...

    def detect_busy_state(self, tab: ChromeTabRef) -> bool: ...

    def find_prompt_input(self, tab: ChromeTabRef, timeout_ms: int) -> None: ...

    def verify_upload_complete(self, tab: ChromeTabRef) -> dict[str, object]: ...

    def submit_prompt(self, tab: ChromeTabRef) -> None: ...

    def wait_for_response(
        self,
        tab: ChromeTabRef,
        *,
        timeout_ms: int,
        stable_seconds: int,
        excluded_text: str,
        expect_images: bool,
        should_cancel: Callable[[], bool] | None = None,
    ) -> str: ...

    def extract_text_result(self, raw_text: str) -> str: ...

    def extract_image_result(
        self,
        tab: ChromeTabRef,
        *,
        output_dir: Path,
        job_id: str,
        timeout_ms: int,
        max_images: int,
    ) -> ImageExtractionResult: ...

    def best_effort_stop(self, tab: ChromeTabRef) -> bool: ...

    def collect_diagnostics(self) -> ProviderDiagnostics: ...
