# YT Video Pipeline Branch

Branch: `yt-video-pipeline`

This branch is the integration/stabilization branch for:

`M2002HR/YT_Video_Generation_Pipeline`

## Scope

- ChatGPT browser automation is the active provider for the video pipeline.
- Gemini support from upstream remains available but is out of scope for this branch until explicitly re-enabled.
- The pipeline repository owns workflow state, prompt/reference selection, retries across beat jobs, output naming, and resumability.
- Ordak owns real-browser execution: attach/open the configured authenticated Chrome profile, submit prompts/uploads, observe provider state, recover from transient UI/network stalls, and extract text/images.
- Runtime configuration must be supplied by the parent pipeline root environment. Do not require a second authoritative `.env` inside the submodule for pipeline operation.
- Generated media remains owned by the parent pipeline; Ordak storage is operational/diagnostic storage.

## Primary stabilization target

The first hard target is deterministic sequential ChatGPT image generation for video visual beats:

1. canonical style reference
2. canonical character reference
3. optional recurring/video references
4. previous accepted beat image for Beat 02+
5. current beat prompt
6. exactly one accepted standalone 16:9 output image

The system must be resilient to slow generations, intermittent internet, stale ChatGPT pages, temporary UI stalls, and recoverable submission failures.

A Codex implementation goal in the parent repository will define the full acceptance tests and recovery state machine.
