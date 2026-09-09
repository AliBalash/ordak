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
    scripts: list[str] = []

    def fake_execute_javascript(tab: ChromeTabRef, javascript: str) -> str:
        scripts.append(javascript)
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
    assert "for (const existing of Array.from(input.files || []))" in "\n".join(scripts)


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


def test_chatgpt_upload_does_not_treat_an_empty_composer_as_upload_loading(monkeypatch) -> None:
    """Send is disabled until text exists; that must not invalidate a real file thumbnail."""
    captured: dict[str, str] = {}

    def fake_execute(_tab: ChromeTabRef, javascript: str) -> str:
        captured["probe"] = javascript
        return '{"attachment":true,"attachmentCount":1,"loading":false,"hasPreview":true,"submitReady":false}'

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)
    from app.automation.existing_chrome import inspect_upload_state

    state = inspect_upload_state(ChromeTabRef(window_id=1, tab_id=2), provider="chatgpt")
    assert state["attachment"] and state["hasPreview"] and not state["loading"]
    assert 'send-button"][disabled]' not in captured["probe"]


def test_chatgpt_upload_targets_the_composer_input_without_menu_clicks(monkeypatch, tmp_path: Path) -> None:
    image = tmp_path / "reference.png"
    image.write_bytes(b"image")
    scripts: list[str] = []

    def fake_execute(_tab: ChromeTabRef, javascript: str) -> str:
        scripts.append(javascript)
        if 'return "started"' in javascript:
            return "started"
        if "window.__codexUploadChunks = []" in javascript or "window.__codexUploadChunks.push" in javascript:
            return "ok"
        if 'window.__codexUploadStatus || "pending"' in javascript:
            return "attached"
        if "attachment:" in javascript and "hasPreview" in javascript:
            return '{"attachment":true,"loading":false,"hasPreview":true}'
        if 'window.__codexUploadStatus = "done"' in javascript:
            return "ok"
        raise AssertionError(javascript[:120])

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _: None)
    upload_local_file(
        ChromeTabRef(window_id=1, tab_id=2),
        file_path=image,
        file_name=image.name,
        mime_type="image/png",
        provider="chatgpt",
        timeout_ms=5_000,
    )

    combined = "\n".join(scripts)
    assert "document.querySelector('#upload-files')" in combined
    assert "composer-plus-btn')?.click" not in combined


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
        return '{"open": true, "label": "Extra High, 4 of 5", "value": "3", "maximum": "4"}'

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)
    ensure_chatgpt_high_effort(ChromeTabRef(window_id=1, tab_id=2))
    assert len(seen) == 1
    assert "data-model-reasoning-effort-slider" in seen[0]


def test_chatgpt_prefers_extra_high_uses_the_visible_composer_control(monkeypatch) -> None:
    from app.automation.existing_chrome import ensure_chatgpt_preferred_effort

    states = iter([
        {"open": False, "label": "", "value": "", "maximum": "", "pill": True},
        {"open": True, "label": "Pro, 5 of 5", "value": "4", "maximum": "4", "pill": True},
        {"open": True, "label": "Pro, 5 of 5", "value": "4", "maximum": "4", "pill": True},
        {"open": True, "label": "Extra High, 4 of 5", "value": "3", "maximum": "4", "pill": True},
    ])
    seen: list[str] = []

    def fake_execute(tab, javascript):
        seen.append(javascript)
        if "return JSON.stringify({" in javascript:
            return json.dumps(next(states))
        if "return \"opening\"" in javascript:
            return "opening"
        if "ArrowLeft" in javascript:
            return "stepped"
        raise AssertionError(javascript[:100])

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _: None)
    ensure_chatgpt_preferred_effort(ChromeTabRef(window_id=1, tab_id=2))
    assert any("thinking effort" in script.lower() for script in seen)
    assert any("ArrowLeft" in script for script in seen)


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
