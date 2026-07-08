# Development and Testing

This document explains how to work on `ordak`, what the automated tests currently cover, and where manual validation is still important.

## Local development workflow

Typical loop:

1. create and activate `.venv`
2. install dependencies
3. configure `.env`
4. start the correct signed-in Chrome session
5. run the FastAPI app
6. run tests
7. manually verify real browser flows if you changed automation logic

## Useful commands

### Setup

```bash
make setup
```

### Run app

```bash
make run
```

### Run tests

```bash
make test
```

### Syntax compilation check

```bash
make lint
```

### Run migration

```bash
make migrate
```

## Test philosophy

The test suite is intentionally focused on logic and regression protection around the most fragile areas:

- queue behavior
- retry and resume semantics
- route behavior
- Linux remote debugging
- tab rebinding identity
- upload readiness timing
- generated image extraction fallback order
- worker error mapping

The suite does not pretend to fully replace manual end-to-end validation against real provider UIs.

## Test file map

### `tests/test_main_routes.py`

Covers:

- root and diagnostics routes
- health endpoint
- async job creation
- direct provider response routes
- multipart upload request handling
- pin and unpin routes
- cancel, retry, and resume endpoints

This file is the main route-level contract test.

### `tests/test_job_manager.py`

Covers orchestration behavior such as:

- queue processing
- cancellation handling
- retry and resume creation
- conversation listing and tab binding refresh
- pinned state changes
- stale job recovery

This file protects the core state machine.

### `tests/test_existing_chrome_worker.py`

Covers the worker logic in isolation using a fake provider adapter.

Examples:

- Chrome-not-open failure mapping
- normal chat success path
- image generation path
- cancellation behavior
- login-state handling
- conversation state remembering

### `tests/test_browser_remote_debugging.py`

Covers Linux attach behavior such as:

- optional auto-launch
- launch timeout behavior
- correct DevTools launch command construction
- helper profile-opening behavior on Linux

### `tests/test_existing_chrome_linux_tabs.py`

Protects Linux tab identity assumptions, especially around `target_id`.

This is important because stale numeric IDs must not accidentally rebind the wrong DevTools tab.

### `tests/test_existing_chrome_upload.py`

Covers:

- upload completion polling
- attachment readiness timing
- generated image ready-state stabilization
- prompt insertion safety behavior

### `tests/test_provider_image_pipeline.py`

Covers image extraction decision order:

1. download controls
2. asset URL fallback
3. DOM fallback

It also protects the confidence model so low-confidence DOM-only results are not treated as acceptable.

### `tests/test_mock_worker.py`

Small utility regression tests such as answer text cleaning.

## What the tests do not prove

The tests do not fully prove:

- that current live Gemini selectors still match production UI
- that current live ChatGPT selectors still match production UI
- that AppleScript behavior works on every macOS version
- that Linux X11 fallback remains healthy
- that provider image extraction works after a provider DOM redesign

Those areas still require manual validation.

## Manual validation checklist

Run a real browser validation when you change:

- prompt selectors
- upload logic
- submit heuristics
- response waiting heuristics
- generated image extraction
- Linux DevTools attach code
- conversation rebinding behavior

Suggested manual checks:

1. Gemini chat job in an existing conversation
2. ChatGPT chat job in an existing conversation
3. Gemini image analysis with one upload
4. ChatGPT image analysis with one upload
5. Gemini image generation with one upload
6. ChatGPT image generation with one upload
7. retry same tab
8. retry new tab same conversation
9. resume after manual verification or timeout

## Debugging helpers

Useful scripts:

- `scripts/show_diagnostics.py`
- `scripts/cleanup_storage.py`
- `scripts/open_profile.py`
- `scripts/debug_selectors.py`
- `scripts/test_gemini_flow.py`

These are described in more detail in `scripts-and-operations.md`.

## Good maintenance practices

- prefer changing selectors and heuristics in the smallest possible place
- keep Linux attach behavior conservative
- avoid making image extraction more permissive unless you are sure it will not return user-upload images as outputs
- add or update tests whenever job state semantics change
- inspect screenshots and logs before changing timeouts blindly
