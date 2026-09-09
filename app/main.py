from __future__ import annotations

import hashlib

from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
import uuid

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.artifacts import slugify_filename, storage_absolute_path
from app.config import settings
from app.database import init_db
from app.job_manager import JobManager
from app.errors import OrdaKError
from app.schemas import (
    AgentStepListResponse,
    CleanupResponse,
    ConversationListResponse,
    ConversationResponse,
    DiagnosticsResponse,
    GenerationOptions,
    HealthResponse,
    ArtifactLinkResponse,
    JobCreateRequest,
    JobCreateResponse,
    JobListResponse,
    JobResponse,
    ProfileOpenResponse,
    ProviderRunRequest,
    ProviderRunResponse,
    ReferenceSpec,
    ResumeJobRequest,
    RetryJobRequest,
    StorageDiagnosticsResponse,
)
from app.uploads import save_image_upload


def _as_form_bool(value: object) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _absolute_url(request: Request, relative_path: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/{relative_path.lstrip('/')}"


def _generation_from_form(form: object) -> GenerationOptions | None:
    """Read the explicit generation contract out of a multipart form (§5, §18-21)."""

    def text(name: str) -> str | None:
        value = form.get(name)  # type: ignore[union-attr]
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    duration_raw = text("duration_seconds")
    duration: int | None = None
    if duration_raw is not None:
        try:
            duration = int(float(duration_raw))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="duration_seconds must be a number.") from exc

    options = GenerationOptions(
        model=text("model"),
        quality=text("quality"),
        aspect_ratio=text("aspect_ratio"),
        duration_seconds=duration,
        resolution=text("resolution"),
    )
    return None if options.is_empty() else options


def _validate_agent_request(
    *,
    mode: str,
    provider: str,
    agent,
    uploads_present: bool,
    multipart: bool,
) -> None:
    if mode == "agent":
        if provider != "chatgpt":
            raise HTTPException(status_code=422, detail="Agent mode currently supports ChatGPT only.")
        if uploads_present:
            raise HTTPException(status_code=422, detail="Agent mode does not accept image uploads.")
        if multipart:
            raise HTTPException(
                status_code=422,
                detail="Agent mode must be submitted as JSON and does not accept multipart form data.",
            )
        if agent is None:
            raise HTTPException(status_code=422, detail="Agent options are required when mode is agent.")
        return
    if agent is not None:
        raise HTTPException(status_code=422, detail="Agent options are only valid when mode is agent.")


def _artifact_link(request: Request, relative_path: str) -> ArtifactLinkResponse:
    return ArtifactLinkResponse(
        path=relative_path,
        url=_absolute_url(request, relative_path),
        filename=Path(relative_path).name,
    )


