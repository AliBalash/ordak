from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.database import configure_database, init_db
from app.job_manager import JobManager
from app.main import create_app


def _fake_worker(job_id: str, job_request, runtime=None, app_settings=None) -> str:
    runtime.update_status("opening_provider_tab")
    runtime.append_log(f"working:{job_request.question}")
    output_path = settings.browser_output_dir / f"{job_id}.png"
    output_path.write_bytes(b"fake-output")
    if job_request.mode == "image_generate":
        runtime.attach_output_image(output_path)
        runtime.save_answer("Gemini generated image output in the current Chrome tab. Saved images: 1.")
    else:
        runtime.save_answer(f"answer:{job_request.question}")
    runtime.update_status("completed")
    return runtime.answer if hasattr(runtime, "answer") else "done"


def create_test_client(tmp_path: Path) -> TestClient:
    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()
    app = create_app(JobManager(worker=_fake_worker))
    return TestClient(app)


def create_agent_test_client(tmp_path: Path) -> TestClient:
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
        runtime.save_answer("agent-complete")
        runtime.update_status("completed")
        return "agent-complete"

    app = create_app(JobManager(worker=fake_agent_worker, app_settings=app_settings))
    return TestClient(app)


def test_root_and_health_routes(tmp_path: Path) -> None:
    with create_test_client(tmp_path) as client:
        assert client.get("/").status_code == 200
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"


def test_job_routes_and_diagnostics(tmp_path: Path) -> None:
    with create_test_client(tmp_path) as client:
        created = client.post(
            "/api/jobs",
            json={
                "question": "سلام",
                "provider": "gemini",
                "mode": "chat",
                "start_new_chat": True,
            },
        )
        assert created.status_code == 200
        payload = created.json()
        job_id = payload["job_id"]
        conversation_id = payload["conversation_id"]

        deadline = time.time() + 3
        final = None
        while time.time() < deadline:
            final = client.get(f"/api/jobs/{job_id}")
            if final.json()["status"] == "completed":
                break
            time.sleep(0.05)

        assert final is not None
        assert final.status_code == 200
        assert final.json()["status"] == "completed"
        assert client.get(f"/api/conversations/{conversation_id}").status_code == 200
        assert client.post(f"/api/conversations/{conversation_id}/pin").status_code == 200
        assert client.post(f"/api/conversations/{conversation_id}/unpin").status_code == 200
        assert client.get("/api/diagnostics").status_code == 200
        assert client.get("/api/diagnostics/storage").status_code == 200
        assert client.get("/diagnostics").status_code == 200


