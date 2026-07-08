# Architecture

This document explains the current architecture of `ordak`, the role of each major module, and the exact path a request follows through the system.

## System summary

`ordak` is a local FastAPI server that:

1. accepts a request from the web panel or HTTP API
2. persists that request as a job in SQLite
3. enqueues the job into a single-consumer queue
4. attaches to an existing Gemini or ChatGPT browser tab in Google Chrome
5. interacts with the provider UI directly
6. stores the final answer, artifacts, and structured error metadata
7. exposes the evolving job state over HTTP and WebSocket

The project intentionally optimizes for correctness of the real browser session over scale, parallelism, or pure headless execution.

## Layered view

The codebase is easiest to reason about as six layers:

1. presentation layer
2. transport and API layer
3. orchestration layer
4. provider adapter layer
5. browser automation layer
6. persistence and artifact layer

## Presentation layer

Files:

- `app/static/index.html`
- `app/static/diagnostics.html`
- `app/static/app.js`
- `app/static/style.css`

Responsibilities:

- render the local chat-like interface
- let the user switch provider and mode
- upload one image for image analysis or image generation jobs
- show conversation history and pin state
- display logs, screenshots, and generated image outputs
- subscribe to live updates for the current job
- call diagnostics and cleanup endpoints from the diagnostics page

The frontend is a thin client. It does not perform automation. It only orchestrates API calls and renders server state.

## Transport and API layer

Primary file:

- `app/main.py`

Important responsibilities:

- create the FastAPI app in `create_app()`
- initialize directories and the database during lifespan startup
- start and stop the shared `JobManager`
- mount static assets at `/static`
- mount storage artifacts at `/storage`
- serve `/` and `/diagnostics`
- parse JSON and multipart requests
- expose job, provider, diagnostics, conversation, upload, and profile endpoints
- expose job WebSocket updates at `/ws/jobs/{job_id}`

### Startup and shutdown

During application startup:

1. `settings.ensure_directories()` creates the storage directories if they do not exist
2. `init_db()` creates or upgrades the SQLite schema
3. the `JobManager` instance is stored in `app.state.job_manager`
4. `JobManager.start()` recovers stale jobs and starts the queue consumer

During shutdown:

1. the queue dispatcher task is cancelled
2. the application stops accepting new job work

## Orchestration layer

Primary file:

- `app/job_manager.py`

This is the core of the runtime.

### Why it exists

The browser session is shared. If two workers typed into the same Chrome session at the same time, the project would become nondeterministic. The `JobManager` exists to serialize all automation work and preserve recoverable state around it.

### Main responsibilities

- create jobs and conversations
- store job metadata and logs in the database
- push new jobs into an `asyncio.Queue`
- run exactly one job at a time
- hold the browser lock across the entire automation run
- track the active job
- support cancellation, retry, and resume
- maintain WebSocket subscribers per job
- refresh saved conversation tab bindings against live Chrome tabs
- expose diagnostics and storage cleanup
- mark stale in-progress jobs as failed after a restart

### Important fields

The `JobManager` keeps several runtime-only structures:

- `queue`: async queue of job IDs
- `browser_lock`: a `threading.Lock` protecting the shared browser session
- `profile_lock`: a second lock for explicit profile-opening workflows
- `subscribers`: WebSocket connections grouped by `job_id`
- `dispatcher_task`: background task consuming the queue
- `active_job_id`: the job currently holding the browser lock
- `active_controls`: cancellation controls for running jobs
- `cancelled_queued_jobs`: queued job IDs that should be skipped when dequeued

### Queue model

Jobs are processed sequentially by `_consume_queue()`. The queue payload is only the `job_id`; all real state is reloaded from the database when execution begins.

This design has two useful properties:

- it keeps queue memory small
- it ensures the persisted database snapshot remains the source of truth

### Conversation model

Every job belongs to an `ordak` conversation. The conversation is not just a UI convenience. It is also the persistence point for tab rebinding:

- provider
- title
- provider conversation URL
- provider conversation ID derived from the URL
- native or DevTools tab identity
- tab liveness
- pin state
- last successful job and last error code

When a user continues a conversation, the manager attempts to reuse the original provider thread instead of blindly opening a new one.

## Provider adapter layer

Files:

- `app/providers/base.py`
- `app/providers/existing_chrome.py`