def create_app(job_manager: JobManager | None = None) -> FastAPI:
    manager = job_manager or JobManager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_directories()
        init_db()
        app.state.job_manager = manager
        await manager.start()
        yield
        await manager.shutdown()

    app = FastAPI(title=f"{settings.product_name} | {settings.product_label}", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
    app.mount(
        "/storage",
        StaticFiles(directory=settings.browser_screenshot_dir.parent),
        name="storage",
    )

    async def _create_job_from_request(
        request: Request,
        *,
        forced_provider: str | None = None,
    ) -> tuple[JobCreateResponse, bool, int]:
        content_type = request.headers.get("content-type", "")
        uploads: list[str] = []
        if "multipart/form-data" in content_type:
            form = await request.form()
            question = str(form.get("question", "")).strip()
            provider = forced_provider or str(form.get("provider", "gemini")).strip() or "gemini"
            mode = str(form.get("mode", "chat")).strip() or "chat"
            conversation_id = str(form.get("conversation_id", "")).strip() or None
            start_new_chat = _as_form_bool(form.get("start_new_chat"))
            wait_for_completion = _as_form_bool(form.get("wait_for_completion")) if "wait_for_completion" in form else True
            try:
                wait_timeout_seconds = int(str(form.get("wait_timeout_seconds", "300")).strip() or "300")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="wait_timeout_seconds must be an integer.") from exc
            if not question:
                raise HTTPException(status_code=422, detail="Question is required.")
            if provider not in {"gemini", "chatgpt", "flow"}:
                raise HTTPException(status_code=422, detail="Unsupported provider.")
            if mode not in {"chat", "image_analyze", "image_generate", "agent", "video_generate"}:
                raise HTTPException(status_code=422, detail="Unsupported job mode.")
            if wait_timeout_seconds < 1 or wait_timeout_seconds > 3600:
                raise HTTPException(status_code=422, detail="wait_timeout_seconds must be between 1 and 3600.")
            pending_job_id = str(uuid.uuid4())
            upload_items = [item for item in form.getlist("image") if getattr(item, "filename", None)]
            role_items = [str(value).strip() for value in form.getlist("role")]
            if role_items and len(role_items) != len(upload_items):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Received {len(role_items)} role field(s) for {len(upload_items)} upload(s); "
                        "send exactly one role per image, in the same order."
                    ),
                )
            generation = _generation_from_form(form)
            _validate_agent_request(
                mode=mode,
                provider=provider,
                agent=None,
                uploads_present=bool(upload_items),
                multipart=True,
            )
            for upload in upload_items:
                try:
                    saved_upload = await save_image_upload(upload, pending_job_id, settings)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                uploads.append(saved_upload)
            references = [
                ReferenceSpec(
                    role=role_items[index] if index < len(role_items) else "unspecified",
                    path=saved,
                    filename=getattr(upload_items[index], "filename", None),
                    sha256=hashlib.sha256(storage_absolute_path(saved).read_bytes()).hexdigest(),
                    position=index,
                )
                for index, saved in enumerate(uploads)
            ]
            try:
                created = await request.app.state.job_manager.create_job(
                    question,
                    job_id=pending_job_id,
                    provider=provider,
                    mode=mode,
                    conversation_id=conversation_id,
                    start_new_chat=start_new_chat,
                    uploads=uploads,
                    references=references,
                    generation=generation,
                )
                return created, wait_for_completion, wait_timeout_seconds
            except OrdaKError as exc:
                raise HTTPException(status_code=422, detail=exc.message) from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        raw_payload = await request.json()
        if forced_provider is None:
            payload = JobCreateRequest.model_validate(raw_payload)
            _validate_agent_request(
                mode=payload.mode,
                provider=payload.provider,
                agent=payload.agent,
                uploads_present=False,
                multipart=False,
            )
            wait_for_completion = True
            wait_timeout_seconds = 300
            try:
                created = await request.app.state.job_manager.create_job(
                    payload.question,
                    provider=payload.provider,
                    mode=payload.mode,
                    conversation_id=payload.conversation_id,
                    start_new_chat=payload.start_new_chat,
                    uploads=[],
                    agent_options=payload.agent,
                    generation=payload.generation,
                )
                return created, wait_for_completion, wait_timeout_seconds
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        payload = ProviderRunRequest.model_validate(raw_payload)
        _validate_agent_request(
            mode=payload.mode,
            provider=forced_provider,
            agent=payload.agent,
            uploads_present=False,
            multipart=False,
        )
        try:
            created = await request.app.state.job_manager.create_job(
                payload.question,
                provider=forced_provider,
                mode=payload.mode,
                conversation_id=payload.conversation_id,
                start_new_chat=payload.start_new_chat,
                uploads=[],
                agent_options=payload.agent,
                generation=payload.generation,
            )
            return created, payload.wait_for_completion, payload.wait_timeout_seconds
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def _wait_for_job_terminal(job_id: str, *, timeout_seconds: int) -> JobResponse:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            snapshot = app.state.job_manager.get_job_snapshot(job_id).to_response()
            if snapshot.status in {"completed", "failed", "manual_verification_required", "cancelled"}:
                return snapshot
            if asyncio.get_running_loop().time() >= deadline:
                return snapshot
            await asyncio.sleep(0.5)

    def _provider_run_response(request: Request, job: JobResponse) -> ProviderRunResponse:
        try:
            conversation = request.app.state.job_manager.get_conversation(job.conversation_id)
            provider_conversation_url = conversation.external_url
        except KeyError:
            provider_conversation_url = None
        return ProviderRunResponse(
            provider=job.provider,
            job_id=job.job_id,
            job_api_url=_absolute_url(request, f"api/jobs/{job.job_id}"),
            conversation_id=job.conversation_id,
            conversation_api_url=_absolute_url(request, f"api/conversations/{job.conversation_id}"),
            conversation_title=job.conversation_title,
            provider_conversation_url=provider_conversation_url,
            mode=job.mode,
            status=job.status,
            completed=job.status in {"completed", "failed", "manual_verification_required", "cancelled"},
            answer=job.answer,
            error_code=job.error_code,
            error_title=job.error_title,
            error_message=job.error_message,
            suggested_action=job.suggested_action,
            recoverable=job.recoverable,
            uploads=[_artifact_link(request, path) for path in job.uploads],
            references=job.references,
            generation=job.generation,
            generation_receipt=job.generation_receipt,
            output_images=[_artifact_link(request, path) for path in job.output_images],
            output_videos=[_artifact_link(request, path) for path in job.output_videos],
            screenshots=[_artifact_link(request, path) for path in job.screenshots],
            trace_url=_absolute_url(request, job.trace_path) if job.trace_path else None,
            logs=job.logs,
        )

    @app.get("/", include_in_schema=False)
    async def root() -> FileResponse:
        return FileResponse(settings.static_dir / "index.html")

    @app.get("/diagnostics", include_in_schema=False)
    async def diagnostics_page() -> FileResponse:
        return FileResponse(settings.static_dir / "diagnostics.html")

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", database="ready")

    @app.get("/api/diagnostics", response_model=DiagnosticsResponse)
    async def diagnostics(request: Request) -> DiagnosticsResponse:
        return request.app.state.job_manager.diagnostics()

    @app.get("/api/diagnostics/storage", response_model=StorageDiagnosticsResponse)
    async def diagnostics_storage(request: Request) -> StorageDiagnosticsResponse:
        return request.app.state.job_manager.storage_diagnostics()

    @app.post("/api/diagnostics/cleanup", response_model=CleanupResponse)
    async def diagnostics_cleanup(request: Request) -> CleanupResponse:
        return request.app.state.job_manager.cleanup_storage()

    @app.post("/api/jobs", response_model=JobCreateResponse)
    async def create_job_endpoint(request: Request) -> JobCreateResponse:
        created, _, _ = await _create_job_from_request(request)
        return created

    @app.post("/api/providers/{provider}/respond", response_model=ProviderRunResponse)
    async def provider_respond(provider: str, request: Request) -> ProviderRunResponse:
        if provider not in {"gemini", "chatgpt", "flow"}:
            raise HTTPException(status_code=404, detail="Unsupported provider.")
        created, wait_for_completion, wait_timeout_seconds = await _create_job_from_request(
            request,
            forced_provider=provider,
        )
        job = (
            await _wait_for_job_terminal(created.job_id, timeout_seconds=wait_timeout_seconds)
            if wait_for_completion
            else request.app.state.job_manager.get_job_snapshot(created.job_id).to_response()
        )
        return _provider_run_response(request, job)

    @app.post("/api/gemini/respond", response_model=ProviderRunResponse)
    async def gemini_respond(request: Request) -> ProviderRunResponse:
        created, wait_for_completion, wait_timeout_seconds = await _create_job_from_request(
            request,
            forced_provider="gemini",
        )
        job = (
            await _wait_for_job_terminal(created.job_id, timeout_seconds=wait_timeout_seconds)
            if wait_for_completion
            else request.app.state.job_manager.get_job_snapshot(created.job_id).to_response()
        )
        return _provider_run_response(request, job)

    @app.post("/api/chatgpt/respond", response_model=ProviderRunResponse)
    async def chatgpt_respond(request: Request) -> ProviderRunResponse:
        created, wait_for_completion, wait_timeout_seconds = await _create_job_from_request(
            request,
            forced_provider="chatgpt",
        )
        job = (
            await _wait_for_job_terminal(created.job_id, timeout_seconds=wait_timeout_seconds)
            if wait_for_completion
            else request.app.state.job_manager.get_job_snapshot(created.job_id).to_response()
        )
        return _provider_run_response(request, job)

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobResponse)
    async def cancel_job(job_id: str, request: Request) -> JobResponse:
        try:
            snapshot = await request.app.state.job_manager.cancel_job(job_id)
            return snapshot.to_response()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc

    @app.post("/api/jobs/{job_id}/retry", response_model=JobCreateResponse)
    async def retry_job(job_id: str, payload: RetryJobRequest, request: Request) -> JobCreateResponse:
        try:
            return await request.app.state.job_manager.retry_job(job_id, payload.strategy)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/resume", response_model=JobCreateResponse)
    async def resume_job(job_id: str, payload: ResumeJobRequest, request: Request) -> JobCreateResponse:
        try:
            return await request.app.state.job_manager.resume_job(job_id, payload.strategy)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    async def get_job(job_id: str, request: Request) -> JobResponse:
        try:
            return request.app.state.job_manager.get_job_snapshot(job_id).to_response()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc

    @app.get("/api/jobs/{job_id}/steps", response_model=AgentStepListResponse)
    async def get_job_steps(job_id: str, request: Request) -> AgentStepListResponse:
        try:
            return AgentStepListResponse(steps=request.app.state.job_manager.list_agent_steps(job_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc

    @app.get("/api/jobs", response_model=JobListResponse)
    async def list_jobs(request: Request) -> JobListResponse:
        jobs = [snapshot.to_response() for snapshot in request.app.state.job_manager.list_jobs()]
        return JobListResponse(jobs=jobs)

    @app.get("/api/conversations", response_model=ConversationListResponse)
    async def list_conversations(request: Request) -> ConversationListResponse:
        conversations = request.app.state.job_manager.list_conversations()
        return ConversationListResponse(conversations=conversations)

    @app.get("/api/conversations/{conversation_id}", response_model=ConversationResponse)
    async def get_conversation(conversation_id: str, request: Request) -> ConversationResponse:
        try:
            return request.app.state.job_manager.get_conversation(conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found.") from exc

    @app.post("/api/conversations/{conversation_id}/pin", response_model=ConversationResponse)
    async def pin_conversation(conversation_id: str, request: Request) -> ConversationResponse:
        try:
            return request.app.state.job_manager.set_conversation_pinned(conversation_id, True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found.") from exc

    @app.post("/api/conversations/{conversation_id}/unpin", response_model=ConversationResponse)
    async def unpin_conversation(conversation_id: str, request: Request) -> ConversationResponse:
        try:
            return request.app.state.job_manager.set_conversation_pinned(conversation_id, False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found.") from exc

    @app.get("/api/uploads/{filename}")
    async def get_uploaded_file(filename: str) -> FileResponse:
        safe_name = slugify_filename(filename)
        if safe_name != filename:
            raise HTTPException(status_code=404, detail="Upload not found.")
        target = settings.browser_upload_dir / safe_name
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="Upload not found.")
        return FileResponse(
            target,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @app.post("/api/profile/open", response_model=ProfileOpenResponse)
    async def open_profile(request: Request) -> ProfileOpenResponse:
        try:
            opened = request.app.state.job_manager.launch_profile_browser()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not opened:
            raise HTTPException(
                status_code=409,
                detail="Browser profile is currently in use by an automation job.",
            )
        return ProfileOpenResponse(message="Profile browser opened.")

    @app.websocket("/ws/jobs/{job_id}")
    async def job_updates(websocket: WebSocket, job_id: str) -> None:
        await websocket.app.state.job_manager.add_subscriber(job_id, websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            await websocket.app.state.job_manager.remove_subscriber(job_id, websocket)

    return app


app = create_app()
