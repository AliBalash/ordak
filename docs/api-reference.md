# API Reference

This document describes the HTTP and WebSocket interfaces exposed by `app/main.py`.

## API style overview

The API supports two main usage styles:

1. asynchronous job creation and later polling
2. direct provider response calls that still run through the same job system

## Base URLs

Typical local addresses:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/diagnostics`
- `http://127.0.0.1:8000/api/...`

## Accepted content types

- `application/json`
- `multipart/form-data`

Use multipart when sending an image.
Use JSON for agent mode.

## Core enums

### Providers

- `gemini`
- `chatgpt`

### Job modes

- `chat`
- `image_analyze`
- `image_generate`
- `agent`

### Terminal job states

The API treats these as terminal:

- `completed`
- `failed`
- `manual_verification_required`
- `cancelled`

## Response model concepts

The main payload families are:

- `JobCreateResponse`
- `JobResponse`
- `ProviderRunResponse`
- `ConversationSummary`
- `ConversationResponse`
- `DiagnosticsResponse`
- `StorageDiagnosticsResponse`

`JobResponse` is the most important shared model. It contains:

- job identity
- prompt text
- provider
- mode
- conversation linkage
- uploads
- generated outputs
- answer text
- status
- structured error data
- logs
- screenshots
- trace path
- agent workspace and resolved limits
- agent step count
- timestamps

## Root and static routes

### `GET /`

Returns the main panel HTML.

### `GET /diagnostics`

Returns the diagnostics page HTML.

### `GET /api/uploads/{filename}`

Serves a previously uploaded image if the filename passes `slugify_filename()` validation and exists under `settings.browser_upload_dir`.

Important behavior:

- filenames are sanitized
- invalid or missing filenames return `404`
- response includes `Access-Control-Allow-Origin: *`

## Health and diagnostics

### `GET /api/health`

Response:

```json
{
  "status": "ok",
  "database": "ready"
}
```

### `GET /api/diagnostics`

Returns:

- product name and label
- whether Chrome is running
- whether Apple Events automation appears available
- provider session diagnostics for Gemini and ChatGPT
- whether a ChatGPT project URL is configured
- tab binding health
- last success and last error by provider
- the active job, if any
- queue depth

### `GET /api/diagnostics/storage`

Returns counts and total bytes for:

- uploads
- outputs
- traces
- failure HTML dumps

### `POST /api/diagnostics/cleanup`

Triggers storage cleanup and returns:

- `deleted_files`
- `freed_bytes`

## Job endpoints

## `POST /api/jobs`

Creates a new asynchronous job.

### JSON request body

```json
{
  "question": "Reply in one sentence.",
  "provider": "gemini",
  "mode": "chat",
  "conversation_id": null,
  "start_new_chat": false,
  "agent": null
}
```

### JSON field behavior

- `question`: required, 1 to 20000 characters
- `provider`: defaults to `gemini`
- `mode`: defaults to `chat`
- `conversation_id`: optional existing `ordak` conversation ID
- `start_new_chat`: if `true`, clears the saved tab binding and conversation external URL before the new run
- `agent`: required when `mode == "agent"` and rejected for non-agent modes

### Agent JSON example

```json
{
  "question": "Inspect this repository, fix failing tests, and validate the result.",
  "provider": "chatgpt",
  "mode": "agent",
  "start_new_chat": true,
  "agent": {
    "workspace": "/home/you/Code/project",
    "max_steps": 50,
    "command_timeout_seconds": 180,
    "execution_backend": "host",
    "network_enabled": false
  }
}
```

### Multipart request behavior

If `multipart/form-data` is used:

- `question` is required
- `provider` defaults to `gemini`
- `mode` defaults to `chat`
- `conversation_id` is optional
- `start_new_chat` is parsed through `_as_form_bool()`
- one file field named `image` is supported

Multipart mode also accepts:

- `wait_for_completion`
- `wait_timeout_seconds`

Those fields matter mainly for provider direct endpoints but are parsed by the shared helper.

Agent mode does not accept multipart requests or uploads.

### Success response

```json
{
  "job_id": "uuid",
  "conversation_id": "uuid",
  "provider": "gemini",
  "status": "queued"
}
```

### Failure behavior

- invalid provider or mode returns `422`
- invalid timeout range returns `422`
- unsupported upload type or bad upload can return `422`
- conversation provider mismatch can surface as `409`

## `GET /api/jobs`

Returns up to 20 most recent jobs ordered by newest first.

## `GET /api/jobs/{job_id}`

Returns one `JobResponse`.

Important fields in practice:

- `status`
- `answer`
- `error_code`
- `error_title`
- `error_message`
- `suggested_action`
- `recoverable`
- `uploads`

## `GET /api/jobs/{job_id}/steps`

Returns ordered agent-step details for one job.

