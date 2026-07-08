# Setup and Configuration

This document explains how to install, configure, and run `ordak` with the current codebase.

## Supported runtime model

The validated workflow is local desktop usage on:

- macOS
- Linux

Windows defaults exist in `app/config.py`, but this repository's tested and documented browser flows are centered on macOS and Linux.

## Prerequisites

- Python 3.11 or newer
- Google Chrome installed locally
- a real signed-in Gemini and/or ChatGPT browser session
- ability to run a local FastAPI server
- for Linux: Chrome DevTools remote debugging on a reachable port

For image workflows you also need:

- the provider account to have image analysis or generation access in that real browser session

## Installation

### Manual setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

### Helper script

```bash
./run.sh
```

`run.sh` will:

- create `.venv` if needed
- activate it
- install requirements
- install Playwright Chromium
- create `.env` from `.env.example` if missing
- start the app with Uvicorn

## Starting the application

### Direct Uvicorn

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Makefile

```bash
make run
```

### Local URLs

- panel: `http://127.0.0.1:8000/`
- diagnostics: `http://127.0.0.1:8000/diagnostics`
- health: `http://127.0.0.1:8000/api/health`

## Browser setup

## macOS setup

1. open your regular Google Chrome
2. sign in to Gemini and/or ChatGPT there
3. enable `View > Developer > Allow JavaScript from Apple Events`
4. confirm `.env` points to the real Chrome executable and user data directory

Typical macOS values:

```env
BROWSER_PLATFORM=mac
BROWSER_EXECUTABLE_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
BROWSER_USER_DATA_DIR=/Users/<your-user>/Library/Application Support/Google/Chrome
```

## Linux setup

Linux is designed primarily for DevTools attach mode.

Recommended startup command:

```bash
google-chrome \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.config/ordak-chrome"
```

Then:

1. open Gemini and ChatGPT inside that exact Chrome instance
2. sign in there
3. keep that browser session open
4. run `ordak`

Recommended Linux values:

```env
BROWSER_PLATFORM=linux
BROWSER_EXECUTABLE_PATH=/usr/bin/google-chrome
BROWSER_REMOTE_DEBUGGING_URL=http://127.0.0.1:9222
BROWSER_REMOTE_DEBUGGING_AUTO_LAUNCH=false
BROWSER_REMOTE_DEBUGGING_USER_DATA_DIR=/home/<your-user>/.config/ordak-chrome
BROWSER_LINUX_X11_FALLBACK_ENABLED=false
```

## Important Linux behavior

If `BROWSER_REMOTE_DEBUGGING_AUTO_LAUNCH=false` and DevTools is not reachable:

- the worker fails immediately
- it does not silently launch a second unrelated profile for normal jobs

That behavior is deliberate. It helps prevent running requests against the wrong logged-out or wrong-account browser profile.

## Configuration reference

The settings object is built in `app/config.py`. This section documents every currently loaded variable.

## App and database

### `PRODUCT_NAME`

Used for the FastAPI title and diagnostics payload.

Default:

```env
PRODUCT_NAME=ordak
```

### `PRODUCT_LABEL`

Human-facing label used in the UI and diagnostics.

Default:

```env
PRODUCT_LABEL=اردک 🦆
```

### `APP_HOST`

Nominal application host.

Default:

```env
APP_HOST=0.0.0.0
```

### `APP_PORT`

Nominal application port.

Default:

```env
APP_PORT=8000
```

### `DATABASE_URL`

Current code expects SQLite in normal operation.

Default:

```env
DATABASE_URL=sqlite:///./app/storage/jobs.db
```

## Browser platform and executable

### `BROWSER_PLATFORM`

Controls platform-specific behavior.

Supported practical values:

- `mac`
- `linux`
- `auto`

### `BROWSER_HEADLESS`

Used by Playwright helper flows, not the normal existing-Chrome workflow.

Default:

```env
BROWSER_HEADLESS=false
```

### `BROWSER_SLOW_MO_MS`

Playwright slow motion delay for helper/debug flows.

Default:

```env
BROWSER_SLOW_MO_MS=120
```

### `BROWSER_TIMEOUT_MS`

Default UI timeout for Playwright helper flows and some upload-related time windows.

Default:

```env
BROWSER_TIMEOUT_MS=180000
```

### `BROWSER_ENGINE`

Current default is:

```env
BROWSER_ENGINE=chrome
```

The code path is optimized for Chrome-based behavior.

### `BROWSER_EXECUTABLE_PATH`

Path to the local Chrome executable.

Examples:

```env
# macOS
BROWSER_EXECUTABLE_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome

# Linux
BROWSER_EXECUTABLE_PATH=/usr/bin/google-chrome
```

## Linux DevTools attach settings

### `BROWSER_REMOTE_DEBUGGING_URL`

DevTools base URL used on Linux.

Default:

```env
BROWSER_REMOTE_DEBUGGING_URL=http://127.0.0.1:9222
```

### `BROWSER_REMOTE_DEBUGGING_AUTO_LAUNCH`

If `true`, `ensure_linux_remote_debugging_session()` may launch Chrome itself when DevTools is unavailable.

Default:

```env
BROWSER_REMOTE_DEBUGGING_AUTO_LAUNCH=false
```

Recommended for normal local usage:

- keep it `false`
- manage the Chrome 9222 session yourself

