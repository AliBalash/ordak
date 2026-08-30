from __future__ import annotations

from dataclasses import replace

import pytest

from pathlib import Path

from app.automation.browser import (
    _linux_launch_remote_debugging_chrome,
    ensure_linux_remote_debugging_session,
    open_profile_browser_session,
    linux_remote_debugging_available,
)
from app.config import settings


def test_ensure_linux_remote_debugging_session_auto_launches_and_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    local_settings = replace(
        settings,
        browser_remote_debugging_auto_launch=True,
        browser_remote_debugging_launch_timeout_ms=2_000,
    )
    launches: list[str | None] = []
    polls = {"count": 0}

    def fake_available(app_settings=None) -> bool:
        polls["count"] += 1
        return polls["count"] >= 3

    monkeypatch.setattr("app.automation.browser.platform.system", lambda: "Linux")
    monkeypatch.setattr("app.automation.browser._normal_chrome_process_running", lambda: False)
    monkeypatch.setattr("app.automation.browser.linux_remote_debugging_available", fake_available)
    monkeypatch.setattr(
        "app.automation.browser._linux_launch_remote_debugging_chrome",
        lambda *, app_settings=None, target_url=None: launches.append(target_url),
    )
    monkeypatch.setattr("app.automation.browser.time.sleep", lambda _: None)

    ensure_linux_remote_debugging_session(local_settings, target_url="https://chatgpt.com/")

    assert launches == ["https://chatgpt.com/"]
    assert polls["count"] >= 3


def test_ensure_linux_remote_debugging_session_respects_disabled_auto_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_settings = replace(
        settings,
        browser_remote_debugging_auto_launch=False,
    )
    launches: list[str | None] = []

    monkeypatch.setattr("app.automation.browser.platform.system", lambda: "Linux")
    monkeypatch.setattr("app.automation.browser._normal_chrome_process_running", lambda: False)
    monkeypatch.setattr("app.automation.browser.linux_remote_debugging_available", lambda app_settings=None: False)
    monkeypatch.setattr(
        "app.automation.browser._linux_launch_remote_debugging_chrome",
        lambda *, app_settings=None, target_url=None: launches.append(target_url),
    )

    with pytest.raises(RuntimeError, match="remote debugging is not reachable"):
        ensure_linux_remote_debugging_session(local_settings, target_url="https://gemini.google.com/app")

    assert launches == []


def test_ensure_linux_remote_debugging_session_raises_after_launch_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_settings = replace(
        settings,
        browser_remote_debugging_auto_launch=True,
        browser_remote_debugging_launch_timeout_ms=500,
    )
    launches: list[str | None] = []
    monotonic_values = iter([0.0, 0.1, 0.3, 0.6, 0.8])

    monkeypatch.setattr("app.automation.browser.platform.system", lambda: "Linux")
    monkeypatch.setattr("app.automation.browser._normal_chrome_process_running", lambda: False)
    monkeypatch.setattr("app.automation.browser.linux_remote_debugging_available", lambda app_settings=None: False)
    monkeypatch.setattr(
        "app.automation.browser._linux_launch_remote_debugging_chrome",
        lambda *, app_settings=None, target_url=None: launches.append(target_url),
    )
    monkeypatch.setattr("app.automation.browser.time.sleep", lambda _: None)
    monkeypatch.setattr("app.automation.browser.time.monotonic", lambda: next(monotonic_values))

    with pytest.raises(RuntimeError, match="remote debugging did not become reachable"):
        ensure_linux_remote_debugging_session(local_settings, target_url="about:blank")

    assert launches == ["about:blank"]


def test_linux_launch_remote_debugging_chrome_uses_ordak_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_settings = replace(
        settings,
        browser_executable_path=Path("/usr/bin/google-chrome"),
        browser_remote_debugging_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "real-chrome",
        browser_profile_name="Profile 1",
    )
    (local_settings.browser_user_data_dir / local_settings.browser_profile_name).mkdir(parents=True)
    commands: list[list[str]] = []

    monkeypatch.setattr(
        "app.automation.browser.subprocess.Popen",
        lambda cmd, **kwargs: commands.append(cmd),
    )

    _linux_launch_remote_debugging_chrome(
        app_settings=local_settings,
        target_url="https://gemini.google.com/app",
    )

    assert commands == [[
        "/usr/bin/google-chrome",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9222",
        f"--user-data-dir={local_settings.browser_user_data_dir}",
        "--profile-directory=Profile 1",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        "https://gemini.google.com/app",
    ]]


def test_open_profile_browser_session_on_linux_launches_and_opens_gemini_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[str | None] = []
    opened_urls: list[str] = []

    monkeypatch.setattr("app.automation.browser.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "app.automation.browser.linux_remote_debugging_available",
        lambda app_settings=None: False,
    )
    monkeypatch.setattr(
        "app.automation.browser._linux_launch_remote_debugging_chrome",
        lambda *, app_settings=None, target_url=None: launches.append(target_url),
    )
    monkeypatch.setattr(
        "app.automation.browser._wait_for_linux_remote_debugging",
        lambda app_settings=None: None,
    )
    monkeypatch.setattr(
        "app.automation.existing_chrome.open_gemini_tab_in_existing_chrome",
        lambda url: opened_urls.append(url),
    )

    open_profile_browser_session(settings)

    assert launches == ["about:blank"]
    assert opened_urls == [settings.gemini_url]


def test_linux_remote_debugging_available_bypasses_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    proxies: list[dict[object, object]] = []
    opened: list[tuple[str, int]] = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeOpener:
        def open(self, request, timeout=5):
            opened.append((request.full_url, timeout))
            return FakeResponse()

    monkeypatch.setattr("app.automation.browser.ProxyHandler", lambda mapping: proxies.append(mapping) or object())
    monkeypatch.setattr("app.automation.browser.build_opener", lambda handler: FakeOpener())

    assert linux_remote_debugging_available(settings) is True
    assert proxies == [{}]
    assert opened == [(f"{settings.browser_remote_debugging_url.rstrip('/')}/json/version", 5)]
