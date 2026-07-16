from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
import platform
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, ProxyHandler, build_opener

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from app.config import Settings, settings


def slugify_step_name(step_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", step_name).strip("_") or "step"


@dataclass(slots=True)
class BrowserSession:
    playwright: Playwright
    context: BrowserContext
    runtime_user_data_dir: Path | None = None


def _load_local_state(user_data_dir: Path) -> dict[str, object]:
    local_state_path = user_data_dir / "Local State"
    if not local_state_path.exists():
        return {}
    try:
        return json.loads(local_state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def detect_selected_profile(app_settings: Settings | None = None) -> str:
    resolved = app_settings or settings
    configured = resolved.browser_profile_name.strip()
    if configured and configured.lower() != "auto":
        return configured
    local_state = _load_local_state(resolved.browser_user_data_dir)
    last_used = (
        local_state.get("profile", {}).get("last_used")
        if isinstance(local_state.get("profile"), dict)
        else None
    )
    if isinstance(last_used, str) and last_used.strip():
        return last_used.strip()
    return "Default"


def prepare_runtime_profile(
    app_settings: Settings | None = None,
    *,
    job_id: str | None = None,
) -> tuple[Path, str]:
    resolved = app_settings or settings
    selected_profile = detect_selected_profile(resolved)
    source_root = resolved.browser_user_data_dir
    source_profile_dir = source_root / selected_profile
    if not source_profile_dir.exists():
        raise FileNotFoundError(
            f"Chrome profile '{selected_profile}' was not found in {source_root}."
        )

    runtime_root = resolved.browser_runtime_root_dir / (job_id or uuid.uuid4().hex)
    runtime_root.mkdir(parents=True, exist_ok=True)

    _copy_profile_tree(source_root, source_profile_dir, runtime_root, selected_profile)
    return runtime_root, selected_profile


def _copy_profile_tree(
    source_root: Path,
    source_profile_dir: Path,
    target_root: Path,
    selected_profile: str,
) -> None:
    local_state_path = source_root / "Local State"
    if local_state_path.exists():
        shutil.copy2(local_state_path, target_root / "Local State")

    first_run_path = source_root / "First Run"
    if first_run_path.exists():
        shutil.copy2(first_run_path, target_root / "First Run")

    shutil.copytree(
        source_profile_dir,
        target_root / selected_profile,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "Crashpad",
            "Code Cache",
            "Cache",
            "GPUCache",
            "GrShaderCache",
            "ShaderCache",
            "Safe Browsing*",
            "*.tmp",
            "*.log",
            "Singleton*",
            "lockfile",
        ),
    )


def has_persistent_login_profile(app_settings: Settings | None = None) -> bool:
    resolved = app_settings or settings
    selected_profile = detect_selected_profile(resolved)
    return (resolved.browser_login_profile_dir / selected_profile).exists()


def ensure_persistent_login_profile(
    app_settings: Settings | None = None,
) -> tuple[Path, str]:
    resolved = app_settings or settings
    selected_profile = detect_selected_profile(resolved)
    target_root = resolved.browser_login_profile_dir
    target_profile_dir = target_root / selected_profile
    if target_profile_dir.exists() and any(target_profile_dir.iterdir()):
        return target_root, selected_profile

    source_root = resolved.browser_user_data_dir
    source_profile_dir = source_root / selected_profile
    if not source_profile_dir.exists():
        raise FileNotFoundError(
            f"Chrome profile '{selected_profile}' was not found in {source_root}."
        )

    target_root.mkdir(parents=True, exist_ok=True)
    _copy_profile_tree(source_root, source_profile_dir, target_root, selected_profile)
    return target_root, selected_profile


def create_browser_context(
    app_settings: Settings | None = None,
    *,
    job_id: str | None = None,
    prefer_persistent_login: bool = True,
) -> BrowserSession:
    resolved = app_settings or settings
    playwright = sync_playwright().start()
    runtime_user_data_dir: Path | None = None
    profile_name = resolved.browser_profile_name

    launch_kwargs: dict[str, object] = {
        "headless": resolved.browser_headless,
        "slow_mo": resolved.browser_slow_mo_ms,
        "accept_downloads": True,
        "viewport": {"width": 1365, "height": 900},
    }

    if resolved.browser_engine == "chrome":
        if prefer_persistent_login and has_persistent_login_profile(resolved):
            user_data_dir, profile_name = ensure_persistent_login_profile(resolved)
        else:
            runtime_user_data_dir, profile_name = prepare_runtime_profile(
                resolved,
                job_id=job_id,
            )
            user_data_dir = runtime_user_data_dir
        launch_kwargs["executable_path"] = str(resolved.browser_executable_path)
        launch_kwargs["args"] = [f"--profile-directory={profile_name}"]
    else:
        user_data_dir = resolved.browser_profile_dir

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(user_data_dir),
        **launch_kwargs,
    )
    context.set_default_timeout(resolved.browser_timeout_ms)
    return BrowserSession(
        playwright=playwright,
        context=context,
        runtime_user_data_dir=runtime_user_data_dir,
    )


