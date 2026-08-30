from __future__ import annotations

import pytest

from app.automation.existing_chrome import (
    ChromeTabInfo,
    ChromeTabRef,
    _fetch_json,
    _linux_find_tab,
    _linux_execute_javascript,
    get_tab_info,
    read_latest_response_baseline,
    submit_prompt,
    wait_for_response_stable,
)
from app.providers.existing_chrome import ExistingChromeProviderAdapter


def test_linux_find_tab_requires_matching_target_id(monkeypatch) -> None:
    tabs = [
        ChromeTabInfo(
            window_id=0,
            tab_id=0,
            url="https://chatgpt.com/c/new",
            title="new",
            window_key="linux-devtools",
            target_id="new-target",
        ),
    ]
    monkeypatch.setattr("app.automation.existing_chrome._linux_list_google_chrome_tabs", lambda: tabs)

    result = _linux_find_tab(
        ChromeTabRef(window_id=0, tab_id=0, window_key="linux-devtools", target_id="stale-target")
    )

    assert result is None


def test_get_tab_info_does_not_fallback_to_zero_ids_for_linux_devtools(monkeypatch) -> None:
    tabs = [
        ChromeTabInfo(
            window_id=0,
            tab_id=0,
            url="https://chatgpt.com/c/new",
            title="new",
            window_key="linux-devtools",
            target_id="new-target",
        ),
    ]
    monkeypatch.setattr("app.automation.existing_chrome.list_google_chrome_tabs", lambda: tabs)

    result = get_tab_info(
        ChromeTabRef(window_id=0, tab_id=0, window_key="linux-devtools", target_id="stale-target")
    )

    assert result is None


def test_rebind_tab_prefers_first_linux_devtools_match_when_no_tab_is_marked_active(
    monkeypatch,
) -> None:
    adapter = ExistingChromeProviderAdapter("chatgpt")
    tabs = [
        ChromeTabInfo(
            window_id=0,
            tab_id=0,
            url="https://chatgpt.com/c/latest",
            title="latest",
            active=False,
            window_key="linux-devtools",
            target_id="latest-target",
        ),
        ChromeTabInfo(
            window_id=0,
            tab_id=0,
            url="https://chatgpt.com/c/older",
            title="older",
            active=False,
            window_key="linux-devtools",
            target_id="older-target",
        ),
    ]
    monkeypatch.setattr("app.providers.existing_chrome.list_google_chrome_tabs", lambda: tabs)

    rebound = adapter.rebind_tab(conversation_url=None, tab_ref=None)

    assert rebound.tab is not None
    assert rebound.tab.target_id == "latest-target"
    assert rebound.info is not None
    assert rebound.info.url == "https://chatgpt.com/c/latest"


def test_rebind_tab_does_not_fall_back_to_a_different_chatgpt_conversation(
    monkeypatch,
) -> None:
    adapter = ExistingChromeProviderAdapter("chatgpt")
    tab = ChromeTabInfo(
        window_id=0,
        tab_id=0,
        url="https://chatgpt.com/c/different",
        title="different",
        active=True,
        window_key="linux-devtools",
        target_id="different-target",
    )
    monkeypatch.setattr("app.providers.existing_chrome.get_tab_info", lambda ref: tab)
    monkeypatch.setattr("app.providers.existing_chrome.list_google_chrome_tabs", lambda: [tab])

    rebound = adapter.rebind_tab(
        conversation_url="https://chatgpt.com/g/custom/c/expected",
        tab_ref=tab.ref,
    )

    assert rebound.tab is None
    assert rebound.error_code is not None


def test_rebind_tab_matches_saved_conversation_id_across_chatgpt_url_shapes(
    monkeypatch,
) -> None:
    adapter = ExistingChromeProviderAdapter("chatgpt")
    tab = ChromeTabInfo(
        window_id=0,
        tab_id=0,
        url="https://chatgpt.com/c/conversation-123",
        title="same conversation",
        window_key="linux-devtools",
        target_id="same-target",
    )
    monkeypatch.setattr("app.providers.existing_chrome.list_google_chrome_tabs", lambda: [tab])

    rebound = adapter.rebind_tab(
        conversation_url="https://chatgpt.com/g/custom/c/conversation-123",
        tab_ref=None,
    )

    assert rebound.tab is not None
    assert rebound.tab.target_id == "same-target"


