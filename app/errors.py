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
    # Strict model contract (master_prompt §5, §18)
    MODEL_NOT_AVAILABLE = "model_not_available"
    MODEL_SELECTION_FAILED = "model_selection_failed"
    MODEL_FEATURE_INCOMPATIBLE = "model_feature_incompatible"
    # Google Flow video provider (master_prompt §27)
    FLOW_LOGIN_REQUIRED = "flow_login_required"
    FLOW_MANUAL_VERIFICATION_REQUIRED = "flow_manual_verification_required"
    FLOW_UPLOAD_FAILED = "flow_upload_failed"
    FLOW_FRAME_UPLOAD_FAILED = "flow_frame_upload_failed"
    FLOW_GENERATION_TIMEOUT = "flow_generation_timeout"
    FLOW_CREDITS_EXHAUSTED = "flow_credits_exhausted"
    FLOW_UI_CHANGED = "flow_ui_changed"
    FLOW_TAB_LOST = "flow_tab_lost"
    FLOW_RESULT_NOT_FOUND = "flow_result_not_found"
    FLOW_DOWNLOAD_FAILED = "flow_download_failed"
    FLOW_POLICY_VIOLATION = "flow_policy_violation"
    FLOW_RECONCILIATION_REQUIRED = "flow_reconciliation_required"
    FLOW_REFERENCE_POLICY_VIOLATION = "flow_reference_policy_violation"
    FLOW_REGION_BLOCKED = "flow_region_blocked"
    INVALID_VIDEO_OUTPUT = "invalid_video_output"


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
    ErrorCode.MODEL_NOT_AVAILABLE: ErrorDescriptor(
        code=ErrorCode.MODEL_NOT_AVAILABLE,
        title="Requested model not available",
        message="The requested generation model is not offered by the provider UI right now.",
        suggested_action="Wait for the model to become available or relaunch with a model the UI exposes. Never substitute another model.",
        recoverable=True,
    ),
    ErrorCode.MODEL_SELECTION_FAILED: ErrorDescriptor(
        code=ErrorCode.MODEL_SELECTION_FAILED,
        title="Model selection failed",
        message="Ordak could not select and positively verify the requested model in the provider UI.",
        suggested_action="Inspect the provider tab via VNC, then resume. Generation is refused until the requested model is verified.",
        recoverable=True,
    ),
    ErrorCode.MODEL_FEATURE_INCOMPATIBLE: ErrorDescriptor(
        code=ErrorCode.MODEL_FEATURE_INCOMPATIBLE,
        title="Model feature incompatible",
        message="The selected model cannot perform the requested operation (duration, aspect ratio, resolution or frame inputs).",
        suggested_action="Relaunch with a compatible model or adjust the request. Ordak will not silently switch models.",
        recoverable=False,
    ),
    ErrorCode.FLOW_LOGIN_REQUIRED: ErrorDescriptor(
        code=ErrorCode.FLOW_LOGIN_REQUIRED,
        title="Google Flow login required",
        message="The Google Flow session is not logged in.",
        suggested_action="Log in to Google Flow through noVNC, then resume the job.",
        recoverable=True,
    ),
    ErrorCode.FLOW_MANUAL_VERIFICATION_REQUIRED: ErrorDescriptor(
        code=ErrorCode.FLOW_MANUAL_VERIFICATION_REQUIRED,
        title="Google Flow manual verification required",
        message="Google Flow is asking for a security or account verification step.",
        suggested_action="Complete the verification through noVNC, then resume. Ordak never bypasses verification.",
        recoverable=True,
    ),
    ErrorCode.FLOW_UPLOAD_FAILED: ErrorDescriptor(
        code=ErrorCode.FLOW_UPLOAD_FAILED,
        title="Flow reference upload failed",
        message="A Flow reference upload did not complete or could not be verified.",
        suggested_action="Resume the job; Ordak re-verifies attachments before generating.",
        recoverable=True,
    ),
    ErrorCode.FLOW_FRAME_UPLOAD_FAILED: ErrorDescriptor(
        code=ErrorCode.FLOW_FRAME_UPLOAD_FAILED,
        title="Flow frame upload failed",
        message="The first-frame or last-frame image could not be attached through the Flow frame controls.",
        suggested_action="Inspect the Flow frame controls via VNC, then resume.",
        recoverable=True,
    ),
    ErrorCode.FLOW_GENERATION_TIMEOUT: ErrorDescriptor(
        code=ErrorCode.FLOW_GENERATION_TIMEOUT,
        title="Flow generation timeout",
        message="Google Flow did not finish the generation before the timeout.",
        suggested_action="Resume the job; Ordak reconciles the existing Flow generation instead of pressing Generate again.",
        recoverable=True,
    ),
    ErrorCode.FLOW_CREDITS_EXHAUSTED: ErrorDescriptor(
        code=ErrorCode.FLOW_CREDITS_EXHAUSTED,
        title="Flow credits exhausted",
        message="Google Flow reports that generation credits are exhausted.",
        suggested_action="Top up credits or wait for the quota reset, then resume. Ordak will not hammer Generate.",
        recoverable=True,
    ),
    ErrorCode.FLOW_UI_CHANGED: ErrorDescriptor(
        code=ErrorCode.FLOW_UI_CHANGED,
        title="Flow UI changed",
        message="The Google Flow layout no longer matches the controls Ordak expects.",
        suggested_action="Capture diagnostics, update the Flow selectors, then retry.",
        recoverable=True,
    ),
    ErrorCode.FLOW_REGION_BLOCKED: ErrorDescriptor(
        code=ErrorCode.FLOW_REGION_BLOCKED,
        title="Flow is not available in this country",
        message=(
            "Google Flow redirected the workspace to its unsupported-country page, so no "
            "video can be generated from this host."
        ),
        suggested_action=(
            "This is a restriction on the server's location, not a UI change and not a bug. "
            "Run the video stages from a permitted location, or wait and resume: the check "
            "runs before any credit is spent, so resuming costs nothing."
        ),
        recoverable=True,
    ),
    ErrorCode.FLOW_TAB_LOST: ErrorDescriptor(
        code=ErrorCode.FLOW_TAB_LOST,
        title="Flow tab lost",
        message="The Google Flow workspace tab is no longer available.",
        suggested_action="Resume the job; Ordak reopens the same Flow workspace and reconciles before generating.",
        recoverable=True,
    ),
    ErrorCode.FLOW_RESULT_NOT_FOUND: ErrorDescriptor(
        code=ErrorCode.FLOW_RESULT_NOT_FOUND,
        title="Flow result not found",
        message="Ordak could not locate a generated clip matching this job's submission fingerprint.",
        suggested_action="Inspect the Flow workspace via VNC, then resume so Ordak can reconcile the existing generation.",
        recoverable=True,
    ),
    ErrorCode.FLOW_DOWNLOAD_FAILED: ErrorDescriptor(
        code=ErrorCode.FLOW_DOWNLOAD_FAILED,
        title="Flow download failed",
        message="The generated Flow clip could not be downloaded into the job output directory.",
        suggested_action="Resume the job; Ordak re-downloads the existing generation without spending new credits.",
        recoverable=True,
    ),
    ErrorCode.FLOW_POLICY_VIOLATION: ErrorDescriptor(
        code=ErrorCode.FLOW_POLICY_VIOLATION,
        title="Flow rejected the prompt",
        message="Google Flow rejected the prompt for policy reasons.",
        suggested_action="Rewrite the clip prompt, then retry. No other provider is used as a fallback.",
        recoverable=False,
    ),
    ErrorCode.FLOW_RECONCILIATION_REQUIRED: ErrorDescriptor(
        code=ErrorCode.FLOW_RECONCILIATION_REQUIRED,
        title="Flow reconciliation required",
        message="A previous Flow submission may already have consumed credits; Ordak refuses a blind duplicate Generate.",
        suggested_action="Resume the job so Ordak can inspect the Flow workspace and recover the matching generation.",
        recoverable=True,
    ),
    ErrorCode.FLOW_REFERENCE_POLICY_VIOLATION: ErrorDescriptor(
        code=ErrorCode.FLOW_REFERENCE_POLICY_VIOLATION,
        title="Flow reference policy violation",
        message="A forbidden style-sheet reference was about to be uploaded to Google Flow.",
        suggested_action="Fix the job builder: Flow may only receive the canonical character/book design sheet plus first/last frame inputs.",
        recoverable=False,
    ),
    ErrorCode.INVALID_VIDEO_OUTPUT: ErrorDescriptor(
        code=ErrorCode.INVALID_VIDEO_OUTPUT,
        title="Invalid video output",
        message="The downloaded video artifact failed ffprobe validation.",
        suggested_action="Resume the job so Ordak re-downloads and re-validates the generation.",
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
