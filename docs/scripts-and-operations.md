# Scripts and Operations

This document describes operational commands, helper scripts, and day-to-day maintenance workflows.

## Primary entry points

The repository can be operated through:

- direct `uvicorn`
- `run.sh`
- `make` targets
- Python helper scripts under `scripts/`

## Make targets

Defined in `Makefile`.

### `make setup`

Runs:

- `python3 -m venv .venv`
- dependency installation
- `playwright install chromium`
- `.env` initialization from `.env.example`

### `make run`

Runs:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### `make test`

Runs:

```bash
python -m pytest -q
```

### `make lint`

Runs:

```bash
python -m compileall app scripts tests
```

This is a syntax check, not a style linter.

### `make diagnostics`

Runs:

```bash
python scripts/show_diagnostics.py
```

### `make clean-storage`

Runs:

```bash
python scripts/cleanup_storage.py
```

### `make migrate`

Runs:

```bash
python -m alembic upgrade head
```

## `run.sh`

Purpose:

- one-command local bootstrap

Behavior:

1. create `.venv` if missing
2. activate it
3. install Python requirements
4. install Playwright Chromium
5. copy `.env.example` to `.env` if needed
6. run Uvicorn

## Utility scripts

## `scripts/show_diagnostics.py`

Purpose:

- print the same diagnostics model the API exposes

Use when:

- you want quick terminal visibility
- the UI is unavailable
- you are debugging provider readiness

Example:

```bash
.venv/bin/python scripts/show_diagnostics.py
```

## `scripts/cleanup_storage.py`

Purpose:

- print storage stats before cleanup
- run cleanup
- print storage stats after cleanup

Example:

```bash
.venv/bin/python scripts/cleanup_storage.py
```

## `scripts/open_profile.py`

Purpose:

- invoke the helper browser-opening flow

Important note:

- this is not the same thing as the normal attach-only Linux worker flow
- it is a helper for opening a session, not the core job runtime path

Example:

```bash
.venv/bin/python scripts/open_profile.py
```

## `scripts/debug_selectors.py`

Purpose:

- open a helper browser context
- navigate to Gemini
- inspect visible textboxes, buttons, and contenteditable nodes
- save a simplified DOM payload
- save a screenshot

Artifacts are typically saved under:

- `app/storage/logs/`
- `app/storage/screenshots/`

Use this when prompt detection or send-button logic appears broken.

## `scripts/test_gemini_flow.py`

Purpose:

- run the worker manually from a terminal
- print statuses, logs, screenshots, trace, and final answer

Despite the filename, it supports:

- `--provider=gemini`
- `--provider=chatgpt`
- `--mode=chat`
- `--mode=image_analyze`
- `--mode=image_generate`

Examples:

```bash
.venv/bin/python scripts/test_gemini_flow.py
.venv/bin/python scripts/test_gemini_flow.py --provider=chatgpt
.venv/bin/python scripts/test_gemini_flow.py --provider=gemini --mode=image_analyze
```

## Operational workflows

## Normal startup workflow

1. start or verify the correct signed-in Chrome session
2. on Linux, verify DevTools is reachable
3. start the app
4. open `/diagnostics`
5. confirm provider `login_state: ready`
6. open `/`
7. run a small test prompt

## Linux DevTools verification

Before blaming the app, verify Chrome itself:

```bash
curl http://127.0.0.1:9222/json/version
curl http://127.0.0.1:9222/json/list
```

If those fail, `ordak` cannot attach.

## Recommended Linux Chrome command

```bash
google-chrome \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.config/ordak-chrome"
```

## Diagnostics-first workflow

Before changing code, check:

1. `GET /api/diagnostics`
2. `GET /api/diagnostics/storage`
3. current screenshots
4. current logs
5. whether the provider tab itself is visibly blocked by login or verification

This avoids spending time fixing the wrong layer.

## Cleanup workflow

Use cleanup when:

- local artifact storage keeps growing
- old uploads or outputs are no longer needed
- you want to keep only recent or pinned conversation artifacts

Preferred order:

1. inspect `/api/diagnostics/storage`
2. pin important conversations
3. run cleanup
4. inspect storage again

## Database and migration workflow

For a clean local reset:

1. stop the app
2. back up or remove `app/storage/jobs.db` if appropriate
3. restart the app or run migrations

For normal schema upgrades:

```bash
make migrate
```

Remember that `init_db()` also contains SQLite compatibility logic that may patch local schema gaps during startup.

## Container workflow caveat

The Docker files can start the Python app, but they do not remove the need for a real authenticated Chrome session. Do not treat them as a complete production deployment recipe for browser-native automation.
