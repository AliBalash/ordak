from __future__ import annotations

from pathlib import Path
import json
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import init_db
from app.job_manager import JobManager


def main() -> None:
    init_db()
    manager = JobManager()
    payload = manager.diagnostics().model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

