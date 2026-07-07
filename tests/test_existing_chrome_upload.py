from __future__ import annotations

import json
from pathlib import Path

from app.automation.existing_chrome import (
    ChromeTabRef,
    upload_local_file,
    wait_for_generated_image_ready,
)


def test_upload_local_file_waits_until_loading_clears(monkeypatch, tmp_path: Path) -> None:
    upload_path = tmp_path / "sample-image-test.png"
    upload_path.write_bytes(b"fake-image")

    calls = {
        "status_polls": 0,
        "readiness_polls": 0,
        "marked_done": 0,
    }

    def fake_execute_javascript(tab: ChromeTabRef, javascript: str) -> str:
        assert tab == ChromeTabRef(window_id=1, tab_id=2)
        if 'return "started"' in javascript:
            return "started"
        if "window.__codexUploadChunks = []" in javascript:
            return "ok"
        if "window.__codexUploadChunks.push" in javascript:
            return "ok"
        if 'window.__codexUploadStatus || "pending"' in javascript:
            calls["status_polls"] += 1
            return "attached"
        if "attachment:" in javascript and "hasPreview" in javascript:
            calls["readiness_polls"] += 1
            loading = calls["readiness_polls"] < 3
            return json.dumps(
                {
                    "attachment": True,
                    "loading": loading,
                    "hasPreview": True,
                }
            )
        if 'window.__codexUploadStatus = "done"' in javascript:
            calls["marked_done"] += 1
            return "ok"
        raise AssertionError(f"Unexpected javascript probe: {javascript[:120]}")

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute_javascript)
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _: None)

    upload_local_file(
        ChromeTabRef(window_id=1, tab_id=2),
        file_path=upload_path,
        file_name="sample-image-test.png",
        mime_type="image/png",
        timeout_ms=5_000,
    )

    assert calls["status_polls"] >= 1
    assert calls["readiness_polls"] >= 4
    assert calls["marked_done"] == 1


def test_wait_for_generated_image_ready_requires_stable_ready_state(monkeypatch) -> None:
    states = iter(
        [
            {"assistantImageCount": 0, "loading": True, "busy": True},
            {"assistantImageCount": 1, "loading": False, "busy": False},
            {"assistantImageCount": 1, "loading": False, "busy": False},
        ]
    )

    monkeypatch.setattr(
        "app.automation.existing_chrome.inspect_generated_image_state",
        lambda tab, provider="gemini": next(states),
    )
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _: None)

    assert wait_for_generated_image_ready(
        ChromeTabRef(window_id=1, tab_id=2),
        timeout_ms=5_000,
    )
