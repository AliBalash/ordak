# ordak

`ordak` is a local FastAPI application that automates the real Gemini and ChatGPT web interfaces through an already signed-in Google Chrome session. It does not call direct LLM APIs. Instead, it drives the provider UIs, waits for real responses, extracts text or generated images, and exposes the results through a local web panel, HTTP API, and WebSocket updates.

The project is designed for personal desktop workflows, local tools, and internal automation where reusing the exact browser session matters more than running in a fully headless cloud environment.

## Core capabilities

- Reuses an existing signed-in Chrome session instead of creating a fully separate browser workflow.
- Supports both Gemini and ChatGPT through one local orchestration layer.
- Handles chat, image analysis, and image generation / image editing jobs.
- Adds Ordex Agent Mode for iterative local coding tasks through a Custom GPT in the real ChatGPT UI.
- Persists conversations, jobs, tab bindings, logs, screenshots, traces, uploads, and output images.
- Exposes asynchronous job APIs, direct provider response APIs, and live WebSocket job updates.
- Includes a built-in web panel and a diagnostics page for operational visibility.
- Supports both macOS and Linux.
- On Linux, attaches to an existing Chrome DevTools session, typically `http://127.0.0.1:9222`.

## How it works

1. A user submits a request from the web panel or HTTP API.
2. `ordak` creates a persisted job and schedules it in the local job queue.
3. The worker opens or rebinds a Gemini or ChatGPT tab in an existing Chrome session.
4. The automation layer inserts the prompt, optionally uploads an image, submits the request, waits for the provider UI to stabilize, and extracts the result.
5. The final answer, generated images, logs, screenshots, and metadata are saved locally and exposed through the API and panel.

## Ordex Agent Mode

Ordex Agent Mode keeps the normal Chrome-backed workflow, but lets a Custom GPT issue one structured local tool action at a time and receive `TOOL_RESULT` responses in the same ChatGPT conversation.

Agent mode currently:

- supports `chatgpt` only
- uses a separate `CHATGPT_AGENT_URL`
- persists per-step records under `agent_steps`
- supports `exec`, `read_file`, `list_directory`, `write_file`, and `apply_patch`
- confines file access to configured workspace roots

Typical flow:

1. Start Chrome on Linux with remote debugging on `127.0.0.1:9222` or open your regular signed-in Chrome on macOS.
2. Sign in to ChatGPT in that exact Chrome session.
3. Set `CHATGPT_AGENT_URL` and `AGENT_ALLOWED_WORKSPACE_ROOTS` in `.env`.
4. In the panel, choose `ChatGPT` + `Agent`, provide a workspace, and send a coding task.

## Platform model

### macOS

- Uses the real installed Google Chrome application.
- Requires Chrome support for JavaScript from Apple Events:
  `View > Developer > Allow JavaScript from Apple Events`

### Linux

- Uses Chrome DevTools remote debugging as the primary runtime.
- Recommended mode is attach-only to a Chrome instance that you start yourself with `--remote-debugging-port=9222`.
- Normal worker flows do not silently launch a second profile when remote debugging is unavailable.
- An older X11 fallback path exists but is disabled by default and is intended only for advanced debugging.

## Quick start

### 1. Create the environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

### 2. Configure your local browser

Set `BROWSER_PLATFORM` in `.env`:

- `mac` for macOS
- `linux` for Linux

For Linux, start Chrome with remote debugging enabled:

```bash
google-chrome \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.config/ordak-chrome"
```

Then sign in to Gemini and ChatGPT inside that exact Chrome profile.

Important Linux defaults:

- `BROWSER_REMOTE_DEBUGGING_URL=http://127.0.0.1:9222`
- `BROWSER_REMOTE_DEBUGGING_AUTO_LAUNCH=false`
- `BROWSER_REMOTE_DEBUGGING_USER_DATA_DIR=/home/<your-user>/.config/ordak-chrome`

### 3. Run the app

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/diagnostics`

You can also use the helper script:

```bash
./run.sh
```

## Project structure

```text
app/
  automation/      Browser control, DOM execution, provider interaction
  providers/       Provider adapter abstraction and concrete Chrome-backed adapters
  static/          Web panel and diagnostics UI
  storage/         SQLite DB, uploads, outputs, screenshots, traces, logs, profiles
alembic/           Database migrations
docs/              Full project documentation
scripts/           Utilities for diagnostics, cleanup, profile opening, manual tests
tests/             Automated test suite
```

## Main HTTP endpoints

### Health and diagnostics

- `GET /api/health`
- `GET /api/diagnostics`
- `GET /api/diagnostics/storage`
- `POST /api/diagnostics/cleanup`

### Jobs

- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/steps`
- `POST /api/jobs/{job_id}/cancel`
- `POST /api/jobs/{job_id}/retry`
- `POST /api/jobs/{job_id}/resume`

### Direct provider responses

- `POST /api/providers/{provider}/respond`
- `POST /api/gemini/respond`
- `POST /api/chatgpt/respond`

### Conversations

- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `POST /api/conversations/{conversation_id}/pin`
- `POST /api/conversations/{conversation_id}/unpin`

### Browser/profile helpers

- `POST /api/profile/open`
- `GET /api/uploads/{filename}`
- `GET /`
- `GET /diagnostics`
- `WS /ws/jobs/{job_id}`

## Validation status

The project includes automated coverage for:

- Linux remote-debugging behavior
- Chrome tab rebinding and tab identity handling
- Upload flow and generated image readiness
- Worker orchestration, errors, retries, and image extraction
- Agent protocol parsing, workspace confinement, executor behavior, job-step persistence, and agent routes
- Job manager queue behavior and recovery
- Main route behavior and direct provider endpoints

Manual end-to-end validation is still recommended whenever you change selectors, browser-attachment behavior, or provider-specific extraction logic, because the project depends on third-party web UIs that can change over time.

## Documentation map

- [Documentation index](docs/README.md)
- [Architecture](docs/architecture.md)
- [Setup and configuration](docs/setup-and-configuration.md)
- [API reference](docs/api-reference.md)
- [API examples](docs/api-examples.md)
- [Job lifecycle and errors](docs/job-lifecycle-and-errors.md)
- [Browser automation](docs/browser-automation.md)
- [Data model and storage](docs/data-model-and-storage.md)
- [Repository map](docs/repository-map.md)
- [Frontend panel](docs/frontend-panel.md)
- [Development and testing](docs/development-and-testing.md)
- [Scripts and operations](docs/scripts-and-operations.md)
- [Troubleshooting](docs/troubleshooting.md)

## Important constraints

- This is a local desktop automation project, not a high-scale server product.
- Reliability depends on the real provider UIs remaining compatible with the current selectors and DOM heuristics.
- The browser session used by `ordak` must already be authenticated with the target provider.
- Linux production-like usage should prefer attach-only DevTools mode rather than automatically launching extra Chrome profiles.

## License and ownership notes

No project license file is currently present in the repository root. If you intend to distribute or publish the project outside private/internal use, add an explicit license and contribution policy first.
