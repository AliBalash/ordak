# Browser Automation

This document explains how `ordak` controls Google Chrome and why the automation code looks the way it does.

## Core strategy

`ordak` is built around one design choice:

- reuse an already authenticated, real Chrome session

This gives the app access to:

- the user's actual provider cookies
- subscription-gated features
- provider-specific image tools
- real conversation URLs
- project-scoped ChatGPT flows when configured

The tradeoff is that the project depends on UI stability and local browser state.

## Main automation modules

- `app/automation/gemini_worker.py`
- `app/automation/existing_chrome.py`
- `app/automation/browser.py`
- `app/automation/extraction.py`
- `app/automation/selectors.py`
- `app/automation/human_like.py`
- `app/providers/existing_chrome.py`

## Worker flow

The main worker entry point is `run_gemini_job()`. Despite the name, it is provider-generic.

The normal flow is:

1. check whether Chrome is available
2. on Linux, ensure DevTools attachability
3. try to rebind an existing tab or open a new provider tab
4. remember the live tab identity
5. detect login or verification blockers
6. locate the prompt input
7. optionally switch image mode
8. optionally upload one image
9. insert the effective prompt
10. submit
11. wait for response stability
12. extract text or images

## Effective prompt behavior

The worker adjusts the prompt for image modes:

- `image_generate` with upload: wraps the request as an edit/generate instruction using the uploaded reference image
- `image_generate` without upload: prefixes the prompt as a generation request
- `image_analyze` with upload: frames the prompt as an image analysis instruction
- `chat`: passes the original question unchanged

This is implemented in `_effective_prompt()`.

## Platform model

## macOS

On macOS the code expects:

- Chrome already running
- the provider already logged in
- JavaScript from Apple Events allowed

The repository still contains AppleScript-oriented control paths for Chrome.

## Linux

Linux is designed around DevTools remote debugging attach mode.

Important helpers:

- `linux_remote_debugging_available()`
- `ensure_linux_remote_debugging_session()`
- `_linux_open_tab_via_devtools()`
- `_linux_execute_javascript()`
- `_linux_list_google_chrome_tabs()`

Recommended runtime pattern:

- user starts the correct Chrome profile on `9222`
- `ordak` attaches to that exact browser

## Linux X11 fallback

The code also contains:

- AT-SPI window walking
- `xdotool` integration
- X11 prompt and button helpers

This path is guarded by `BROWSER_LINUX_X11_FALLBACK_ENABLED` and is best treated as a fallback debugging path, not the primary runtime.

## Tab identity and rebinding

One of the most important pieces of the project is how it keeps a conversation attached to the correct browser tab.

### Saved tab identity

`Conversation` stores:

- `tab_window_id`
- `tab_id`
- `tab_window_key`
- `tab_target_id`
- `external_url`
- `external_conversation_id`
- `tab_alive`

### Rebinding order

When reopening a conversation, `JobManager` and the provider adapter work together to match by:

1. `target_id`
2. numeric window/tab pair
3. exact external URL
4. extracted external conversation ID inside matching provider URLs
5. provider-domain fallback

### Why `target_id` matters on Linux

DevTools tabs may not have reliable numeric IDs for durable rebinding. The test suite explicitly protects against fallback mistakes where a stale `target_id` should not be treated as a valid match.

## Prompt input discovery

Prompt finding is intentionally heuristic.

The code relies on:

- provider-specific prompt selector lists in `existing_chrome.py`
- shared utilities in `selectors.py`
- fallback handling for contenteditable and textarea variants

This is necessary because both providers frequently change DOM structure, data attributes, and accessibility labels.

## Prompt insertion

Prompt insertion uses JavaScript execution in the target tab.

Important safety detail:

- tests assert the fallback path avoids naive `innerHTML` replacement
- the implementation prefers safer DOM replacement methods such as `replaceChildren()`

This reduces the chance of injecting broken markup or tripping provider editor behavior.

## Submit behavior

Submit logic is also heuristic:

- find the appropriate send button if possible
- otherwise use provider-specific interaction strategies

If submit cannot be confirmed, the worker raises a structured `submit_failed` path.

## Upload handling

The normal product currently supports one uploaded image per job.

The upload path:

1. reads the local file
2. streams or injects it into the provider tab
3. polls provider-side attachment status markers
4. waits until the DOM preview is ready
5. only then allows prompt submission to continue

Tests cover important timing edges such as:

- `attached` state
- `awaiting-ack` state
- DOM readiness catching up after status changes

## Login and verification detection

The worker explicitly checks for:

- normal ready state
- login required
- manual verification required

If the provider page requires human verification, the job ends as `manual_verification_required` rather than pretending it can continue.

## Busy-state and response waiting

Two different ideas matter here:

1. whether the provider is still busy
2. whether the final response is stable enough to accept

The adapter's `wait_for_response()` delegates to `wait_for_response_stable()` with:

- provider-specific timeout
- provider-specific stable seconds
- excluded text to avoid echoing the user's prompt
- whether images are expected
- optional cancellation callback

This prevents grabbing incomplete streaming output too early.

## Image generation flow

Image generation is intentionally conservative.

High-level path:

1. switch the provider UI into create-image mode when needed
2. submit the prompt
3. wait until generated image state looks stable
4. inspect the latest assistant turn
5. try export strategies in order

### Export order

The current adapter uses this order:

1. provider download or open-image affordances
2. durable asset URLs
3. DOM fallback

### Confidence model

Image extraction returns `ImageExtractionResult` with:

- `artifacts`
- `source`
- `confidence`
- `technical_notes`

`is_acceptable` only returns true for:

- non-empty artifacts
- confidence `high` or `medium`

That means low-confidence DOM-only results are intentionally rejected. The project prefers missing an image over returning the wrong one.

## Diagnostics support

Provider adapters can collect diagnostics including:

- whether matching provider tabs exist
- login state
- whether the provider appears busy
- currently visible provider tabs
- adapter notes when exceptions occur

This powers `/api/diagnostics`.

## Helper Playwright flows

`browser.py` also contains Playwright-based helper flows for:

- profile preparation
- persistent login snapshots
- screenshots
- traces
- selector debugging

These are useful for debugging and scripts, but the main production-like flow remains existing-Chrome control.

## Fragile areas to watch

The most change-sensitive parts of the automation are:

- prompt selectors
- submit button selectors
- login and verification detection
- busy-state heuristics
- generated image extraction from the latest assistant turn
- ChatGPT image generation affordances

If provider UIs change, these are the first places to inspect.
