from __future__ import annotations

import json
from pathlib import Path

from app.automation.existing_chrome import (
    ChromeTabRef,
    insert_prompt,
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


def test_upload_local_file_accepts_awaiting_ack_when_dom_readiness_catches_up(
    monkeypatch, tmp_path: Path
) -> None:
    upload_path = tmp_path / "api-style-upload-name-sample-image-test.png"
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
            return "awaiting-ack"
        if "attachment:" in javascript and "hasPreview" in javascript:
            calls["readiness_polls"] += 1
            ready = calls["readiness_polls"] >= 2
            return json.dumps(
                {
                    "attachment": ready,
                    "loading": False,
                    "hasPreview": ready,
                    "submitReady": ready,
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
        file_name=upload_path.name,
        mime_type="image/png",
        timeout_ms=5_000,
    )

    assert calls["status_polls"] >= 1
    assert calls["readiness_polls"] >= 2
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


def test_insert_prompt_fallback_avoids_trusted_html_innerhtml(monkeypatch) -> None:
    scripts: list[str] = []

    def fake_execute_javascript(tab: ChromeTabRef, javascript: str) -> str:
        scripts.append(javascript)
        assert 'target.innerHTML = ""' not in javascript
        if "reasoning effort" in javascript:
            return "high"
        if "target.replaceChildren();" in javascript:
            return "ok"
        return "ok"

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute_javascript)

    insert_prompt(
        ChromeTabRef(window_id=1, tab_id=2),
        "hello world",
        provider="gemini",
    )

    assert any("target.replaceChildren();" in script for script in scripts)


def test_chatgpt_high_effort_is_verified_before_prompt(monkeypatch) -> None:
    from app.automation.existing_chrome import ensure_chatgpt_high_effort

    seen = []

    def fake_execute(tab, javascript):
        seen.append(javascript)
        return "high"

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)
    ensure_chatgpt_high_effort(ChromeTabRef(window_id=1, tab_id=2))
    assert len(seen) == 1
    assert "reasoning effort" in seen[0]


def test_chatgpt_high_effort_uses_the_visible_composer_control(monkeypatch) -> None:
    from app.automation.existing_chrome import ensure_chatgpt_high_effort

    replies = iter(["opened", "selected", "high"])
    seen: list[str] = []

    def fake_execute(tab, javascript):
        seen.append(javascript)
        return next(replies)

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _: None)
    ensure_chatgpt_high_effort(ChromeTabRef(window_id=1, tab_id=2))
    assert "trigger.dispatchEvent" in seen[0]
    assert "trigger.el" not in seen[0]


def test_chatgpt_submit_accepts_busy_state_without_duplicate_retry(monkeypatch) -> None:
    from app.automation.existing_chrome import submit_prompt

    calls = {"submit": 0, "confirm": 0}

    def fake_execute(tab, javascript):
        if "[data-message-author-role=\"user\"]" in javascript and "const busy = Array.from" not in javascript:
            return "0"
        if "const busy = Array.from" in javascript:
            calls["confirm"] += 1
            return '{"promptEmpty":false,"userCount":0,"busy":true}'
        if "const sendButton" in javascript:
            calls["submit"] += 1
            return "clicked"
        raise AssertionError(javascript[:100])

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _: None)
    submit_prompt(ChromeTabRef(window_id=1, tab_id=2), provider="chatgpt")
    assert calls == {"submit": 1, "confirm": 1}