def test_latest_chatgpt_response_baseline_uses_only_latest_assistant_turn(
    monkeypatch,
) -> None:
    captured = {}

    def fake_execute(tab, script):
        captured["script"] = script
        return '{"text":"Thinking","assistantTurnCount":6}'

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)

    baseline = read_latest_response_baseline(
        ChromeTabRef(window_id=0, tab_id=0),
        provider="chatgpt",
    )

    assert baseline.text == "Thinking"
    assert baseline.assistant_turn_count == 6
    assert "roots.at(-1)" in captured["script"]
    assert "main .markdown" not in captured["script"]


def test_wait_for_chatgpt_response_never_scans_back_from_thinking(
    monkeypatch,
) -> None:
    clock = [0.0]
    captured = {}

    def fake_monotonic():
        clock[0] += 0.2
        return clock[0]

    def fake_execute(tab, script):
        captured["script"] = script
        return (
            '{"answer":"","busy":false,"generatedImages":0,'
            '"assistantTurnCount":6,"latestTransient":true}'
        )

    monkeypatch.setattr(
        "app.automation.existing_chrome._linux_should_use_x11_backend",
        lambda: False,
    )
    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)
    monkeypatch.setattr("app.automation.existing_chrome.time.monotonic", fake_monotonic)
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _: None)

    with pytest.raises(TimeoutError):
        wait_for_response_stable(
            ChromeTabRef(window_id=0, tab_id=0),
            timeout_ms=1_000,
            stable_seconds=1,
            excluded_text="continue",
            previous_response="RUN\nold-command",
            previous_assistant_turn_count=5,
            provider="chatgpt",
        )

    assert "assistantRoots.at(-1)" in captured["script"]
    assert ".find((text)" not in captured["script"]
    assert "latestText !== clean(previousResponse)" in captured["script"]


def test_wait_for_chatgpt_response_refreshes_same_chat_and_recovers_answer(
    monkeypatch,
) -> None:
    clock = [0.0]
    refreshed = []
    probes = []
    recovery_messages = []

    def fake_monotonic():
        clock[0] += 0.2
        return clock[0]

    def fake_execute(tab, script):
        if "'recovering'" in script:
            refreshed.append(script)
            return "recovering"
        probes.append(script)
        if refreshed:
            return (
                '{"answer":"RUN\\nnew-command","busy":false,"generatedImages":0,'
                '"assistantTurnCount":6,"latestTransient":false}'
            )
        return (
            '{"answer":"","busy":false,"generatedImages":0,'
            '"assistantTurnCount":6,"latestTransient":true}'
        )

    monkeypatch.setattr(
        "app.automation.existing_chrome._linux_should_use_x11_backend",
        lambda: False,
    )
    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)
    monkeypatch.setattr("app.automation.existing_chrome.time.monotonic", fake_monotonic)
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "app.automation.existing_chrome.wait_for_prompt_input",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.automation.existing_chrome.wait_for_chatgpt_workspace_ready",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.automation.existing_chrome.get_tab_info",
        lambda tab: ChromeTabInfo(
            window_id=0,
            tab_id=0,
            url="https://chatgpt.com/g/custom/c/conversation-123",
            title="saved chat",
        ),
    )

    answer = wait_for_response_stable(
        ChromeTabRef(window_id=0, tab_id=0),
        timeout_ms=5_000,
        stable_seconds=0,
        excluded_text="continue",
        previous_response="RUN\nold-command",
        previous_assistant_turn_count=5,
        provider="chatgpt",
        stall_refresh_seconds=1,
        max_stall_refreshes=1,
        recovery_callback=recovery_messages.append,
    )

    assert answer == "RUN\nnew-command"
    assert len(refreshed) == 1
    assert "conversation-123" in refreshed[0]
    assert recovery_messages == [
        "ChatGPT response is still pending (page is idle with no new result). Refreshing the exact conversation "
        "to reconcile the latest assistant turn (1/1)."
    ]
    assert any("assistantAfterExcludedUser" in script for script in probes)
    assert any("const requireOrderedUserTurn = true" in script for script in probes)
    assert any("ORDAK_EXCHANGE_ID_" in script for script in probes)
    assert any(
        "latestText !== clean(previousResponse) || assistantAfterExcludedUser" in script
        for script in probes
    )


