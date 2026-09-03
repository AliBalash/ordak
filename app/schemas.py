from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


Provider = Literal["gemini", "chatgpt", "flow"]


JobStatus = Literal[
    "queued",
    "running",
    "checking_browser",
    "opening_provider_tab",
    "opening_gemini_tab",
    "opening_browser",
    "navigating_to_gemini",
    "checking_login",
    "finding_input",
    "submitting_prompt",
    "waiting_for_response",
    "extracting_answer",
    "preparing_agent",
    "waiting_for_agent",
    "parsing_agent_action",
    "executing_agent_action",
    "sending_agent_result",
    "finalizing_agent",
    "cancelling",
    "cancelled",
    "completed",
    "failed",
    "manual_verification_required",
]

JobMode = Literal["chat", "image_analyze", "image_generate", "agent", "video_generate"]
RetryStrategy = Literal["same_tab", "new_tab_same_conversation", "new_chat"]
ResumeStrategy = Literal["same_tab", "new_tab_same_conversation"]
ExecutionBackend = Literal["host", "docker", "podman"]


class LogEntry(BaseModel):
    timestamp: datetime
    level: str = "info"
    message: str


class AgentOptions(BaseModel):
    workspace: str = Field(min_length=1, max_length=4096)
    max_steps: int | None = Field(default=None, ge=1, le=200)
    command_timeout_seconds: int | None = Field(default=None, ge=1, le=1800)
    execution_backend: ExecutionBackend | None = None
    network_enabled: bool | None = None


#: Reference roles are validated by app/flow_policy.py, not by the type system, so a
#: forbidden role produces a clean policy rejection instead of a schema crash.
KNOWN_REFERENCE_ROLES = (
    "character_sheet",
    "book_design_sheet",
    "first_frame",
    "last_frame",
    "style_reference",
    "world_keyframe",
    "previous_beat",
    "unspecified",
)


class GenerationOptions(BaseModel):
    """Explicit generation contract for a job (master_prompt §5, §18-21).

    The caller states exactly what it wants; the worker must select it in the live UI,
    verify it, and refuse to generate when verification fails. Nothing is inferred and
    nothing is silently substituted.
    """

    model: str | None = Field(default=None, max_length=120)
    quality: str | None = Field(default=None, max_length=40)
    aspect_ratio: str | None = Field(default=None, max_length=20)
    duration_seconds: int | None = Field(default=None, ge=1, le=120)
    resolution: str | None = Field(default=None, max_length=20)

    def is_empty(self) -> bool:
        return not any(
            (
                self.model,
                self.quality,
                self.aspect_ratio,
                self.duration_seconds,
                self.resolution,
            )
        )


class ReferenceSpec(BaseModel):
    """A single uploaded reference and the role it plays in the job."""

    role: str = Field(default="unspecified", max_length=60)
    path: str
    filename: str | None = None


class GenerationReceipt(BaseModel):
    """What the worker actually observed in the provider UI."""

    provider: Provider
    requested_model: str | None = None
    actual_model_label: str | None = None
    model_verified: bool = False
    pro_regeneration_used: bool = False
    requested_quality: str | None = None
    requested_aspect_ratio: str | None = None
    actual_aspect_ratio: str | None = None
    requested_duration_seconds: int | None = None
    actual_duration_seconds: int | None = None
    requested_resolution: str | None = None
    actual_resolution: str | None = None
    workspace_url: str | None = None
    submission_fingerprint: str | None = None
    reference_roles: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class JobCreateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)
    provider: Provider = "gemini"
    mode: JobMode = "chat"
    conversation_id: str | None = None
    start_new_chat: bool = False
    agent: AgentOptions | None = None
    generation: GenerationOptions | None = None


class ProviderRunRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)
    mode: JobMode = "chat"
    conversation_id: str | None = None
    start_new_chat: bool = False
    wait_for_completion: bool = True
    wait_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    agent: AgentOptions | None = None
    generation: GenerationOptions | None = None



class JobCreateResponse(BaseModel):
    job_id: str
    conversation_id: str
    provider: Provider = "gemini"
    status: JobStatus