def test_provider_direct_response_json_endpoint(tmp_path: Path) -> None:
    with create_test_client(tmp_path) as client:
        response = client.post(
            "/api/gemini/respond",
            json={
                "question": "سلام",
                "mode": "chat",
                "start_new_chat": True,
                "wait_for_completion": True,
                "wait_timeout_seconds": 10,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["provider"] == "gemini"
        assert payload["status"] == "completed"
        assert payload["completed"] is True
        assert payload["answer"] == "answer:سلام"
        assert payload["job_api_url"].endswith(f"/api/jobs/{payload['job_id']}")
        assert payload["conversation_api_url"].endswith(f"/api/conversations/{payload['conversation_id']}")
        assert payload["output_images"] == []


def test_provider_direct_response_multipart_image_endpoint(tmp_path: Path) -> None:
    with create_test_client(tmp_path) as client:
        response = client.post(
            "/api/chatgpt/respond",
            data={
                "question": "پس‌زمینه را حذف کن.",
                "mode": "image_generate",
                "start_new_chat": "true",
                "wait_for_completion": "true",
                "wait_timeout_seconds": "10",
            },
            files={"image": ("sample.png", b"fake-image", "image/png")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["provider"] == "chatgpt"
        assert payload["status"] == "completed"
        assert payload["completed"] is True
        assert "Saved images: 1." in payload["answer"]
        assert len(payload["uploads"]) == 1
        assert len(payload["output_images"]) == 1
        assert payload["uploads"][0]["url"].endswith(payload["uploads"][0]["path"])
        assert payload["output_images"][0]["url"].endswith(payload["output_images"][0]["path"])


def test_profile_open_endpoint(tmp_path: Path, monkeypatch) -> None:
    with create_test_client(tmp_path) as client:
        monkeypatch.setattr(client.app.state.job_manager, "launch_profile_browser", lambda: True)
        response = client.post("/api/profile/open")
        assert response.status_code == 200
        assert response.json()["message"] == "Profile browser opened."


def test_cancel_retry_resume_endpoints(tmp_path: Path) -> None:
    def slow_worker(job_id: str, job_request, runtime=None, app_settings=None) -> str:
        runtime.update_status("waiting_for_response")
        deadline = time.time() + 0.5
        while time.time() < deadline:
            if runtime.should_cancel():
                runtime.save_error("Job cancelled.", "cancelled", None)
                raise RuntimeError("cancelled")
            time.sleep(0.05)
        runtime.save_error("boom", "failed", "response_timeout")
        raise RuntimeError("boom")

    configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db()
    app = create_app(JobManager(worker=slow_worker))
    with TestClient(app) as client:
        created = client.post(
            "/api/jobs",
            json={
                "question": "سلام",
                "provider": "gemini",
                "mode": "chat",
                "start_new_chat": True,
            },
        )
        job_id = created.json()["job_id"]
        time.sleep(0.2)
        cancelled = client.post(f"/api/jobs/{job_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] in {"cancelling", "cancelled"}
        time.sleep(0.7)
        retry = client.post(f"/api/jobs/{job_id}/retry", json={"strategy": "same_tab"})
        assert retry.status_code == 200
        resume = client.post(f"/api/jobs/{job_id}/resume", json={"strategy": "same_tab"})
        assert resume.status_code in {200, 409}
        time.sleep(1.2)


def test_agent_job_routes_and_steps_endpoint(tmp_path: Path) -> None:
    with create_agent_test_client(tmp_path) as client:
        created = client.post(
            "/api/jobs",
            json={
                "question": "Inspect and fix",
                "provider": "chatgpt",
                "mode": "agent",
                "start_new_chat": True,
                "agent": {
                    "workspace": str(tmp_path),
                },
            },
        )
        assert created.status_code == 200
        job_id = created.json()["job_id"]

        deadline = time.time() + 3
        final = None
        while time.time() < deadline:
            final = client.get(f"/api/jobs/{job_id}")
            if final.json()["status"] == "completed":
                break
            time.sleep(0.05)

        assert final is not None
        assert final.json()["agent_workspace"] == str(tmp_path.resolve())
        assert final.json()["agent_step_count"] == 1

        steps = client.get(f"/api/jobs/{job_id}/steps")
        assert steps.status_code == 200
        assert steps.json()["steps"][0]["command_id"] == "step-1"


def test_agent_mode_validation_errors(tmp_path: Path) -> None:
    with create_agent_test_client(tmp_path) as client:
        wrong_provider = client.post(
            "/api/jobs",
            json={
                "question": "Inspect and fix",
                "provider": "gemini",
                "mode": "agent",
                "start_new_chat": True,
                "agent": {"workspace": str(tmp_path)},
            },
        )
        assert wrong_provider.status_code == 422

        missing_agent = client.post(
            "/api/jobs",
            json={
                "question": "Inspect and fix",
                "provider": "chatgpt",
                "mode": "agent",
                "start_new_chat": True,
            },
        )
        assert missing_agent.status_code == 422

        multipart_agent = client.post(
            "/api/jobs",
            data={
                "question": "Inspect and fix",
                "provider": "chatgpt",
                "mode": "agent",
                "start_new_chat": "true",
            },
            files={"image": ("", b"", "application/octet-stream")},
        )
        assert multipart_agent.status_code == 422
