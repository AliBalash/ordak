# Job Lifecycle and Errors

This document explains how jobs move through `ordak`, how retry and resume differ, and how structured errors are surfaced.

## Job lifecycle overview

Every request that performs provider work becomes a persisted job row.

The lifecycle has four major phases:

1. creation
2. queued waiting
3. active browser execution
4. terminal completion or failure

## Job creation

Jobs are created by `JobManager.create_job()`.

Creation does the following:

1. resolves or generates `job_id`
2. resolves or generates `conversation_id`
3. creates the conversation if needed
4. clears the saved conversation tab binding when `start_new_chat=true`
5. builds initial metadata
6. inserts the `jobs` row with status `queued`
7. appends an initial log entry
8. places the job ID on the async queue
9. broadcasts a snapshot to subscribers

Initial status:

- `queued`

## Queue waiting

Queued jobs sit in `JobManager.queue` until `_consume_queue()` pulls them.

Because only one browser job runs at once:

- a queued job may wait behind other jobs
- queue order matters
- diagnostics expose the queue depth

## Execution phase

When dequeued, `_run_job_sync()`:

1. creates a `JobControl`
2. marks the job active
3. takes `browser_lock`
4. sets job status to `running`
5. loads the current snapshot and conversation
6. determines whether to reuse a saved tab or force a new one
7. builds `AutomationJobRequest`
8. builds a `WorkerRuntime` of callbacks
9. invokes the worker

## Runtime callback model

The worker never updates the database directly. It reports progress through runtime callbacks, including:

- `update_status()`
- `append_log()`
- `attach_screenshot()`
- `attach_output_image()`
- `remember_conversation_state()`
- `set_trace_path()`
- `save_answer()`
- `save_error()`

This keeps browser automation logic separate from persistence and API broadcasting.

## Intermediate statuses

The current code recognizes these non-terminal statuses:

- `queued`
- `running`
- `checking_browser`
- `opening_provider_tab`
- `opening_gemini_tab`
- `opening_browser`
- `navigating_to_gemini`
- `checking_login`
- `finding_input`
- `submitting_prompt`
- `waiting_for_response`
- `extracting_answer`
- `cancelling`

Not every job will hit every state. Some names are historical but remain part of the schema and UI mapping.

## Terminal statuses

The current terminal states are:

- `completed`
- `failed`
- `manual_verification_required`
- `cancelled`

These are the statuses that stop wait loops in `app/main.py`.

## Completion behavior

Jobs normally finish as `completed` when:

- answer extraction succeeds for text modes, or
- image extraction returns acceptable artifacts for image generation

The manager also sets `finished_at` if completion is recorded without that timestamp already set.

## Failure behavior

There are two main failure families:

1. structured expected automation failures
2. unexpected exceptions

### Structured failures

These are represented by `error_code` values from `app/errors.py`.

### Unexpected failures

If the worker raises an unhandled exception:

- the manager stores the message as `error_message`
- status becomes `failed`
- a log entry is appended

## Cancellation model

## Queued cancellation

If a job has not started yet:

- `cancel_job()` adds it to `cancelled_queued_jobs`
- status becomes `cancelled`
- `finished_at` is set
- a warning log is added

When the queue later dequeues that job ID, it simply skips execution.

## Running cancellation

If a job is active:

- its `JobControl.cancel_event` is set
- status becomes `cancelling`
- the worker can observe `runtime.checkpoint()`
- provider-specific stop logic may run
- final state becomes `cancelled` or `failed` depending on where cancellation is observed

## Retry model

Retry means "create a new attempt based on an old job."

Implemented in `JobManager.retry_job()`.

Retry:

- always creates a new job ID
- copies question, provider, mode, and uploads
- sets `retry_of_job_id`
- may keep or replace the conversation depending on strategy

### Retry strategies

#### `same_tab`

- keep the same `conversation_id`
- attempt to reuse the bound tab

