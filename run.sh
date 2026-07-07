#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp -n .env.example .env || true
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

