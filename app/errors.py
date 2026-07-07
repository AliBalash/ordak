from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    LOGIN_REQUIRED = "login_required"
    MANUAL_VERIFICATION_REQUIRED = "manual_verification_required"
    TAB_LOST = "tab_lost"
    UPLOAD_INCOMPLETE = "upload_incomplete"
    SUBMIT_FAILED = "submit_failed"
    RESPONSE_TIMEOUT = "response_timeout"
    RESULT_NOT_EXTRACTABLE = "result_not_extractable"
    PROVIDER_UI_CHANGED = "provider_ui_changed"
    PROJECT_URL_MISSING = "project_url_missing"
    CHROME_NOT_OPEN = "chrome_not_open"
    APPLE_EVENTS_BLOCKED = "apple_events_blocked"


@dataclass(slots=True, frozen=True)
class ErrorDescriptor:
    code: ErrorCode
    title: str
    message: str
    suggested_action: str
    recoverable: bool


ERROR_DESCRIPTORS: dict[ErrorCode, ErrorDescriptor] = {
    ErrorCode.LOGIN_REQUIRED: ErrorDescriptor(
        code=ErrorCode.LOGIN_REQUIRED,
        title="Login required",
        message="The provider session is not logged in inside your regular Google Chrome.",
        suggested_action="Open the logged-in provider page in your normal Chrome, then resume or retry.",
        recoverable=True,
    ),
    ErrorCode.MANUAL_VERIFICATION_REQUIRED: ErrorDescriptor(
        code=ErrorCode.MANUAL_VERIFICATION_REQUIRED,
        title="Manual verification required",
        message="Manual verification required. Automation paused.",
        suggested_action="Complete the verification in Chrome, then resume the job.",
        recoverable=True,
    ),
    ErrorCode.TAB_LOST: ErrorDescriptor(
        code=ErrorCode.TAB_LOST,
        title="Conversation tab lost",
        message="The saved provider tab is no longer available.",
        suggested_action="Retry in a new tab or reopen the original conversation URL, then resume.",
        recoverable=True,
    ),
    ErrorCode.UPLOAD_INCOMPLETE: ErrorDescriptor(
        code=ErrorCode.UPLOAD_INCOMPLETE,
        title="Upload incomplete",
        message="The uploaded image did not finish attaching before submit.",
        suggested_action="Retry in the same tab or a new tab after the attachment fully appears.",
        recoverable=True,
    ),
    ErrorCode.SUBMIT_FAILED: ErrorDescriptor(
        code=ErrorCode.SUBMIT_FAILED,
        title="Submit failed",
        message="The provider composer did not submit the prompt.",
        suggested_action="Retry in the same tab or open a fresh chat tab.",
        recoverable=True,
    ),
    ErrorCode.RESPONSE_TIMEOUT: ErrorDescriptor(
        code=ErrorCode.RESPONSE_TIMEOUT,
        title="Response timeout",
        message="The provider did not finish generating a stable response before the timeout.",
        suggested_action="Retry or resume after the provider finishes loading.",
        recoverable=True,
    ),
    ErrorCode.RESULT_NOT_EXTRACTABLE: ErrorDescriptor(
        code=ErrorCode.RESULT_NOT_EXTRACTABLE,
        title="Result not extractable",
        message="The provider finished, but ordak could not capture a reliable result artifact.",
        suggested_action="Retry the job or inspect the provider tab directly before retrying.",
        recoverable=True,
    ),
    ErrorCode.PROVIDER_UI_CHANGED: ErrorDescriptor(
        code=ErrorCode.PROVIDER_UI_CHANGED,
        title="Provider UI changed",
        message="The provider layout no longer matches the selectors ordak expects.",
        suggested_action="Use diagnostics, then update selectors or retry after opening a stable chat view.",
        recoverable=True,
    ),
    ErrorCode.PROJECT_URL_MISSING: ErrorDescriptor(
        code=ErrorCode.PROJECT_URL_MISSING,
        title="Project URL missing",
        message="A ChatGPT project URL is required for clean project-scoped chats.",
        suggested_action="Create a ChatGPT project, set its URL, then retry.",
        recoverable=True,
    ),
    ErrorCode.CHROME_NOT_OPEN: ErrorDescriptor(
        code=ErrorCode.CHROME_NOT_OPEN,
        title="Chrome is not open",
        message="Google Chrome is not currently open.",
        suggested_action="Open your regular Google Chrome first, then retry.",
        recoverable=True,
    ),
    ErrorCode.APPLE_EVENTS_BLOCKED: ErrorDescriptor(
        code=ErrorCode.APPLE_EVENTS_BLOCKED,
        title="Chrome Apple Events blocked",
        message="Google Chrome is blocking JavaScript from Apple Events.",
        suggested_action="Enable View > Developer > Allow JavaScript from Apple Events in Chrome, then retry.",
        recoverable=True,
    ),
}


def get_error_descriptor(code: str | None) -> ErrorDescriptor | None:
    if not code:
        return None
    try:
        resolved = ErrorCode(code)
    except ValueError:
        return None
    return ERROR_DESCRIPTORS.get(resolved)


class OrdaKError(RuntimeError):
    def __init__(
        self,
        *,
        code: ErrorCode,
        message: str | None = None,
        technical_details: str | None = None,
    ) -> None:
        descriptor = ERROR_DESCRIPTORS[code]
        super().__init__(message or descriptor.message)
        self.code = code
        self.message = message or descriptor.message
        self.technical_details = technical_details
        self.title = descriptor.title
        self.suggested_action = descriptor.suggested_action
        self.recoverable = descriptor.recoverable


class JobCancelled(RuntimeError):
    def __init__(self, message: str = "Job cancelled.") -> None:
        super().__init__(message)
        self.message = message
