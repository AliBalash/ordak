"""A merely-selected picker row must be confirmed, never toggle-clicked to death.

Regression: Flow's picker row click can only *select* while a slow backend still
processes the attachment. The old code clicked the row three times on fixed
4.8s windows and then gave up with FLOW_UPLOAD_FAILED even though the row was
selected and the enabled ``Add to prompt`` confirmation would have finished
the job. The new code never re-clicks an already-active row and presses the
confirmation until the picker actually closes.
"""
from __future__ import annotations

import pytest

from app.automation import flow_references as fr
from app.errors import ErrorCode, OrdaKError


ROW = {"x": 10.0, "y": 20.0, "w": 100, "h": 20, "active": True}
CONFIRM = {"x": 30.0, "y": 40.0}


def test_active_row_is_confirmed_not_reclicked(monkeypatch) -> None:
    clicks: list[tuple[float, float]] = []
    state = {"closed": False, "confirm_clicks": 0}

    def fake_evaluate(tab, script):
        if state["closed"]:
            return {"open": False}
        return {
            "open": True,
            "row": dict(ROW),
            "confirm": dict(CONFIRM),
            "confirmEnabled": True,
        }

    def fake_click(tab, x, y):
        clicks.append((x, y))
        if (x, y) == (30.0, 40.0):
            state["confirm_clicks"] += 1
            if state["confirm_clicks"] >= 2:
                state["closed"] = True

    monkeypatch.setattr(fr, "_evaluate", fake_evaluate)
    monkeypatch.setattr(fr, "dispatch_mouse_click", fake_click)
    monkeypatch.setattr(fr.time, "sleep", lambda _seconds: None)

    fr._select_uploaded_asset(object(), "some_asset.png")

    assert clicks, "the confirmation was never pressed"
    assert all(click == (30.0, 40.0) for click in clicks), f"row clicks would toggle selection: {clicks}"


def test_inactive_row_is_clicked_once_before_confirm(monkeypatch) -> None:
    clicks: list[tuple[float, float]] = []
    state = {"closed": False, "row_clicks": 0}

    def fake_evaluate(tab, script):
        if state["closed"]:
            return {"open": False}
        return {
            "open": True,
            "row": {"x": 10.0, "y": 20.0, "w": 100, "h": 20, "active": state["row_clicks"] > 0},
            "confirm": dict(CONFIRM),
            "confirmEnabled": True,
        }

    def fake_click(tab, x, y):
        clicks.append((x, y))
        if (x, y) == (10.0, 20.0):
            state["row_clicks"] += 1
        else:
            state["closed"] = True

    monkeypatch.setattr(fr, "_evaluate", fake_evaluate)
    monkeypatch.setattr(fr, "dispatch_mouse_click", fake_click)
    monkeypatch.setattr(fr.time, "sleep", lambda _seconds: None)

    fr._select_uploaded_asset(object(), "some_asset.png")

    assert clicks[0] == (10.0, 20.0)
    assert clicks.count((10.0, 20.0)) == 1, f"row clicked more than once: {clicks}"
    assert (30.0, 40.0) in clicks


def test_missing_row_still_fails_closed_with_same_contract(monkeypatch) -> None:
    monkeypatch.setattr(fr, "_evaluate", lambda tab, script: {"open": True, "row": None})
    monkeypatch.setattr(fr.time, "sleep", lambda _seconds: None)
    with pytest.raises(OrdaKError) as excinfo:
        fr._select_uploaded_asset(object(), "ghost.png", timeout_s=0)
    assert excinfo.value.code is ErrorCode.FLOW_UPLOAD_FAILED
    assert "never offered the uploaded asset 'ghost.png'" in excinfo.value.message
