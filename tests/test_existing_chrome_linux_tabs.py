from __future__ import annotations

from app.automation.existing_chrome import ChromeTabInfo, ChromeTabRef, _linux_find_tab, get_tab_info
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
