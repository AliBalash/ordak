from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, ProxyHandler, build_opener

from websockets.sync.client import connect as websocket_connect

from app.artifacts import slugify_filename
from app.config import settings


ProviderName = Literal["gemini", "chatgpt"]


PROVIDER_LABELS: dict[ProviderName, str] = {
    "gemini": "Gemini",
    "chatgpt": "ChatGPT",
}

GENERATED_IMAGE_SELECTORS = [
    "img",
    "picture img",
    "generated-image img",
    ".generated-images-container img",
    ".image-gallery img",
]

X11_PROMPT_LABELS: dict[ProviderName, tuple[str, ...]] = {
    "gemini": ("Enter a prompt for Gemini",),
    "chatgpt": ("Message ChatGPT", "Send a message", "Ask anything"),
}

TEXTUAL_ACCESSIBILITY_ROLES = {
    "static",
    "paragraph",
    "heading",
    "label",
    "link",
}

PROMPT_SELECTORS: dict[ProviderName, list[str]] = {
    "gemini": [
        '.ql-editor[contenteditable="true"][role="textbox"][aria-label*="Gemini"]',
        '[data-test-id="textarea-inner"] .ql-editor[contenteditable="true"]',
        "rich-textarea .ql-editor[contenteditable=\"true\"]",
        '[role="textbox"][aria-label*="Gemini"]',
        'div[contenteditable="true"][role="textbox"]',
        "textarea",
    ],
    "chatgpt": [
        "#prompt-textarea",
        'div#prompt-textarea[contenteditable="true"]',
        '[data-testid="prompt-textarea"]',
        'div[contenteditable="true"][role="textbox"][aria-label*="ChatGPT"]',
        'textarea[placeholder*="Message"]',
        "textarea",
    ],
}


def _assistant_root_selectors(provider: ProviderName) -> list[str]:
    if provider == "chatgpt":
        return [
            '[data-message-author-role="assistant"]',
            'article[data-testid^="conversation-turn-"] [data-message-author-role="assistant"]',
            'main [data-message-author-role="assistant"]',
            "main article",
            "main .markdown",
            "main .prose",
        ]
    return [
        "[data-response-id]",
        '[data-message-author-role="model"]',
        "main article",
        "main .model-response",
        "main .markdown",
        "main .prose",
    ]


def _image_loading_selectors(provider: ProviderName) -> list[str]:
    if provider == "chatgpt":
        return [
            '[aria-busy="true"]',
            '[role="progressbar"]',
            '[role="status"]',
            ".animate-spin",
            '[data-testid*="loading" i]',
            '[data-testid*="generating" i]',
            '[data-testid*="spinner" i]',
        ]
    return [
        '[aria-busy="true"]',
        '[role="progressbar"]',
        '[role="status"]',
        "mat-spinner",
        "md-progress-circular",
        '[data-test-id*="loading" i]',
        '[data-test-id*="spinner" i]',
    ]


@dataclass(slots=True)
class ChromeTabRef:
    window_id: int
    tab_id: int
    window_key: str | None = None
    target_id: str | None = None


@dataclass(slots=True)
class ChromeTabInfo:
    window_id: int
    tab_id: int
    url: str
    title: str
    active: bool = False
    window_key: str | None = None
    target_id: str | None = None
    websocket_debugger_url: str | None = None

    @property
    def ref(self) -> ChromeTabRef:
        return ChromeTabRef(
            window_id=self.window_id,
            tab_id=self.tab_id,
            window_key=self.window_key,
            target_id=self.target_id,
        )


@dataclass(slots=True, frozen=True)
class ResponseBaseline:
    text: str
    assistant_turn_count: int


def _browser_platform() -> str:
    configured = settings.browser_platform.strip().lower()
    if configured in {"mac", "macos", "darwin"}:
        return "mac"
    if configured in {"linux", "lin"}:
        return "linux"
    system = platform.system().strip().lower()
    if system == "darwin":
        return "mac"
    if system == "linux":
        return "linux"
    return configured or system


def _is_mac_backend() -> bool:
    return _browser_platform() == "mac"


def _remote_debugging_base_url() -> str:
    return settings.browser_remote_debugging_url.rstrip("/")


def _fetch_json(url: str, *, method: str = "GET") -> Any:
    request = Request(url, method=method)
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _linux_error(message: str) -> RuntimeError:
    return RuntimeError(
        f"{message} Start Chrome on Linux with remote debugging enabled, for example: "
        f"{settings.browser_executable_path} --remote-debugging-port=9222"
    )


def _linux_x11_error(message: str) -> RuntimeError:
    return RuntimeError(
        f"{message} On Linux without DevTools, ordak needs an interactive X11 session with "
        "Google Chrome already open and xdotool available."
    )


