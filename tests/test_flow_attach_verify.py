"""Slow backends must not fail an attachment that actually landed.

Regression: the frame-slot and ingredient-chip verifications polled a fixed 8
times (~16s). On a slow night the picked asset rendered into the slot/chips
after the polls gave up, killing a run whose attachment had actually
succeeded. Both verifications are deadline-driven now with identical error
contracts.
"""
from __future__ import annotations

import pytest

from app.automation import flow_references as fr
from app.errors import ErrorCode, OrdaKError


@pytest.fixture
def fast_clock(monkeypatch) -> None:
    """Deadline loops run instantly but still observe their time budget."""
    now = [1000.0]

    def fake_sleep(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(fr.time, "sleep", fake_sleep)
    monkeypatch.setattr(fr.time, "monotonic", lambda: now[0])


def test_frame_slot_filling_late_still_returns_src(monkeypatch, tmp_path, fast_clock) -> None:
    source = tmp_path / "frame.png"
    source.write_bytes(b"fake-png")
    reads = {"count": 0}

    def fake_slots(tab):
        reads["count"] += 1
        filled = reads["count"] >= 3
        return [
            {"filled": filled, "src": "https://flow-content.google/image/x" if filled else None, "x": 1.0, "y": 2.0},
            {"filled": False, "src": None, "x": 3.0, "y": 4.0},
        ]

    monkeypatch.setattr(fr, "read_frame_slots", fake_slots)
    monkeypatch.setattr(fr, "dispatch_mouse_click", lambda tab, x, y: None)
    monkeypatch.setattr(fr, "_wait_for_picker", lambda tab: None)
    monkeypatch.setattr(fr, "_select_uploaded_asset", lambda tab, name: None)
    monkeypatch.setattr(fr, "close_picker", lambda tab: (_ for _ in ()).throw(AssertionError("picker must not close on success")))

    assert fr.attach_frame(object(), "first_frame", source) == "https://flow-content.google/image/x"


def test_frame_slot_never_filling_fails_with_same_contract(monkeypatch, tmp_path, fast_clock) -> None:
    source = tmp_path / "frame.png"
    source.write_bytes(b"fake-png")
    monkeypatch.setattr(fr, "read_frame_slots", lambda tab: [{"filled": False, "src": None, "x": 1.0, "y": 2.0}, {"filled": False, "src": None, "x": 3.0, "y": 4.0}])
    monkeypatch.setattr(fr, "dispatch_mouse_click", lambda tab, x, y: None)
    monkeypatch.setattr(fr, "_wait_for_picker", lambda tab: None)
    monkeypatch.setattr(fr, "_select_uploaded_asset", lambda tab, name: None)
    monkeypatch.setattr(fr, "close_picker", lambda tab: None)
    with pytest.raises(OrdaKError) as excinfo:
        fr.attach_frame(object(), "first_frame", source)
    assert excinfo.value.code is ErrorCode.FLOW_FRAME_UPLOAD_FAILED
    assert "in its first_frame slot after upload" in excinfo.value.message


def test_ingredient_chip_appearing_late_still_returns_src(monkeypatch, tmp_path, fast_clock) -> None:
    source = tmp_path / "ref.png"
    source.write_bytes(b"fake-png")
    reads = {"count": 0}

    def fake_composer(tab):
        reads["count"] += 1
        chips = [{"src": "https://flow-content.google/image/y"}] if reads["count"] >= 4 else []
        return {"add": {"x": 1.0, "y": 2.0}, "chips": chips}

    monkeypatch.setattr(fr, "read_composer", fake_composer)
    monkeypatch.setattr(fr, "dispatch_mouse_click", lambda tab, x, y: None)
    monkeypatch.setattr(fr, "_wait_for_picker", lambda tab: None)
    monkeypatch.setattr(fr, "ensure_file_input", lambda tab, runtime=None: None)
    monkeypatch.setattr(fr, "set_file_input_files", lambda tab, selector, paths: None)
    monkeypatch.setattr(fr, "_select_uploaded_asset", lambda tab, name: None)
    monkeypatch.setattr(fr, "close_picker", lambda tab: (_ for _ in ()).throw(AssertionError("picker must not close on success")))

    assert fr.attach_ingredient(object(), "character_sheet", source) == "https://flow-content.google/image/y"


def test_ingredient_chip_never_appearing_fails_with_same_contract(monkeypatch, tmp_path, fast_clock) -> None:
    source = tmp_path / "ref.png"
    source.write_bytes(b"fake-png")
    monkeypatch.setattr(fr, "read_composer", lambda tab: {"add": {"x": 1.0, "y": 2.0}, "chips": []})
    monkeypatch.setattr(fr, "dispatch_mouse_click", lambda tab, x, y: None)
    monkeypatch.setattr(fr, "_wait_for_picker", lambda tab: None)
    monkeypatch.setattr(fr, "ensure_file_input", lambda tab, runtime=None: None)
    monkeypatch.setattr(fr, "set_file_input_files", lambda tab, selector, paths: None)
    monkeypatch.setattr(fr, "_select_uploaded_asset", lambda tab, name: None)
    monkeypatch.setattr(fr, "close_picker", lambda tab: None)
    with pytest.raises(OrdaKError) as excinfo:
        fr.attach_ingredient(object(), "character_sheet", source)
    assert excinfo.value.code is ErrorCode.FLOW_UPLOAD_FAILED
    assert "as an attached reference after upload" in excinfo.value.message
