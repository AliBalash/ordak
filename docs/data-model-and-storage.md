# Data Model and Storage

This document explains the SQLite schema, the runtime storage layout, and how cleanup works.

## Database overview

The current default database is SQLite:

```env
DATABASE_URL=sqlite:///./app/storage/jobs.db
```

Database setup lives in:

- `app/database.py`
- `app/models.py`
- `alembic/versions/20260704_0001_ordak_core_upgrade.py`

## ORM models

There are two main tables:

- `conversations`
- `jobs`

## `conversations` table

Model: `app.models.Conversation`

Important columns:

- `id`
- `provider`
- `title`
- `external_url`
- `external_conversation_id`
- `tab_window_id`
- `tab_id`
- `tab_window_key`
- `tab_target_id`
- `tab_alive`
- `last_successful_job_id`
- `last_error_code`
- `pinned`
- `created_at`
- `updated_at`

### Purpose

`conversations` represents the logical thread inside `ordak`, plus the last known browser identity of the related provider thread.

This table is how the app keeps retries and resumes anchored to a meaningful browser conversation.

## `jobs` table

Model: `app.models.Job`

Important columns:

- `id`
- `question`
- `answer`
- `status`
- `error_message`
- `error_code`
- `provider`
- `conversation_id`
- `conversation_title`
- `mode`
- `start_new_chat`
- `retry_of_job_id`
- `run_strategy`
- `recoverable`
- `suggested_action`
- `cancel_requested_at`
- `created_at`
- `started_at`
- `finished_at`
- `screenshot_paths`
- `trace_path`
- `logs`
- `uploads_json`
- `output_images_json`
- `metadata_json`

### Why some fields are JSON strings

The current schema stores several list-like fields as serialized JSON strings instead of separate child tables:

- logs
- screenshot paths
- uploads
- output images
- metadata

This is acceptable for the local MVP-style architecture because:

- job counts are relatively small
- access patterns are simple
- the UI mostly reads full job snapshots

## Schema bootstrap behavior

`init_db()` does two things:

1. `Base.metadata.create_all()`
2. `_ensure_sqlite_schema(engine)` for SQLite compatibility upgrades

The second part is important. The app contains its own pragmatic SQLite migration logic in addition to Alembic.

## SQLite compatibility upgrade path

`_ensure_sqlite_schema()` can:

- create the `conversations` table if missing
- add missing columns to existing tables
- backfill provider, conversation, mode, upload, and output metadata into older rows
- infer conversation records for older jobs

This makes the app more forgiving when opened against older local databases.

## Provider and mode inference

`_infer_provider_and_mode()` tries to reconstruct provider and mode from:

- explicit columns
- answer text
- log text
- uploads
- output images

This is a recovery-oriented heuristic for older databases, not a primary runtime code path for new rows.

## Session handling

Database engine and sessions are configured through:

- `configure_database()`
- `get_engine()`
- `SessionLocal`

SQLite runs with:

- `check_same_thread=False`

That is necessary because work spans async server code and worker threads.

## Storage directories

Default storage root is under:

- `app/storage/`

Important directories:

- `app/storage/uploads/`
- `app/storage/outputs/`
- `app/storage/screenshots/`
- `app/storage/traces/`
- `app/storage/logs/`
- `app/storage/profiles/`

## What goes where

### `uploads/`

Stores images accepted from API multipart requests.

These are user-provided input artifacts.

### `outputs/`

Stores generated or extracted image results.

These are output artifacts.

### `screenshots/`

Stores screenshots captured during runs or debugging flows.

### `traces/`

Stores Playwright trace archives from helper or debug flows.

### `logs/`

Stores debug payloads and failure HTML dumps.

### `profiles/`

Stores helper login and runtime profile data used by Playwright-based flows.

## Path normalization

`app/artifacts.py` provides:

- `slugify_filename()`
- `storage_relative_path()`
- `storage_absolute_path()`

The normal pattern is:

- save the file on disk
- store a repository-relative storage path in the database
- reconstruct the absolute path only when needed

This simplifies UI URLs and cleanup behavior.

## Upload validation

Upload handling lives in `app/uploads.py`.

Important high-level rules:

- uploads are saved before job creation completes
- filenames are normalized
- bad upload state raises `422` from the API layer
- current normal product behavior assumes a single uploaded image per job

## Cleanup behavior

Storage cleanup is implemented in `JobManager.cleanup_storage()`.

### Cleanup inputs

Cleanup looks at:

- all conversations
- all jobs
- pinned conversations
- a recent cutoff based on `STORAGE_RETENTION_DAYS`

### Preservation rules

Uploads and outputs are preserved if they are referenced by:

- a pinned conversation, or
- a job newer than the retention cutoff

### Trace and failure HTML cleanup

Trace files and failure HTML dumps are pruned by count limits:

- `MAX_TRACES`
- `MAX_FAILURE_HTML_DUMPS`

The newest files are kept.

### Cleanup response

`cleanup_storage()` returns:

- `deleted_files`
- `freed_bytes`

## Diagnostics behavior

`storage_diagnostics()` counts:

- uploads
- outputs
- traces
- `*_failure.html` files

It also reports the total byte size of those tracked file families.

## Pinning and retention

Conversation pinning is not only a UI feature.

Pinned conversations:

- stay marked in `conversations.pinned`
- protect related artifacts from cleanup

That makes pinning the right choice for important long-lived conversations or reference image jobs you want to keep around.
