# ordak Documentation

This directory contains the code-grounded documentation set for `ordak`.

`ordak` is a local FastAPI application that drives the real Gemini and ChatGPT web UIs through an already signed-in Google Chrome session. It does not call provider APIs directly. Everything in this documentation set is written against the current repository state and focuses on how the code actually behaves today.

## Documentation map

- [Architecture](architecture.md)
- [Setup and configuration](setup-and-configuration.md)
- [API reference](api-reference.md)
- [API examples](api-examples.md)
- [Job lifecycle and errors](job-lifecycle-and-errors.md)
- [Browser automation](browser-automation.md)
- [Data model and storage](data-model-and-storage.md)
- [Repository map](repository-map.md)
- [Frontend panel](frontend-panel.md)
- [Development and testing](development-and-testing.md)
- [Scripts and operations](scripts-and-operations.md)
- [Troubleshooting](troubleshooting.md)

## Recommended reading order

For first-time orientation:

1. repository root `README.md`
2. [Setup and configuration](setup-and-configuration.md)
3. [Architecture](architecture.md)
4. [API reference](api-reference.md)
5. [Job lifecycle and errors](job-lifecycle-and-errors.md)
6. [Browser automation](browser-automation.md)

For maintainers and contributors:

1. [Repository map](repository-map.md)
2. [Data model and storage](data-model-and-storage.md)
3. [Frontend panel](frontend-panel.md)
4. [Development and testing](development-and-testing.md)
5. [Scripts and operations](scripts-and-operations.md)
6. [Troubleshooting](troubleshooting.md)

## What this documentation set covers

- the application lifecycle from FastAPI startup to worker shutdown
- the job queue and why only one browser job runs at a time
- how conversations are tracked, rebound, pinned, retried, and resumed
- how Linux DevTools attach mode differs from macOS Chrome control
- how uploads, screenshots, traces, logs, and generated images are stored
- every environment variable currently loaded by `app/config.py`
- the HTTP and WebSocket interfaces exposed by `app/main.py`
- the current automated test coverage and what still needs manual validation

## Ground rules and scope

- `ordak` is a local desktop automation project, not a multi-tenant hosted service.
- The most important design decision is reuse of a real signed-in Chrome session.
- Reliability depends on provider UI stability. Selector or DOM changes can break automation without any local code changes.
- Linux is designed around DevTools attach mode first. The old X11 path still exists, but it is intentionally off by default and should be treated as a debugging fallback.
- The Docker files in the repository are technically runnable, but they do not replace the requirement for a real authenticated browser session. Read the setup guide before assuming a containerized deployment will behave like a normal local desktop run.
