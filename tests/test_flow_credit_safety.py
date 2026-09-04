"""A restart in the middle of a paid Flow job must reconcile, never re-Generate (§22, §80).

Credits are spent on the Generate click, so the pending marker written just before it is
the only thing standing between a crash and a double charge. These tests drive
``_reconcile_pending`` through every state a restart can leave behind.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.automation import flow_worker
from app.automation.existing_chrome import ChromeTabRef
from app.automation.flow_worker import PendingSubmission
from app.errors import ErrorCode, OrdaKError

TAB = ChromeTabRef(window_id=1, tab_id=2)
FINGERPRINT = "fp-" + "a" * 16


def _pending(tmp_path: Path, **overrides) -> Path:
    payload = PendingSubmission(
        fingerprint=FINGERPRINT,
        job_id="job-1",
        workspace_url="https://labs.google/fx/tools/flow/project/abc",
        prompt_sha256="b" * 64,
        results_before=2,
        results_media=["https://x/media.getMediaUrlRedirect?name=old1",
                       "https://x/media.getMediaUrlRedirect?name=old2"],
        submitted_at=1.0,
    ).to_dict()
    payload.update(overrides)
    marker = tmp_path / flow_worker.PENDING_MARKER
    marker.write_text(json.dumps(payload), encoding="utf-8")
    return marker


def _media(monkeypatch: pytest.MonkeyPatch, urls: list[str]) -> None:
    monkeypatch.setattr(flow_worker, "_result_media", lambda tab: list(urls))


def test_no_marker_means_nothing_to_reconcile(tmp_path: Path) -> None:
    assert flow_worker._reconcile_pending(TAB, tmp_path, FINGERPRINT, None) is None


def test_a_finished_video_on_disk_finishes_the_job_and_clears_the_marker(tmp_path: Path) -> None:
    marker = _pending(tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 32)

    result = flow_worker._reconcile_pending(TAB, tmp_path, FINGERPRINT, None)

    assert result == video
    assert not marker.exists(), "a reconciled marker must not trip the next run"


def test_a_new_result_is_downloaded_instead_of_regenerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pending(tmp_path)
    fresh = "https://x/media.getMediaUrlRedirect?name=new"
    _media(monkeypatch, [
        "https://x/media.getMediaUrlRedirect?name=old1",
        "https://x/media.getMediaUrlRedirect?name=old2",
        fresh,
    ])
    downloads: list[str | None] = []

    def _download(tab, output_dir, runtime, media_url, *, job_id):
        downloads.append(media_url)
        target = Path(output_dir) / "recovered.mp4"
        target.write_bytes(b"\x00" * 32)
        return target

    monkeypatch.setattr(flow_worker, "_download_flow_video", _download)

    result = flow_worker._reconcile_pending(TAB, tmp_path, FINGERPRINT, None)

    assert result is not None and result.name == "recovered.mp4"
    assert downloads == [fresh], "the newly appeared asset is the one to fetch"


def test_no_result_and_no_file_stops_for_a_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pending(tmp_path)
    _media(monkeypatch, [
        "https://x/media.getMediaUrlRedirect?name=old1",
        "https://x/media.getMediaUrlRedirect?name=old2",
    ])
    monkeypatch.setattr(
        flow_worker,
        "_download_flow_video",
        lambda *a, **k: pytest.fail("a blind retry would spend credits twice"),
    )

    with pytest.raises(OrdaKError) as excinfo:
        flow_worker._reconcile_pending(TAB, tmp_path, FINGERPRINT, None)
    assert excinfo.value.code is ErrorCode.FLOW_RECONCILIATION_REQUIRED


def test_a_marker_from_a_different_request_stops_for_a_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Different fingerprint means the unaccounted submission was not this one."""
    _pending(tmp_path, fingerprint="fp-something-else")
    _media(monkeypatch, ["https://x/media.getMediaUrlRedirect?name=whatever"] * 5)
    monkeypatch.setattr(
        flow_worker,
        "_download_flow_video",
        lambda *a, **k: pytest.fail("must not download against a mismatched fingerprint"),
    )

    with pytest.raises(OrdaKError) as excinfo:
        flow_worker._reconcile_pending(TAB, tmp_path, FINGERPRINT, None)
    assert excinfo.value.code is ErrorCode.FLOW_RECONCILIATION_REQUIRED


def test_an_unreadable_marker_is_treated_as_absent(tmp_path: Path) -> None:
    (tmp_path / flow_worker.PENDING_MARKER).write_text("{not json", encoding="utf-8")
    assert flow_worker._reconcile_pending(TAB, tmp_path, FINGERPRINT, None) is None


def test_the_marker_is_written_before_generate_and_carries_the_baseline(tmp_path: Path) -> None:
    pending = PendingSubmission(
        fingerprint=FINGERPRINT,
        job_id="job-9",
        workspace_url="https://labs.google/fx/tools/flow/project/abc",
        prompt_sha256="c" * 64,
        reference_sha256={"first_frame": "d" * 64},
        settings={"model": "gemini_omni_1_1_flash", "duration": "4s", "outputs": "x1"},
        results_before=3,
        results_media=["https://x/media.getMediaUrlRedirect?name=a"],
        submitted_at=123.0,
    )
    flow_worker._record_pending(tmp_path, pending)

    written = json.loads((tmp_path / flow_worker.PENDING_MARKER).read_text(encoding="utf-8"))
    assert written["fingerprint"] == FINGERPRINT
    assert written["results_before"] == 3
    assert written["reference_sha256"] == {"first_frame": "d" * 64}
    assert written["settings"]["outputs"] == "x1", "outputs must be recorded: x2 doubles the cost"

    flow_worker._clear_pending(tmp_path)
    assert not (tmp_path / flow_worker.PENDING_MARKER).exists()