#### `new_tab_same_conversation`

- keep the same `conversation_id`
- force `target_tab=None` during execution so a fresh browser tab is opened for the same conversation

#### `new_chat`

- create a fresh `conversation_id`
- reset conversation title and binding

## Resume model

Resume is narrower than retry.

Implemented in `JobManager.resume_job()`.

Allowed source statuses:

- `failed`
- `manual_verification_required`
- `cancelled`

Resume keeps:

- the same `conversation_id`
- the same provider
- the same uploads

Resume is intended for recoverable situations where the existing provider thread still matters.

### When resume is rejected

Resume raises `ValueError` if:

- the source job is not in an allowed state
- the job has a non-recoverable structured error

## Restart recovery

At startup, `JobManager.start()` calls `_recover_stale_incomplete_jobs()`.

Statuses treated as stale-incomplete include:

- `running`
- `checking_browser`
- `opening_provider_tab`
- `opening_gemini_tab`
- `opening_browser`
- `navigating_to_gemini`
- `checking_login`
- `finding_input`
- `submitting_prompt`
- `waiting_for_response`
- `extracting_answer`
- `cancelling`

These jobs are marked:

- `failed`
- `error_code=response_timeout`
- recoverable according to the descriptor

This prevents stuck forever-running jobs after a crash or restart.

## Structured error model

Structured errors live in `app/errors.py`.

Each descriptor defines:

- `code`
- `title`
- `message`
- `suggested_action`
- `recoverable`

## Error reference

### `login_required`

Meaning:

- the provider page is not logged in in the browser session `ordak` is using

Common action:

- sign in manually in Chrome
- retry or resume

Recoverable:

- yes

### `manual_verification_required`

Meaning:

- a captcha, verification prompt, or manual challenge blocks automation

Common action:

- complete verification in the provider tab
- resume

Recoverable:

- yes

### `tab_lost`

Meaning:

- the conversation's saved tab identity can no longer be matched

Common action:

- retry in a new tab
- reopen the provider conversation URL
- then resume or retry

Recoverable:

- yes

### `upload_incomplete`

Meaning:

- the image attachment did not become ready before submit

Common action:

- retry after the provider attachment preview appears

Recoverable:

- yes

### `submit_failed`

Meaning:

- prompt insertion happened but the provider composer did not successfully submit

Common action:

- retry in the same tab or open a fresh one

Recoverable:

- yes

### `response_timeout`

Meaning:

- the provider never reached a stable final state within the timeout

Common action:

- inspect the tab
- retry or resume if the provider eventually finishes

Recoverable:

- yes

### `result_not_extractable`

Meaning:

- the provider UI appears to have produced something, but `ordak` could not capture a trustworthy final artifact

Common action:

- inspect screenshots, logs, and the provider tab
- retry after checking whether selectors or DOM changed

Recoverable:

- yes

### `provider_ui_changed`

Meaning:

- provider layout or selectors likely drifted away from current assumptions

Common action:

- run diagnostics and selector debugging
- update extraction or prompt selectors

Recoverable:

- yes

### `project_url_missing`

Meaning:

- a ChatGPT project URL was expected for a clean project-scoped flow

Recoverable:

- yes

### `chrome_not_open`

Meaning:

- the browser session needed by the worker is not available

Recoverable:

- yes

### `apple_events_blocked`

Meaning:

- macOS Chrome is blocking JavaScript from Apple Events

Recoverable:

- yes

## How errors appear in API responses

Structured errors surface through:

- `status`
- `error_code`
- `error_title`
- `error_message`
- `suggested_action`
- `recoverable`

Clients should use these fields rather than inferring recovery behavior from status text alone.

## Logs and screenshots during failures

Many failure paths also record:

- log entries in `job.logs`
- screenshots in `job.screenshot_paths`
- sometimes failure HTML dumps or traces depending on the path used

Those artifacts are often more useful than the plain `error_message` when debugging provider UI drift.