def _linux_devtools_version() -> dict[str, Any]:
    try:
        payload = _fetch_json(f"{_remote_debugging_base_url()}/json/version")
        if not isinstance(payload, dict):
            raise ValueError("Invalid DevTools version payload.")
        return payload
    except (URLError, HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise _linux_error(
            f"Could not connect to Chrome DevTools at {settings.browser_remote_debugging_url}."
        ) from exc


def _linux_devtools_available() -> bool:
    try:
        payload = _fetch_json(f"{_remote_debugging_base_url()}/json/version")
        if not isinstance(payload, dict):
            return False
        return True
    except Exception:
        return False


def _linux_x11_available() -> bool:
    return bool(os.getenv("DISPLAY")) and shutil.which("xdotool") is not None


def _linux_should_use_x11_backend() -> bool:
    return (
        _browser_platform() == "linux"
        and settings.browser_linux_x11_fallback_enabled
        and not _linux_devtools_available()
        and _linux_x11_available()
    )


def _linux_atspi():
    try:
        dist_paths = (
            "/usr/lib/python3/dist-packages",
            "/usr/local/lib/python3/dist-packages",
        )
        for candidate in dist_paths:
            if candidate not in sys.path and Path(candidate).exists():
                sys.path.append(candidate)
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on system packages
        raise _linux_x11_error("AT-SPI is not available for the current desktop session.") from exc
    return Atspi


def _linux_atspi_text_value(accessible: Any) -> str:
    text_iface = accessible.get_text_iface()
    if not text_iface:
        return ""
    char_count = text_iface.get_character_count()
    if char_count <= 0:
        return ""
    Atspi = _linux_atspi()
    return str(Atspi.Text.get_text(text_iface, 0, char_count) or "")


def _linux_atspi_walk(accessible: Any, visitor: Callable[[Any, str], bool], path: str = "") -> bool:
    if visitor(accessible, path):
        return True
    for index in range(accessible.get_child_count()):
        if _linux_atspi_walk(accessible.get_child_at_index(index), visitor, f"{path}/{index}"):
            return True
    return False


def _linux_atspi_find_nodes(
    root: Any,
    predicate: Callable[[Any], bool],
) -> list[tuple[Any, str]]:
    matches: list[tuple[Any, str]] = []

    def visitor(accessible: Any, path: str) -> bool:
        if predicate(accessible):
            matches.append((accessible, path))
        return False

    _linux_atspi_walk(root, visitor)
    return matches


def _linux_x11_window_id(window_name: str) -> int:
    candidates = [window_name.strip()]
    if " - Google Chrome - " in window_name:
        candidates.append(window_name.split(" - Google Chrome - ", 1)[0] + " - Google Chrome")
    if " - Mohammad Hossein" in window_name:
        candidates.append(window_name.removesuffix(" - Mohammad Hossein"))
    if " - " in window_name:
        parts = window_name.split(" - ")
        for keep in range(len(parts) - 1, 1, -1):
            candidates.append(" - ".join(parts[:keep]))
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", candidate],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.splitlines()[0].strip())
    class_match = subprocess.run(
        ["xdotool", "search", "--onlyvisible", "--class", "google-chrome"],
        capture_output=True,
        text=True,
        check=False,
    )
    if class_match.returncode == 0 and class_match.stdout.strip():
        return int(class_match.stdout.splitlines()[0].strip())
    raise _linux_x11_error(f"Could not map the Google Chrome window '{window_name}' to an X11 window id.")


def _linux_atspi_chrome_windows() -> list[tuple[Any, int]]:
    Atspi = _linux_atspi()
    desktop = Atspi.get_desktop(0)
    windows: list[tuple[Any, int]] = []
    for app_index in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(app_index)
        if (app.get_name() or "").strip() != "Google Chrome":
            continue
        for window_index in range(app.get_child_count()):
            window = app.get_child_at_index(window_index)
            name = (window.get_name() or "").strip()
            if not name:
                continue
            try:
                windows.append((window, _linux_x11_window_id(name)))
            except RuntimeError:
                continue
    return windows


def _linux_atspi_primary_window() -> tuple[Any, int]:
    windows = _linux_atspi_chrome_windows()
    if not windows:
        raise _linux_x11_error("Google Chrome is not visible in the current X11 desktop session.")
    return windows[0]


def _linux_atspi_selected_page_tab(window: Any) -> tuple[Any | None, int]:
    Atspi = _linux_atspi()
    selected_index = 0
    selected_tab = None

    def predicate(accessible: Any) -> bool:
        nonlocal selected_index, selected_tab
        if accessible.get_role_name() != "page tab":
            return False
        states = accessible.get_state_set()
        if states.contains(Atspi.StateType.SELECTED):
            selected_tab = accessible
            return True
        selected_index += 1
        return False

    _linux_atspi_walk(window, lambda accessible, _: predicate(accessible))
    return selected_tab, selected_index


def _linux_atspi_address_value(window: Any) -> str:
    nodes = _linux_atspi_find_nodes(
        window,
        lambda accessible: accessible.get_role_name() == "entry"
        and "address and search bar" in (accessible.get_name() or "").lower(),
    )
    if not nodes:
        return ""
    return _linux_atspi_text_value(nodes[0][0]).strip()


def _linux_atspi_document_root(window: Any) -> Any | None:
    nodes = _linux_atspi_find_nodes(window, lambda accessible: accessible.get_role_name() == "document web")
    return nodes[0][0] if nodes else None


def _linux_atspi_prompt_entry(window: Any, provider: ProviderName) -> Any | None:
    labels = X11_PROMPT_LABELS[provider]
    matches = _linux_atspi_find_nodes(
        window,
        lambda accessible: accessible.get_role_name() == "entry"
        and any(label in (accessible.get_name() or "") for label in labels),
    )
    return matches[0][0] if matches else None


def _linux_atspi_flat_text_items(window: Any) -> list[tuple[str, str]]:
    document = _linux_atspi_document_root(window)
    if document is None:
        return []
    items: list[tuple[str, str]] = []

    def visitor(accessible: Any, _: str) -> bool:
        role = accessible.get_role_name()
        name = (accessible.get_name() or "").strip()
        if role in TEXTUAL_ACCESSIBILITY_ROLES and name:
            items.append((role, name))
        elif role == "entry":
            label = (accessible.get_name() or "").strip()
            if label:
                items.append((role, label))
        return False

    _linux_atspi_walk(document, visitor)
    return items


def _linux_normalize_accessibility_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u200c", " ")).strip()


def _linux_x11_ignore_response_text(value: str, excluded_text: str) -> bool:
    normalized = _linux_normalize_accessibility_text(value)
    excluded = _linux_normalize_accessibility_text(excluded_text)
    if not normalized:
        return True
    if excluded and normalized == excluded:
        return True
    ignored_patterns = (
        "conversation with gemini",
        "gemini said",
        "gemini replied",
        "your privacy & gemini",
        "opens in a new window",
        "gemini is ai and can make mistakes",
        "ask gemini",
        "activity",
        "from your ip address",
    )
    return any(pattern in normalized.lower() for pattern in ignored_patterns)


def _linux_x11_latest_gemini_answer(items: list[tuple[str, str]], excluded_text: str) -> str:
    blocks: list[str] = []
    current: list[str] = []
    collecting = False
    saw_gemini_heading = False
    for role, value in items:
        normalized = _linux_normalize_accessibility_text(value)
        if role == "heading" and normalized == "Gemini said":
            saw_gemini_heading = True
            if current:
                blocks.append("\n".join(current).strip())
            current = []
            collecting = True
            continue
        if role == "heading" and normalized.startswith("You said"):
            if collecting and current:
                blocks.append("\n".join(current).strip())
            current = []
            collecting = False
            continue
        if role == "entry" and "Enter a prompt for Gemini" in value:
            if collecting and current:
                blocks.append("\n".join(current).strip())
            current = []
            collecting = False
            continue
        if collecting and role in TEXTUAL_ACCESSIBILITY_ROLES and not _linux_x11_ignore_response_text(value, excluded_text):
            current.append(normalized)
    if current:
        blocks.append("\n".join(current).strip())
    for block in reversed(blocks):
        if block and not _linux_x11_ignore_response_text(block, excluded_text):
            return block
    if saw_gemini_heading:
        return ""
    for role, value in reversed(items):
        if role in TEXTUAL_ACCESSIBILITY_ROLES and not _linux_x11_ignore_response_text(value, excluded_text):
            return _linux_normalize_accessibility_text(value)
    return ""


def _linux_x11_button(window: Any, patterns: tuple[str, ...]) -> Any | None:
    lowered = tuple(pattern.lower() for pattern in patterns)
    matches = _linux_atspi_find_nodes(
        window,
        lambda accessible: accessible.get_role_name() == "button"
        and any(pattern in (accessible.get_name() or "").lower() for pattern in lowered),
    )
    return matches[0][0] if matches else None


def _linux_x11_busy(window: Any) -> bool:
    return _linux_x11_button(window, ("stop response", "stop generating", "stop answering", "cancel")) is not None


def _linux_x11_current_tab_info() -> ChromeTabInfo:
    window, window_id = _linux_atspi_primary_window()
    selected_tab, selected_index = _linux_atspi_selected_page_tab(window)
    title = (selected_tab.get_name() or window.get_name() or "Google Chrome").strip() if selected_tab else (window.get_name() or "Google Chrome").strip()
    url = _linux_atspi_address_value(window)
    return ChromeTabInfo(
        window_id=window_id,
        tab_id=selected_index,
        url=f"https://{url}" if url and "://" not in url else url,
        title=title,
        active=True,
        window_key="linux-x11",
        target_id=f"{window_id}:{selected_index}",
    )


def _linux_x11_activate_window(window_id: int) -> None:
    result = subprocess.run(
        ["xdotool", "windowactivate", "--sync", str(window_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise _linux_x11_error("Could not bring the current Google Chrome window to the foreground.")


def _linux_x11_click(accessible: Any, *, window_id: int) -> None:
    Atspi = _linux_atspi()
    component = accessible.get_component()
    extents = component.get_extents(Atspi.CoordType.SCREEN)
    x = extents.x + max(extents.width // 2, 1)
    y = extents.y + max(extents.height // 2, 1)
    _linux_x11_activate_window(window_id)
    subprocess.run(
        ["xdotool", "mousemove", str(x), str(y)],
        capture_output=True,
        text=True,
        check=False,
    )
    result = subprocess.run(
        ["xdotool", "click", "1"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise _linux_x11_error("Could not click the requested control in Google Chrome.")


def _linux_x11_key(*keys: str, window_id: int) -> None:
    for key in keys:
        result = subprocess.run(
            ["xdotool", "key", "--window", str(window_id), "--clearmodifiers", key],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise _linux_x11_error(f"Could not send the key {key!r} to Google Chrome.")


def _linux_x11_type_text(text: str, *, window_id: int) -> None:
    for index, line in enumerate(text.splitlines() or [""]):
        if line:
            result = subprocess.run(
                ["xdotool", "type", "--delay", "20", line],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise _linux_x11_error("Could not type the prompt into the current Google Chrome tab.")
        if index < len(text.splitlines()) - 1:
            _linux_x11_key("Shift+Return", window_id=window_id)


def _linux_x11_open_url_in_current_chrome(target_url: str) -> ChromeTabRef:
    info = _linux_x11_current_tab_info()
    _linux_x11_activate_window(info.window_id)
    _linux_x11_key("ctrl+t", window_id=info.window_id)
    time.sleep(0.4)
    _linux_x11_key("ctrl+l", window_id=info.window_id)
    time.sleep(0.2)
    result = subprocess.run(
        ["xdotool", "type", "--delay", "8", target_url],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise _linux_x11_error("Could not type the target URL into Google Chrome.")
    _linux_x11_key("Return", window_id=info.window_id)
    deadline = time.monotonic() + 15
    target_host = urlparse(target_url).netloc.lower()
    while time.monotonic() < deadline:
        current = _linux_x11_current_tab_info()
        if current.url:
            current_host = urlparse(current.url).netloc.lower()
            if current_host == target_host:
                return current.ref
        time.sleep(0.5)
    return _linux_x11_current_tab_info().ref


def _linux_x11_prompt_entry(tab: ChromeTabRef, provider: ProviderName) -> tuple[Any, int]:
    window, window_id = _linux_atspi_primary_window()
    prompt = _linux_atspi_prompt_entry(window, provider)
    if prompt is None:
        raise RuntimeError(f"Could not find {PROVIDER_LABELS[provider]} input box in the current Chrome tab.")
    if tab.window_id and tab.window_id != window_id:
        raise RuntimeError("The active Google Chrome window no longer matches the saved tab.")
    return prompt, window_id


def _linux_x11_login_state(provider: ProviderName) -> str | None:
    window, _ = _linux_atspi_primary_window()
    if _linux_atspi_prompt_entry(window, provider) is not None:
        return None
    page_text = " ".join(value for _, value in _linux_atspi_flat_text_items(window)).lower()
    if re.search(r"verify|captcha|unusual traffic|human verification|prove you are human", page_text):
        return "manual_verification_required"
    if re.search(r"sign in|log in|choose an account|continue to gemini|continue with google|welcome back|sign up", page_text):
        return "login_required"
    return "login_required"


def _linux_list_google_chrome_tabs() -> list[ChromeTabInfo]:
    _linux_devtools_version()
    payload = _fetch_json(f"{_remote_debugging_base_url()}/json/list")
    if not isinstance(payload, list):
        return []
    tabs: list[ChromeTabInfo] = []
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("type") != "page":
            continue
        target_id = str(entry.get("id") or "").strip()
        url = str(entry.get("url") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not target_id:
            continue
        tabs.append(
            ChromeTabInfo(
                window_id=0,
                tab_id=0,
                url=url,
                title=title,
                active=False,
                window_key="linux-devtools",
                target_id=target_id,
                websocket_debugger_url=str(entry.get("webSocketDebuggerUrl") or "").strip() or None,
            )
        )
    return tabs


def _linux_find_tab(tab: ChromeTabRef) -> ChromeTabInfo | None:
    for candidate in _linux_list_google_chrome_tabs():
        if tab.target_id and candidate.target_id == tab.target_id:
            return candidate
    return None


def _linux_open_tab_via_devtools(target_url: str) -> ChromeTabRef | None:
    encoded = quote(target_url, safe=":/?&=%#")
    for endpoint in (
        f"{_remote_debugging_base_url()}/json/new?{encoded}",
        f"{_remote_debugging_base_url()}/json/new?{quote(target_url, safe='')}",
    ):
        try:
            payload = _fetch_json(endpoint, method="PUT")
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("id"):
            info = ChromeTabInfo(
                window_id=0,
                tab_id=0,
                url=str(payload.get("url") or target_url),
                title=str(payload.get("title") or ""),
                active=False,
                window_key="linux-devtools",
                target_id=str(payload["id"]),
                websocket_debugger_url=str(payload.get("webSocketDebuggerUrl") or "").strip() or None,
            )
            return info.ref
    return None


def _linux_open_url_in_existing_chrome(target_url: str) -> ChromeTabRef:
    direct = _linux_open_tab_via_devtools(target_url)
    if direct is not None:
        return direct

    before_ids = {tab.target_id for tab in _linux_list_google_chrome_tabs() if tab.target_id}
    result = subprocess.run(
        [str(settings.browser_executable_path), target_url],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Could not ask Chrome to open a new tab.").strip()
        raise _linux_error(message)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        tabs = _linux_list_google_chrome_tabs()
        matching = [
            tab for tab in tabs
            if tab.url == target_url and tab.target_id not in before_ids
        ]
        if matching:
            return matching[-1].ref
        time.sleep(0.5)
    raise _linux_error("Chrome did not expose the new tab through DevTools in time.")


def _coerce_javascript_result(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _linux_execute_javascript(tab: ChromeTabRef, javascript: str) -> str:
    info = _linux_find_tab(tab)
    if info is None or not info.websocket_debugger_url:
        raise _linux_error("Could not find the requested Google Chrome tab.")

    with websocket_connect(
        info.websocket_debugger_url,
        proxy=None,
        open_timeout=5,
        close_timeout=2,
    ) as websocket:
        websocket.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": javascript,
                        "returnByValue": True,
                        "awaitPromise": True,
                        "userGesture": True,
                    },
                }
            )
        )
        while True:
            message = json.loads(websocket.recv())
            if message.get("id") != 1:
                continue
            result = message.get("result", {})
            if result.get("exceptionDetails"):
                details = result["exceptionDetails"]
                description = (
                    details.get("exception", {}).get("description")
                    or details.get("text")
                    or "JavaScript execution failed."
                )
                raise RuntimeError(str(description))
            value = result.get("result", {}).get("value")
            if value is None and "description" in result.get("result", {}):
                value = result["result"]["description"]
            return _coerce_javascript_result(value)


def _run_osascript(script: str, *args: str) -> str:
    command = ["osascript", "-"] + list(args)
    result = subprocess.run(
        command,
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "osascript failed").strip()
        if "Executing JavaScript through AppleScript is turned off" in message:
            raise RuntimeError(
                "Google Chrome blocks JavaScript from Apple Events. In Chrome enable View > Developer > Allow JavaScript from Apple Events, then retry."
            )
        raise RuntimeError(message)
    return result.stdout.strip()


def is_google_chrome_running() -> bool:
    if not _is_mac_backend():
        if _linux_devtools_available():
            return True
        if _linux_should_use_x11_backend():
            try:
                _linux_atspi_primary_window()
                return True
            except RuntimeError:
                return False
        return False
    script = """
on run argv
    tell application "System Events"
        if exists process "Google Chrome" then
            return "true"
        end if
        return "false"
    end tell
end run
"""
    return _run_osascript(script) == "true"


def list_google_chrome_tabs() -> list[ChromeTabInfo]:
    if not _is_mac_backend():
        if _linux_should_use_x11_backend():
            return [_linux_x11_current_tab_info()]
        return _linux_list_google_chrome_tabs()
    script = """
on run argv
    tell application "Google Chrome"
        if not running then return "[]"
        set tabItems to {}
        repeat with targetWindow in windows
            set windowIdText to (id of targetWindow) as string
            set activeIndex to active tab index of targetWindow
            set tabCounter to 0
            repeat with targetTab in tabs of targetWindow
                set tabCounter to tabCounter + 1
                set tabIdText to (id of targetTab) as string
                set tabUrl to URL of targetTab
                set tabTitle to title of targetTab
                set isActive to (tabCounter is activeIndex)
                set end of tabItems to windowIdText & "||" & tabIdText & "||" & tabUrl & "||" & tabTitle & "||" & (isActive as string)
            end repeat
        end repeat
        return my joinLines(tabItems)
    end tell
end run

on joinLines(itemsList)
    set AppleScript's text item delimiters to linefeed
    set joinedText to itemsList as string
    set AppleScript's text item delimiters to ""
    return joinedText
end joinLines
"""
    raw = _run_osascript(script)
    if not raw.strip():
        return []
    tabs: list[ChromeTabInfo] = []
    for line in raw.splitlines():
        parts = line.split("||", 4)
        if len(parts) != 5:
            continue
        window_id, tab_id, url, title, active = parts
        tabs.append(
            ChromeTabInfo(
                window_id=int(window_id),
                tab_id=int(tab_id),
                url=url,
                title=title,
                active=active.strip().lower() == "true",
            )
        )
    return tabs


def get_tab_info(tab: ChromeTabRef) -> ChromeTabInfo | None:
    for candidate in list_google_chrome_tabs():
        if tab.target_id and candidate.target_id == tab.target_id:
            return candidate
        if tab.window_key == "linux-devtools" or candidate.window_key == "linux-devtools":
            continue
        if candidate.window_id == tab.window_id and candidate.tab_id == tab.tab_id:
            return candidate
    return None


def open_url_in_existing_chrome(target_url: str) -> ChromeTabRef:
    if not _is_mac_backend():
        if _linux_should_use_x11_backend():
            return _linux_x11_open_url_in_current_chrome(target_url)
        return _linux_open_url_in_existing_chrome(target_url)
    script = """
on run argv
    set targetUrl to item 1 of argv
    tell application "Google Chrome"
        if not running then error "Google Chrome is not running."
        if (count of windows) = 0 then error "No Google Chrome window is open."
        activate
        set targetWindow to front window
        tell targetWindow
            make new tab with properties {URL:targetUrl}
            set active tab index to (count of tabs)
            set targetTab to active tab
            return (id as string) & ":" & (id of targetTab as string)
        end tell
    end tell
end run
"""
    raw = _run_osascript(script, target_url)
    window_id, tab_id = raw.split(":")
    return ChromeTabRef(window_id=int(window_id), tab_id=int(tab_id))


def open_provider_tab_in_existing_chrome(provider: ProviderName, target_url: str) -> ChromeTabRef:
    return open_url_in_existing_chrome(target_url)


def open_gemini_tab_in_existing_chrome(gemini_url: str) -> ChromeTabRef:
    return open_url_in_existing_chrome(gemini_url)


def execute_javascript(tab: ChromeTabRef, javascript: str) -> str:
    if not _is_mac_backend():
        return _linux_execute_javascript(tab, javascript)
    script = """
on run argv
    set targetWindowId to (item 1 of argv) as integer
    set targetTabId to item 2 of argv
    set jsCode to item 3 of argv
    tell application "Google Chrome"
        set targetWindow to first window whose id is targetWindowId
        tell targetWindow
            repeat with targetTab in tabs
                if ((id of targetTab) as string) is targetTabId then
                    tell targetTab
                        return execute javascript jsCode
                    end tell
                end if
            end repeat
        end tell
    end tell
    error "Could not find the requested Google Chrome tab."
end run
"""
    return _run_osascript(
        script,
        str(tab.window_id),
        str(tab.tab_id),
        javascript,
    )


def wait_for_prompt_input(
    tab: ChromeTabRef,
    timeout_ms: int,
    *,
    provider: ProviderName = "gemini",
) -> None:
    if _linux_should_use_x11_backend():
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            try:
                _linux_x11_prompt_entry(tab, provider)
                return
            except RuntimeError:
                time.sleep(0.5)
        raise TimeoutError(
            f"Could not find {PROVIDER_LABELS[provider]} input box in the current Chrome tab."
        )
    deadline = time.monotonic() + timeout_ms / 1000
    probe = """
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const selectors = __SELECTORS__;
  const found = selectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .some((el) => isVisible(el));
  return found ? "ready" : "waiting";
})()
"""
    probe = probe.replace(
        "__SELECTORS__", json.dumps(PROMPT_SELECTORS[provider], ensure_ascii=False)
    )
    while time.monotonic() < deadline:
        if execute_javascript(tab, probe) == "ready":
            return
        time.sleep(1)
    raise TimeoutError(
        f"Could not find {PROVIDER_LABELS[provider]} input box in the current Chrome tab."
    )


def detect_login_or_verification(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> str | None:
    if _linux_should_use_x11_backend():
        return _linux_x11_login_state(provider)
    probe = """
(() => {
  const bodyText = (document.body?.innerText || "").toLowerCase();
  const promptSelectors = __SELECTORS__;
  const prompt = promptSelectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .find((element) => !!element);
  const authCtas = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .map((element) => `${element.innerText || ""} ${element.getAttribute("aria-label") || ""}`.toLowerCase());
  const hasPrompt = !!prompt;
  const hasLoginCta = authCtas.some((text) => /(^|\\s)(sign in|log in|sign up|continue with google|continue with apple)(\\s|$)/.test(text));
  const hasLoggedOutMarketingCopy = /log in to get answers based on saved chats|get responses tailored to you|see plans and pricing|welcome back/.test(bodyText);
  if (!hasPrompt && /verify|captcha|unusual traffic|human verification|prove you are human/.test(bodyText)) {
    return "manual_verification_required";
  }
  if ((hasLoginCta || hasLoggedOutMarketingCopy) && /log in|sign in|sign up|choose an account|continue with google|continue with apple|welcome back/.test(bodyText)) {
    return "login_required";
  }
  if (!hasPrompt && /sign in|log in|choose an account|continue to gemini|continue with google|continue with apple|welcome back|sign up/.test(bodyText)) {
    return "login_required";
  }
  return "";
})()
"""
    probe = probe.replace(
        "__SELECTORS__", json.dumps(PROMPT_SELECTORS[provider], ensure_ascii=False)
    )
    state = execute_javascript(tab, probe).strip()
    return state or None


def activate_create_image_mode(
    tab: ChromeTabRef,
    timeout_ms: int = 20_000,
    *,
    provider: ProviderName = "gemini",
) -> bool:
    label_pattern = (
        r"create image|image generation"
        if provider == "gemini"
        else r"create image|generate image|4o image|image"
    )
    menu_selectors = (
        ['[data-test-id="bard-mode-menu-button"]', 'button[aria-haspopup="menu"]']
        if provider == "gemini"
        else ['button[aria-haspopup="menu"]', '#composer-plus-btn', 'button[aria-label*="tools" i]', 'button[aria-label*="more" i]']
    )
    script = """
(() => {
  const isVisible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const clickLikeUser = (element) => {
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {
      element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    });
  };
  const direct = Array.from(document.querySelectorAll('button, [role="button"], [role="menuitem"], [role="option"]'))
    .find((el) => isVisible(el) && /__LABEL_PATTERN__/.test(`${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase()));
  if (direct) {
    clickLikeUser(direct);
    return "clicked";
  }
  const menuSelectors = __MENU_SELECTORS__;
  const modeButton = menuSelectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .find((el) => isVisible(el));
  if (modeButton) {
    clickLikeUser(modeButton);
    return "menu-opened";
  }
  return "not-found";
})()
"""
    script = script.replace("__LABEL_PATTERN__", label_pattern).replace(
        "__MENU_SELECTORS__", json.dumps(menu_selectors, ensure_ascii=False)
    )
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        result = execute_javascript(tab, script).strip()
        if result == "clicked":
            return True
        time.sleep(0.8)
    return False


def upload_local_file(
    tab: ChromeTabRef,
    *,
    file_path: Path,
    file_name: str,
    mime_type: str,
    timeout_ms: int = 90_000,
    provider: ProviderName = "gemini",
) -> None:
    payload_name = json.dumps(file_name, ensure_ascii=False)
    payload_mime = json.dumps(mime_type, ensure_ascii=False)
    payload_provider = json.dumps(provider, ensure_ascii=False)
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    chunk_size = 60_000
    chunks = [encoded[index : index + chunk_size] for index in range(0, len(encoded), chunk_size)]

    execute_javascript(
        tab,
        """
(() => {
  window.__codexUploadChunks = [];
  window.__codexUploadStatus = "preparing";
  return "ok";
})()
""",
    )
    for chunk in chunks:
        execute_javascript(
            tab,
            f"""
(() => {{
  window.__codexUploadChunks = window.__codexUploadChunks || [];
  window.__codexUploadChunks.push({json.dumps(chunk)});
  return "ok";
}})()
""",
        )

    bootstrap = f"""
(() => {{
  const fileName = {payload_name};
  const mimeType = {payload_mime};
  const provider = {payload_provider};
  window.__codexUploadStatus = "pending";
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const getDropTarget = () => {{
    const selectors = provider === "chatgpt"
      ? ["form", "#prompt-textarea", '[data-testid="composer"]', 'main']
      : ['[xapfileselectordropzone]', '.xap-uploader-dropzone', '[file-drop-zone]', '[data-node-type="input-area"]'];
    return selectors
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .find((el) => isVisible(el)) || null;
  }};
  const setFilesOnInput = (file) => {{
    const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
    for (const input of inputs) {{
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(file);
      input.files = dataTransfer.files;
      if (!input.files || input.files.length < 1) continue;
      input.dispatchEvent(new Event('input', {{ bubbles: true, composed: true }}));
      input.dispatchEvent(new Event('change', {{ bubbles: true, composed: true }}));
      const dropTarget = getDropTarget();
      if (provider !== "chatgpt" && dropTarget) {{
        for (const type of ["dragenter", "dragover", "drop"]) {{
          dropTarget.dispatchEvent(new DragEvent(type, {{ bubbles: true, cancelable: true, dataTransfer }}));
        }}
      }}
      return true;
    }}
    return false;
  }};
  const triggerUploadMenu = () => {{
    const trigger = Array.from(document.querySelectorAll('button, [role="button"]')).find((el) => {{
      if (!isVisible(el) || el.disabled) return false;
      const text = `${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase();
      return /upload & tools|upload|add files|files|photos|image/.test(text);
    }});
    if (!trigger) return false;
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {{
      trigger.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window }}));
    }});
    return true;
  }};
  const clickUploadFilesItem = () => {{
    const action = Array.from(document.querySelectorAll('button, [role="button"], [role="menuitem"]')).find((el) => {{
      if (!isVisible(el) || el.disabled) return false;
      const text = `${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase();
      return provider === "chatgpt"
        ? /upload files|upload from computer|photos|files|add photos/.test(text)
        : /upload files/.test(text);
    }});
    if (!action) return false;
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {{
      action.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window }}));
    }});
    return true;
  }};
  const dispatchPaste = (file) => {{
    const target = document.querySelector('.ql-editor[role="textbox"], [role="textbox"], div[contenteditable="true"], textarea') || getDropTarget();
    if (!target) return false;
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    const event = new ClipboardEvent("paste", {{ bubbles: true, cancelable: true }});
    Object.defineProperty(event, "clipboardData", {{ value: dataTransfer }});
    target.dispatchEvent(event);
    return true;
  }};
  const dispatchDrop = async (file) => {{
    const target = getDropTarget();
    if (!target) return false;
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    for (const type of ["dragenter", "dragover", "drop"]) {{
      target.dispatchEvent(new DragEvent(type, {{ bubbles: true, cancelable: true, dataTransfer }}));
    }}
    return true;
  }};
  const uploadLooksAttached = () => {{
    const body = document.body?.innerText || "";
    const fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
    if (fileInputs.some((input) => input.files && input.files.length > 0)) return true;
    if (body.includes(fileName)) return true;
    if (document.querySelector('img[src^="blob:"]')) return true;
    if (document.querySelector('[data-test-id*="upload" i], [data-testid*="upload" i], [data-test-id*="attachment" i], [data-testid*="attachment" i]')) return true;
    if (provider === "chatgpt" && document.querySelector('#prompt-textarea img, form img, [data-testid="composer-plus-btn"] + * img')) return true;
    return false;
  }};
  (async () => {{
    try {{
      const base64 = (window.__codexUploadChunks || []).join("");
      if (!base64) throw new Error("Upload content buffer is empty.");
      const binary = atob(base64);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {{
        bytes[index] = binary.charCodeAt(index);
      }}
      const file = new File([bytes], fileName, {{ type: mimeType || "application/octet-stream" }});
      let attached = setFilesOnInput(file);
      if (!attached) {{
        triggerUploadMenu();
        await wait(800);
        attached = setFilesOnInput(file);
      }}
      if (!attached) {{
        clickUploadFilesItem();
        await wait(800);
        attached = setFilesOnInput(file);
      }}
      if (!attached) {{
        attached = dispatchPaste(file);
        await wait(600);
      }}
      if (!attached) {{
        attached = setFilesOnInput(file);
      }}
      if (!attached) {{
        const dropped = await dispatchDrop(file);
        if (!dropped) throw new Error("Could not find an upload target in the current Chrome tab.");
      }}
      await wait(1500);
      window.__codexUploadStatus = uploadLooksAttached() ? "attached" : "awaiting-ack";
      window.__codexUploadChunks = [];
    }} catch (error) {{
      const message = error instanceof Error ? error.message : String(error);
      window.__codexUploadStatus = `error:${{message}}`;
      window.__codexUploadChunks = [];
    }}
  }})();
  return "started";
}})()
"""
    result = execute_javascript(tab, bootstrap)
    if result != "started":
        raise RuntimeError("Could not start image upload in the current Chrome tab.")

    deadline = time.monotonic() + timeout_ms / 1000
    ready_checks = 0
    while time.monotonic() < deadline:
        state = execute_javascript(
            tab,
            """
(() => window.__codexUploadStatus || "pending")()
""",
        ).strip()
        if state.startswith("error:"):
            raise RuntimeError(state.removeprefix("error:").strip() or "Image upload failed.")
        if state in {"attached", "awaiting-ack", "done"}:
            upload_state = inspect_upload_state(tab, provider=provider)
            if (
                upload_state.get("attachment")
                and upload_state.get("hasPreview")
                and not upload_state.get("loading")
                and upload_state.get("submitReady", True)
            ):
                ready_checks += 1
                if ready_checks >= 2:
                    execute_javascript(
                        tab,
                        """
(() => {
  window.__codexUploadStatus = "done";
  return "ok";
})()
""",
                    )
                    return
            else:
                ready_checks = 0
        time.sleep(1)
    raise TimeoutError("Timed out while waiting for image upload in the current Chrome tab.")


def inspect_upload_state(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> dict[str, Any]:
    readiness_probe = """
(() => {
  const isVisible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const provider = __PROVIDER__;
  const attachmentSelectors = provider === "chatgpt"
    ? ['img[src^="blob:"]', 'form img', '[data-testid*="attachment" i]', '[data-testid*="composer" i] img']
    : ['gem-media-attachment', '.gem-attachment', 'mat-basic-chip', '[data-test-id*="upload" i]'];
  const loadingSelectors = provider === "chatgpt"
    ? ['[role="status"]', '.animate-spin', '[data-testid*="uploading" i]', '[data-testid="send-button"][disabled]']
    : ['.gem-attachment-content.loading', '.gem-attachment-loading-container', 'mat-spinner[aria-label="Loading image"]'];
  const attachments = attachmentSelectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .filter((el, index, items) => isVisible(el) && items.indexOf(el) === index);
  const loadingBySelector = loadingSelectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .some((el) => {
      if (!isVisible(el)) return false;
      if (provider === "chatgpt" && el.matches('[data-testid="send-button"][disabled]')) return true;
      return /upload|loading|processing/.test(`${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase());
    });
  const loadingByCursor = provider === "chatgpt" && Array.from(document.querySelectorAll('*')).some((el) => {
    if (!isVisible(el)) return false;
    const className = (el.className || '').toString();
    return className.includes('cursor-wait');
  });
  const loading = !!loadingBySelector || !!loadingByCursor;
  const hasPreview = !!Array.from(document.querySelectorAll('img')).find((img) => {
    const src = img.src || '';
    return isVisible(img) && ((src.startsWith('blob:')) || (provider === "chatgpt" && img.naturalWidth >= 48 && img.naturalHeight >= 48));
  });
  const sendButton = Array.from(document.querySelectorAll('button, [role="button"]'))
    .find((el) => {
      if (!isVisible(el)) return false;
      const text = `${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase();
      return /send message|send prompt|send|submit/.test(text) || el.getAttribute("data-testid") === "send-button";
    });
  return JSON.stringify({
    attachment: attachments.length > 0,
    attachmentCount: attachments.length,
    loading,
    hasPreview,
    submitReady: !!sendButton && !sendButton.disabled,
  });
})()
"""
    readiness_probe = readiness_probe.replace(
        "__PROVIDER__", json.dumps(provider, ensure_ascii=False)
    )
    return json.loads(execute_javascript(tab, readiness_probe) or "{}")


def insert_prompt(
    tab: ChromeTabRef,
    prompt: str,
    *,
    provider: ProviderName = "gemini",
) -> None:
    if _linux_should_use_x11_backend():
        entry, window_id = _linux_x11_prompt_entry(tab, provider)
        _linux_x11_click(entry, window_id=window_id)
        time.sleep(0.15)
        _linux_x11_key("ctrl+a", "BackSpace", window_id=window_id)
        time.sleep(0.15)
        _linux_x11_type_text(prompt, window_id=window_id)
        window, _ = _linux_atspi_primary_window()
        items = _linux_atspi_flat_text_items(window)
        normalized_prompt = _linux_normalize_accessibility_text(prompt)
        if not any(normalized_prompt in _linux_normalize_accessibility_text(value) for _, value in items):
            raise RuntimeError(
                f"Could not insert prompt into {PROVIDER_LABELS[provider]} in the current Chrome tab."
            )
        return
    payload = json.dumps(prompt, ensure_ascii=False)
    script = f"""
(() => {{
  const text = {payload};
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const selectors = {json.dumps(PROMPT_SELECTORS[provider], ensure_ascii=False)};
  const candidates = selectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .filter((el, index, arr) => arr.indexOf(el) === index)
    .filter((el) => isVisible(el))
    .sort((a, b) => b.getBoundingClientRect().height - a.getBoundingClientRect().height);
  const target = candidates[0];
  if (!target) return "missing";
  target.focus();
  const dispatch = (type, extra = {{}}) => target.dispatchEvent(new InputEvent(type, {{ bubbles: true, cancelable: true, ...extra }}));
  if (target.tagName === "TEXTAREA" || target.tagName === "INPUT") {{
    target.value = text;
    target.dispatchEvent(new Event("input", {{ bubbles: true }}));
    target.dispatchEvent(new Event("change", {{ bubbles: true }}));
  }} else {{
    const selection = window.getSelection();
    if (selection) {{
      const range = document.createRange();
      range.selectNodeContents(target);
      selection.removeAllRanges();
      selection.addRange(range);
    }}
    document.execCommand("selectAll", false);
    const inserted = document.execCommand("insertText", false, text);
    if (!inserted || (target.innerText || "").trim() !== text.trim()) {{
      target.replaceChildren();
      const lines = text.split("\\n");
      lines.forEach((line) => {{
        const p = document.createElement("p");
        if (line) {{
          p.textContent = line;
        }} else {{
          p.appendChild(document.createElement("br"));
        }}
        target.appendChild(p);
      }});
    }}
    dispatch("beforeinput", {{ data: text, inputType: "insertText" }});
    dispatch("input", {{ data: text, inputType: "insertText" }});
    target.dispatchEvent(new KeyboardEvent("keyup", {{ key: "Unidentified", bubbles: true }}));
  }}
  const normalize = (value) => (value || "").replace(/\\r\\n/g, "\\n").replace(/\\n+/g, "\\n").trim();
  const finalText = normalize(target.innerText || target.value || "");
  const expected = normalize(text);
  return finalText === expected || finalText.includes(expected) ? "ok" : `mismatch:${{finalText}}`;
}})()
"""
    result = execute_javascript(tab, script)
    if result == "ok":
        return

    verify = execute_javascript(
        tab,
        f"""
(() => {{
  const selectors = {json.dumps(PROMPT_SELECTORS[provider], ensure_ascii=False)};
  const target = selectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .find((element) => !!element);
  if (!target) return "missing";
  const normalize = (value) => (value || "").replace(/\\r\\n/g, "\\n").replace(/\\n+/g, "\\n").trim();
  const current = normalize(target.innerText || target.value || "");
  const expected = normalize({payload});
  return current === expected || current.includes(expected) ? "ok" : current;
}})()
""",
    )
    if verify != "ok":
        raise RuntimeError(
            f"Could not insert prompt into {PROVIDER_LABELS[provider]} in the current Chrome tab."
        )


def submit_prompt(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> None:
    if _linux_should_use_x11_backend():
        entry, window_id = _linux_x11_prompt_entry(tab, provider)
        _linux_x11_click(entry, window_id=window_id)
        time.sleep(0.15)
        _linux_x11_key("Return", window_id=window_id)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            window, _ = _linux_atspi_primary_window()
            if _linux_x11_busy(window):
                return
            items = _linux_atspi_flat_text_items(window)
            if not any("enter a prompt for gemini" in (value or "").lower() and "ask gemini" not in (value or "").lower() for _, value in items):
                return
            time.sleep(0.4)
        return
    provider_selectors = json.dumps(PROMPT_SELECTORS[provider], ensure_ascii=False)
    script = """
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter((el) => isVisible(el));
  const sendButton = buttons.find((el) => {
    const text = `${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase();
    return /send message|send prompt|send|submit/.test(text) || el.getAttribute("data-testid") === "send-button";
  });
  const selectors = __SELECTORS__;
  const target = selectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .find((element) => !!element);
  const busyBefore = buttons.some((el) => /stop|cancel/.test(`${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase()));
  if (sendButton && !sendButton.disabled) {
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {
      sendButton.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    });
    return "clicked";
  }
  if (!target) return "missing";
  target.focus();
  ["keydown", "keypress", "keyup"].forEach((type) => {
    target.dispatchEvent(new KeyboardEvent(type, {
      key: "Enter",
      code: "Enter",
      keyCode: 13,
      which: 13,
      bubbles: true,
      cancelable: true,
    }));
  });
  const busyAfterEnter = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((el) => isVisible(el))
    .some((el) => /stop|cancel/.test(`${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase()));
  const promptText = (target.innerText || target.value || "").trim();
  if (busyAfterEnter || promptText === "") {
    return "enter";
  }
  return busyBefore ? "busy" : "not_submitted";
})()
"""
    script = script.replace("__SELECTORS__", provider_selectors)
    deadline = time.monotonic() + 12
    last_result = "not_submitted"
    initial_user_count = int(
        execute_javascript(
            tab,
            """
(() => document.querySelectorAll(
  '[data-message-author-role="user"], [data-message-author-role="human"]'
).length)()
""",
        )
        or 0
    )
    verification_script = """
(() => {
  const selectors = __SELECTORS__;
  const target = selectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .find((element) => !!element);
  const promptText = (target?.innerText || target?.value || "").trim();
  const userCount = document.querySelectorAll(
    '[data-message-author-role="user"], [data-message-author-role="human"]'
  ).length;
  return JSON.stringify({ promptEmpty: promptText === "", userCount });
})()
""".replace("__SELECTORS__", provider_selectors)
    while time.monotonic() < deadline:
        result = execute_javascript(tab, script)
        last_result = result or "not_submitted"
        if last_result in {"clicked", "enter", "busy"}:
            confirmation_deadline = min(deadline, time.monotonic() + 3)
            while time.monotonic() < confirmation_deadline:
                confirmation = json.loads(
                    execute_javascript(tab, verification_script) or "{}"
                )
                user_turn_added = int(
                    confirmation.get("userCount") or 0
                ) > initial_user_count
                if user_turn_added or (
                    provider != "chatgpt" and confirmation.get("promptEmpty")
                ):
                    return
                time.sleep(0.25)
            last_result = f"{last_result}_not_confirmed"
        time.sleep(0.75)
    raise RuntimeError(
        f"Could not submit the {PROVIDER_LABELS[provider]} prompt in the current Chrome tab. Last submit state: {last_result}."
    )


def detect_busy_state(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> bool:
    if _linux_should_use_x11_backend():
        window, _ = _linux_atspi_primary_window()
        return _linux_x11_busy(window)
    script = """
(() => {
  const isVisible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const busy = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((el) => isVisible(el))
    .some((el) => {
      const text = `${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase().trim();
      if (/stopped thinking/.test(text)) return false;
      return /stop answering|stop generating|stop|cancel/.test(text);
    });
  return busy ? "true" : "false";
})()
"""
    return execute_javascript(tab, script).strip() == "true"


def best_effort_stop(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> bool:
    if _linux_should_use_x11_backend():
        window, window_id = _linux_atspi_primary_window()
        button = _linux_x11_button(window, ("stop response", "stop generating", "stop answering", "cancel"))
        if button is None:
            return False
        _linux_x11_click(button, window_id=window_id)
        return True
    script = """
(() => {
  const isVisible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const target = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((el) => isVisible(el))
    .find((el) => {
      const text = `${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase().trim();
      if (/stopped thinking/.test(text)) return false;
      return /stop answering|stop generating|stop|cancel/.test(text);
    });
  if (!target) return "false";
  ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {
    target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
  });
  return "true";
})()
"""
    return execute_javascript(tab, script).strip() == "true"


def save_tab_html(tab: ChromeTabRef, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = execute_javascript(
        tab,
        """
(() => document.documentElement?.outerHTML || "")()
""",
    )
    output_path.write_text(html or "", encoding="utf-8")
    return output_path


def inspect_generated_image_state(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> dict[str, Any]:
    script = f"""
(() => {{
  const assistantRootSelectors = {json.dumps(_assistant_root_selectors(provider), ensure_ascii=False)};
  const loadingSelectors = {json.dumps(_image_loading_selectors(provider), ensure_ascii=False)};
  const generatedImageSelectors = {json.dumps(GENERATED_IMAGE_SELECTORS, ensure_ascii=False)};
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const collectAssistantRoots = () => {{
    const candidates = assistantRootSelectors
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((el) => isVisible(el));
    const unique = [];
    const seen = new Set();
    for (const candidate of candidates) {{
      const root = candidate.closest('article[data-testid^="conversation-turn-"]')
        || candidate.closest("article")
        || candidate;
      if (!root || !isVisible(root) || seen.has(root)) continue;
      seen.add(root);
      unique.push(root);
    }}
    return unique.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  }};
  const assistantRoots = collectAssistantRoots();
  const latestAssistantRoot = [...assistantRoots]
    .reverse()
    .find((root) => generatedImageSelectors
      .flatMap((selector) => Array.from(root.querySelectorAll(selector)))
      .some((img) => isVisible(img) && (img.naturalWidth || img.width || 0) >= 96 && (img.naturalHeight || img.height || 0) >= 96))
    || assistantRoots.at(-1)
    || null;
  const scope = latestAssistantRoot || document;
  const rootText = `${{latestAssistantRoot?.innerText || ""}}`.toLowerCase();
  const assistantImages = generatedImageSelectors
    .flatMap((selector) => Array.from(scope.querySelectorAll(selector)))
    .filter((img) => isVisible(img) && (img.naturalWidth || img.width || 0) >= 96 && (img.naturalHeight || img.height || 0) >= 96)
    .filter((img) => !img.closest('[data-message-author-role="user"]'));
  const downloadAffordance = Array.from(scope.querySelectorAll('a, button, [role="button"], [role="menuitem"]'))
    .filter((el) => isVisible(el))
    .some((el) => /download|save image|open image/.test(`${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase()));
  const generatedMarker = downloadAffordance
    || /generated image|created with|open image|download image|image ready/.test(rootText)
    || assistantImages.some((img) => /generated image/.test((img.alt || "").toLowerCase()));
  const loading = loadingSelectors
    .flatMap((selector) => Array.from(scope.querySelectorAll(selector)))
    .some((el) => isVisible(el) && /loading|creating|generating|rendering|processing/.test(`${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase() || "loading"))
    || (!!latestAssistantRoot && latestAssistantRoot.getAttribute("aria-busy") === "true");
  const busy = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((el) => isVisible(el))
    .some((el) => {{
      const text = `${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase().trim();
      if (/stopped thinking/.test(text)) return false;
      return /stop answering|stop generating|stop|cancel/.test(text);
    }});
  return JSON.stringify({{
    assistantImageCount: assistantImages.length,
    generatedMarker,
    downloadAffordance,
    loading,
    busy,
  }});
}})()
"""
    return json.loads(execute_javascript(tab, script) or "{}")


def wait_for_generated_image_ready(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
    timeout_ms: int = 90_000,
) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    stable_checks = 0
    while time.monotonic() < deadline:
        state = inspect_generated_image_state(tab, provider=provider)
        has_images = int(state.get("assistantImageCount") or 0) > 0
        is_ready = has_images and not state.get("loading") and not state.get("busy")
        if is_ready:
            stable_checks += 1
            if stable_checks >= 2:
                return True
        else:
            stable_checks = 0
        time.sleep(1)
    return False


def _read_generated_image_export_payloads(
    tab: ChromeTabRef,
    *,
    output_dir: Path,
    job_id: str,
    timeout_ms: int,
) -> list[Path]:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        state = execute_javascript(
            tab,
            """
(() => window.__codexGeneratedImageExport?.status || "pending")()
""",
        ).strip()
        if state == "done":
            break
        if state.startswith("error:"):
            raise RuntimeError(state.removeprefix("error:").strip() or "Generated image export failed.")
        time.sleep(1)
    else:
        raise TimeoutError("Timed out while exporting generated images from the current Chrome tab.")

    metadata = json.loads(
        execute_javascript(
            tab,
            """
(() => JSON.stringify(window.__codexGeneratedImageExport?.items || []))()
""",
        )
        or "[]"
    )
    saved_paths: list[Path] = []
    chunk_size = 50_000
    seen_payload_hashes: set[bytes] = set()
    for index, item in enumerate(metadata):
        size = int(item.get("size") or 0)
        if size < 1:
            continue
        data_url_parts: list[str] = []
        for start in range(0, size, chunk_size):
            chunk = execute_javascript(
                tab,
                f"""
(() => (window.__codexGeneratedImageExport?.payloads?.[{index}]?.data || "").slice({start}, {start + chunk_size}))()
""",
            )
            if not chunk:
                break
            data_url_parts.append(chunk)
        data_url = "".join(data_url_parts)
        if not data_url.startswith("data:") or "," not in data_url:
            continue
        header, encoded = data_url.split(",", 1)
        mime = item.get("mime") or header.split(";")[0].removeprefix("data:") or "image/png"
        if not str(mime).lower().startswith("image/"):
            continue
        payload_bytes = base64.b64decode(encoded)
        if not _looks_like_image_bytes(payload_bytes):
            continue
        if payload_bytes in seen_payload_hashes:
            continue
        seen_payload_hashes.add(payload_bytes)
        suffix = ".jpg" if "jpeg" in mime else ".webp" if "webp" in mime else ".png"
        file_name = slugify_filename(f"{job_id}_output_{index + 1}{suffix}")
        output_path = output_dir / file_name
        output_path.write_bytes(payload_bytes)
        saved_paths.append(output_path)

    execute_javascript(
        tab,
        """
(() => {
  window.__codexGeneratedImageExport = { status: "cleared", items: [], payloads: [] };
  return "ok";
})()
""",
    )
    return saved_paths


def _looks_like_image_bytes(payload: bytes) -> bool:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if payload.startswith(b"\xff\xd8\xff"):
        return True
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return True
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return True
    return False


def export_generated_images(
    tab: ChromeTabRef,
    *,
    output_dir: Path,
    job_id: str,
    provider: ProviderName = "gemini",
    max_images: int = 4,
    timeout_ms: int = 90_000,
    strategy: Literal["download", "asset_url", "dom"] = "dom",
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    assistant_root_selectors = _assistant_root_selectors(provider)
    bootstrap = f"""
(() => {{
  window.__codexGeneratedImageExport = {{ status: "pending", items: [], payloads: [] }};
  const strategy = {json.dumps(strategy)};
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const readAsDataUrl = (blob) => new Promise((resolve, reject) => {{
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("Could not read generated image blob."));
    reader.readAsDataURL(blob);
  }});
  const exportUrl = async (source, index, forcedStem = null) => {{
    if (!source) return null;
    if ((strategy === "download" || strategy === "asset_url") && /^(data:|blob:)/.test(source)) return null;
    try {{
      const response = await fetch(source);
      const blob = await response.blob();
      const mime = blob.type || "image/png";
      if (!mime.toLowerCase().startsWith("image/")) return null;
      const ext = mime.includes("jpeg") ? "jpg" : mime.includes("webp") ? "webp" : "png";
      const data = await readAsDataUrl(blob);
      return {{ name: `${{forcedStem || `generated_${{index + 1}}`}}.${{ext}}`, mime, data }};
    }} catch (_error) {{}}
    return null;
  }};
  const exportImage = async (img, index) => {{
    const source = img.currentSrc || img.src || "";
    if (!source) return null;
    const exported = await exportUrl(source, index);
    if (exported) return exported;
    if (strategy !== "dom") return null;
    try {{
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth || img.width || 0;
      canvas.height = img.naturalHeight || img.height || 0;
      const ctx = canvas.getContext("2d");
      if (!ctx || !canvas.width || !canvas.height) return null;
      ctx.drawImage(img, 0, 0);
      const data = canvas.toDataURL("image/png");
      return {{ name: `generated_${{index + 1}}.png`, mime: "image/png", data }};
    }} catch (_error) {{
      return null;
    }}
  }};
  const assistantRootSelectors = {json.dumps(assistant_root_selectors, ensure_ascii=False)};
  const collectAssistantRoots = () => {{
    const candidates = assistantRootSelectors
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((el) => isVisible(el));
    const unique = [];
    const seen = new Set();
    for (const candidate of candidates) {{
      const root = candidate.closest('article[data-testid^="conversation-turn-"]')
        || candidate.closest('article')
        || candidate;
      if (!root || !isVisible(root) || seen.has(root)) continue;
      seen.add(root);
      unique.push(root);
    }}
    return unique.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  }};
  (async () => {{
    try {{
      const generatedImageSelectors = {json.dumps(GENERATED_IMAGE_SELECTORS, ensure_ascii=False)};
      const assistantRoots = collectAssistantRoots();
      const latestAssistantRoot = [...assistantRoots]
        .reverse()
        .find((root) => generatedImageSelectors.some((selector) => Array.from(root.querySelectorAll(selector)).some((img) => isVisible(img))))
        || assistantRoots.at(-1)
        || null;
      const imageScope = latestAssistantRoot || document;
      const rootText = `${{latestAssistantRoot?.innerText || ""}}`.toLowerCase();
      const downloadAffordance = Array.from(imageScope.querySelectorAll('a, button, [role="button"], [role="menuitem"]'))
        .filter((el) => isVisible(el))
        .some((el) => /download|save image|open image/.test(`${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase()));
      const downloadLinks = Array.from(imageScope.querySelectorAll('a[href]'))
        .map((link) => {{
          const href = link.href || "";
          const text = `${{link.innerText || ""}} ${{link.getAttribute("aria-label") || ""}}`.toLowerCase();
          return {{
            href,
            text,
            visible: isVisible(link),
            generatedHint:
              /download|save image|open image|generated image/.test(text)
              || /[?&](download|format)=/i.test(href)
              || /\\.(png|jpe?g|webp)(\\?|#|$)/i.test(href),
          }};
        }})
        .filter((item) => item.href && item.visible)
        .sort((a, b) => Number(b.generatedHint) - Number(a.generatedHint));
      const candidates = generatedImageSelectors
        .flatMap((selector) => Array.from(imageScope.querySelectorAll(selector)))
        .filter((img) => isVisible(img) && img.naturalWidth >= 160 && img.naturalHeight >= 160)
        .filter((img) => !img.closest('[data-message-author-role="user"]'))
        .map((img) => ({{
          img,
          src: img.currentSrc || img.src || "",
          area: (img.naturalWidth || img.width || 0) * (img.naturalHeight || img.height || 0),
          top: img.getBoundingClientRect().top,
          generatedHint:
            /generated image/i.test(img.alt || "")
            || downloadAffordance
            || /generated image|created with|open image|download image|image ready/.test(rootText),
        }}))
        .filter((item) => item.src)
        .sort((a, b) => Number(b.generatedHint) - Number(a.generatedHint) || b.area - a.area || a.top - b.top);
      const payloads = [];
      if (strategy === "download") {{
        const seenLinks = new Set();
        for (const link of downloadLinks) {{
          if (seenLinks.has(link.href)) continue;
          seenLinks.add(link.href);
          const exported = await exportUrl(link.href, payloads.length, `generated_${{payloads.length + 1}}`);
          if (exported?.data) payloads.push(exported);
          if (payloads.length >= {max_images}) break;
        }}
      }} else {{
        const preferredCandidates = candidates.filter((item) => item.generatedHint);
        const rankedCandidates = preferredCandidates.length ? preferredCandidates : candidates;
        const unique = [];
        const seen = new Set();
        for (const item of rankedCandidates) {{
          if (seen.has(item.src)) continue;
          seen.add(item.src);
          unique.push(item.img);
          if (unique.length >= {max_images}) break;
        }}
        for (let index = 0; index < unique.length; index += 1) {{
          const exported = await exportImage(unique[index], index);
          if (exported?.data) payloads.push(exported);
        }}
      }}
      window.__codexGeneratedImageExport = {{
        status: "done",
        items: payloads.map((item) => ({{
          name: item.name,
          mime: item.mime,
          size: item.data.length,
        }})),
        payloads,
      }};
    }} catch (error) {{
      const message = error instanceof Error ? error.message : String(error);
      window.__codexGeneratedImageExport = {{ status: `error:${{message}}`, items: [], payloads: [] }};
    }}
  }})();
  return "started";
}})()
"""
    result = execute_javascript(tab, bootstrap)
    if result != "started":
        raise RuntimeError("Could not start generated image export in the current Chrome tab.")
    return _read_generated_image_export_payloads(
        tab,
        output_dir=output_dir,
        job_id=job_id,
        timeout_ms=timeout_ms,
    )


def save_generated_images(
    tab: ChromeTabRef,
    *,
    output_dir: Path,
    job_id: str,
    provider: ProviderName = "gemini",
    max_images: int = 4,
    timeout_ms: int = 90_000,
) -> list[Path]:
    return export_generated_images(
        tab,
        output_dir=output_dir,
        job_id=job_id,
        provider=provider,
        max_images=max_images,
        timeout_ms=timeout_ms,
        strategy="dom",
    )


def wait_for_response_stable(
    tab: ChromeTabRef,
    *,
    timeout_ms: int,
    stable_seconds: int,
    excluded_text: str,
    previous_response: str = "",
    previous_assistant_turn_count: int | None = None,
    expect_images: bool = False,
    provider: ProviderName = "gemini",
    should_cancel: Callable[[], bool] | None = None,
    stall_refresh_seconds: int = 0,
    max_stall_refreshes: int = 0,
    recovery_callback: Callable[[str], None] | None = None,
) -> str:
    if _linux_should_use_x11_backend():
        if provider != "gemini":
            raise TimeoutError("Linux X11 fallback currently supports Gemini text responses only.")
        deadline = time.monotonic() + timeout_ms / 1000
        stable_since = time.monotonic()
        last_text = ""
        while time.monotonic() < deadline:
            if should_cancel and should_cancel():
                raise TimeoutError("__ORD_CANCELLED__")
            window, _ = _linux_atspi_primary_window()
            busy = _linux_x11_busy(window)
            current = _linux_x11_latest_gemini_answer(_linux_atspi_flat_text_items(window), excluded_text)
            if current and current != last_text:
                last_text = current
                stable_since = time.monotonic()
            elif current and not busy and (time.monotonic() - stable_since) >= stable_seconds:
                return current
            time.sleep(1)
        raise TimeoutError(
            f"{PROVIDER_LABELS[provider]} did not finish response within the timeout in the current Chrome tab."
        )
    started_at = time.monotonic()
    deadline = started_at + timeout_ms / 1000
    stable_since = time.monotonic()
    last_meaningful_progress = stable_since
    last_text = ""
    last_generated_images = 0
    refresh_count = 0
    payload = json.dumps(excluded_text, ensure_ascii=False)
    previous_payload = json.dumps(previous_response, ensure_ascii=False)
    previous_turn_count_payload = json.dumps(previous_assistant_turn_count)
    assistant_root_selectors = (
        [
            '[data-message-author-role="assistant"]',
        ]
        if provider == "chatgpt"
        else [
            '[data-response-id]',
            '[data-message-author-role="model"]',
            'main .model-response',
            'main .markdown',
            'main .prose',
        ]
    )
    probe = f"""
(() => {{
  const excluded = {payload};
  const previousResponse = {previous_payload};
  const previousAssistantTurnCount = {previous_turn_count_payload};
  const transientPatterns = [
    /^thinking(?:\\.\\.\\.)?$/i,
    /^analyzing(?:\\.\\.\\.)?$/i,
    /^searching(?:\\.\\.\\.)?$/i,
    /^working(?:\\.\\.\\.)?$/i,
    /^reasoning(?:\\.\\.\\.)?$/i,
    /^reasoned for \\d+/i,
  ];
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const clean = (text) => (text || "").replace(/\\r\\n/g, "\\n").trim();
  const assistantRootSelectors = {json.dumps(assistant_root_selectors, ensure_ascii=False)};
  const assistantRoots = assistantRootSelectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .map((candidate) => candidate.closest('article[data-testid^="conversation-turn-"]') || candidate.closest('article') || candidate)
    .filter((root, index, arr) => root && arr.indexOf(root) === index);
  const userRoots = Array.from(document.querySelectorAll(
    '[data-message-author-role="user"], section[data-turn="user"]'
  ))
    .map((candidate) => candidate.closest('[data-testid^="conversation-turn-"]') || candidate.closest('article') || candidate)
    .filter((root, index, arr) => root && arr.indexOf(root) === index);
  const latestAssistantRoot = assistantRoots.at(-1) || null;
  const latestUserRoot = userRoots.at(-1) || null;
  const latestText = clean(latestAssistantRoot?.innerText || "");
  const latestUserText = clean(latestUserRoot?.innerText || "");
  const latestTransient = transientPatterns.some((pattern) => pattern.test(latestText));
  const exchangeMarker = clean(excluded).match(/ORDAK_EXCHANGE_ID_[a-f0-9]+/i)?.[0] || "";
  const latestUserMatchesExcluded = exchangeMarker
    ? latestUserText.includes(exchangeMarker)
    : latestUserText === clean(excluded)
      || latestUserText.startsWith(`${{clean(excluded)}}\\n`)
      || latestUserText.startsWith(clean(excluded));
  const assistantAfterExcludedUser = Boolean(
    latestAssistantRoot
    && latestUserRoot
    && latestUserMatchesExcluded
    && (latestUserRoot.compareDocumentPosition(latestAssistantRoot) & Node.DOCUMENT_POSITION_FOLLOWING)
  );
  const requireOrderedUserTurn = {json.dumps(provider == "chatgpt")};
  const turnAdvanced = requireOrderedUserTurn
    ? assistantAfterExcludedUser
    : previousAssistantTurnCount === null
      || assistantRoots.length > previousAssistantTurnCount
      || latestText !== clean(previousResponse);
  const answer = turnAdvanced
    && latestText
    && latestText !== clean(excluded)
    && (latestText !== clean(previousResponse) || assistantAfterExcludedUser)
    && !latestTransient
      ? latestText
      : "";
  const allGeneratedImageCandidates = Array.from((latestAssistantRoot || document).querySelectorAll('img, generated-image img, .generated-images-container img, .image-gallery img, picture img'))
    .filter((img) => isVisible(img) && img.naturalWidth >= 96 && img.naturalHeight >= 96)
    .filter((img) => !img.closest('[data-message-author-role="user"]'));
  const generatedHintCandidates = allGeneratedImageCandidates.filter((img) => /generated image/i.test(img.alt || ""));
  const generatedImages = (generatedHintCandidates.length ? generatedHintCandidates : allGeneratedImageCandidates).length;
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter((el) => isVisible(el));
  const busy = buttons.some((el) => {{
    const text = `${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase().trim();
    if (/stopped thinking/.test(text)) return false;
    return /stop answering|stop generating|stop|cancel/.test(text);
  }});
  return JSON.stringify({{
    answer,
    busy,
    generatedImages,
    assistantTurnCount: assistantRoots.length,
    latestTransient,
  }});
}})()
"""
    while time.monotonic() < deadline:
        if should_cancel and should_cancel():
            raise TimeoutError("__ORD_CANCELLED__")
        state = json.loads(execute_javascript(tab, probe) or "{}")
        current = (state.get("answer") or "").strip()
        busy = bool(state.get("busy"))
        generated_images = int(state.get("generatedImages") or 0)
        now = time.monotonic()
        if busy or current != last_text or generated_images != last_generated_images:
            # A visible stop control, streaming text, or a changing image count
            # is positive progress.  Never turn an active generation into a
            # stall merely because it has taken a long time.
            last_meaningful_progress = now
        should_refresh = (
            provider == "chatgpt"
            and not busy
            and stall_refresh_seconds > 0
            and refresh_count < max_stall_refreshes
            and now - last_meaningful_progress >= stall_refresh_seconds
        )
        if should_refresh:
            refresh_count += 1
            message = (
                "ChatGPT response is still pending. Refreshing the exact conversation "
                f"to reconcile the latest assistant turn ({refresh_count}/{max_stall_refreshes})."
            )
            if recovery_callback is not None:
                recovery_callback(message)
            info = get_tab_info(tab)
            recovery_url = info.url if info is not None else ""
            try:
                if recovery_url:
                    execute_javascript(
                        tab,
                        f"window.location.href = {recovery_url!r}; 'recovering'",
                    )
                else:
                    execute_javascript(tab, "window.location.reload(); 'recovering'")
            except RuntimeError:
                pass
            time.sleep(4)
            try:
                wait_for_prompt_input(
                    tab,
                    timeout_ms=min(30_000, max(1_000, int((deadline - time.monotonic()) * 1000))),
                    provider=provider,
                )
            except (RuntimeError, TimeoutError):
                pass
            stable_since = time.monotonic()
            last_meaningful_progress = stable_since
            last_text = ""
            last_generated_images = 0
            continue
        stable_elapsed = time.monotonic() - stable_since
        if current and current != last_text:
            last_text = current
            stable_since = time.monotonic()
            stable_elapsed = 0
        elif current and not busy and stable_elapsed >= stable_seconds:
            return current
        elif expect_images and generated_images > 0 and not busy and stable_elapsed >= stable_seconds:
            return f"__GENERATED_IMAGES__:{generated_images}"
        last_generated_images = generated_images
        time.sleep(1)
    raise TimeoutError(
        f"{PROVIDER_LABELS[provider]} did not finish response within the timeout in the current Chrome tab."
    )


def read_latest_response_baseline(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> ResponseBaseline:
    selectors = (
        [
            '[data-response-id]',
            '[data-message-author-role="model"]',
            'main article',
            'main [role="article"]',
            'main .markdown',
            'main .model-response',
        ]
        if provider == "gemini"
        else [
            '[data-message-author-role="assistant"]',
            'article[data-testid^="conversation-turn-"] [data-message-author-role="assistant"]',
            'main [data-message-author-role="assistant"]',
            'main article',
            'main .markdown',
            'main .prose',
        ]
    )
    root_selectors = (
        ['[data-message-author-role="assistant"]']
        if provider == "chatgpt"
        else selectors
    )
    script = f"""
(() => {{
  const clean = (text) => (text || "").replace(/\\r\\n/g, "\\n").trim();
  const roots = {json.dumps(root_selectors, ensure_ascii=False)}
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .map((candidate) => candidate.closest('article[data-testid^="conversation-turn-"]') || candidate.closest('article') || candidate)
    .filter((root, index, arr) => root && arr.indexOf(root) === index);
  return JSON.stringify({{
    text: clean(roots.at(-1)?.innerText || ""),
    assistantTurnCount: roots.length,
  }});
}})()
"""
    state = json.loads(execute_javascript(tab, script) or "{}")
    return ResponseBaseline(
        text=str(state.get("text") or "").strip(),
        assistant_turn_count=int(state.get("assistantTurnCount") or 0),
    )


def read_latest_response_text(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> str:
    return read_latest_response_baseline(tab, provider=provider).text