### `BROWSER_REMOTE_DEBUGGING_LAUNCH_TIMEOUT_MS`

How long the app waits for DevTools to appear after an auto-launch.

Default:

```env
BROWSER_REMOTE_DEBUGGING_LAUNCH_TIMEOUT_MS=30000
```

### `BROWSER_REMOTE_DEBUGGING_USER_DATA_DIR`

Profile root used when `ordak` auto-launches a DevTools-enabled Chrome session.

Default on Linux:

```env
BROWSER_REMOTE_DEBUGGING_USER_DATA_DIR=/home/<your-user>/.config/ordak-chrome
```

## Chrome profile and runtime directories

### `BROWSER_USER_DATA_DIR`

Path to your normal local Chrome user data root.

Examples:

```env
# macOS
BROWSER_USER_DATA_DIR=/Users/<your-user>/Library/Application Support/Google/Chrome

# Linux
BROWSER_USER_DATA_DIR=/home/<your-user>/.config/google-chrome
```

### `BROWSER_PROFILE_NAME`

Chrome profile name such as `Default`.

Default:

```env
BROWSER_PROFILE_NAME=Default
```

If set to `auto`, `detect_selected_profile()` will try to read Chrome's `Local State` to infer the last used profile.

### `BROWSER_PROFILE_DIR`

Playwright persistent profile path for helper flows.

### `BROWSER_LOGIN_PROFILE_DIR`

Persistent login snapshot root used by helper flows.

### `BROWSER_RUNTIME_ROOT_DIR`

Temporary runtime profile root created for certain Playwright sessions.

### `BROWSER_SCREENSHOT_DIR`

Directory for worker screenshots.

### `BROWSER_TRACE_DIR`

Directory for Playwright trace archives.

### `BROWSER_UPLOAD_DIR`

Directory for user-uploaded images accepted by API multipart requests.

### `BROWSER_OUTPUT_DIR`

Directory for saved generated-image artifacts.

### `BROWSER_LINUX_X11_FALLBACK_ENABLED`

Enables the old Linux X11 fallback in `app/automation/existing_chrome.py`.

Default:

```env
BROWSER_LINUX_X11_FALLBACK_ENABLED=false
```

This should stay off unless you are intentionally debugging the fallback path.

## Provider URLs and timeouts

### `GEMINI_URL`

Default:

```env
GEMINI_URL=https://gemini.google.com/app
```

### `GEMINI_RESPONSE_TIMEOUT_MS`

Maximum wait for Gemini response completion.

Default:

```env
GEMINI_RESPONSE_TIMEOUT_MS=240000
```

### `GEMINI_STABLE_RESPONSE_SECONDS`

How long the response must remain stable before it is accepted.

Default:

```env
GEMINI_STABLE_RESPONSE_SECONDS=4
```

### `CHATGPT_URL`

Default:

```env
CHATGPT_URL=https://chatgpt.com/
```

### `CHATGPT_PROJECT_URL`

Optional project-scoped starting URL for ChatGPT.

If set, `provider_new_chat_url("chatgpt")` prefers it over the base ChatGPT URL.

This matters when you want new chats to stay inside a specific ChatGPT project.

### `CHATGPT_RESPONSE_TIMEOUT_MS`

Default:

```env
CHATGPT_RESPONSE_TIMEOUT_MS=240000
```

### `CHATGPT_STABLE_RESPONSE_SECONDS`

Default:

```env
CHATGPT_STABLE_RESPONSE_SECONDS=4
```

## Retention and image extraction settings

### `STORAGE_RETENTION_DAYS`

Files referenced by recent or pinned conversations are preserved during cleanup.

Default:

```env
STORAGE_RETENTION_DAYS=14
```

### `MAX_OUTPUT_IMAGES_PER_JOB`

Upper bound passed into generated image export logic.

Default:

```env
MAX_OUTPUT_IMAGES_PER_JOB=4
```

### `MAX_TRACES`

How many trace files cleanup keeps.

Default:

```env
MAX_TRACES=80
```

### `MAX_FAILURE_HTML_DUMPS`

How many saved failure HTML dumps cleanup keeps.

Default:

```env
MAX_FAILURE_HTML_DUMPS=80
```

## Human typing settings

These are used by helper logic in `app/automation/human_like.py`.

### `HUMAN_TYPING_ENABLED`

Default:

```env
HUMAN_TYPING_ENABLED=false
```

### `HUMAN_TYPING_DELAY_MIN_MS`

Default:

```env
HUMAN_TYPING_DELAY_MIN_MS=15
```

### `HUMAN_TYPING_DELAY_MAX_MS`

Default:

```env
HUMAN_TYPING_DELAY_MAX_MS=55
```

## First-run verification checklist

After setup, verify:

1. `GET /api/health` returns `ok`
2. `GET /api/diagnostics` shows `chrome_running: true`
3. provider `login_state` is `ready` for the provider you intend to use
4. on Linux, `curl http://127.0.0.1:9222/json/version` works
5. the panel at `/` loads without missing static assets

## Container notes

The repository includes a `Dockerfile` and `docker-compose.yml`, but the project still relies on a real logged-in browser session. A container alone does not provide the authenticated Chrome environment the automation expects.

Treat container files as development helpers, not as proof that `ordak` is designed for detached server-side automation.
