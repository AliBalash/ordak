"""Input events only reach a tab that is in front of its window.

Chrome answers ``Input.dispatchMouseEvent`` with an empty result even when the
target tab is backgrounded and the render widget throws the event away, so a
click on a background tab looks successful and does nothing. The pipeline keeps
ChatGPT, Gemini and Flow open at once, so every input batch must raise its own
tab first.
"""
from __future__ import annotations

import json

import pytest

from app.automation.existing_chrome import (
    ChromeTabInfo,
    ChromeTabRef,
    _linux_cdp_commands,
    dispatch_key,
    dispatch_mouse_click,
)


class FakeWebSocket:
    def __init__(self, sent: list[dict]) -> None:
        self._sent = sent

    def __enter__(self) -> "FakeWebSocket":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def send(self, payload: str) -> None:
        self._sent.append(json.loads(payload))

    def recv(self, *args, **kwargs) -> str:
        return json.dumps({"id": self._sent[-1]["id"], "result": {}})


@pytest.fixture()
def sent(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    monkeypatch.setattr(
        "app.automation.existing_chrome._linux_find_tab",
        lambda tab: ChromeTabInfo(
            window_id=0,
            tab_id=0,
            url="https://labs.google/fx/tools/flow/project/abc",
            title="Flow",
            window_key="linux-devtools",
            target_id="target-1",
            websocket_debugger_url="ws://127.0.0.1:9222/devtools/page/target-1",
        ),
    )
    monkeypatch.setattr("app.automation.existing_chrome._is_mac_backend", lambda: False)
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _s: None)
    monkeypatch.setattr(
        "app.automation.existing_chrome.websocket_connect",
        lambda uri, **kwargs: FakeWebSocket(captured),
    )
    return captured


def methods(sent: list[dict]) -> list[str]:
    return [entry["method"] for entry in sent]


def test_mouse_click_raises_the_tab_first(sent) -> None:
    dispatch_mouse_click(ChromeTabRef(window_id=0, tab_id=0), 10.0, 20.0)
    assert methods(sent)[0] == "Page.bringToFront"
    assert methods(sent).count("Page.bringToFront") == 1
    assert methods(sent)[1:] == [
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
    ]


def test_click_coordinates_are_passed_through_unscaled(sent) -> None:
    """Input takes CSS pixels. Flow renders at devicePixelRatio 0.25, and scaling the
    coordinates by it lands the click on whatever sits a quarter of the way in."""
    dispatch_mouse_click(ChromeTabRef(window_id=0, tab_id=0), 4005.25, 3895.0)
    for entry in sent[1:]:
        assert entry["params"]["x"] == 4005.25
        assert entry["params"]["y"] == 3895.0


def test_key_events_raise_the_tab_first(sent) -> None:
    dispatch_key(ChromeTabRef(window_id=0, tab_id=0), key="Escape", code="Escape")
    assert methods(sent)[0] == "Page.bringToFront"
    assert "Input.dispatchKeyEvent" in methods(sent)


def test_non_input_batches_are_left_alone(sent) -> None:
    _linux_cdp_commands(
        ChromeTabRef(window_id=0, tab_id=0),
        [{"method": "Browser.setDownloadBehavior", "params": {}}],
    )
    assert methods(sent) == ["Browser.setDownloadBehavior"]


def test_ids_stay_sequential_so_replies_are_matched(sent) -> None:
    dispatch_mouse_click(ChromeTabRef(window_id=0, tab_id=0), 1.0, 2.0)
    assert [entry["id"] for entry in sent] == [1, 2, 3, 4]
