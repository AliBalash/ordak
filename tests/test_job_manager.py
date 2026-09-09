from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path

from app.config import settings
from app.database import configure_database, init_db
from app.job_manager import JobManager
from app.models import Conversation, Job
from app.database import SessionLocal
from app.schemas import AgentOptions


def test_job_manager_runs_jobs_sequentially(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()
    running = 0
    max_running = 0

    def fake_worker(job_id: str, job_request, runtime=None, app_settings=None) -> str:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        runtime.update_status("opening_browser")
        runtime.append_log(f"working on {job_request.question}")
        output_path = settings.browser_output_dir / f"{job_id}.png"
        output_path.write_bytes(b"fake-output")
        runtime.attach_output_image(output_path)
        runtime.save_answer(f"answer:{job_request.question}")
        runtime.update_status("completed")
        running -= 1
        return f"answer:{job_request.question}"

    async def scenario() -> None:
        manager = JobManager(worker=fake_worker)
        await manager.start()
        first = await manager.create_job("first", mode="chat", start_new_chat=True)
        second = await manager.create_job(
            "second",
            provider="chatgpt",
            mode="image_analyze",
            uploads=["storage/uploads/example.png"],
            start_new_chat=True,
        )
        await asyncio.wait_for(manager.queue.join(), timeout=5)
        await manager.shutdown()
        assert manager.get_job_snapshot(first.job_id).status == "completed"
        assert manager.get_job_snapshot(second.job_id).status == "completed"
        assert manager.get_job_snapshot(second.job_id).provider == "chatgpt"
        assert manager.get_job_snapshot(second.job_id).mode == "image_analyze"
        assert manager.get_job_snapshot(second.job_id).uploads == ["storage/uploads/example.png"]
        assert manager.get_job_snapshot(second.job_id).output_images == [
            f"storage/outputs/{second.job_id}.png"
        ]

    asyncio.run(scenario())
    assert max_running == 1


def test_cancel_queued_job_marks_cancelled(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()

    async def scenario() -> None:
        manager = JobManager(worker=lambda *args, **kwargs: "unused")
        job = await manager.create_job("queued", start_new_chat=True)
        snapshot = await manager.cancel_job(job.job_id)
        assert snapshot.status == "cancelled"

    asyncio.run(scenario())


def test_cancel_running_job_marks_cancelled(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()

    def cancellable_worker(job_id: str, job_request, runtime=None, app_settings=None) -> str:
        runtime.update_status("waiting_for_response")
        while not runtime.should_cancel():
            time.sleep(0.05)
        raise RuntimeError("worker noticed cancellation")

    async def scenario() -> None:
        manager = JobManager(worker=cancellable_worker)
        await manager.start()
        job = await manager.create_job("running", start_new_chat=True)
        await asyncio.sleep(0.2)
        snapshot = await manager.cancel_job(job.job_id)
        assert snapshot.status in {"cancelling", "cancelled"}
        await asyncio.wait_for(manager.queue.join(), timeout=5)
        final = manager.get_job_snapshot(job.job_id)
        await manager.shutdown()
        assert final.status in {"cancelled", "failed"}

    asyncio.run(scenario())


def test_retry_and_resume_create_new_jobs(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()

    def fake_worker(job_id: str, job_request, runtime=None, app_settings=None) -> str:
        runtime.save_error("boom", "failed", "response_timeout")
        raise RuntimeError("boom")

    async def scenario() -> None:
        manager = JobManager(worker=fake_worker)
        await manager.start()
        job = await manager.create_job("original", start_new_chat=True)
        await asyncio.wait_for(manager.queue.join(), timeout=5)
        failed = manager.get_job_snapshot(job.job_id)
        assert failed.status == "failed"

        retry_job = await manager.retry_job(job.job_id, "same_tab")
        resume_job = await manager.resume_job(job.job_id, "same_tab")
        assert retry_job.job_id != job.job_id
        assert resume_job.job_id != job.job_id
        assert manager.get_job_snapshot(retry_job.job_id).retry_of_job_id == job.job_id
        assert manager.get_job_snapshot(resume_job.job_id).retry_of_job_id == job.job_id
        await manager.shutdown()

    asyncio.run(scenario())


def test_list_conversations_refreshes_tab_binding(tmp_path: Path, monkeypatch) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()
    manager = JobManager(worker=lambda *args, **kwargs: "unused")

    with SessionLocal() as session:
        conversation = Conversation(
            id="conv-1",
            provider="chatgpt",
            title="Pinned test",
            external_url="https://chatgpt.com/c/abc",
            external_conversation_id="abc",
            tab_alive=False,
        )
        session.add(conversation)
        session.commit()

    monkeypatch.setattr("app.job_manager.is_google_chrome_running", lambda: True)
    monkeypatch.setattr(
        "app.job_manager.list_google_chrome_tabs",
        lambda: [
            type(
                "Tab",
                (),
                {
                    "window_id": 1,
                    "tab_id": 2,
                    "url": "https://chatgpt.com/c/abc",
                    "title": "ChatGPT",
                    "active": True,
                },
            )()
        ],
    )

    conversations = manager.list_conversations()
    assert conversations[0].tab_alive is True


def test_pin_conversation_updates_state(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()
    manager = JobManager(worker=lambda *args, **kwargs: "unused")

    async def scenario() -> None:
        created = await manager.create_job("first", start_new_chat=True)
        response = manager.set_conversation_pinned(created.conversation_id, True)
        assert response.pinned is True
        response = manager.set_conversation_pinned(created.conversation_id, False)
        assert response.pinned is False

    asyncio.run(scenario())


def test_tab_lost_error_marks_conversation_tab_dead(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()

    def tab_lost_worker(job_id: str, job_request, runtime=None, app_settings=None) -> str:
        runtime.remember_conversation_state(
            type(
                "TabInfo",
                (),
                {
                    "window_id": 7,
                    "tab_id": 8,
                    "url": "https://chatgpt.com/c/example",
                    "title": "ChatGPT",
                },
            )()
        )
        runtime.save_error("tab missing", "failed", "tab_lost")
        raise RuntimeError("tab lost")

    async def scenario() -> None:
        manager = JobManager(worker=tab_lost_worker)
        await manager.start()
        job = await manager.create_job("original", provider="chatgpt", start_new_chat=True)
        await asyncio.wait_for(manager.queue.join(), timeout=5)
        conversation = manager.get_conversation(job.conversation_id)
        await manager.shutdown()
        assert conversation.tab_alive is False
        assert conversation.external_url == "https://chatgpt.com/c/example"

    asyncio.run(scenario())


def test_remember_conversation_state_preserves_specific_chat_url(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()
    manager = JobManager(worker=lambda *args, **kwargs: "unused")
    specific_url = "https://chatgpt.com/g/custom/c/conversation-123"

    with SessionLocal() as session:
        session.add(
            Conversation(
                id="conv-specific-url",
                provider="chatgpt",
                title="Specific URL",
                external_url=specific_url,
                external_conversation_id="conversation-123",
            )
        )
        session.commit()

    manager._remember_conversation_state(
        "conv-specific-url",
        type(
            "TabInfo",
            (),
            {
                "window_id": 0,
                "tab_id": 0,
                "window_key": "linux-devtools",
                "target_id": "target-1",
                "url": "https://chatgpt.com/g/custom",
            },
        )(),
    )

    assert manager.get_conversation("conv-specific-url").external_url == specific_url


def test_start_recovers_stale_running_jobs(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()

    with SessionLocal() as session:
        conversation = Conversation(
            id="conv-stale",
            provider="gemini",
            title="Stale",
        )
        job = Job(
            id="job-stale",
            question="hello",
            status="waiting_for_response",
            provider="gemini",
            conversation_id="conv-stale",
            conversation_title="Stale",
        )
        session.add(conversation)
        session.add(job)
        session.commit()

    async def scenario() -> None:
        manager = JobManager(worker=lambda *args, **kwargs: "unused")
        await manager.start()
        snapshot = manager.get_job_snapshot("job-stale")
        await manager.shutdown()
        assert snapshot.status == "failed"
        assert snapshot.error_code == "response_timeout"
        assert snapshot.recoverable is True
        assert "restarted" in (snapshot.error_message or "")

    asyncio.run(scenario())


def test_agent_jobs_persist_resolved_config_and_steps(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()
    app_settings = replace(
        settings,
        agent_allowed_workspace_roots=(tmp_path.resolve(),),
        chatgpt_agent_url="https://chatgpt.com/g/g-agent",
    )

    def fake_agent_worker(job_id: str, job_request, runtime=None, app_settings=None) -> str:
        step_id = runtime.start_agent_step(1, "step-1", "exec", '{"tool":"exec"}')
        runtime.finish_agent_step(step_id, "completed", '{"ok":true}', None)
        runtime.save_answer("agent-done")
        runtime.update_status("completed")
        return "agent-done"

    async def scenario() -> None:
        manager = JobManager(worker=fake_agent_worker, app_settings=app_settings)
        await manager.start()
        created = await manager.create_job(
            "fix tests",
            provider="chatgpt",
            mode="agent",
            start_new_chat=True,
            agent_options=AgentOptions(workspace=str(tmp_path)),
        )
        await asyncio.wait_for(manager.queue.join(), timeout=5)
        snapshot = manager.get_job_snapshot(created.job_id)
        steps = manager.list_agent_steps(created.job_id)
        await manager.shutdown()

        assert snapshot.status == "completed"
        assert snapshot.agent_workspace == str(tmp_path.resolve())
        assert snapshot.agent_step_count == 1
        assert snapshot.agent_execution_backend == app_settings.agent_execution_backend
        assert len(steps) == 1
        assert steps[0].command_id == "step-1"

    asyncio.run(scenario())


def test_agent_retry_and_resume_continue_the_same_conversation(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()
    app_settings = replace(
        settings,
        agent_allowed_workspace_roots=(tmp_path.resolve(),),
        chatgpt_agent_url="https://chatgpt.com/g/g-agent",
    )

    def fake_agent_worker(job_id: str, job_request, runtime=None, app_settings=None) -> str:
        runtime.save_answer("agent-failed")
        runtime.save_error("need retry", "failed", "agent_protocol_error")
        raise RuntimeError("agent-failed")

    async def scenario() -> None:
        manager = JobManager(worker=fake_agent_worker, app_settings=app_settings)
        await manager.start()
        created = await manager.create_job(
            "fix tests",
            provider="chatgpt",
            mode="agent",
            start_new_chat=True,
            agent_options=AgentOptions(workspace=str(tmp_path)),
        )
        await asyncio.wait_for(manager.queue.join(), timeout=5)
        retry_job = await manager.retry_job(created.job_id, "same_tab")
        resume_job = await manager.resume_job(
            created.job_id,
            "new_tab_same_conversation",
        )
        await manager.shutdown()
        assert retry_job.job_id != created.job_id
        assert retry_job.conversation_id == created.conversation_id
        assert resume_job.conversation_id == created.conversation_id
        retry_snapshot = manager.get_job_snapshot(retry_job.job_id)
        resume_snapshot = manager.get_job_snapshot(resume_job.job_id)
        assert retry_snapshot.start_new_chat is False
        assert retry_snapshot.run_strategy == "same_tab"
        assert resume_snapshot.start_new_chat is False
        assert resume_snapshot.run_strategy == "new_tab_same_conversation"

    asyncio.run(scenario())


def test_start_marks_abandoned_queue_recoverable_instead_of_waiting_forever(tmp_path: Path) -> None:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()
    with SessionLocal() as session:
        session.add(Job(id='abandoned-queue',question='pending',provider='gemini',mode='chat',status='queued',start_new_chat=True))
        session.commit()
    async def scenario():
        manager = JobManager(worker=lambda *a, **k: "unused")
        await manager.start()
        try:
            snapshot = manager.get_job_snapshot('abandoned-queue')
            assert snapshot.status == 'failed'
            assert snapshot.recoverable is True
        finally:
            await manager.shutdown()
    asyncio.run(scenario())
