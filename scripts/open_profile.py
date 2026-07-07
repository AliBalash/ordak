from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.automation.browser import open_profile_browser_session
from app.config import settings


if __name__ == "__main__":
    print("Opening Gemini in the current Google Chrome window.")
    open_profile_browser_session(settings)