class JobResponse(BaseModel):
    job_id: str
    question: str
    conversation_id: str
    conversation_title: str
    provider: Provider = "gemini"
    mode: JobMode = "chat"
    start_new_chat: bool = False
    retry_of_job_id: str | None = None
    uploads: list[str] = Field(default_factory=list)
    references: list[ReferenceSpec] = Field(default_factory=list)
    generation: GenerationOptions | None = None
    generation_receipt: GenerationReceipt | None = None
    output_images: list[str] = Field(default_factory=list)
    output_videos: list[str] = Field(default_factory=list)
    answer: str | None
    status: str
    error_code: str | None = None
    error_title: str | None = None
    error_message: str | None = None
    suggested_action: str | None = None
    recoverable: bool = False
    logs: list[LogEntry]
    screenshots: list[str]
    trace_path: str | None
    agent_workspace: str | None = None
    agent_step_count: int = 0
    agent_max_steps: int | None = None
    agent_command_timeout_seconds: int | None = None
    agent_execution_backend: str | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None = None


class ArtifactLinkResponse(BaseModel):
    path: str
    url: str
    filename: str


class ProviderRunResponse(BaseModel):
    provider: Provider
    job_id: str
    job_api_url: str
    conversation_id: str
    conversation_api_url: str
    conversation_title: str
    provider_conversation_url: str | None = None
    mode: JobMode = "chat"
    status: JobStatus
    completed: bool = False
    answer: str | None = None
    error_code: str | None = None
    error_title: str | None = None
    error_message: str | None = None
    suggested_action: str | None = None
    recoverable: bool = False
    uploads: list[ArtifactLinkResponse] = Field(default_factory=list)
    references: list[ReferenceSpec] = Field(default_factory=list)
    generation: GenerationOptions | None = None
    generation_receipt: GenerationReceipt | None = None
    output_images: list[ArtifactLinkResponse] = Field(default_factory=list)
    output_videos: list[ArtifactLinkResponse] = Field(default_factory=list)
    screenshots: list[ArtifactLinkResponse] = Field(default_factory=list)
    trace_url: str | None = None
    logs: list[LogEntry] = Field(default_factory=list)


class JobListResponse(BaseModel):
    jobs: list[JobResponse]


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    provider: Provider = "gemini"
    mode: JobMode = "chat"
    preview: str
    job_count: int
    last_job_id: str
    last_status: str
    external_url: str | None = None
    tab_alive: bool = False
    pinned: bool = False
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]


class ConversationResponse(BaseModel):
    conversation_id: str
    title: str
    provider: Provider = "gemini"
    mode: JobMode = "chat"
    external_url: str | None = None
    tab_alive: bool = False
    pinned: bool = False
    jobs: list[JobResponse]


class HealthResponse(BaseModel):
    status: str
    database: str


class ProfileOpenResponse(BaseModel):
    message: str


class RetryJobRequest(BaseModel):
    strategy: RetryStrategy = "same_tab"


class ResumeJobRequest(BaseModel):
    strategy: ResumeStrategy = "same_tab"


class DiagnosticsProviderState(BaseModel):
    logged_in: bool
    login_state: str
    busy: bool
    open_tabs: list[dict[str, object]]
    active_tab: dict[str, object] | None = None
    notes: list[str] = Field(default_factory=list)


class DiagnosticsResponse(BaseModel):
    product_name: str
    product_label: str
    chrome_running: bool
    apple_events_allowed: bool
    provider_sessions: dict[str, DiagnosticsProviderState]
    project_url_configured: dict[str, bool]
    tab_binding_health: dict[str, object]
    last_success_by_provider: dict[str, str | None]
    last_error_by_provider: dict[str, str | None]
    active_job: dict[str, object] | None = None
    queue_depth: int
    agent: dict[str, object] = Field(default_factory=dict)


class StorageDiagnosticsResponse(BaseModel):
    uploads_count: int
    outputs_count: int
    traces_count: int
    failure_html_count: int
    total_bytes: int


class CleanupResponse(BaseModel):
    deleted_files: int
    freed_bytes: int


class AgentStepResponse(BaseModel):
    id: str
    job_id: str
    sequence: int
    command_id: str
    tool: str
    status: str
    request_json: str
    result_json: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None


class AgentStepListResponse(BaseModel):
    steps: list[AgentStepResponse]


class WebSocketEvent(BaseModel):
    type: Literal["snapshot", "log", "completed", "failed"]
    job: JobResponse
    log: LogEntry | None = None
