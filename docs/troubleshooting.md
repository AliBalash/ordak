# Troubleshooting

This document covers the most common failure modes in the current codebase and how to diagnose them.

## First triage checklist

Before going deep, check:

1. `GET /api/diagnostics`
2. `GET /api/diagnostics/storage`
3. whether Chrome is visibly open
4. whether the provider tab is visibly logged in
5. whether a captcha or verification page is blocking progress
6. recent job screenshots and logs

## Chrome session is not detected

Symptoms:

- jobs fail before provider interaction
- diagnostics show Chrome unavailable
- `error_code=chrome_not_open`

Checks:

1. confirm Chrome is actually running
2. confirm the target provider is logged in in that same Chrome session
3. verify `.env` values for:
   - `BROWSER_PLATFORM`
   - `BROWSER_EXECUTABLE_PATH`
   - `BROWSER_USER_DATA_DIR`
4. open `/diagnostics`

## Linux remote debugging is not reachable

Symptoms:

- Linux jobs fail immediately
- error text mentions remote debugging not reachable

Checks:

```bash
curl http://127.0.0.1:9222/json/version
curl http://127.0.0.1:9222/json/list
```

If those fail:

1. start Chrome manually with remote debugging enabled
2. verify the port matches `.env`
3. verify you are looking at the same Chrome instance you intend to automate

Recommended launch command:

```bash
google-chrome \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.config/ordak-chrome"
```

## Linux uses the wrong profile or a logged-out session

Symptoms:

- the provider opens logged out
- automation attaches to a different account
- expected conversation URLs do not appear in `/json/list`

Prevention:

- keep `BROWSER_REMOTE_DEBUGGING_AUTO_LAUNCH=false`
- manually start the correct 9222 Chrome profile
- sign in there before starting jobs

Recovery:

1. close the wrong DevTools session
2. start the intended signed-in session
3. verify with `/json/list`
4. retry the job

## Apple Events access fails on macOS

Symptoms:

- Chrome is open but automation still fails
- diagnostics suggest browser automation is not ready
- JavaScript execution cannot control the page

Fix:

1. open Chrome
2. enable `View > Developer > Allow JavaScript from Apple Events`
3. restart Chrome if needed

## Provider login is missing

Symptoms:

- `error_code=login_required`
- provider tab opens but the worker stops early

Fix:

1. open the provider manually in the same Chrome session
2. sign in
3. retry or resume the job

## Manual verification or captcha appears

Symptoms:

- `error_code=manual_verification_required`
- provider shows challenge or human verification

Fix:

1. switch to the provider tab
2. complete the verification manually
3. resume the job

## Prompt box is not found

Symptoms:

- status sticks around `finding_input`
- failure eventually suggests provider UI drift

What to inspect:

1. run `scripts/debug_selectors.py`
2. inspect saved screenshot and debug DOM payload
3. check whether the provider composer changed layout or label text

Likely causes:

- selector drift
- consent modal
- provider redesign
- viewport-specific layout change

## Prompt is inserted but not submitted

Symptoms:

- `error_code=submit_failed`
- text appears in the provider composer, but generation does not start

Checks:

1. inspect the live provider tab
2. look for disabled send button state
3. look for modal overlays
4. inspect recent screenshots and logs

## Upload never becomes ready

Symptoms:

- image jobs fail around upload completion
- `error_code=upload_incomplete`

Checks:

1. confirm only one image is being sent
2. inspect whether the provider shows a preview thumbnail
3. inspect timing in logs
4. confirm the uploaded file is valid and not empty

## Response never stabilizes

Symptoms:

- `error_code=response_timeout`
- status remains `waiting_for_response` too long

Checks:

1. inspect the provider tab directly
2. see whether generation is still streaming
3. confirm network or provider slowness
4. inspect whether the provider has silently stalled

Mitigations:

- retry later
- resume in the same tab if the provider eventually finishes
- adjust timeout settings only after confirming a real timing need

## Final answer could not be extracted

Symptoms:

- `error_code=result_not_extractable`
- provider visibly answered, but `ordak` did not capture a usable result

Checks:

1. inspect screenshots
2. inspect logs
3. inspect the provider DOM manually
4. look for layout changes in the latest assistant turn

## Image generation finished but no output image is returned

Symptoms:

- image appears visible in the provider UI
- API returns no usable `output_images`

Important design detail:

`ordak` intentionally rejects low-confidence results.

Export order is:

1. download controls
2. asset URLs
3. DOM fallback

If none of those produce acceptable-confidence artifacts, the job may fail or complete without usable output images.

Checks:

1. confirm the image belongs to the latest assistant turn
2. inspect whether a download/open-image affordance exists
3. inspect logs for which extraction stage failed

## Conversation tab binding looks wrong

Symptoms:

- retries or resumes attach to the wrong thread
- conversations show `tab_alive=false` unexpectedly
- `tab_lost` appears even though a provider tab is open

Checks:

1. inspect `/api/diagnostics`
2. inspect `/api/conversations`
3. on Linux, inspect `/json/list`
4. compare saved `external_url` with live provider URLs

Notes:

- Linux matching now relies heavily on `target_id`
- stale numeric IDs alone should not be trusted

## Storage is growing too large

Symptoms:

- `app/storage/` keeps growing
- old screenshots, traces, and outputs accumulate

Checks:

1. inspect `/api/diagnostics/storage`
2. pin important conversations first
3. run cleanup

Commands:

```bash
curl -X POST http://127.0.0.1:8000/api/diagnostics/cleanup
```

or

```bash
.venv/bin/python scripts/cleanup_storage.py
```

## Restart left jobs stuck in running states

Symptoms:

- after a crash or restart, some jobs were previously mid-flight

Expected behavior:

- on startup, the manager marks stale in-progress jobs as `failed`
- the error code becomes `response_timeout`
- the user can retry or resume if appropriate

If this did not happen, inspect database integrity and startup logs.

## When to change code versus when to change environment

Change environment first if:

- Chrome is closed
- DevTools is unreachable
- the wrong profile is signed in
- a verification challenge is blocking the provider

Change code only after environment checks pass and the failure still reproduces reliably.