```json
{
  "steps": [
    {
      "id": "uuid",
      "job_id": "uuid",
      "sequence": 1,
      "command_id": "step-0001",
      "tool": "exec",
      "status": "completed",
      "request_json": "{\"version\":1,...}",
      "result_json": "{\"ok\":true,...}",
      "started_at": "2026-07-14T00:00:00Z",
      "finished_at": "2026-07-14T00:00:01Z",
      "duration_ms": 1000,
      "error_message": null
    }
  ]
}
```
- `output_images`
- `screenshots`
- `trace_path`
- `logs`

## `POST /api/jobs/{job_id}/cancel`

Requests cancellation.

Behavior:

- if the job is already terminal, the snapshot is returned unchanged
- if the job is still queued, it is immediately marked `cancelled`
- if the job is running, `cancel_requested_at` is set and the status becomes `cancelling`

## `POST /api/jobs/{job_id}/retry`

Request body:

```json
{
  "strategy": "same_tab"
}
```

Valid strategies:

- `same_tab`
- `new_tab_same_conversation`
- `new_chat`

Behavior:

- creates a brand new job
- copies the source question, provider, mode, and uploads
- sets `retry_of_job_id` to the source job ID
- if the strategy is `new_chat`, a fresh `conversation_id` is used

## `POST /api/jobs/{job_id}/resume`

Request body:

```json
{
  "strategy": "same_tab"
}
```

Valid strategies:

- `same_tab`
- `new_tab_same_conversation`

Resume is more restrictive than retry:

- only `failed`, `manual_verification_required`, or `cancelled` jobs can be resumed
- if the source job has an `error_code` and is not marked recoverable, resume returns `409`
- resume keeps the same `conversation_id`

## Provider direct endpoints

## `POST /api/providers/{provider}/respond`

Generic direct-response endpoint.

Supported path values:

- `gemini`
- `chatgpt`

## `POST /api/gemini/respond`

Gemini shortcut for the generic endpoint.

## `POST /api/chatgpt/respond`

ChatGPT shortcut for the generic endpoint.

### JSON request body

```json
{
  "question": "Reply with one sentence.",
  "mode": "chat",
  "conversation_id": null,
  "start_new_chat": false,
  "wait_for_completion": true,
  "wait_timeout_seconds": 300
}
```

### Provider request fields

- `question`: required
- `mode`: defaults to `chat`
- `conversation_id`: optional
- `start_new_chat`: defaults to `false`
- `wait_for_completion`: defaults to `true`
- `wait_timeout_seconds`: 1 to 900

### Direct endpoint behavior

These endpoints still create a normal queued job. They only differ in what the HTTP call does afterward:

- if `wait_for_completion=true`, the route polls until terminal or timeout
- if `wait_for_completion=false`, the route returns the current snapshot immediately

### Response fields

`ProviderRunResponse` includes:

- `provider`
- `job_id`
- `job_api_url`
- `conversation_id`
- `conversation_api_url`
- `conversation_title`
- `provider_conversation_url`
- `mode`
- `status`
- `completed`
- `answer`
- `error_code`
- `error_title`
- `error_message`
- `suggested_action`
- `recoverable`
- artifact links for uploads, outputs, screenshots
- `trace_url`
- `logs`

## Conversation endpoints

## `GET /api/conversations`

Returns up to 30 recent conversations ordered by `updated_at` descending.

Each item contains:

- `conversation_id`
- `title`
- `provider`
- `mode`
- preview text
- job count
- last job ID
- last status
- external provider URL
- `tab_alive`
- `pinned`
- timestamps

## `GET /api/conversations/{conversation_id}`

Returns the conversation and all jobs inside it ordered oldest to newest.

## `POST /api/conversations/{conversation_id}/pin`

Sets `pinned=true`.

## `POST /api/conversations/{conversation_id}/unpin`

Sets `pinned=false`.

Pinning matters because storage cleanup protects artifacts referenced by pinned conversations.

## Profile endpoint

### `POST /api/profile/open`

Requests the helper browser-opening flow through `JobManager.launch_profile_browser()`.

Possible outcomes:

- `200` with `"Profile browser opened."`
- `409` if the browser profile is currently in use by an automation job
- `409` if the browser helper path itself raises a runtime error

## WebSocket endpoint

### `WS /ws/jobs/{job_id}`

The socket accepts a subscriber for one job.

Behavior:

- connection is accepted immediately
- server sends one initial `snapshot` event
- later sends `snapshot`, `log`, `completed`, or `failed` events
- the loop remains open until the client disconnects

Initial payload shape:

```json
{
  "type": "snapshot",
  "job": { "...": "JobResponse" },
  "log": null
}
```

## Error status mapping

Common route-level HTTP statuses:

- `404` for unsupported provider paths and missing jobs/conversations/uploads
- `409` for retry/resume/provider mismatch conflicts
- `422` for invalid request content

Application-level automation failures generally do not become HTTP errors after a job is created. Instead, they appear inside the returned job payload as:

- `status`
- `error_code`
- `error_title`
- `error_message`
- `suggested_action`
- `recoverable`
