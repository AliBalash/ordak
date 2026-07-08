# Frontend Panel

This document explains the browser UI shipped inside `app/static/`.

## Frontend scope

The frontend is intentionally lightweight. It does not automate providers. It is a local operator console for:

- creating jobs
- browsing conversations
- following live progress
- inspecting screenshots and outputs
- triggering retries, resumes, and pin actions
- checking diagnostics

## Main files

- `app/static/index.html`
- `app/static/diagnostics.html`
- `app/static/app.js`
- `app/static/style.css`

## Main panel layout

`index.html` is split into two major columns:

- left sidebar
- main chat stage

### Sidebar sections

- brand card
- action buttons
- conversation history
- live details
- screenshot artifacts

### Main stage sections

- current conversation header
- provider switch
- mode indicator
- conversation thread
- composer
- upload preview area

## Diagnostics page

`diagnostics.html` is a separate page that fetches:

- `/api/diagnostics`
- `/api/diagnostics/storage`

It also exposes a cleanup button that calls:

- `POST /api/diagnostics/cleanup`

The page refreshes automatically every 15 seconds.

## Frontend state model

`app.js` maintains client-side state such as:

- `currentJobId`
- `currentConversationId`
- `currentConversationTitle`
- `currentConversationExternalUrl`
- `currentConversationTabAlive`
- `currentConversationPinned`
- `currentConversationJobs`
- `currentProvider`
- `currentMode`
- `pendingNewChat`

This state drives what the UI renders and which actions remain enabled.

## Provider and mode switching

The panel supports:

- provider switch between Gemini and ChatGPT
- mode switch between `chat`, `image_analyze`, and `image_generate`

`syncModeButtons()` updates:

- active button state
- theme
- textarea placeholder
- composer hints
- current mode label and icon

An important guard exists here:

- once a conversation already has jobs, switching to the other provider is disabled unless the user is explicitly preparing a new chat

This matches the backend rule that one conversation belongs to one provider.

## Conversation rendering

The sidebar shows recent conversations from `/api/conversations`.

Each conversation item reflects:

- title
- preview
- provider
- status
- pin state
- tab health

When a conversation is opened, the panel fetches `/api/conversations/{conversation_id}` and renders the full job history.

## Job rendering

The thread is rendered as a sequence of user and assistant messages derived from job history.

Displayed job data includes:

- prompt
- answer text
- output images
- upload previews
- screenshots
- status labels
- errors and suggested actions

The UI also supports before/after comparison rendering for image-editing style outputs.

## Submission flow

Submitting a prompt typically does the following:

1. build either JSON or multipart payload depending on image presence
2. include provider, mode, conversation, and `start_new_chat` state
3. call the API
4. store `job_id` and `conversation_id`
5. subscribe to the job WebSocket
6. render updates as snapshots and logs arrive

## Upload preview behavior

The panel currently supports selecting one image file.

The frontend:

- creates a local object URL
- shows file name and size
- supports clearing the image before submit

This matches the backend's one-image-per-job assumption.

## Live updates

The main live-update path is WebSocket:

- `WS /ws/jobs/{job_id}`

The frontend also has enough state to refresh conversation data and update the rendered thread when jobs change.

Rendered live details include:

- status badge
- short job ID
- recent logs
- screenshot links

## Status mapping

`app.js` maps backend statuses to human-readable labels such as:

- `checking_browser` -> `Checking Chrome`
- `finding_input` -> `Finding Input`
- `waiting_for_response` -> `Waiting for Answer`
- `manual_verification_required` -> `Verification Required`

This keeps the UI readable without hiding the underlying lifecycle.

## Retry, resume, and pin actions

The frontend exposes actions that map directly to backend routes:

- retry
- resume
- pin
- unpin

These are not speculative client-side actions. The panel always round-trips to the server and re-renders from real backend state.

## URL behavior

The panel stores the active conversation in the browser URL query string using:

- `?conversation=<id>`

This makes deep-linking or refreshing into a conversation possible.

## What the frontend intentionally does not do

- it does not implement provider-specific DOM automation
- it does not keep an independent job state machine
- it does not infer recoverability on its own
- it does not bypass the queue

The backend remains the single source of truth.