def close_browser_context(
    session: BrowserSession | None,
) -> None:
    try:
        if session is not None:
            session.context.close()
    except BaseException:
        pass
    finally:
        try:
            if session is not None:
                session.playwright.stop()
        except BaseException:
            pass
        if session is not None and session.runtime_user_data_dir is not None:
            shutil.rmtree(session.runtime_user_data_dir, ignore_errors=True)


def start_trace(context: BrowserContext) -> None:
    context.tracing.start(screenshots=True, snapshots=True, sources=True)


def stop_trace(
    context: BrowserContext,
    job_id: str,
    app_settings: Settings | None = None,
) -> Path:
    resolved = app_settings or settings
    trace_path = resolved.browser_trace_dir / f"{job_id}.zip"
    context.tracing.stop(path=str(trace_path))
    return trace_path


def take_screenshot(
    page: Page,
    job_id: str,
    step_name: str,
    app_settings: Settings | None = None,
) -> Path:
    resolved = app_settings or settings
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    file_name = f"{job_id}_{timestamp}_{slugify_step_name(step_name)}.png"
    target = resolved.browser_screenshot_dir / file_name
    page.screenshot(path=str(target), full_page=True)
    return target


def linux_remote_debugging_available(app_settings: Settings | None = None) -> bool:
    resolved = app_settings or settings
    try:
        opener = build_opener(ProxyHandler({}))
        request = Request(f"{resolved.browser_remote_debugging_url.rstrip('/')}/json/version")
        with opener.open(request, timeout=5) as response:
            return response.status == 200
    except (URLError, HTTPError, TimeoutError, ValueError):
        return False


def _linux_remote_debugging_port(app_settings: Settings | None = None) -> int:
    resolved = app_settings or settings
    parsed = urlparse(resolved.browser_remote_debugging_url)
    if parsed.scheme not in {"http", "https"} or parsed.port is None:
        raise RuntimeError(
            "BROWSER_REMOTE_DEBUGGING_URL must include an explicit host and port, for example http://127.0.0.1:9222."
        )
    return parsed.port


def _linux_launch_remote_debugging_chrome(
    *,
    app_settings: Settings | None = None,
    target_url: str | None = None,
) -> None:
    resolved = app_settings or settings
    port = _linux_remote_debugging_port(resolved)
    user_data_dir = resolved.browser_remote_debugging_user_data_dir
    user_data_dir.mkdir(parents=True, exist_ok=True)
    launch_url = target_url or "about:blank"
    cmd = [
        str(resolved.browser_executable_path),
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        launch_url,
    ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _wait_for_linux_remote_debugging(app_settings: Settings | None = None) -> None:
    resolved = app_settings or settings
    deadline = time.monotonic() + resolved.browser_remote_debugging_launch_timeout_ms / 1000
    while time.monotonic() < deadline:
        if linux_remote_debugging_available(resolved):
            return
        time.sleep(0.5)
    raise RuntimeError(
        "Google Chrome remote debugging did not become reachable after launch. "
        f"Expected DevTools at {resolved.browser_remote_debugging_url}."
    )


def _linux_remote_debugging_unavailable_message(app_settings: Settings | None = None) -> str:
    resolved = app_settings or settings
    return (
        "Google Chrome remote debugging is not reachable. "
        f"Start Chrome on Linux with DevTools exposed at {resolved.browser_remote_debugging_url}, "
        "keep the signed-in Gemini/ChatGPT session there, then retry."
    )


def ensure_linux_remote_debugging_session(
    app_settings: Settings | None = None,
    *,
    target_url: str | None = None,
) -> None:
    resolved = app_settings or settings
    if platform.system().lower() != "linux":
        return
    if linux_remote_debugging_available(resolved):
        return
    if not resolved.browser_remote_debugging_auto_launch:
        raise RuntimeError(_linux_remote_debugging_unavailable_message(resolved))

    _linux_launch_remote_debugging_chrome(
        app_settings=resolved,
        target_url=target_url,
    )
    _wait_for_linux_remote_debugging(resolved)


def open_profile_browser_session(app_settings: Settings | None = None) -> None:
    resolved = app_settings or settings
    from app.automation.existing_chrome import open_gemini_tab_in_existing_chrome

    if platform.system().lower() == "linux":
        if not linux_remote_debugging_available(resolved):
            _linux_launch_remote_debugging_chrome(
                app_settings=resolved,
                target_url="about:blank",
            )
            _wait_for_linux_remote_debugging(resolved)
    open_gemini_tab_in_existing_chrome(resolved.gemini_url)


def _launch_normal_chrome_for_login(
    *,
    user_data_dir: Path,
    profile_name: str,
    gemini_url: str,
    executable_path: Path,
) -> None:
    system = platform.system().lower()
    if system == "darwin":
        app_path = str(executable_path).split("/Contents/MacOS/")[0]
        cmd = [
            "open",
            "-na",
            app_path,
            "--args",
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_name}",
            "--new-window",
            "--no-first-run",
            gemini_url,
        ]
    elif system == "windows":
        cmd = [
            str(executable_path),
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_name}",
            "--new-window",
            "--no-first-run",
            gemini_url,
        ]
    else:
        cmd = [
            str(executable_path),
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_name}",
            "--new-window",
            "--no-first-run",
            gemini_url,
        ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
