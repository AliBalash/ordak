from __future__ import annotations

from pathlib import Path

from app.automation.existing_chrome import ChromeTabRef
from app.providers.existing_chrome import ExistingChromeProviderAdapter


def test_extract_image_result_prefers_download_stage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = ExistingChromeProviderAdapter("chatgpt")
    saved = tmp_path / "downloaded.png"
    saved.write_bytes(b"image")
    strategies: list[str] = []

    monkeypatch.setattr(adapter, "_wait_for_generated_image_ready", lambda tab, timeout_ms: True)
    monkeypatch.setattr(
        adapter,
        "_inspect_generated_image_state",
        lambda tab: {"generatedMarker": True, "downloadAffordance": True},
    )

    def fake_export(tab, *, output_dir, job_id, timeout_ms, max_images, strategy):
        strategies.append(strategy)
        return [saved] if strategy == "download" else []

    monkeypatch.setattr(adapter, "_export_generated_images", fake_export)

    result = adapter.extract_image_result(
        ChromeTabRef(window_id=1, tab_id=1),
        output_dir=tmp_path,
        job_id="job-1",
        timeout_ms=10_000,
        max_images=4,
    )

    assert strategies == ["download"]
    assert result.source == "download"
    assert result.confidence == "high"
    assert result.artifacts == [saved]
    assert result.is_acceptable is True


def test_extract_image_result_falls_back_to_asset_url(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = ExistingChromeProviderAdapter("gemini")
    saved = tmp_path / "asset.png"
    saved.write_bytes(b"image")
    strategies: list[str] = []

    monkeypatch.setattr(adapter, "_wait_for_generated_image_ready", lambda tab, timeout_ms: True)
    monkeypatch.setattr(
        adapter,
        "_inspect_generated_image_state",
        lambda tab: {"generatedMarker": False, "downloadAffordance": False},
    )

    def fake_export(tab, *, output_dir, job_id, timeout_ms, max_images, strategy):
        strategies.append(strategy)
        if strategy == "asset_url":
            return [saved]
        return []

    monkeypatch.setattr(adapter, "_export_generated_images", fake_export)

    result = adapter.extract_image_result(
        ChromeTabRef(window_id=1, tab_id=1),
        output_dir=tmp_path,
        job_id="job-2",
        timeout_ms=10_000,
        max_images=4,
    )

    assert strategies == ["download", "asset_url"]
    assert result.source == "asset_url"
    assert result.confidence == "medium"
    assert result.artifacts == [saved]
    assert result.is_acceptable is True


def test_extract_image_result_rejects_low_confidence_dom_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = ExistingChromeProviderAdapter("chatgpt")
    saved = tmp_path / "dom.png"
    saved.write_bytes(b"image")
    strategies: list[str] = []

    monkeypatch.setattr(adapter, "_wait_for_generated_image_ready", lambda tab, timeout_ms: True)
    monkeypatch.setattr(
        adapter,
        "_inspect_generated_image_state",
        lambda tab: {"generatedMarker": False, "downloadAffordance": False},
    )

    def fake_export(tab, *, output_dir, job_id, timeout_ms, max_images, strategy):
        strategies.append(strategy)
        if strategy == "dom":
            return [saved]
        return []

    monkeypatch.setattr(adapter, "_export_generated_images", fake_export)

    result = adapter.extract_image_result(
        ChromeTabRef(window_id=1, tab_id=1),
        output_dir=tmp_path,
        job_id="job-3",
        timeout_ms=10_000,
        max_images=4,
    )

    assert strategies == ["download", "asset_url", "dom"]
    assert result.source == "dom"
    assert result.confidence == "low"
    assert result.artifacts == [saved]
    assert result.is_acceptable is False


def test_extract_image_result_returns_low_confidence_when_render_never_stabilizes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = ExistingChromeProviderAdapter("gemini")
    called = False

    monkeypatch.setattr(adapter, "_wait_for_generated_image_ready", lambda tab, timeout_ms: False)
    monkeypatch.setattr(
        adapter,
        "_inspect_generated_image_state",
        lambda tab: {"generatedMarker": True, "downloadAffordance": True},
    )

    def fake_export(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(adapter, "_export_generated_images", fake_export)

    result = adapter.extract_image_result(
        ChromeTabRef(window_id=1, tab_id=1),
        output_dir=tmp_path,
        job_id="job-4",
        timeout_ms=10_000,
        max_images=4,
    )

    assert called is False
    assert result.artifacts == []
    assert result.source == "dom"
    assert result.confidence == "low"
    assert result.is_acceptable is False


def test_wait_for_response_uses_full_timeout_window(monkeypatch) -> None:
    adapter = ExistingChromeProviderAdapter("gemini")
    captured: dict[str, object] = {}

    def fake_wait(tab, *, timeout_ms, stable_seconds, excluded_text, expect_images, provider, should_cancel=None):
        captured.update(
            timeout_ms=timeout_ms,
            stable_seconds=stable_seconds,
            excluded_text=excluded_text,
            expect_images=expect_images,
            provider=provider,
            cancelled=bool(should_cancel and should_cancel()),
        )
        return "final answer"

    monkeypatch.setattr("app.providers.existing_chrome.wait_for_response_stable", fake_wait)

    answer = adapter.wait_for_response(
        ChromeTabRef(window_id=1, tab_id=1),
        timeout_ms=240_000,
        stable_seconds=4,
        excluded_text="سلام",
        expect_images=False,
        should_cancel=lambda: False,
    )

    assert answer == "final answer"
    assert captured == {
        "timeout_ms": 240_000,
        "stable_seconds": 4,
        "excluded_text": "سلام",
        "expect_images": False,
        "provider": "gemini",
        "cancelled": False,
    }


def test_wait_for_response_stops_provider_on_cancel(monkeypatch) -> None:
    adapter = ExistingChromeProviderAdapter("gemini")
    stop_calls = 0

    def fake_wait(*args, **kwargs):
        raise TimeoutError("__ORD_CANCELLED__")

    def fake_stop(tab):
        nonlocal stop_calls
        stop_calls += 1
        return True

    monkeypatch.setattr("app.providers.existing_chrome.wait_for_response_stable", fake_wait)
    monkeypatch.setattr(adapter, "best_effort_stop", fake_stop)

    try:
        adapter.wait_for_response(
            ChromeTabRef(window_id=1, tab_id=1),
            timeout_ms=240_000,
            stable_seconds=4,
            excluded_text="سلام",
            expect_images=False,
            should_cancel=lambda: True,
        )
    except TimeoutError as exc:
        assert str(exc) == "__ORD_CANCELLED__"
    else:
        raise AssertionError("TimeoutError was expected")

    assert stop_calls == 1
