# Architecture

`ordak` is a local FastAPI service that coordinates browser-driven provider runs against real Gemini and ChatGPT tabs in an existing Chrome session.

## Main layers

### API layer

- `app.main` exposes the HTTP and WebSocket endpoints.
- Job creation endpoints accept either async job creation or direct provider-style request/response flows.
- Static routes expose the local web panel and stored artifacts.

### Job orchestration

- `app.job_manager` owns queueing, state transitions, retries, cancellation, diagnostics, and storage cleanup.
- Each run is tracked as a job and linked to a persisted conversation record.
- The direct provider endpoints still create internal jobs so all execution paths share one engine.

### Provider abstraction

- `app.providers.base` defines the provider contract.
- `app.providers.existing_chrome` adapts that contract to the browser automation layer.
- The provider-specific URLs, response timeouts, and stability windows come from `app.config`.

### Browser automation

- `app.automation.existing_chrome` drives the already-open Chrome session.
- `app.automation.selectors` keeps DOM selectors in one place.
- `app.automation.extraction` handles answer and image extraction.
- `app.automation.human_like` provides optional slower typing behavior.

### Persistence and storage

- SQLite is used for the local database.
- Alembic migrations live under `alembic/`.
- Runtime artifacts are written under `app/storage/`:
  - `uploads/` for user-provided files
  - `outputs/` for extracted output images
  - `screenshots/` for captured run state
  - `traces/` for Playwright traces
  - `logs/` for failure HTML dumps and UI checks

## Execution flow

1. The user submits a prompt from the web panel or HTTP API.
2. The API validates the request and creates a job.
3. The job manager schedules the job and binds it to a conversation.
4. The provider adapter resolves the target provider and asks the automation layer to drive Chrome.
5. The automation layer opens or reuses the target tab, submits the prompt, waits for completion, and extracts artifacts.
6. The job manager persists the final state and exposes it through polling, diagnostics, and the UI.
