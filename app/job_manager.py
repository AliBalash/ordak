from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import WebSocket
from sqlalchemy import func, select

from app.artifacts import storage_absolute_path, storage_relative_path
from app.automation.browser import open_profile_browser_session
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
from app.config import Settings, settings
from app.database import SessionLocal
from app.errors import ErrorCode, get_error_descriptor
from app.models import Conversation, Job
from app.providers import get_provider_adapter
from app.schemas import (
    CleanupResponse,
    ConversationResponse,
    ConversationSummary,
    DiagnosticsProviderState,
    DiagnosticsResponse,
    JobCreateResponse,
    JobMode,
    JobResponse,
    LogEntry,
    Provider,
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
    output_images: list[str]
    answer: str | None
    status: str
    error_code: str | None
    error_message: str | None
    recoverable: bool
    suggested_action: str | None
    logs: list[dict[str, Any]]
    screenshots: list[str]
    trace_path: str | None
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
            output_images=self.output_images,
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
    ) -> JobCreateResponse:
        uploads = uploads or []
        resolved_job_id = job_id or str(uuid.uuid4())
        resolved_conversation_id = (
            str(uuid.uuid4()) if start_new_chat or not conversation_id else conversation_id
        )
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
            conversation.external_url = None

        metadata = {
            "conversation_id": resolved_conversation_id,
            "conversation_title": conversation.title,
            "provider": provider,
            "start_new_chat": bool(start_new_chat or not conversation_id),
            "mode": mode,
            "uploads": uploads,
            "output_images": [],
            "retry_of_job_id": retry_of_job_id,
            "run_strategy": run_strategy,
        }
        job = Job(
            id=resolved_job_id,
            question=question.strip(),
            status="queued",
            provider=provider,
            conversation_id=resolved_conversation_id,
            conversation_title=conversation.title,
            mode=mode,
            start_new_chat=bool(start_new_chat or not conversation_id),
            retry_of_job_id=retry_of_job_id,
            run_strategy=run_strategy,
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
            output_images_json=_dumps_list([]),
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
            return self._snapshot_from_model(job, conversation)

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
                self._snapshot_from_model(job, conversations.get(job.conversation_id))
                for job in rows
            ]

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
            snapshots = [self._snapshot_from_model(job, conversation) for job in jobs]
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
        for provider in ("gemini", "chatgpt"):
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
        for provider in ("gemini", "chatgpt"):
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
            },
            tab_binding_health=tab_binding_health,
            last_success_by_provider=last_success_by_provider,
            last_error_by_provider=last_error_by_provider,
            active_job=active_job,
            queue_depth=self.queue.qsize(),
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
                target_tab = None
                if conversation and conversation.tab_window_id and conversation.tab_id:
                    target_tab = ChromeTabRef(
                        window_id=conversation.tab_window_id,
                        tab_id=conversation.tab_id,
                    )
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
                    run_strategy=snapshot.run_strategy,
                )
                runtime = WorkerRuntime(
                    update_status=lambda status: self._set_status(job_id, status),
                    append_log=lambda message, level="info": self._append_log(job_id, message, level),
                    attach_screenshot=lambda path: self._attach_screenshot(job_id, path),
                    attach_output_image=lambda path: self._attach_output_image(job_id, path),
                    remember_conversation_state=lambda tab_info: self._remember_conversation_state(
                        snapshot.conversation_id, tab_info
                    ),
                    set_trace_path=lambda path: self._set_trace_path(job_id, path),
                    save_answer=lambda answer: self._save_answer(job_id, answer),
                    save_error=lambda message, status="failed", error_code=None: self._save_error(
                        job_id, message, status, error_code
                    ),
                    should_cancel=control.is_cancel_requested,
                )
                try:
                    self.worker(job_id, job_request, runtime=runtime, app_settings=self.settings)
                except GeminiAutomationError:
                    pass
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
            snapshot = self._snapshot_from_model(job, conversation)
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
            snapshot = self._snapshot_from_model(job, conversation)
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
            snapshot = self._snapshot_from_model(job, conversation)
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
            return self._snapshot_from_model(job, conversation)

    def _snapshot_from_model(
        self,
        job: Job,
        conversation: Conversation | None,
    ) -> JobSnapshot:
        metadata = {
            "conversation_id": job.id,
            "conversation_title": self._make_conversation_title(job.question),
            "provider": "gemini",
            "start_new_chat": True,
            "mode": "chat",
            "uploads": [],
            "output_images": [],
        }
        metadata_rows = _loads_list(job.metadata_json)
        if metadata_rows:
            metadata.update(metadata_rows[0])
        uploads = _loads_list(job.uploads_json) or list(metadata.get("uploads", []))
        output_images = _loads_list(job.output_images_json) or list(metadata.get("output_images", []))
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
            output_images=output_images,
            answer=job.answer,
            status=job.status,
            error_code=job.error_code,
            error_message=job.error_message,
            recoverable=recoverable,
            suggested_action=job.suggested_action,
            logs=_loads_list(job.logs),
            screenshots=_loads_list(job.screenshot_paths),
            trace_path=job.trace_path,
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
            conversation.external_url = tab_info.url
            conversation.external_conversation_id = self._extract_external_conversation_id(tab_info.url)
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
        by_url = {tab.url: tab for tab in tabs if tab.url}
        conversations = session.execute(select(Conversation)).scalars().all()
        changed = False
        for conversation in conversations:
            matched = None
            if conversation.tab_window_id and conversation.tab_id:
                matched = by_ref.get((conversation.tab_window_id, conversation.tab_id))
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
                    or conversation.external_url != matched.url
                ):
                    conversation.tab_window_id = matched.window_id
                    conversation.tab_id = matched.tab_id
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
