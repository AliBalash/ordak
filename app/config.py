from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import platform
from typing import Literal

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    return int(value.strip())


def _as_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _resolve_path(value: str, *, base_dir: Path = ROOT_DIR) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _split_csv(value: str | None) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _resolve_csv_paths(
    value: str | None,
    *,
    default: list[str] | None = None,
) -> tuple[Path, ...]:
    if value is None:
        raw_values = list(default or [])
    else:
        raw_values = _split_csv(value)
    return tuple(
        _resolve_path(os.path.expanduser(item), base_dir=Path("/"))
        for item in raw_values
    )


def _split_csv_upper(value: str | None, *, default: list[str] | None = None) -> tuple[str, ...]:
    raw_values = _split_csv(value) or list(default or [])
    cleaned = []
    for item in raw_values:
        normalized = item.strip()
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return tuple(cleaned)


def _default_chrome_executable() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if system == "windows":
        return r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    return "/usr/bin/google-chrome"


def _default_chrome_user_data_dir() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return str(Path.home() / "Library/Application Support/Google/Chrome")
    if system == "windows":
        return str(Path.home() / "AppData/Local/Google/Chrome/User Data")
    return str(Path.home() / ".config/google-chrome")


def _default_remote_debugging_user_data_dir() -> str:
    system = platform.system().lower()
    if system == "linux":
        return str(Path.home() / ".config/ordak-chrome")
    return str(ROOT_DIR / "app" / "storage" / "profiles" / "gemini-login")


@dataclass(slots=True)
class Settings:
    product_name: str
    product_label: str
    browser_platform: str
    app_host: str
    app_port: int
    database_url: str
    browser_headless: bool
    browser_slow_mo_ms: int
    browser_timeout_ms: int
    browser_engine: str
    browser_executable_path: Path
    browser_remote_debugging_url: str
    browser_remote_debugging_auto_launch: bool
    browser_remote_debugging_launch_timeout_ms: int
    browser_remote_debugging_user_data_dir: Path
    browser_linux_x11_fallback_enabled: bool
    browser_user_data_dir: Path
    browser_profile_name: str
    browser_profile_dir: Path
    browser_login_profile_dir: Path
    browser_runtime_root_dir: Path
    browser_screenshot_dir: Path
    browser_trace_dir: Path
    browser_log_dir: Path
    browser_upload_dir: Path
    browser_output_dir: Path
    gemini_url: str
    gemini_response_timeout_ms: int
    gemini_stable_response_seconds: int
    chatgpt_url: str
    chatgpt_project_url: str | None
    chatgpt_agent_url: str | None
    chatgpt_response_timeout_ms: int
    chatgpt_stable_response_seconds: int
    chatgpt_stall_refresh_seconds: int
    chatgpt_max_stall_refreshes: int
    agent_enabled: bool
    agent_allowed_workspace_roots: tuple[Path, ...]
    agent_default_max_steps: int
    agent_max_allowed_steps: int
    agent_default_command_timeout_seconds: int
    agent_max_command_timeout_seconds: int
    agent_max_stdout_chars: int
    agent_max_stderr_chars: int
    agent_max_file_read_chars: int
    agent_max_file_write_chars: int
    agent_max_protocol_errors: int
    agent_max_exchange_recoveries: int
    agent_recovery_delay_seconds: int
    agent_execution_backend: Literal["host", "docker", "podman"]
    agent_container_image: str | None
    agent_network_enabled: bool
    agent_reject_dangerous_commands: bool
    agent_env_allowlist: tuple[str, ...]
    agent_step_log_dir: Path
    storage_retention_days: int
    max_output_images_per_job: int
    max_traces: int
    max_failure_html_dumps: int
    human_typing_enabled: bool
    human_typing_delay_min_ms: int
    human_typing_delay_max_ms: int
    static_dir: Path

    @property
    def database_path(self) -> Path:
        if self.database_url.startswith("sqlite:///"):
            raw_path = self.database_url.removeprefix("sqlite:///")
            return _resolve_path(raw_path)
        raise ValueError("DATABASE_URL must be a sqlite:/// URL for this MVP.")

    def ensure_directories(self) -> None:
        self.static_dir.mkdir(parents=True, exist_ok=True)
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self.browser_login_profile_dir.mkdir(parents=True, exist_ok=True)
        self.browser_remote_debugging_user_data_dir.mkdir(parents=True, exist_ok=True)
        self.browser_runtime_root_dir.mkdir(parents=True, exist_ok=True)
        self.browser_screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.browser_trace_dir.mkdir(parents=True, exist_ok=True)
        self.browser_log_dir.mkdir(parents=True, exist_ok=True)
        self.browser_upload_dir.mkdir(parents=True, exist_ok=True)
        self.browser_output_dir.mkdir(parents=True, exist_ok=True)
        self.agent_step_log_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def provider_url(self, provider: str, mode: str | None = None) -> str:
        if provider == "chatgpt" and mode == "agent":
            return self.chatgpt_agent_url or self.chatgpt_url
        return self.chatgpt_url if provider == "chatgpt" else self.gemini_url

    def provider_new_chat_url(self, provider: str, mode: str | None = None) -> str:
        if provider == "chatgpt":
            if mode == "agent":
                return self.chatgpt_agent_url or self.chatgpt_url
            return self.chatgpt_project_url or self.chatgpt_url
        return self.gemini_url

    def provider_response_timeout_ms(self, provider: str) -> int:
        return (
            self.chatgpt_response_timeout_ms
            if provider == "chatgpt"
            else self.gemini_response_timeout_ms
        )

    def provider_stable_response_seconds(self, provider: str) -> int:
        return (
            self.chatgpt_stable_response_seconds
            if provider == "chatgpt"
            else self.gemini_stable_response_seconds
        )