The adapter layer isolates provider-specific behavior while keeping the worker generic.

### Common provider interface

`ProviderAdapter` defines operations such as:

- `open_tab()`
- `rebind_tab()`
- `detect_login_state()`
- `detect_busy_state()`
- `find_prompt_input()`
- `verify_upload_complete()`
- `submit_prompt()`
- `wait_for_response()`
- `extract_text_result()`
- `extract_image_result()`
- `best_effort_stop()`
- `collect_diagnostics()`

### Current concrete implementation

The repository currently ships one implementation family:

- existing Chrome backed adapters for Gemini and ChatGPT

Both `GeminiAdapter` and `ChatGPTAdapter` inherit from `ExistingChromeProviderAdapter`. Most differences are handled by provider-specific selectors, URL matching, and heuristics.

## Browser automation layer

Files:

- `app/automation/gemini_worker.py`
- `app/automation/existing_chrome.py`
- `app/automation/browser.py`
- `app/automation/extraction.py`
- `app/automation/selectors.py`
- `app/automation/human_like.py`

### Worker role

`run_gemini_job()` is historically named but now drives both providers.

It is responsible for:

1. checking browser readiness
2. attaching to Linux DevTools if needed
3. reusing or opening a provider tab
4. checking login and verification state
5. switching to provider image mode when needed
6. uploading one image if present
7. inserting the prompt
8. submitting the prompt
9. waiting until the UI becomes stable
10. extracting text or image outputs

### Existing Chrome role

`app/automation/existing_chrome.py` is the lowest-level browser interaction module in this repository. It contains:

- Chrome tab discovery
- DevTools-based tab opening and JavaScript execution
- optional macOS AppleScript paths
- optional Linux X11 fallback paths
- prompt insertion heuristics
- file upload transport
- response busy-state detection
- generated image extraction

This module is large because it contains most of the UI-specific survival logic.

## Persistence and artifact layer

Files:

- `app/database.py`
- `app/models.py`
- `app/artifacts.py`
- `app/uploads.py`
- `app/storage/`

Responsibilities:

- define and initialize the SQLite schema
- store job and conversation state
- save uploaded images
- save generated image outputs
- save screenshots and traces
- normalize storage paths to repository-relative values

### Why artifact paths are stored as relative paths

The database stores storage-relative paths such as `storage/uploads/file.png` instead of absolute machine paths. This simplifies:

- API responses
- UI linking
- portability across local environments
- cleanup logic

## End-to-end execution flow

### Async job flow

1. client calls `POST /api/jobs`
2. `app/main.py` validates payload and optionally stores an uploaded image
3. `JobManager.create_job()` creates a conversation if needed, inserts the job row, and enqueues the job ID
4. queue consumer calls `_run_job_sync(job_id)` in a worker thread
5. worker drives the browser
6. runtime callbacks update job status, logs, screenshots, answers, output images, and trace path
7. `JobManager` broadcasts snapshots to WebSocket subscribers
8. client polls `/api/jobs/{job_id}` or stays subscribed over WebSocket

### Direct provider response flow

1. client calls `POST /api/providers/{provider}/respond` or the provider shortcut endpoint
2. `app/main.py` still creates a normal queued job
3. endpoint optionally waits for a terminal state using `_wait_for_job_terminal()`
4. response is normalized into `ProviderRunResponse`

This means the "direct" endpoints are not a separate execution engine. They are a convenience wrapper over the same job system.

## Concurrency model

The system is intentionally low-concurrency.

- multiple HTTP clients can create jobs
- only one job can control the browser at a time
- WebSocket subscriptions are per job and can be many-to-one
- storage and DB updates happen frequently and are synchronous enough for local use

This is a good tradeoff for a local shared-browser tool.

## Restart recovery model

When the app starts, `JobManager._recover_stale_incomplete_jobs()` marks previously running jobs as failed with `response_timeout`.

This protects the UI and API from permanently orphaned "running" jobs after a crash, restart, or abrupt shutdown.

## Design constraints

- browser correctness is more important than throughput
- provider DOM changes are expected and must be diagnosable
- image extraction prefers false negatives over returning the wrong artifact
- Linux attach-only behavior is preferred to accidentally launching a different profile
- direct provider endpoints reuse the same persisted job model rather than bypassing it
