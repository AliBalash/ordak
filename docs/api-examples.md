# API Examples

This document provides concrete examples for the main `ordak` flows.

Assume the server is running at `http://127.0.0.1:8000`.

## Health

```bash
curl http://127.0.0.1:8000/api/health
```

## Diagnostics

```bash
curl http://127.0.0.1:8000/api/diagnostics
curl http://127.0.0.1:8000/api/diagnostics/storage
```

## Create an async Gemini chat job

```bash
curl -X POST http://127.0.0.1:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Reply in one short sentence.",
    "provider": "gemini",
    "mode": "chat",
    "start_new_chat": true
  }'
```

## Create an async ChatGPT chat job

```bash
curl -X POST http://127.0.0.1:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Reply in one short sentence.",
    "provider": "chatgpt",
    "mode": "chat",
    "start_new_chat": true
  }'
```

## Continue an existing conversation

```bash
curl -X POST http://127.0.0.1:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Continue the previous thread and summarize it in Persian.",
    "provider": "gemini",
    "conversation_id": "YOUR_CONVERSATION_ID",
    "mode": "chat",
    "start_new_chat": false
  }'
```

## Poll a job

```bash
curl http://127.0.0.1:8000/api/jobs/YOUR_JOB_ID
```

## List recent jobs

```bash
curl http://127.0.0.1:8000/api/jobs
```

## Run Gemini and wait for completion

```bash
curl -X POST http://127.0.0.1:8000/api/gemini/respond \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Reply with exactly: ORDAK_GEMINI_TEXT_EXAMPLE",
    "mode": "chat",
    "start_new_chat": true,
    "wait_for_completion": true,
    "wait_timeout_seconds": 300
  }'
```

## Run ChatGPT and wait for completion

```bash
curl -X POST http://127.0.0.1:8000/api/chatgpt/respond \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Reply with exactly: ORDAK_CHATGPT_TEXT_EXAMPLE",
    "mode": "chat",
    "start_new_chat": true,
    "wait_for_completion": true,
    "wait_timeout_seconds": 300
  }'
```

## Use the provider-generic endpoint

```bash
curl -X POST http://127.0.0.1:8000/api/providers/gemini/respond \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Reply with exactly: PROVIDER_GENERIC_OK",
    "mode": "chat",
    "start_new_chat": true,
    "wait_for_completion": true
  }'
```

## Analyze an image with Gemini

```bash
curl -X POST http://127.0.0.1:8000/api/gemini/respond \
  -F 'question=If you can analyze the uploaded image, reply with exactly: GEMINI_IMAGE_ANALYZE_OK' \
  -F 'mode=image_analyze' \
  -F 'start_new_chat=true' \
  -F 'wait_for_completion=true' \
  -F 'wait_timeout_seconds=300' \
  -F 'image=@sample-image-test.png;type=image/png'
```

## Analyze an image with ChatGPT

```bash
curl -X POST http://127.0.0.1:8000/api/chatgpt/respond \
  -F 'question=If you can analyze the uploaded image, reply with exactly: CHATGPT_IMAGE_ANALYZE_OK' \
  -F 'mode=image_analyze' \
  -F 'start_new_chat=true' \
  -F 'wait_for_completion=true' \
  -F 'wait_timeout_seconds=300' \
  -F 'image=@sample-image-test.png;type=image/png'
```

## Generate or edit an image with Gemini

```bash
curl -X POST http://127.0.0.1:8000/api/gemini/respond \
  -F 'question=Remove the background from the uploaded image and replace it with solid white.' \
  -F 'mode=image_generate' \
  -F 'start_new_chat=true' \
  -F 'wait_for_completion=true' \
  -F 'wait_timeout_seconds=300' \
  -F 'image=@sample-image-test.png;type=image/png'
```

## Generate or edit an image with ChatGPT

```bash
curl -X POST http://127.0.0.1:8000/api/chatgpt/respond \
  -F 'question=Edit the uploaded image by replacing its background with solid white.' \
  -F 'mode=image_generate' \
  -F 'start_new_chat=true' \
  -F 'wait_for_completion=true' \
  -F 'wait_timeout_seconds=300' \
  -F 'image=@sample-image-test.png;type=image/png'
```

## Cancel a running job

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/YOUR_JOB_ID/cancel
```

## Retry a job in the same tab

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/YOUR_JOB_ID/retry \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "same_tab"
  }'
```

## Retry a job in a new tab but same provider conversation

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/YOUR_JOB_ID/retry \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "new_tab_same_conversation"
  }'
```

## Retry a job as a fresh chat

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/YOUR_JOB_ID/retry \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "new_chat"
  }'
```

## Resume a recoverable job

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/YOUR_JOB_ID/resume \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "same_tab"
  }'
```

## List conversations

```bash
curl http://127.0.0.1:8000/api/conversations
```

## Read one conversation

```bash
curl http://127.0.0.1:8000/api/conversations/YOUR_CONVERSATION_ID
```

## Pin a conversation

```bash
curl -X POST http://127.0.0.1:8000/api/conversations/YOUR_CONVERSATION_ID/pin
```

## Unpin a conversation

```bash
curl -X POST http://127.0.0.1:8000/api/conversations/YOUR_CONVERSATION_ID/unpin
```

## Open the helper profile/session

```bash
curl -X POST http://127.0.0.1:8000/api/profile/open
```

## Cleanup storage

```bash
curl -X POST http://127.0.0.1:8000/api/diagnostics/cleanup
```

## Minimal WebSocket example

In browser JavaScript:

```js
const socket = new WebSocket("ws://127.0.0.1:8000/ws/jobs/YOUR_JOB_ID");

socket.onmessage = (event) => {
  const payload = JSON.parse(event.data);
  console.log(payload.type, payload.job.status, payload.log);
};

socket.onopen = () => {
  socket.send("ready");
};
```

Note:

- the server sends an initial snapshot immediately after `accept()`
- the current implementation keeps the socket loop alive by awaiting `receive_text()`
- a client may send keepalive text frames, but the job updates themselves arrive from the server