def load_settings() -> Settings:
    default_agent_roots = [
        str(Path.home() / "Code"),
        str(Path.home() / "Projects"),
        "/tmp",
    ]
    raw_agent_backend = (os.getenv("AGENT_EXECUTION_BACKEND", "host").strip().lower() or "host")
    if raw_agent_backend not in {"host", "docker", "podman"}:
        raise ValueError("AGENT_EXECUTION_BACKEND must be one of: host, docker, podman.")
    settings = Settings(
        product_name=os.getenv("PRODUCT_NAME", "ordak"),
        product_label=os.getenv("PRODUCT_LABEL", "اردک 🦆"),
        browser_platform=_as_optional_str(os.getenv("BROWSER_PLATFORM")) or "auto",
        app_host=os.getenv("APP_HOST", "127.0.0.1"),
        app_port=_as_int(os.getenv("APP_PORT"), 8000),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./app/storage/jobs.db"),
        browser_headless=_as_bool(os.getenv("BROWSER_HEADLESS"), False),
        browser_slow_mo_ms=_as_int(os.getenv("BROWSER_SLOW_MO_MS"), 120),
        browser_timeout_ms=_as_int(os.getenv("BROWSER_TIMEOUT_MS"), 180_000),
        browser_engine=os.getenv("BROWSER_ENGINE", "chrome").strip().lower(),
        browser_executable_path=_resolve_path(
            os.getenv("BROWSER_EXECUTABLE_PATH", _default_chrome_executable()),
            base_dir=Path("/"),
        ),
        browser_remote_debugging_url=os.getenv(
            "BROWSER_REMOTE_DEBUGGING_URL",
            "http://127.0.0.1:9222",
        ).strip(),
        browser_remote_debugging_auto_launch=_as_bool(
            os.getenv("BROWSER_REMOTE_DEBUGGING_AUTO_LAUNCH"), False
        ),
        browser_remote_debugging_launch_timeout_ms=_as_int(
            os.getenv("BROWSER_REMOTE_DEBUGGING_LAUNCH_TIMEOUT_MS"), 30_000
        ),
        browser_remote_debugging_user_data_dir=_resolve_path(
            os.getenv(
                "BROWSER_REMOTE_DEBUGGING_USER_DATA_DIR",
                _default_remote_debugging_user_data_dir(),
            ),
            base_dir=Path("/"),
        ),
        browser_linux_x11_fallback_enabled=_as_bool(
            os.getenv("BROWSER_LINUX_X11_FALLBACK_ENABLED"), False
        ),
        browser_user_data_dir=_resolve_path(
            os.getenv("BROWSER_USER_DATA_DIR", _default_chrome_user_data_dir()),
            base_dir=Path("/"),
        ),
        browser_profile_name=os.getenv("BROWSER_PROFILE_NAME", "Default").strip() or "Default",
        browser_profile_dir=_resolve_path(
            os.getenv("BROWSER_PROFILE_DIR", "./app/storage/profiles/gemini")
        ),
        browser_login_profile_dir=_resolve_path(
            os.getenv("BROWSER_LOGIN_PROFILE_DIR", "./app/storage/profiles/gemini-login")
        ),
        browser_runtime_root_dir=_resolve_path(
            os.getenv("BROWSER_RUNTIME_ROOT_DIR", "./app/storage/profiles/runtime")
        ),
        browser_screenshot_dir=_resolve_path(
            os.getenv("BROWSER_SCREENSHOT_DIR", "./app/storage/screenshots")
        ),
        browser_trace_dir=_resolve_path(
            os.getenv("BROWSER_TRACE_DIR", "./app/storage/traces")
        ),
        browser_log_dir=_resolve_path("./app/storage/logs"),
        browser_upload_dir=_resolve_path(
            os.getenv("BROWSER_UPLOAD_DIR", "./app/storage/uploads")
        ),
        browser_output_dir=_resolve_path(
            os.getenv("BROWSER_OUTPUT_DIR", "./app/storage/outputs")
        ),
        gemini_url=os.getenv("GEMINI_URL", "https://gemini.google.com/app"),
        gemini_response_timeout_ms=_as_int(
            os.getenv("GEMINI_RESPONSE_TIMEOUT_MS"), 240_000
        ),
        gemini_stable_response_seconds=_as_int(
            os.getenv("GEMINI_STABLE_RESPONSE_SECONDS"), 4
        ),
        chatgpt_url=os.getenv("CHATGPT_URL", "https://chatgpt.com/"),
        chatgpt_project_url=_as_optional_str(os.getenv("CHATGPT_PROJECT_URL")),
        chatgpt_agent_url=_as_optional_str(
            os.getenv(
                "CHATGPT_AGENT_URL",
                "https://chatgpt.com/g/g-6a554689586881919525ccaa3c11368e-ordex",
            )
        ),
        chatgpt_response_timeout_ms=_as_int(
            os.getenv("CHATGPT_RESPONSE_TIMEOUT_MS"), 240_000
        ),
        chatgpt_stable_response_seconds=_as_int(
            os.getenv("CHATGPT_STABLE_RESPONSE_SECONDS"), 4
        ),
        chatgpt_stall_refresh_seconds=_as_int(
            os.getenv("CHATGPT_STALL_REFRESH_SECONDS"), 75
        ),
        chatgpt_max_stall_refreshes=_as_int(
            os.getenv("CHATGPT_MAX_STALL_REFRESHES"), 2
        ),
        agent_enabled=_as_bool(os.getenv("AGENT_ENABLED"), True),
        agent_allowed_workspace_roots=_resolve_csv_paths(
            os.getenv("AGENT_ALLOWED_WORKSPACE_ROOTS"),
            default=default_agent_roots,
        ),
        agent_default_max_steps=_as_int(os.getenv("AGENT_DEFAULT_MAX_STEPS"), 50),
        agent_max_allowed_steps=_as_int(os.getenv("AGENT_MAX_ALLOWED_STEPS"), 200),
        agent_default_command_timeout_seconds=_as_int(
            os.getenv("AGENT_DEFAULT_COMMAND_TIMEOUT_SECONDS"), 180
        ),
        agent_max_command_timeout_seconds=_as_int(
            os.getenv("AGENT_MAX_COMMAND_TIMEOUT_SECONDS"), 1800
        ),
        agent_max_stdout_chars=_as_int(os.getenv("AGENT_MAX_STDOUT_CHARS"), 30_000),
        agent_max_stderr_chars=_as_int(os.getenv("AGENT_MAX_STDERR_CHARS"), 30_000),
        agent_max_file_read_chars=_as_int(os.getenv("AGENT_MAX_FILE_READ_CHARS"), 1_000_000),
        agent_max_file_write_chars=_as_int(os.getenv("AGENT_MAX_FILE_WRITE_CHARS"), 2_000_000),
        agent_max_protocol_errors=_as_int(os.getenv("AGENT_MAX_PROTOCOL_ERRORS"), 5),
        agent_max_exchange_recoveries=_as_int(
            os.getenv("AGENT_MAX_EXCHANGE_RECOVERIES"), 3
        ),
        agent_recovery_delay_seconds=_as_int(
            os.getenv("AGENT_RECOVERY_DELAY_SECONDS"), 5
        ),
        agent_execution_backend=raw_agent_backend,  # type: ignore[arg-type]
        agent_container_image=_as_optional_str(os.getenv("AGENT_CONTAINER_IMAGE", "ordak-agent:latest")),
        agent_network_enabled=_as_bool(os.getenv("AGENT_NETWORK_ENABLED"), False),
        agent_reject_dangerous_commands=_as_bool(
            os.getenv("AGENT_REJECT_DANGEROUS_COMMANDS"),
            True,
        ),
        agent_env_allowlist=_split_csv_upper(
            os.getenv("AGENT_ENV_ALLOWLIST"),
            default=["PATH", "LANG", "LC_ALL", "TERM", "JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT"],
        ),
        agent_step_log_dir=_resolve_path(
            os.getenv("AGENT_STEP_LOG_DIR", "./app/storage/agent-steps")
        ),
        storage_retention_days=_as_int(
            os.getenv("STORAGE_RETENTION_DAYS"), 14
        ),
        max_output_images_per_job=_as_int(
            os.getenv("MAX_OUTPUT_IMAGES_PER_JOB"), 4
        ),
        max_traces=_as_int(
            os.getenv("MAX_TRACES"), 80
        ),
        max_failure_html_dumps=_as_int(
            os.getenv("MAX_FAILURE_HTML_DUMPS"), 80
        ),
        human_typing_enabled=_as_bool(os.getenv("HUMAN_TYPING_ENABLED"), False),
        human_typing_delay_min_ms=_as_int(
            os.getenv("HUMAN_TYPING_DELAY_MIN_MS"), 15
        ),
        human_typing_delay_max_ms=_as_int(
            os.getenv("HUMAN_TYPING_DELAY_MAX_MS"), 55
        ),
        static_dir=ROOT_DIR / "app" / "static",
    )
    if settings.agent_enabled and not settings.agent_allowed_workspace_roots:
        raise ValueError(
            "AGENT_ALLOWED_WORKSPACE_ROOTS must not be empty when AGENT_ENABLED=true."
        )
    settings.ensure_directories()
    return settings


settings = load_settings()
