from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import WebSocket
from sqlalchemy import func, select

from app.agent.types import AgentResolvedConfig
from app.agent.workspace import resolve_workspace
from app.agent.policy import container_runtime_available, validate_execution_backend
from app.artifacts import storage_absolute_path, storage_relative_path
from app.automation.browser import linux_remote_debugging_available, open_profile_browser_session
from app.automation.existing_chrome import (
    ChromeTabInfo,
    ChromeTabRef,
    execute_javascript,
    get_tab_info,
    is_google_chrome_running,
    list_google_chrome_tabs,
)
from app.automation.gemini_worker import (
    AutomationJobRequest,
    GeminiAutomationError,
    WorkerRuntime,
    run_gemini_job,
)
from app.automation.flow_worker import run_flow_job
from app.config import Settings, settings
from app.database import SessionLocal
from app.errors import ErrorCode, OrdaKError, get_error_descriptor
from app.models import AgentStep, Conversation, Job
from app.providers import get_provider_adapter
from app.schemas import (
    AgentOptions,
    AgentStepResponse,
    CleanupResponse,
    ConversationResponse,
    ConversationSummary,
    DiagnosticsProviderState,
    DiagnosticsResponse,
    GenerationOptions,
    GenerationReceipt,
    JobCreateResponse,
    JobMode,
    JobResponse,
    LogEntry,
    Provider,
    ReferenceSpec,
    StorageDiagnosticsResponse,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _loads_list(raw_value: str | None) -> list[Any]:
    if not raw_value:
        return []
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _dumps_list(raw_value: list[Any]) -> str:
    return json.dumps(raw_value, ensure_ascii=False)


def _loads_obj(raw_value: str | None) -> dict[str, Any] | None:
    if not raw_value:
        return None
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _dumps_obj(raw_value: dict[str, Any] | None) -> str | None:
    if raw_value is None:
        return None
    return json.dumps(raw_value, ensure_ascii=False)


@dataclass(slots=True)
class JobSnapshot:
    job_id: str
    question: str
    conversation_id: str
    conversation_title: str
    provider: Provider
    start_new_chat: bool
    mode: JobMode
    retry_of_job_id: str | None
    run_strategy: str | None
    uploads: list[str]
    references: list[dict[str, Any]]
    generation: dict[str, Any] | None
    generation_receipt: dict[str, Any] | None
    output_images: list[str]
    output_videos: list[str]
    answer: str | None
    status: str
    error_code: str | None
    error_message: str | None
    recoverable: bool
    suggested_action: str | None
    logs: list[dict[str, Any]]
    screenshots: list[str]
    trace_path: str | None
    agent_workspace: str | None
    agent_step_count: int
    agent_max_steps: int | None
    agent_command_timeout_seconds: int | None
    agent_execution_backend: str | None
    agent_network_enabled: bool | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None

    def to_response(self) -> JobResponse:
        descriptor = get_error_descriptor(self.error_code)
        return JobResponse(
            job_id=self.job_id,
            question=self.question,
            conversation_id=self.conversation_id,
            conversation_title=self.conversation_title,
            provider=self.provider,
            mode=self.mode,
            start_new_chat=self.start_new_chat,
            retry_of_job_id=self.retry_of_job_id,
            uploads=self.uploads,
            references=[ReferenceSpec.model_validate(item) for item in self.references],
            generation=(
                GenerationOptions.model_validate(self.generation) if self.generation else None
            ),
            generation_receipt=(
                GenerationReceipt.model_validate(self.generation_receipt)
                if self.generation_receipt
                else None
            ),
            output_images=self.output_images,
            output_videos=self.output_videos,
            answer=self.answer,
            status=self.status,
            error_code=self.error_code,
            error_title=descriptor.title if descriptor else None,
            error_message=self.error_message,
            suggested_action=self.suggested_action or (descriptor.suggested_action if descriptor else None),
            recoverable=self.recoverable if self.recoverable is not None else bool(descriptor and descriptor.recoverable),
            logs=[LogEntry(**entry) for entry in self.logs],
            screenshots=self.screenshots,
            trace_path=self.trace_path,
            agent_workspace=self.agent_workspace,
            agent_step_count=self.agent_step_count,
            agent_max_steps=self.agent_max_steps,
            agent_command_timeout_seconds=self.agent_command_timeout_seconds,
            agent_execution_backend=self.agent_execution_backend,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            cancel_requested_at=self.cancel_requested_at,
        )


@dataclass(slots=True)
class JobControl:
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def is_cancel_requested(self) -> bool:
        return self.cancel_event.is_set()


class JobManager:
    def __init__(
        self,
        *,
        worker: Callable[..., str] = run_gemini_job,
        app_settings: Settings | None = None,
    ) -> None:
        self.worker = worker
        self.settings = app_settings or settings
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.browser_lock = threading.Lock()
        self.profile_lock = threading.Lock()
        self.subscribers: dict[str, set[WebSocket]] = defaultdict(set)
        self.dispatcher_task: asyncio.Task[None] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.active_job_id: str | None = None
        self.active_controls: dict[str, JobControl] = {}
        self.cancelled_queued_jobs: set[str] = set()

    async def start(self) -> None:
        if self.dispatcher_task is not None:
            return
        self._recover_stale_incomplete_jobs()
        self.loop = asyncio.get_running_loop()
        self.dispatcher_task = asyncio.create_task(self._consume_queue())

    async def shutdown(self) -> None:
        if self.dispatcher_task is None:
            return
        self.dispatcher_task.cancel()
        try:
            await self.dispatcher_task
        except asyncio.CancelledError:
            pass
        self.dispatcher_task = None

    async def create_job(
        self,
        question: str,
        *,
        job_id: str | None = None,
        mode: JobMode = "chat",
        provider: Provider = "gemini",
        conversation_id: str | None = None,
        start_new_chat: bool = False,
        uploads: list[str] | None = None,
        retry_of_job_id: str | None = None,
        run_strategy: str | None = None,
        agent_options: AgentOptions | None = None,
        references: list[ReferenceSpec] | None = None,
        generation: GenerationOptions | None = None,
    ) -> JobCreateResponse:
        uploads = uploads or []
        references = references or []
        if generation is not None and generation.is_empty():
            generation = None
        if references and len(references) != len(uploads):
            raise ValueError(
                f"references ({len(references)}) must describe every upload ({len(uploads)})."
            )
        if provider == "flow":
            from app.flow_policy import validate_references as validate_flow_references

            if uploads and not references:
                raise ValueError(
                    "Flow jobs must declare an explicit role for every reference upload."
                )
            validate_flow_references(
                [(ref.role, ref.path) for ref in references]
            )
        resolved_agent_workspace: str | None = None
        resolved_agent_max_steps: int | None = None
        resolved_agent_command_timeout_seconds: int | None = None
        resolved_agent_execution_backend: str | None = None
        resolved_agent_network_enabled: bool | None = None
        if mode == "agent":
            if not self.settings.agent_enabled:
                raise ValueError("Agent mode is disabled on this ordak instance.")
            if provider != "chatgpt":
                raise ValueError("Agent mode currently supports ChatGPT only.")
            if uploads:
                raise ValueError("Agent mode does not accept image uploads.")
            if agent_options is None:
                raise ValueError("Agent mode requires agent workspace options.")
            if not self.settings.chatgpt_agent_url:
                raise ValueError("CHATGPT_AGENT_URL is not configured.")
            resolved_workspace = resolve_workspace(
                agent_options.workspace,
                self.settings.agent_allowed_workspace_roots,
            )
            resolved_agent_workspace = str(resolved_workspace.workspace)
            requested_max_steps = agent_options.max_steps or self.settings.agent_default_max_steps
            if requested_max_steps > self.settings.agent_max_allowed_steps:
                raise ValueError(
                    f"Agent max_steps must be <= {self.settings.agent_max_allowed_steps}."
                )
            requested_timeout = (
                agent_options.command_timeout_seconds
                or self.settings.agent_default_command_timeout_seconds
            )
            if requested_timeout > self.settings.agent_max_command_timeout_seconds:
                raise ValueError(
                    f"Agent command_timeout_seconds must be <= {self.settings.agent_max_command_timeout_seconds}."
                )
            resolved_agent_max_steps = requested_max_steps
            resolved_agent_command_timeout_seconds = requested_timeout
            resolved_agent_execution_backend = validate_execution_backend(
                agent_options.execution_backend,
                self.settings,
            )
            resolved_agent_network_enabled = (
                agent_options.network_enabled
                if agent_options.network_enabled is not None
                else self.settings.agent_network_enabled
            )
        elif agent_options is not None:
            raise ValueError("Agent options are only valid when mode is agent.")
        resolved_job_id = job_id or str(uuid.uuid4())
        resolved_conversation_id = conversation_id or str(uuid.uuid4())
        conversation = self._get_or_create_conversation(
            resolved_conversation_id,
            provider=provider,
            title=self._make_conversation_title(question),
        )
        if conversation.provider != provider:
            raise ValueError(
                f"Conversation provider mismatch. This conversation belongs to {conversation.provider}."
            )
        if start_new_chat:
            conversation.title = self._make_conversation_title(question)
            conversation.updated_at = utcnow()
            conversation.tab_alive = False
            conversation.tab_id = None
            conversation.tab_window_id = None
            conversation.tab_window_key = None
            conversation.tab_target_id = None
            conversation.external_url = None
            conversation.external_conversation_id = None

        metadata = {
            "conversation_id": resolved_conversation_id,
            "conversation_title": conversation.title,
            "provider": provider,
            "start_new_chat": bool(start_new_chat),
            "mode": mode,
            "uploads": uploads,
            "output_images": [],
            "retry_of_job_id": retry_of_job_id,
            "run_strategy": run_strategy,
            "agent_workspace": resolved_agent_workspace,
            "agent_max_steps": resolved_agent_max_steps,
            "agent_command_timeout_seconds": resolved_agent_command_timeout_seconds,
            "agent_execution_backend": resolved_agent_execution_backend,
            "agent_network_enabled": resolved_agent_network_enabled,
        }
        job = Job(
            id=resolved_job_id,
            question=question.strip(),
            status="queued",
            provider=provider,
            conversation_id=resolved_conversation_id,
            conversation_title=conversation.title,
            mode=mode,
            start_new_chat=bool(start_new_chat),
            retry_of_job_id=retry_of_job_id,
            run_strategy=run_strategy,
            agent_workspace=resolved_agent_workspace,
            agent_max_steps=resolved_agent_max_steps,
            agent_command_timeout_seconds=resolved_agent_command_timeout_seconds,
            agent_execution_backend=resolved_agent_execution_backend,
            agent_network_enabled=resolved_agent_network_enabled,
            logs=_dumps_list(
                [
                    {
                        "timestamp": utcnow().isoformat(),
                        "level": "info",
                        "message": f"Job queued in {mode} mode.",
                    }
                ]
            ),
            screenshot_paths=_dumps_list([]),
            uploads_json=_dumps_list(uploads),
            references_json=_dumps_list([ref.model_dump() for ref in references]),
            generation_json=_dumps_obj(generation.model_dump() if generation else None),
            output_images_json=_dumps_list([]),
            output_videos_json=_dumps_list([]),
            metadata_json=_dumps_list([metadata]),
        )
        with SessionLocal() as session:
            session.merge(conversation)
            session.add(job)
            session.commit()
        await self.queue.put(job.id)
        await self._broadcast_snapshot(self.get_job_snapshot(job.id), event_type="snapshot")
        return JobCreateResponse(
            job_id=job.id,
            conversation_id=resolved_conversation_id,
            provider=provider,
            status="queued",
        )

    async def cancel_job(self, job_id: str) -> JobSnapshot:
        snapshot = self.get_job_snapshot(job_id)
        if snapshot.status in {"completed", "failed", "manual_verification_required", "cancelled"}:
            return snapshot
        if snapshot.status == "queued":
            self.cancelled_queued_jobs.add(job_id)
            updated = self._update_job(
                job_id,
                status="cancelled",
                error_message="Job cancelled before execution.",
                finished_at=utcnow(),
            )
            self._append_log(job_id, "Queued job cancelled before execution.", level="warning")
            return updated
        control = self.active_controls.get(job_id)
        if control is not None:
            control.request_cancel()
        return self._update_job(job_id, status="cancelling", cancel_requested_at=utcnow())

    async def retry_job(self, job_id: str, strategy: str) -> JobCreateResponse:
        source = self.get_job_snapshot(job_id)
        if source.mode == "agent":
            continue_same_conversation = strategy in {
                "same_tab",
                "new_tab_same_conversation",
            }
            return await self.create_job(
                source.question,
                provider=source.provider,
                mode=source.mode,
                conversation_id=(
                    source.conversation_id if continue_same_conversation else None
                ),
                start_new_chat=not continue_same_conversation,
                uploads=[],
                retry_of_job_id=source.job_id,
                run_strategy=strategy,
                agent_options=AgentOptions(
                    workspace=source.agent_workspace or "",
                    max_steps=source.agent_max_steps,
                    command_timeout_seconds=source.agent_command_timeout_seconds,
                    execution_backend=source.agent_execution_backend,
                    network_enabled=source.agent_network_enabled,
                ),
            )
        start_new_chat = strategy == "new_chat"
        conversation_id = None if start_new_chat else source.conversation_id
        return await self.create_job(
            source.question,
            provider=source.provider,
            mode=source.mode,
            conversation_id=conversation_id,
            start_new_chat=start_new_chat,
            uploads=list(source.uploads),
            retry_of_job_id=source.job_id,
            run_strategy=strategy,
        )

    async def resume_job(self, job_id: str, strategy: str) -> JobCreateResponse:
        source = self.get_job_snapshot(job_id)
        if source.mode == "agent":
            if source.status not in {
                "failed",
                "manual_verification_required",
                "cancelled",
            }:
                raise ValueError(
                    "Only failed, manual verification, or cancelled jobs can be resumed."
                )
            return await self.create_job(
                source.question,
                provider=source.provider,
                mode=source.mode,
                conversation_id=source.conversation_id,
                start_new_chat=False,
                uploads=[],
                retry_of_job_id=source.job_id,
                run_strategy=strategy,
                agent_options=AgentOptions(
                    workspace=source.agent_workspace or "",
                    max_steps=source.agent_max_steps,
                    command_timeout_seconds=source.agent_command_timeout_seconds,
                    execution_backend=source.agent_execution_backend,
                    network_enabled=source.agent_network_enabled,
                ),
            )
        if source.status not in {"failed", "manual_verification_required", "cancelled"}:
            raise ValueError("Only failed, manual verification, or cancelled jobs can be resumed.")
        if not source.recoverable and source.error_code:
            raise ValueError("This job is not resumable.")
        return await self.create_job(
            source.question,
            provider=source.provider,
            mode=source.mode,
            conversation_id=source.conversation_id,
            start_new_chat=False,
            uploads=list(source.uploads),
            retry_of_job_id=source.job_id,
            run_strategy=strategy,
        )

    async def add_subscriber(self, job_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.subscribers[job_id].add(websocket)
        snapshot = self.get_job_snapshot(job_id)
        await websocket.send_json(
            {"type": "snapshot", "job": snapshot.to_response().model_dump(mode="json"), "log": None}
        )

    async def remove_subscriber(self, job_id: str, websocket: WebSocket) -> None:
        self.subscribers[job_id].discard(websocket)
        if not self.subscribers[job_id]:
            self.subscribers.pop(job_id, None)

    def get_job_snapshot(self, job_id: str) -> JobSnapshot:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            conversation = (
                session.get(Conversation, job.conversation_id) if job.conversation_id else None
            )
            return self._snapshot_from_model(job, conversation, session=session)

    def list_jobs(self, limit: int = 20) -> list[JobSnapshot]:
        with SessionLocal() as session:
            self._refresh_conversation_bindings(session)
            rows = session.execute(
                select(Job).order_by(Job.created_at.desc()).limit(limit)
            ).scalars().all()
            conversations = {
                conversation.id: conversation
                for conversation in session.execute(select(Conversation)).scalars().all()
            }
            return [
                self._snapshot_from_model(job, conversations.get(job.conversation_id), session=session)
                for job in rows
            ]

    def list_agent_steps(self, job_id: str) -> list[AgentStepResponse]:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            steps = session.execute(
                select(AgentStep)
                .where(AgentStep.job_id == job_id)
                .order_by(AgentStep.sequence.asc())
            ).scalars().all()
            return [
                AgentStepResponse(
                    id=step.id,
                    job_id=step.job_id,
                    sequence=step.sequence,
                    command_id=step.command_id,
                    tool=step.tool,
                    status=step.status,
                    request_json=step.request_json,
                    result_json=step.result_json,
                    started_at=step.started_at,
                    finished_at=step.finished_at,
                    duration_ms=step.duration_ms,
                    error_message=step.error_message,
                )
                for step in steps
            ]

    def _conversation_agent_command_ids(
        self,
        conversation_id: str,
        *,
        excluding_job_id: str | None = None,
    ) -> tuple[str, ...]:
        with SessionLocal() as session:
            statement = (
                select(AgentStep.command_id)
                .join(Job, AgentStep.job_id == Job.id)
                .where(Job.conversation_id == conversation_id)
            )
            if excluding_job_id:
                statement = statement.where(AgentStep.job_id != excluding_job_id)
            command_ids = session.execute(statement).scalars().all()
        return tuple(dict.fromkeys(command_ids))

    def list_conversations(self, limit: int = 30) -> list[ConversationSummary]:
        with SessionLocal() as session:
            self._refresh_conversation_bindings(session)
            conversations = session.execute(
                select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)
            ).scalars().all()
            jobs = session.execute(select(Job).order_by(Job.created_at.asc())).scalars().all()
        grouped: dict[str, list[Job]] = defaultdict(list)
        for job in jobs:
            if job.conversation_id:
                grouped[job.conversation_id].append(job)

        summaries: list[ConversationSummary] = []
        for conversation in conversations:
            related_jobs = grouped.get(conversation.id, [])
            if related_jobs:
                last_job = related_jobs[-1]
                preview = last_job.answer or last_job.error_message or last_job.question
                last_status = last_job.status
                last_job_id = last_job.id
                mode = last_job.mode
            else:
                preview = ""
                last_status = "queued"
                last_job_id = ""
                mode = "chat"
            summaries.append(
                ConversationSummary(
                    conversation_id=conversation.id,
                    title=conversation.title,
                    provider=conversation.provider,
                    mode=mode,
                preview=preview,
                    job_count=len(related_jobs),
                    last_job_id=last_job_id,
                    last_status=last_status,
                    external_url=conversation.external_url,
                    tab_alive=conversation.tab_alive,
                    pinned=conversation.pinned,
                    created_at=conversation.created_at,
                    updated_at=conversation.updated_at,
                )
            )
        return summaries

    def get_conversation(self, conversation_id: str) -> ConversationResponse:
        with SessionLocal() as session:
            self._refresh_conversation_bindings(session)
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            jobs = session.execute(
                select(Job)
                .where(Job.conversation_id == conversation_id)
                .order_by(Job.created_at.asc())
            ).scalars().all()
            snapshots = [self._snapshot_from_model(job, conversation, session=session) for job in jobs]
        return ConversationResponse(
            conversation_id=conversation.id,
            title=conversation.title,
            provider=conversation.provider,
            mode=snapshots[-1].mode if snapshots else "chat",
            external_url=conversation.external_url,
            tab_alive=conversation.tab_alive,
            pinned=conversation.pinned,
            jobs=[snapshot.to_response() for snapshot in snapshots],
        )

    def set_conversation_pinned(self, conversation_id: str, pinned: bool) -> ConversationResponse:
        with SessionLocal() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            conversation.pinned = pinned
            conversation.updated_at = utcnow()
            session.add(conversation)
            session.commit()
        return self.get_conversation(conversation_id)

    def launch_profile_browser(self) -> bool:
        if self.browser_lock.locked():
            return False
        with self.profile_lock:
            with self.browser_lock:
                open_profile_browser_session(app_settings=self.settings)
            return True

    def diagnostics(self) -> DiagnosticsResponse:
        chrome_running = is_google_chrome_running()
        apple_events_allowed = self._probe_apple_events(chrome_running)
        provider_sessions: dict[str, DiagnosticsProviderState] = {}
        for provider in ("gemini", "chatgpt", "flow"):
            diagnostics = get_provider_adapter(provider).collect_diagnostics()
            provider_sessions[provider] = DiagnosticsProviderState(
                logged_in=diagnostics.logged_in,
                login_state=diagnostics.login_state,
                busy=diagnostics.busy,
                open_tabs=diagnostics.open_tabs,
                active_tab=diagnostics.active_tab,
                notes=diagnostics.notes,
            )
        with SessionLocal() as session:
            self._refresh_conversation_bindings(session)
            conversations = session.execute(select(Conversation)).scalars().all()
            jobs = session.execute(select(Job)).scalars().all()
        tab_alive = sum(1 for conversation in conversations if conversation.tab_alive)
        tab_binding_health = {
            "tracked_conversations": len(conversations),
            "alive_tabs": tab_alive,
            "lost_tabs": len(conversations) - tab_alive,
        }
        last_success_by_provider: dict[str, str | None] = {}
        last_error_by_provider: dict[str, str | None] = {}
        for provider in ("gemini", "chatgpt", "flow"):
            provider_jobs = [job for job in jobs if job.provider == provider]
            success = [job for job in provider_jobs if job.status == "completed"]
            error = [job for job in provider_jobs if job.error_code]
            last_success_by_provider[provider] = success[-1].id if success else None
            last_error_by_provider[provider] = error[-1].error_code if error else None
        active_job = self.get_job_snapshot(self.active_job_id).to_response().model_dump(mode="json") if self.active_job_id else None
        return DiagnosticsResponse(
            product_name=self.settings.product_name,
            product_label=self.settings.product_label,
            chrome_running=chrome_running,
            apple_events_allowed=apple_events_allowed,
            provider_sessions=provider_sessions,
            project_url_configured={
                "gemini": True,
                "chatgpt": bool(self.settings.chatgpt_project_url),
                "flow": True,
            },
            tab_binding_health=tab_binding_health,
            last_success_by_provider=last_success_by_provider,
            last_error_by_provider=last_error_by_provider,
            active_job=active_job,
            queue_depth=self.queue.qsize(),
            agent={
                "enabled": self.settings.agent_enabled,
                "chatgpt_agent_url_configured": bool(self.settings.chatgpt_agent_url),
                "allowed_workspace_root_count": len(self.settings.agent_allowed_workspace_roots),
                "execution_backend": self.settings.agent_execution_backend,
                "container_runtime_available": container_runtime_available(
                    self.settings.agent_execution_backend
                ),
                "remote_debugging_reachable": linux_remote_debugging_available(self.settings),
                "step_log_dir_writable": os.access(self.settings.agent_step_log_dir, os.W_OK),
            },
        )

    def storage_diagnostics(self) -> StorageDiagnosticsResponse:
        uploads = list(self.settings.browser_upload_dir.glob("*"))
        outputs = list(self.settings.browser_output_dir.glob("*"))
        traces = list(self.settings.browser_trace_dir.glob("*"))
        failure_html = list(self.settings.browser_log_dir.glob("*_failure.html"))
        files = uploads + outputs + traces + failure_html
        total_bytes = sum(file.stat().st_size for file in files if file.is_file())
        return StorageDiagnosticsResponse(
            uploads_count=len(uploads),
            outputs_count=len(outputs),
            traces_count=len(traces),
            failure_html_count=len(failure_html),
            total_bytes=total_bytes,
        )

    def cleanup_storage(self) -> CleanupResponse:
        referenced_uploads: set[str] = set()
        referenced_outputs: set[str] = set()
        pinned_conversations: set[str] = set()
        recent_cutoff = utcnow() - timedelta(days=self.settings.storage_retention_days)
        with SessionLocal() as session:
            conversations = session.execute(select(Conversation)).scalars().all()
            jobs = session.execute(select(Job)).scalars().all()
        for conversation in conversations:
            if conversation.pinned:
                pinned_conversations.add(conversation.id)
        for job in jobs:
            if job.conversation_id in pinned_conversations or job.created_at >= recent_cutoff:
                referenced_uploads.update(_loads_list(job.uploads_json))
                referenced_outputs.update(_loads_list(job.output_images_json))
                if hasattr(job, "output_videos_json"):
                    referenced_outputs.update(_loads_list(job.output_videos_json))
        deleted_files = 0
        freed_bytes = 0

        def remove_candidates(files: list[Path], referenced: set[str]) -> None:
            nonlocal deleted_files, freed_bytes
            for file_path in files:
                relative = storage_relative_path(file_path, self.settings)
                if relative in referenced:
                    continue
                size = file_path.stat().st_size if file_path.exists() else 0
                file_path.unlink(missing_ok=True)
                deleted_files += 1
                freed_bytes += size

        remove_candidates(list(self.settings.browser_upload_dir.glob("*")), referenced_uploads)
        remove_candidates(list(self.settings.browser_output_dir.glob("*")), referenced_outputs)

        for directory, limit in (
            (self.settings.browser_trace_dir, self.settings.max_traces),
            (self.settings.browser_log_dir, self.settings.max_failure_html_dumps),
        ):
            files = sorted(
                [path for path in directory.glob("*") if path.is_file()],
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for file_path in files[limit:]:
                if file_path.name.endswith("_failure.html") or directory == self.settings.browser_trace_dir:
                    size = file_path.stat().st_size
                    file_path.unlink(missing_ok=True)
                    deleted_files += 1
                    freed_bytes += size
        return CleanupResponse(deleted_files=deleted_files, freed_bytes=freed_bytes)

    async def _consume_queue(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                if job_id in self.cancelled_queued_jobs:
                    self.cancelled_queued_jobs.discard(job_id)
                    continue
                await asyncio.to_thread(self._run_job_sync, job_id)
            finally:
                self.queue.task_done()

    def _recover_stale_incomplete_jobs(self) -> None:
        active_statuses = {
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
        }
        descriptor = get_error_descriptor(ErrorCode.RESPONSE_TIMEOUT.value)
        with SessionLocal() as session:
            jobs = session.execute(select(Job).where(Job.status.in_(active_statuses))).scalars().all()
            if not jobs:
                return
            for job in jobs:
                job.status = "failed"
                job.finished_at = utcnow()
                job.error_code = ErrorCode.RESPONSE_TIMEOUT.value
                job.error_message = (
                    "ordak restarted while this job was still running. Retry or resume it."
                )
                job.recoverable = descriptor.recoverable if descriptor else True
                job.suggested_action = descriptor.suggested_action if descriptor else None
                session.add(job)
                if job.conversation_id:
                    conversation = session.get(Conversation, job.conversation_id)
                    if conversation is not None:
                        conversation.updated_at = utcnow()
                        conversation.last_error_code = job.error_code
                        session.add(conversation)
            session.commit()

    def _run_job_sync(self, job_id: str) -> None:
        control = JobControl()
        self.active_controls[job_id] = control
        self.active_job_id = job_id
        try:
            with self.browser_lock:
                self._set_status(job_id, "running", started_at=utcnow())
                snapshot = self.get_job_snapshot(job_id)
                conversation = self._get_conversation_model(snapshot.conversation_id)
                target_tab = self._conversation_tab_ref(conversation)
                if snapshot.run_strategy == "new_tab_same_conversation":
                    target_tab = None
                elif snapshot.start_new_chat:
                    target_tab = None

                job_request = AutomationJobRequest(
                    question=snapshot.question,
                    provider=snapshot.provider,
                    conversation_id=snapshot.conversation_id,
                    start_new_chat=snapshot.start_new_chat,
                    target_tab=target_tab,
                    conversation_url=conversation.external_url if conversation else None,
                    mode=snapshot.mode,
                    upload_paths=[
                        storage_absolute_path(path, self.settings) for path in snapshot.uploads
                    ],
                    generation=(
                        GenerationOptions.model_validate(snapshot.generation)
                        if snapshot.generation
                        else None
                    ),
                    references=[
                        (
                            str(item.get("role") or "unspecified"),
                            storage_absolute_path(str(item.get("path")), self.settings),
                        )
                        for item in snapshot.references
                        if item.get("path")
                    ],
                    run_strategy=snapshot.run_strategy,
                    agent_config=(
                        AgentResolvedConfig(
                            workspace=Path(snapshot.agent_workspace),
                            workspace_display=snapshot.agent_workspace,
                            max_steps=snapshot.agent_max_steps or self.settings.agent_default_max_steps,
                            command_timeout_seconds=(
                                snapshot.agent_command_timeout_seconds
                                or self.settings.agent_default_command_timeout_seconds
                            ),
                            execution_backend=(
                                snapshot.agent_execution_backend
                                or self.settings.agent_execution_backend
                            ),
                            network_enabled=bool(
                                snapshot.agent_network_enabled
                                if snapshot.agent_network_enabled is not None
                                else self.settings.agent_network_enabled
                            ),
                        )
                        if snapshot.agent_workspace
                        else None
                    ),
                )
                runtime = WorkerRuntime(
                    update_status=lambda status: self._set_status(job_id, status),
                    append_log=lambda message, level="info": self._append_log(job_id, message, level),
                    attach_screenshot=lambda path: self._attach_screenshot(job_id, path),
                    attach_output_image=lambda path: self._attach_output_image(job_id, path),
                    attach_output_video=lambda path: self._attach_output_video(job_id, path),
                    attach_generation_receipt=lambda receipt: self._attach_generation_receipt(
                        job_id, receipt
                    ),
                    remember_conversation_state=lambda tab_info: self._remember_conversation_state(
                        snapshot.conversation_id, tab_info
                    ),
                    set_trace_path=lambda path: self._set_trace_path(job_id, path),
                    save_answer=lambda answer: self._save_answer(job_id, answer),
                    save_error=lambda message, status="failed", error_code=None: self._save_error(
                        job_id, message, status, error_code
                    ),
                    should_cancel=control.is_cancel_requested,
                    start_agent_step=lambda sequence, command_id, tool, request_json: self._start_agent_step(
                        job_id,
                        sequence=sequence,
                        command_id=command_id,
                        tool=tool,
                        request_json=request_json,
                    ),
                    finish_agent_step=lambda step_id, status, result_json=None, error_message=None: self._finish_agent_step(
                        job_id,
                        step_id=step_id,
                        status=status,
                        result_json=result_json,
                        error_message=error_message,
                    ),
                    agent_max_protocol_errors=self.settings.agent_max_protocol_errors,
                    agent_seen_command_ids=self._conversation_agent_command_ids(
                        snapshot.conversation_id,
                        excluding_job_id=job_id,
                    ),
                )
                try:
                    # Dispatch to Flow worker for video generation
                    if job_request.provider == "flow" and job_request.mode == "video_generate":
                        run_flow_job(job_id, job_request, runtime=runtime, app_settings=self.settings)
                    else:
                        self.worker(job_id, job_request, runtime=runtime, app_settings=self.settings)
                except GeminiAutomationError:
                    pass
                except OrdaKError as exc:
                    # A structured provider error must keep its code in the job record;
                    # falling through to the generic handler would erase it.
                    detail = (
                        f"{exc.message} ({exc.technical_details})"
                        if exc.technical_details
                        else exc.message
                    )
                    self._save_error(job_id, detail, "failed", exc.code.value)
                    self._append_log(job_id, f"[{exc.code.value}] {detail}", level="error")
                except Exception as exc:
                    self._save_error(job_id, str(exc) or "Unexpected job failure.", "failed", None)
                    self._append_log(job_id, str(exc) or "Unexpected job failure.", level="error")
                finally:
                    final_snapshot = self.get_job_snapshot(job_id)
                    if final_snapshot.status not in {
                        "completed",
                        "failed",
                        "manual_verification_required",
                        "cancelled",
                    }:
                        if control.is_cancel_requested():
                            self._update_job(
                                job_id,
                                status="cancelled",
                                finished_at=utcnow(),
                                error_message="Job cancelled.",
                            )
                        else:
                            self._update_job(job_id, status="failed", finished_at=utcnow())
        finally:
            self.active_controls.pop(job_id, None)
            if self.active_job_id == job_id:
                self.active_job_id = None


    def _attach_output_video(self, job_id: str, path: Path | str, metadata: dict[str, Any] | None = None) -> None:
        from app.artifacts import storage_relative_path

        relative_path = storage_relative_path(Path(path), self.settings) if isinstance(path, Path) else str(path)
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            output_videos = _loads_list(job.output_videos_json)
            output_videos.append(relative_path)
            job.output_videos_json = _dumps_list(output_videos)
            # also persist to metadata for backward compat
            try:
                metadata_rows = json.loads(job.metadata_json or "[]")
                existing = metadata_rows[0] if isinstance(metadata_rows, list) and metadata_rows else {}
            except json.JSONDecodeError:
                existing = {}
            existing["output_videos"] = output_videos
            if metadata is not None:
                existing.update(metadata)
            # keep as list wrapper like other fields
            job.metadata_json = _dumps_list([existing])
            session.add(job)
            session.commit()

    def _attach_generation_receipt(self, job_id: str, receipt: Any) -> None:
        """Persist what the worker actually observed in the provider UI (§8, §23)."""
        if receipt is None:
            return
        if hasattr(receipt, "model_dump"):
            payload = receipt.model_dump()
        elif isinstance(receipt, dict):
            payload = dict(receipt)
        else:
            return
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            existing = _loads_obj(job.generation_receipt_json) or {}
            existing.update({k: v for k, v in payload.items() if v is not None})
            job.generation_receipt_json = _dumps_obj(existing)
            session.add(job)
            session.commit()

    def _set_status(
        self,
        job_id: str,
        status: str,
        *,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        snapshot = self._update_job(
            job_id,
            status=status,
            **({"started_at": started_at} if started_at is not None else {}),
            **({"finished_at": finished_at} if finished_at is not None else {}),
        )
        event_type = "completed" if status == "completed" else "failed" if status in {
            "failed",
            "manual_verification_required",
            "cancelled",
        } else "snapshot"
        self._schedule_broadcast(event_type, snapshot)

    def _append_log(self, job_id: str, message: str, level: str = "info") -> None:
        entry = {"timestamp": utcnow().isoformat(), "level": level, "message": message}
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            logs = _loads_list(job.logs)
            logs.append(entry)
            job.logs = _dumps_list(logs)
            session.add(job)
            session.commit()
            conversation = session.get(Conversation, job.conversation_id) if job.conversation_id else None
            snapshot = self._snapshot_from_model(job, conversation, session=session)
        self._schedule_broadcast("log", snapshot, log=entry)

    def _attach_screenshot(self, job_id: str, path: Path) -> None:
        relative_path = storage_relative_path(path, self.settings)
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            screenshots = _loads_list(job.screenshot_paths)
            screenshots.append(relative_path)
            job.screenshot_paths = _dumps_list(screenshots)
            session.add(job)
            session.commit()
            conversation = session.get(Conversation, job.conversation_id) if job.conversation_id else None
            snapshot = self._snapshot_from_model(job, conversation, session=session)
        self._schedule_broadcast("snapshot", snapshot)

    def _set_trace_path(self, job_id: str, path: Path) -> None:
        relative_path = storage_relative_path(path, self.settings)
        snapshot = self._update_job(job_id, trace_path=relative_path)
        self._schedule_broadcast("snapshot", snapshot)

    def _save_answer(self, job_id: str, answer: str) -> None:
        snapshot = self._update_job(
            job_id,
            answer=answer,
            error_message=None,
            error_code=None,
            recoverable=False,
            suggested_action=None,
        )
        self._schedule_broadcast("snapshot", snapshot)

    def _attach_output_image(self, job_id: str, path: Path) -> None:
        relative_path = storage_relative_path(path, self.settings)
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            output_images = _loads_list(job.output_images_json)
            output_images.append(relative_path)
            job.output_images_json = _dumps_list(output_images)
            metadata_rows = _loads_list(job.metadata_json)
            metadata = metadata_rows[0] if metadata_rows else {}
            metadata["output_images"] = output_images
            job.metadata_json = _dumps_list([metadata])
            session.add(job)
            session.commit()
            conversation = session.get(Conversation, job.conversation_id) if job.conversation_id else None
            snapshot = self._snapshot_from_model(job, conversation, session=session)
        self._schedule_broadcast("snapshot", snapshot)

    def _start_agent_step(
        self,
        job_id: str,
        *,
        sequence: int,
        command_id: str,
        tool: str,
        request_json: str,
    ) -> str:
        with SessionLocal() as session:
            step = AgentStep(
                job_id=job_id,
                sequence=sequence,
                command_id=command_id,
                tool=tool,
                status="running",
                request_json=request_json,
                started_at=utcnow(),
            )
            session.add(step)
            session.commit()
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            conversation = session.get(Conversation, job.conversation_id) if job.conversation_id else None
            snapshot = self._snapshot_from_model(job, conversation, session=session)
        self._schedule_broadcast("snapshot", snapshot)
        return step.id

    def _finish_agent_step(
        self,
        job_id: str,
        *,
        step_id: str,
        status: str,
        result_json: str | None,
        error_message: str | None,
    ) -> None:
        with SessionLocal() as session:
            step = session.get(AgentStep, step_id)
            if step is None:
                raise KeyError(step_id)
            step.status = status
            step.result_json = result_json
            step.error_message = error_message
            step.finished_at = utcnow()
            started_at = step.started_at
            finished_at = step.finished_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            if finished_at.tzinfo is None:
                finished_at = finished_at.replace(tzinfo=timezone.utc)
            step.duration_ms = int((finished_at - started_at).total_seconds() * 1000)
            session.add(step)
            session.commit()
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            conversation = session.get(Conversation, job.conversation_id) if job.conversation_id else None
            snapshot = self._snapshot_from_model(job, conversation, session=session)
        self._schedule_broadcast("snapshot", snapshot)

    def _save_error(
        self,
        job_id: str,
        message: str,
        status: str,
        error_code: str | None,
    ) -> None:
        descriptor = get_error_descriptor(error_code)
        snapshot = self._update_job(
            job_id,
            status=status,
            error_message=message,
            error_code=error_code,
            recoverable=descriptor.recoverable if descriptor else False,
            suggested_action=descriptor.suggested_action if descriptor else None,
            finished_at=utcnow(),
        )
        self._schedule_broadcast("failed", snapshot)
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job and job.conversation_id:
                conversation = session.get(Conversation, job.conversation_id)
                if conversation is not None:
                    conversation.last_error_code = error_code
                    if error_code == "tab_lost":
                        conversation.tab_alive = False
                    session.add(conversation)
                    session.commit()

    def _update_job(self, job_id: str, **updates: Any) -> JobSnapshot:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            for key, value in updates.items():
                setattr(job, key, value)
            if updates.get("status") == "completed" and job.finished_at is None:
                job.finished_at = utcnow()
            if job.conversation_id:
                conversation = session.get(Conversation, job.conversation_id)
                if conversation is not None:
                    conversation.updated_at = utcnow()
                    if updates.get("status") == "completed":
                        conversation.last_successful_job_id = job.id
                        conversation.last_error_code = None
                    session.add(conversation)
            session.add(job)
            session.commit()
            conversation = session.get(Conversation, job.conversation_id) if job.conversation_id else None
            return self._snapshot_from_model(job, conversation, session=session)

    def _snapshot_from_model(
        self,
        job: Job,
        conversation: Conversation | None,
        *,
        session=None,
    ) -> JobSnapshot:
        metadata = {
            "conversation_id": job.id,
            "conversation_title": self._make_conversation_title(job.question),
            "provider": "gemini",
            "start_new_chat": True,
            "mode": "chat",
            "uploads": [],
            "output_images": [],
            "output_videos": [],
            "agent_workspace": None,
            "agent_max_steps": None,
            "agent_command_timeout_seconds": None,
            "agent_execution_backend": None,
            "agent_network_enabled": None,
        }
        metadata_rows = _loads_list(job.metadata_json)
        if metadata_rows:
            metadata.update(metadata_rows[0])
        uploads = _loads_list(job.uploads_json) or list(metadata.get("uploads", []))
        output_images = _loads_list(job.output_images_json) or list(metadata.get("output_images", []))
        output_videos = _loads_list(getattr(job, "output_videos_json", None)) or list(metadata.get("output_videos", []))
        if session is None:
            with SessionLocal() as nested_session:
                agent_step_count = nested_session.execute(
                    select(func.count()).select_from(AgentStep).where(AgentStep.job_id == job.id)
                ).scalar_one()
        else:
            agent_step_count = session.execute(
                select(func.count()).select_from(AgentStep).where(AgentStep.job_id == job.id)
            ).scalar_one()
        conversation_id = job.conversation_id or metadata.get("conversation_id", job.id)
        conversation_title = (
            job.conversation_title
            or (conversation.title if conversation else None)
            or metadata.get("conversation_title", self._make_conversation_title(job.question))
        )
        recoverable = (
            bool(job.recoverable)
            if job.recoverable is not None
            else bool(get_error_descriptor(job.error_code))
        )
        return JobSnapshot(
            job_id=job.id,
            question=job.question,
            conversation_id=conversation_id,
            conversation_title=conversation_title,
            provider=job.provider or metadata.get("provider", "gemini"),
            start_new_chat=bool(job.start_new_chat if job.start_new_chat is not None else metadata.get("start_new_chat", False)),
            mode=job.mode or metadata.get("mode", "chat"),
            retry_of_job_id=job.retry_of_job_id,
            run_strategy=job.run_strategy,
            uploads=uploads,
            references=_loads_list(getattr(job, "references_json", None)),
            generation=_loads_obj(getattr(job, "generation_json", None)),
            generation_receipt=_loads_obj(getattr(job, "generation_receipt_json", None)),
            output_images=output_images,
            output_videos=output_videos,
            answer=job.answer,
            status=job.status,
            error_code=job.error_code,
            error_message=job.error_message,
            recoverable=recoverable,
            suggested_action=job.suggested_action,
            logs=_loads_list(job.logs),
            screenshots=_loads_list(job.screenshot_paths),
            trace_path=job.trace_path,
            agent_workspace=job.agent_workspace or metadata.get("agent_workspace"),
            agent_step_count=int(agent_step_count or 0),
            agent_max_steps=(
                job.agent_max_steps
                if job.agent_max_steps is not None
                else metadata.get("agent_max_steps")
            ),
            agent_command_timeout_seconds=(
                job.agent_command_timeout_seconds
                if job.agent_command_timeout_seconds is not None
                else metadata.get("agent_command_timeout_seconds")
            ),
            agent_execution_backend=(
                job.agent_execution_backend or metadata.get("agent_execution_backend")
            ),
            agent_network_enabled=(
                job.agent_network_enabled
                if job.agent_network_enabled is not None
                else metadata.get("agent_network_enabled")
            ),
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            cancel_requested_at=job.cancel_requested_at,
        )

    def _remember_conversation_state(self, conversation_id: str, tab_info: ChromeTabInfo) -> None:
        with SessionLocal() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return
            conversation.tab_window_id = tab_info.window_id
            conversation.tab_id = tab_info.tab_id
            conversation.tab_window_key = getattr(tab_info, "window_key", None)
            conversation.tab_target_id = getattr(tab_info, "target_id", None)
            existing_url = conversation.external_url or ""
            new_url = tab_info.url or ""
            keep_specific_url = "/c/" in existing_url and "/c/" not in new_url
            if not keep_specific_url:
                conversation.external_url = new_url
                conversation.external_conversation_id = self._extract_external_conversation_id(
                    new_url
                )
            conversation.tab_alive = True
            conversation.updated_at = utcnow()
            session.add(conversation)
            session.commit()

    def _get_or_create_conversation(
        self,
        conversation_id: str,
        *,
        provider: Provider,
        title: str,
    ) -> Conversation:
        with SessionLocal() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                conversation = Conversation(
                    id=conversation_id,
                    provider=provider,
                    title=title,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                session.add(conversation)
                session.commit()
                session.refresh(conversation)
                session.expunge(conversation)
                return conversation
            session.expunge(conversation)
            return conversation

    def _refresh_conversation_bindings(self, session) -> None:
        if not is_google_chrome_running():
            conversations = session.execute(select(Conversation)).scalars().all()
            changed = False
            for conversation in conversations:
                if conversation.tab_alive:
                    conversation.tab_alive = False
                    changed = True
                    session.add(conversation)
            if changed:
                session.commit()
            return

        tabs = list_google_chrome_tabs()
        by_ref = {(tab.window_id, tab.tab_id): tab for tab in tabs}
        by_target_id = {
            getattr(tab, "target_id"): tab
            for tab in tabs
            if getattr(tab, "target_id", None)
        }
        by_url = {tab.url: tab for tab in tabs if tab.url}
        conversations = session.execute(select(Conversation)).scalars().all()
        changed = False
        for conversation in conversations:
            matched = None
            if conversation.tab_target_id:
                matched = by_target_id.get(conversation.tab_target_id)
            if conversation.tab_window_id and conversation.tab_id:
                matched = matched or by_ref.get((conversation.tab_window_id, conversation.tab_id))
            if matched is None and conversation.external_url:
                matched = by_url.get(conversation.external_url)
            if matched is None and conversation.external_conversation_id:
                matched = next(
                    (
                        tab
                        for tab in tabs
                        if conversation.external_conversation_id in (tab.url or "")
                        and self._tab_matches_provider(tab, conversation.provider)
                    ),
                    None,
                )
            alive = matched is not None
            if conversation.tab_alive != alive:
                conversation.tab_alive = alive
                changed = True
            if matched is not None:
                if (
                    conversation.tab_window_id != matched.window_id
                    or conversation.tab_id != matched.tab_id
                    or conversation.tab_window_key != matched.window_key
                    or conversation.tab_target_id != matched.target_id
                    or conversation.external_url != matched.url
                ):
                    conversation.tab_window_id = matched.window_id
                    conversation.tab_id = matched.tab_id
                    conversation.tab_window_key = getattr(matched, "window_key", None)
                    conversation.tab_target_id = getattr(matched, "target_id", None)
                    conversation.external_url = matched.url
                    conversation.external_conversation_id = self._extract_external_conversation_id(
                        matched.url
                    )
                    changed = True
            session.add(conversation)
        if changed:
            session.commit()

    def _get_conversation_model(self, conversation_id: str) -> Conversation | None:
        with SessionLocal() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return None
            session.expunge(conversation)
            return conversation

    def _conversation_tab_ref(self, conversation: Conversation | None) -> ChromeTabRef | None:
        if conversation is None:
            return None
        if conversation.tab_target_id:
            return ChromeTabRef(
                window_id=conversation.tab_window_id or 0,
                tab_id=conversation.tab_id or 0,
                window_key=conversation.tab_window_key,
                target_id=conversation.tab_target_id,
            )
        if conversation.tab_window_id and conversation.tab_id:
            return ChromeTabRef(
                window_id=conversation.tab_window_id,
                tab_id=conversation.tab_id,
                window_key=conversation.tab_window_key,
            )
        return None

    def _make_conversation_title(self, question: str) -> str:
        compact = " ".join(question.strip().split())
        if len(compact) <= 52:
            return compact or "New chat"
        return compact[:49].rstrip() + "..."

    def _extract_external_conversation_id(self, url: str | None) -> str | None:
        if not url:
            return None
        chunks = [chunk for chunk in url.rstrip("/").split("/") if chunk]
        if not chunks:
            return None
        return chunks[-1]

    def _tab_matches_provider(self, tab: ChromeTabInfo, provider: str) -> bool:
        url = (tab.url or "").lower()
        if provider == "chatgpt":
            return "chatgpt.com" in url
        return "gemini.google.com" in url or "bard.google.com" in url

    def _probe_apple_events(self, chrome_running: bool) -> bool:
        if not chrome_running:
            return False
        try:
            tabs = list_google_chrome_tabs()
            if not tabs:
                return False
            execute_javascript(tabs[0].ref, "(() => 'ok')()")
            return True
        except Exception:
            return False

    def _schedule_broadcast(
        self,
        event_type: str,
        snapshot: JobSnapshot,
        *,
        log: dict[str, Any] | None = None,
    ) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._broadcast_snapshot(snapshot, event_type=event_type, log=log),
            self.loop,
        )

    async def _broadcast_snapshot(
        self,
        snapshot: JobSnapshot,
        *,
        event_type: str,
        log: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "type": event_type,
            "job": snapshot.to_response().model_dump(mode="json"),
            "log": log,
        }
        websockets = list(self.subscribers.get(snapshot.job_id, set()))
        stale: list[WebSocket] = []
        for websocket in websockets:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            await self.remove_subscriber(snapshot.job_id, websocket)