def test_active_chatgpt_generation_is_not_treated_as_stall(monkeypatch) -> None:
    clock = [0.0]
    refreshes = []

    def fake_monotonic():
        clock[0] += 0.25
        return clock[0]

    def fake_execute(tab, script):
        if "'recovering'" in script:
            refreshes.append(script)
            return "recovering"
        return '{"answer":"","busy":true,"generatedImages":0,"assistantTurnCount":1,"latestTransient":true}'

    monkeypatch.setattr("app.automation.existing_chrome._linux_should_use_x11_backend", lambda: False)
    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", fake_execute)
    monkeypatch.setattr("app.automation.existing_chrome.time.monotonic", fake_monotonic)
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _: None)

    with pytest.raises(TimeoutError):
        wait_for_response_stable(
            ChromeTabRef(window_id=0, tab_id=0), timeout_ms=1_000, stable_seconds=1,
            excluded_text="prompt", provider="chatgpt", stall_refresh_seconds=1,
            max_stall_refreshes=3,
        )
    assert refreshes == []


def test_fetch_json_bypasses_proxy_for_devtools(monkeypatch) -> None:
    proxies = []
    opened = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"Browser":"Chrome"}'

    class FakeOpener:
        def open(self, request, timeout=5):
            opened.append((request.full_url, timeout))
            return FakeResponse()

    monkeypatch.setattr(
        "app.automation.existing_chrome.ProxyHandler",
        lambda mapping: proxies.append(mapping) or object(),
    )
    monkeypatch.setattr("app.automation.existing_chrome.build_opener", lambda handler: FakeOpener())

    assert _fetch_json("http://127.0.0.1:9222/json/version") == {"Browser": "Chrome"}
    assert proxies == [{}]
    assert opened == [("http://127.0.0.1:9222/json/version", 5)]


def test_linux_execute_javascript_disables_proxy(monkeypatch) -> None:
    captured = {}

    class FakeWebSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def send(self, payload):
            captured["payload"] = payload

        def recv(self):
            return '{"id": 1, "result": {"result": {"value": "ok"}}}'

    monkeypatch.setattr(
        "app.automation.existing_chrome._linux_find_tab",
        lambda tab: ChromeTabInfo(
            window_id=0,
            tab_id=0,
            url="https://chatgpt.com/",
            title="ChatGPT",
            window_key="linux-devtools",
            target_id="target-1",
            websocket_debugger_url="ws://127.0.0.1:9222/devtools/page/target-1",
        ),
    )

    def fake_connect(uri, **kwargs):
        captured["uri"] = uri
        captured["kwargs"] = kwargs
        return FakeWebSocket()

    monkeypatch.setattr("app.automation.existing_chrome.websocket_connect", fake_connect)

    assert _linux_execute_javascript(ChromeTabRef(window_id=0, tab_id=0), "1 + 1") == "ok"
    assert captured["uri"] == "ws://127.0.0.1:9222/devtools/page/target-1"
    assert captured["kwargs"]["proxy"] is None


def test_submit_prompt_requires_a_new_chatgpt_user_turn(monkeypatch) -> None:
    calls = 0

    def fake_execute(tab, script):
        nonlocal calls
        calls += 1
        if script.lstrip().startswith("(() => document.querySelectorAll("):
            return "1"
        if "JSON.stringify({ promptEmpty" in script:
            return '{"promptEmpty":true,"userCount":2}'
        return "clicked"

    monkeypatch.setattr(
        "app.automation.existing_chrome._linux_should_use_x11_backend",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.automation.existing_chrome.execute_javascript",
        fake_execute,
    )

    submit_prompt(
        ChromeTabRef(window_id=1, tab_id=2),
        provider="chatgpt",
    )

    assert calls == 3


def test_submit_prompt_rejects_an_unconfirmed_click(monkeypatch) -> None:
    clock = [0.0]

    def fake_monotonic():
        clock[0] += 1.0
        return clock[0]

    def fake_execute(tab, script):
        if script.lstrip().startswith("(() => document.querySelectorAll("):
            return "1"
        if "JSON.stringify({ promptEmpty" in script:
            return '{"promptEmpty":true,"userCount":1}'
        return "clicked"

    monkeypatch.setattr(
        "app.automation.existing_chrome._linux_should_use_x11_backend",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.automation.existing_chrome.execute_javascript",
        fake_execute,
    )
    monkeypatch.setattr(
        "app.automation.existing_chrome.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr("app.automation.existing_chrome.time.sleep", lambda _: None)

    try:
        submit_prompt(
            ChromeTabRef(window_id=1, tab_id=2),
            provider="chatgpt",
        )
    except RuntimeError as exc:
        assert "not_confirmed" in str(exc)
    else:
        raise AssertionError("Unconfirmed prompt submission should fail.")
