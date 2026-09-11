"""Gemini jobs fail closed unless the UI confirms Extended Thinking."""
from __future__ import annotations

import pytest

from app.automation import gemini_worker as gw
from app.errors import ErrorCode, OrdaKError


class Runtime:
    def __init__(self) -> None:
        self.logs: list[str] = []

    def append_log(self, message: str, level: str = "info") -> None:
        self.logs.append(message)


def test_extended_thinking_already_enabled_needs_no_click(monkeypatch) -> None:
    monkeypatch.setattr(
        gw, "_read_gemini_extended_thinking_state",
        lambda tab: {"extendedThinkingEnabled": True, "modeLabel": "Open mode picker, currently Flash Extended"},
    )
    result = gw._ensure_gemini_extended_thinking(object(), Runtime())
    assert result["label"].endswith("Flash Extended")


def test_extended_thinking_is_clicked_and_read_back(monkeypatch) -> None:
    states = iter([
        {"extendedThinkingEnabled": False, "modeLabel": "Open mode picker, currently Flash", "picker": {"x": 1, "y": 2}, "menuOpen": False},
        {"extendedThinkingEnabled": False, "modeLabel": "Open mode picker, currently Flash", "picker": {"x": 1, "y": 2}, "menuOpen": True, "thinkingItem": {"x": 3, "y": 4, "active": False}},
        {"extendedThinkingEnabled": True, "modeLabel": "Open mode picker, currently Flash Extended", "picker": {"x": 1, "y": 2}, "menuOpen": False},
    ])
    monkeypatch.setattr(gw, "_read_gemini_extended_thinking_state", lambda tab: next(states))
    clicks: list[tuple[float, float]] = []
    monkeypatch.setattr("app.automation.existing_chrome.dispatch_mouse_click", lambda tab, x, y: clicks.append((x, y)))
    monkeypatch.setattr(gw.time, "sleep", lambda _seconds: None)

    result = gw._ensure_gemini_extended_thinking(object(), Runtime())
    assert result["label"].endswith("Flash Extended")
    assert clicks == [(1.0, 2.0), (3.0, 4.0)]


def test_missing_extended_thinking_toggle_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        gw, "_read_gemini_extended_thinking_state",
        lambda tab: {"extendedThinkingEnabled": False, "modeLabel": "Open mode picker, currently Flash", "picker": None},
    )
    with pytest.raises(OrdaKError) as excinfo:
        gw._ensure_gemini_extended_thinking(object(), Runtime())
    assert excinfo.value.code is ErrorCode.MODEL_SELECTION_FAILED
