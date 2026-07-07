# API Examples

## Create an async job

```bash
curl -X POST http://127.0.0.1:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "question": "سلام. کوتاه جواب بده.",
    "provider": "gemini",
    "mode": "chat",
    "start_new_chat": true
  }'
```

## Poll a job

```bash
curl http://127.0.0.1:8000/api/jobs/<job_id>
```

## Run ChatGPT and wait for completion

```bash
curl -X POST http://127.0.0.1:8000/api/chatgpt/respond \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Summarize this in one sentence.",
    "mode": "chat",
    "start_new_chat": true,
    "wait_for_completion": true,
    "wait_timeout_seconds": 180
  }'
```

## Analyze an image with Gemini

```bash
curl -X POST http://127.0.0.1:8000/api/gemini/respond \
  -F 'question=این تصویر را تحلیل کن.' \
  -F 'mode=image_analyze' \
  -F 'start_new_chat=true' \
  -F 'wait_for_completion=true' \
  -F 'image=@sample-image-test.png'
```

## Read diagnostics

```bash
curl http://127.0.0.1:8000/api/diagnostics
curl http://127.0.0.1:8000/api/diagnostics/storage
```
