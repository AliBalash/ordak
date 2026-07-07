from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings, settings


def slugify_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("._")
    return cleaned or "upload"


def storage_relative_path(path: Path, app_settings: Settings | None = None) -> str:
    resolved = app_settings or settings
    return str(path.relative_to(resolved.browser_screenshot_dir.parent.parent))


def storage_absolute_path(relative_path: str, app_settings: Settings | None = None) -> Path:
    resolved = app_settings or settings
    return (resolved.browser_screenshot_dir.parent.parent / relative_path).resolve()
