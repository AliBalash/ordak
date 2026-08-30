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
    CHROME_CONTROL_UNAVAILABLE = "chrome_control_unavailable"
    APPLE_EVENTS_BLOCKED = "apple_events_blocked"
    AGENT_DISABLED = "agent_disabled"
    AGENT_TARGET_NOT_CONFIGURED = "agent_target_not_configured"
    AGENT_WORKSPACE_REQUIRED = "agent_workspace_required"
    AGENT_WORKSPACE_NOT_FOUND = "agent_workspace_not_found"
    AGENT_WORKSPACE_NOT_ALLOWED = "agent_workspace_not_allowed"
    AGENT_PROTOCOL_ERROR = "agent_protocol_error"
    AGENT_PROTOCOL_LIMIT_EXCEEDED = "agent_protocol_limit_exceeded"
    AGENT_DUPLICATE_COMMAND_ID = "agent_duplicate_command_id"
    AGENT_MAX_STEPS_EXCEEDED = "agent_max_steps_exceeded"
    AGENT_TOOL_NOT_SUPPORTED = "agent_tool_not_supported"
    AGENT_TOOL_POLICY_DENIED = "agent_tool_policy_denied"
    AGENT_COMMAND_TIMEOUT = "agent_command_timeout"
    AGENT_EXECUTION_BACKEND_UNAVAILABLE = "agent_execution_backend_unavailable"
    AGENT_RESULT_TOO_LARGE = "agent_result_too_large"


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
    ErrorCode.CHROME_CONTROL_UNAVAILABLE: ErrorDescriptor(
        code=ErrorCode.CHROME_CONTROL_UNAVAILABLE,
        title="Chrome control unavailable",
        message="The configured Chrome profile is running but Ordak cannot attach through DevTools.",
        suggested_action="Restart that exact Chrome profile with DevTools enabled, then resume the job. Ordak will not open a fallback profile.",
        recoverable=True,
    ),
    ErrorCode.APPLE_EVENTS_BLOCKED: ErrorDescriptor(
        code=ErrorCode.APPLE_EVENTS_BLOCKED,
        title="Chrome Apple Events blocked",
        message="Google Chrome is blocking JavaScript from Apple Events.",
        suggested_action="Enable View > Developer > Allow JavaScript from Apple Events in Chrome, then retry.",
        recoverable=True,
    ),
    ErrorCode.AGENT_DISABLED: ErrorDescriptor(
        code=ErrorCode.AGENT_DISABLED,
        title="Agent mode disabled",
        message="Ordex Agent Mode is disabled on this ordak instance.",
        suggested_action="Enable agent mode in configuration, then retry.",
        recoverable=False,
    ),
    ErrorCode.AGENT_TARGET_NOT_CONFIGURED: ErrorDescriptor(
        code=ErrorCode.AGENT_TARGET_NOT_CONFIGURED,
        title="Agent target missing",
        message="The ChatGPT Ordex agent URL is not configured.",
        suggested_action="Set CHATGPT_AGENT_URL and retry.",
        recoverable=False,
    ),
    ErrorCode.AGENT_WORKSPACE_REQUIRED: ErrorDescriptor(
        code=ErrorCode.AGENT_WORKSPACE_REQUIRED,
        title="Agent workspace required",
        message="Agent mode requires a permitted local workspace path.",
        suggested_action="Provide a workspace path inside an allowed root, then retry.",
        recoverable=False,
    ),
    ErrorCode.AGENT_WORKSPACE_NOT_FOUND: ErrorDescriptor(
        code=ErrorCode.AGENT_WORKSPACE_NOT_FOUND,
        title="Workspace not found",
        message="The requested agent workspace does not exist or is not a directory.",
        suggested_action="Create the workspace directory or choose a valid existing directory, then retry.",
        recoverable=False,
    ),
    ErrorCode.AGENT_WORKSPACE_NOT_ALLOWED: ErrorDescriptor(
        code=ErrorCode.AGENT_WORKSPACE_NOT_ALLOWED,
        title="Workspace not allowed",
        message="The requested agent workspace is outside the configured allowed roots.",
        suggested_action="Choose a workspace inside an allowed root and retry.",
        recoverable=False,
    ),
    ErrorCode.AGENT_PROTOCOL_ERROR: ErrorDescriptor(
        code=ErrorCode.AGENT_PROTOCOL_ERROR,
        title="Agent protocol error",
        message="The Ordex GPT response did not follow the RUN/FINAL protocol.",
        suggested_action="Retry the job or inspect the protocol logs for the invalid response.",
        recoverable=True,
    ),
    ErrorCode.AGENT_PROTOCOL_LIMIT_EXCEEDED: ErrorDescriptor(
        code=ErrorCode.AGENT_PROTOCOL_LIMIT_EXCEEDED,
        title="Agent protocol limit exceeded",
        message="Ordex exceeded the maximum number of protocol errors.",
        suggested_action="Start a fresh agent job after checking the GPT instructions.",
        recoverable=True,
    ),
    ErrorCode.AGENT_DUPLICATE_COMMAND_ID: ErrorDescriptor(
        code=ErrorCode.AGENT_DUPLICATE_COMMAND_ID,
        title="Duplicate agent command ID",
        message="Ordex attempted to reuse a command ID that already ran.",
        suggested_action="Retry the job or inspect the conversation history for repeated RUN actions.",
        recoverable=True,
    ),
    ErrorCode.AGENT_MAX_STEPS_EXCEEDED: ErrorDescriptor(
        code=ErrorCode.AGENT_MAX_STEPS_EXCEEDED,
        title="Agent max steps exceeded",
        message="The agent reached the configured maximum number of executed steps.",
        suggested_action="Increase max steps for a new job or narrow the task scope.",
        recoverable=True,
    ),
    ErrorCode.AGENT_TOOL_NOT_SUPPORTED: ErrorDescriptor(
        code=ErrorCode.AGENT_TOOL_NOT_SUPPORTED,
        title="Unsupported agent tool",
        message="Ordex requested a tool that this bridge does not support.",
        suggested_action="Retry and let Ordex choose one of the supported tools.",
        recoverable=True,
    ),
    ErrorCode.AGENT_TOOL_POLICY_DENIED: ErrorDescriptor(
        code=ErrorCode.AGENT_TOOL_POLICY_DENIED,
        title="Agent tool denied",
        message="The requested tool action was blocked by the server-side safety policy.",
        suggested_action="Retry with a safer command or adjust the task to stay inside the workspace rules.",
        recoverable=True,
    ),
    ErrorCode.AGENT_COMMAND_TIMEOUT: ErrorDescriptor(
        code=ErrorCode.AGENT_COMMAND_TIMEOUT,
        title="Agent command timeout",
        message="A local agent command exceeded the configured timeout.",
        suggested_action="Retry with a shorter command or increase the timeout for a new job.",
        recoverable=True,
    ),
    ErrorCode.AGENT_EXECUTION_BACKEND_UNAVAILABLE: ErrorDescriptor(
        code=ErrorCode.AGENT_EXECUTION_BACKEND_UNAVAILABLE,
        title="Execution backend unavailable",
        message="The requested agent execution backend is not available on this machine.",
        suggested_action="Use the configured host backend or install the requested container runtime.",
        recoverable=False,
    ),
    ErrorCode.AGENT_RESULT_TOO_LARGE: ErrorDescriptor(
        code=ErrorCode.AGENT_RESULT_TOO_LARGE,
        title="Agent result too large",
        message="The tool result exceeded the configured size limits.",
        suggested_action="Retry with smaller reads or narrower commands.",
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
