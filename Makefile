PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
UVICORN ?= .venv/bin/uvicorn
PLAYWRIGHT ?= .venv/bin/playwright

.PHONY: setup run test lint diagnostics clean-storage migrate

setup:
	python3 -m venv .venv
	$(PIP) install -r requirements.txt
	$(PLAYWRIGHT) install chromium
	cp -n .env.example .env || true

run:
	$(UVICORN) app.main:app --reload --host 0.0.0.0 --port 8000

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m compileall app scripts tests

diagnostics:
	$(PYTHON) scripts/show_diagnostics.py

clean-storage:
	$(PYTHON) scripts/cleanup_storage.py

migrate:
	$(PYTHON) -m alembic upgrade head
