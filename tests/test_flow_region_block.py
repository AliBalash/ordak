"""A geo-block must be named as one, before any credit is spent.

Flow answers a blocked location by redirecting the workspace to its unsupported-country
page. The composer is then absent, so the run used to fail with "Could not find Flow input
box in the current Chrome tab" — which sends the operator looking for a DOM change that did
not happen.
"""
from __future__ import annotations

import pytest

from app.automation import flow_worker as fw
from app.errors import ErrorCode, OrdaKError


class Tab:
    window_id = 0
    tab_id = 0
    target_id = "t"


@pytest.mark.parametrize(
    "url",
    [
        "https://flow.google.com/unsupported-country",
        "https://FLOW.GOOGLE.COM/Unsupported-Country?hl=en",
    ],
)
def test_the_redirect_url_is_enough(monkeypatch, url: str) -> None:
    monkeypatch.setattr(
        "app.automation.existing_chrome.execute_javascript",
        lambda tab, script: (_ for _ in ()).throw(AssertionError("should not need the page")),
    )
    assert fw._flow_region_blocked(Tab(), url) is True


def test_the_page_text_is_the_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.automation.existing_chrome.execute_javascript",
        lambda tab, script: "Flow is not available in your country yet. Flow TV",
    )
    assert fw._flow_region_blocked(Tab(), "https://labs.google/fx/tools/flow/project/abc") is True


def test_a_working_project_is_not_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.automation.existing_chrome.execute_javascript",
        lambda tab, script: "Video · 720p · 4s crop_9_16 x1  What do you want to create?",
    )
    assert fw._flow_region_blocked(Tab(), "https://labs.google/fx/tools/flow/project/abc") is False


def test_an_unreadable_page_is_not_called_blocked(monkeypatch) -> None:
    """A probe failure must not be reported as a geo-block; that would hide the real error."""
    def boom(tab, script):
        raise RuntimeError("tab went away")

    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", boom)
    assert fw._flow_region_blocked(Tab(), "https://labs.google/fx/tools/flow/project/abc") is False


def test_ensure_project_raises_the_named_error(monkeypatch) -> None:
    monkeypatch.setattr(fw, "_current_url", lambda tab: "https://flow.google.com/unsupported-country")
    monkeypatch.setattr(fw, "_flow_region_blocked", lambda tab, url: True)
    with pytest.raises(OrdaKError) as excinfo:
        fw._ensure_flow_project(Tab(), runtime=None, app_settings=object())
    assert excinfo.value.code is ErrorCode.FLOW_REGION_BLOCKED
    assert "not available in this country" in excinfo.value.message


def test_a_block_that_lands_after_the_project_loads_is_caught(monkeypatch) -> None:
    """The redirect can arrive a moment after the project URL, so the settled URL decides."""
    urls = iter([
        "https://labs.google/fx/tools/flow/project/abc",   # first read: looks fine
        "https://flow.google.com/unsupported-country",     # settled read: blocked
        "https://flow.google.com/unsupported-country",
    ])
    monkeypatch.setattr(fw, "_current_url", lambda tab: next(urls))
    monkeypatch.setattr(fw.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        fw, "_flow_region_blocked", lambda tab, url: "unsupported" in url
    )
    with pytest.raises(OrdaKError) as excinfo:
        fw._ensure_flow_project(Tab(), runtime=None, app_settings=object())
    assert excinfo.value.code is ErrorCode.FLOW_REGION_BLOCKED


def test_the_descriptor_says_it_is_not_a_bug() -> None:
    from app.errors import get_error_descriptor

    descriptor = get_error_descriptor(ErrorCode.FLOW_REGION_BLOCKED.value)
    assert descriptor is not None
    assert descriptor.recoverable is True
    assert "location" in descriptor.suggested_action.lower()
