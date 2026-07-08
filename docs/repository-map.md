# Repository Map

This document maps the repository at the file and directory level so maintainers can find responsibilities quickly.

## Root files

### `README.md`

Top-level overview, quick start, endpoint summary, and links to `docs/`.

### `CONTRIBUTING.md`

Contributor guidance.

### `.env.example`

Complete environment variable template mirrored by `app/config.py`.

### `requirements.txt`

Python dependencies for app runtime, scripts, and tests.

### `Makefile`

Convenience commands for setup, run, test, lint, diagnostics, cleanup, and migrations.

### `run.sh`

Bootstrap shell script for first-run local startup.

### `Dockerfile`

Slim Python container recipe that installs requirements and Playwright Chromium, then starts Uvicorn.

### `docker-compose.yml`

Simple compose wrapper around the Docker image.

### `pytest.ini`

Pytest configuration.

### `conftest.py`

Shared pytest fixtures and test setup.

### `alembic.ini`

Alembic entry point configuration.

### `sample-image-test.png`

Reference asset for manual or scripted image-flow testing.

## Application package

Directory:

- `app/`

### `app/main.py`

FastAPI application factory and route layer.

Key responsibilities:

- app lifespan startup and shutdown
- static and storage mounts
- request parsing
- job creation
- direct provider response handling
- WebSocket route

### `app/config.py`

Loads environment variables into the `Settings` dataclass and ensures required directories exist.

### `app/database.py`

SQLAlchemy engine/session configuration plus SQLite schema compatibility logic.

### `app/models.py`

ORM models for `Conversation` and `Job`.

### `app/schemas.py`

Pydantic request and response models plus typed literals for providers, modes, statuses, retry strategies, and resume strategies.

### `app/errors.py`

Structured error codes, descriptors, and exception wrappers.

### `app/job_manager.py`

Main orchestration layer for job creation, queue consumption, cancellation, retries, resumes, diagnostics, storage cleanup, and WebSocket broadcasting.

### `app/artifacts.py`

Helpers for safe filenames and storage-relative path handling.

### `app/uploads.py`

Upload validation and file saving for incoming multipart images.

### `app/__init__.py`

Package marker.

## Automation package

Directory:

- `app/automation/`

### `app/automation/gemini_worker.py`

Primary worker used for both Gemini and ChatGPT automation.

Important classes and functions:

- `AutomationJobRequest`
- `WorkerRuntime`
- `GeminiAutomationError`
- `ManualVerificationRequired`
- `run_gemini_job()`

### `app/automation/existing_chrome.py`

Largest browser-facing module in the repository.

Contains:

- platform detection
- Chrome tab discovery
- DevTools execution
- Linux X11 fallback helpers
- prompt insertion
- upload handling
- response waiting
- generated image export

### `app/automation/browser.py`

Playwright support utilities and Linux remote-debugging helpers.

Contains:

- runtime profile preparation
- persistent login profile helpers
- browser context creation
- screenshot and trace helpers
- Linux remote debugging availability and optional launch logic

### `app/automation/extraction.py`

Utilities for extracting and cleaning answer text, waiting for stable responses in Playwright flows, and dumping debug artifacts.

### `app/automation/selectors.py`

Shared prompt/button/login/busy selector heuristics used in some Playwright and debugging paths.

### `app/automation/human_like.py`

Optional slower interaction helpers for Playwright locators and typing.

### `app/automation/__init__.py`

Package marker.

## Provider adapters

Directory:

- `app/providers/`

### `app/providers/base.py`

Defines the provider protocol and common data classes:

- `ImageArtifact`
- `ImageExtractionResult`
- `RebindResult`
- `ProviderDiagnostics`
- `ProviderAdapter`

### `app/providers/existing_chrome.py`

Concrete adapter implementation for existing-Chrome workflows.

Contains:

- `ExistingChromeProviderAdapter`
- `GeminiAdapter`
- `ChatGPTAdapter`
- `get_provider_adapter()`

### `app/providers/__init__.py`

Exports provider lookup.

## Static frontend

Directory:

- `app/static/`

### `index.html`

Main chat panel shell.

### `diagnostics.html`

Operational diagnostics page shell.

### `app.js`

Frontend logic for:

- provider/mode selection
- file preview
- job submission
- conversation list rendering
- WebSocket updates
- retry/resume/pin actions
- diagnostics interactions

### `style.css`

Visual system for the local UI.

### `icons/`

- `gemini.svg`
- `chatgpt.svg`

## Runtime storage

Directory:

- `app/storage/`

Subdirectories:

- `uploads/`
- `outputs/`
- `screenshots/`
- `traces/`
- `logs/`
- `profiles/`

Database file:

- `jobs.db` at runtime

## Migrations

Directory:

- `alembic/`

Important files:

- `alembic/env.py`
- `alembic/script.py.mako`
- `alembic/versions/20260704_0001_ordak_core_upgrade.py`

The current migration adds:

- conversation tracking
- provider and mode metadata
- retry and resume fields
- structured error fields
- upload and output tracking

## Scripts

Directory:

- `scripts/`

### `show_diagnostics.py`

Prints `/api/diagnostics`-equivalent data directly from the Python runtime.

### `cleanup_storage.py`

Runs storage cleanup and prints before/after stats.

### `open_profile.py`

Opens the helper browser flow.

### `debug_selectors.py`

Launches a helper browser context, captures prompt/button visibility information, and saves debug artifacts.

### `test_gemini_flow.py`

Manual worker driver for terminal-based end-to-end testing. Supports provider and mode flags despite its name.

## Tests

Directory:

- `tests/`

Coverage clusters:

- `test_main_routes.py`
- `test_job_manager.py`
- `test_existing_chrome_worker.py`
- `test_browser_remote_debugging.py`
- `test_existing_chrome_linux_tabs.py`
- `test_existing_chrome_upload.py`
- `test_provider_image_pipeline.py`
- `test_mock_worker.py`

The tests focus on logic, orchestration, timing heuristics, and regression protection around the most failure-prone flows.
