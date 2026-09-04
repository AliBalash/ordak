"""Google Flow video generation — verified settings, role-driven references, credit-safe.

This worker refuses to spend Flow credits on anything it cannot prove. Concretely:

* Every generation setting (model, aspect, resolution, duration, reference mode, output
  count) is selected and then re-read from the control that owns it, via
  ``app.automation.flow_settings``. A mismatch raises instead of generating (§18-21).
* References go to the control that matches their role:
  ``first_frame``/``last_frame`` fill Flow's Start/End slots, every other allowed role
  becomes an Ingredients chip, and each attachment is confirmed from the UI (§14-16).
  ``app.flow_policy`` has already rejected style sheets before we get here (§61).
* A submission fingerprint is persisted **before** the Generate click, so a crash or
  restart mid-job reconciles against the workspace instead of paying twice (§22, §80).
* Downloads are routed to a per-job directory with ``Browser.setDownloadBehavior``, so the
  file that lands there is the file this job produced — no mtime guessing (§23).
* ``outputs`` is pinned to ``x1``: every extra output multiplies the credit cost.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.automation import flow_references, flow_settings
from app.automation.existing_chrome import (
    ChromeTabRef,
    dispatch_mouse_click,
    execute_javascript,
    get_tab_info,
    download_to,
    insert_text,
    is_google_chrome_running,
    wait_for_prompt_input,
)
from app.automation.gemini_worker import (
    AutomationJobRequest,
    GeminiAutomationError,
    WorkerRuntime,
    _ensure_linux_browser_ready,
    _map_login_error,
    _runtime_checkpoint,
)
from app.config import Settings, settings
from app.errors import ErrorCode, OrdaKError
from app.flow_policy import validate_references
from app.providers import get_provider_adapter
from app.schemas import GenerationReceipt

FLOW_BASE_URL = "https://labs.google/fx/tools/flow"
FLOW_PROJECT_URL_PREFIX = "https://labs.google/fx/tools/flow/project/"

#: Only ever one output per submission — see the module docstring.
FLOW_OUTPUT_COUNT = "x1"

#: Defaults exist so a malformed request fails loudly instead of silently generating 16:9.
DEFAULT_ASPECT = "9:16"
DEFAULT_RESOLUTION = "720p"
DEFAULT_MODEL = "gemini_omni_1_1_flash"

#: Written next to the job output before Generate; removed once the result is on disk.
PENDING_MARKER = "PENDING_GENERATE.json"
CAPABILITY_SNAPSHOT = "FLOW_CAPABILITY.json"
RECEIPT_SNAPSHOT = "FLOW_RECEIPT.json"

MIN_VIDEO_BYTES = 40_000


def _flow_raise(code: ErrorCode, message: str, details: str | None = None) -> None:
    raise OrdaKError(code=code, message=message, technical_details=details)


def _as_automation_error(exc: OrdaKError) -> GeminiAutomationError:
    """Carry an OrdaKError's structured code into the job record."""
    status = (
        "manual_verification_required"
        if exc.code == ErrorCode.FLOW_MANUAL_VERIFICATION_REQUIRED
        else "failed"
    )
    message = exc.message
    if exc.technical_details:
        message = f"{message} ({exc.technical_details})"
    return GeminiAutomationError(message, status=status, error_code=exc.code.value)


def _log(runtime: WorkerRuntime | None, message: str, level: str = "info") -> None:
    if runtime is not None:
        runtime.append_log(message, level)


def _evaluate(tab: ChromeTabRef, script: str) -> Any:
    raw = execute_javascript(tab, script)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- project


_NEW_PROJECT_JS = r"""
(() => {
  const target = Array.from(document.querySelectorAll('button')).find(
    (b) => (b.innerText || '').includes('New project')
  );
  if (!target) return JSON.stringify({found: false});
  const r = target.getBoundingClientRect();
  return JSON.stringify({
    found: true,
    x: Math.round(r.x + r.width / 2),
    y: Math.round(r.y + r.height / 2),
  });
})()
"""


def _current_url(tab: ChromeTabRef) -> str:
    info = get_tab_info(tab)
    return (info.url or "") if info is not None else ""


def _ensure_flow_project(
    tab: ChromeTabRef,
    runtime: WorkerRuntime | None,
    app_settings: Settings,
) -> str:
    """Make sure the tab is inside a Flow *project*, creating one if needed.

    The composer only exists inside a project; the tool's landing page has no settings
    menu and no reference controls, so generating from it is impossible rather than wrong.
    """
    url = _current_url(tab)
    if FLOW_PROJECT_URL_PREFIX in url:
        _log(runtime, f"Flow project ready: {url}")
        return url
    if "labs.google/fx/tools/flow" not in url:
        _flow_raise(
            ErrorCode.FLOW_TAB_LOST,
            "The Flow tab is no longer on labs.google/fx/tools/flow.",
            f"observed url: {url!r}",
        )
    for attempt in range(8):
        state = _evaluate(tab, _NEW_PROJECT_JS) or {}
        if state.get("found"):
            dispatch_mouse_click(tab, state["x"], state["y"])
            for _ in range(10):
                time.sleep(1.5)
                url = _current_url(tab)
                if FLOW_PROJECT_URL_PREFIX in url:
                    _log(runtime, f"Created a new Flow project: {url}")
                    return url
        time.sleep(1.5 + 0.5 * attempt)
    _flow_raise(
        ErrorCode.FLOW_UI_CHANGED,
        "Could not open or create a Flow project.",
        "The 'New project' control never produced a /project/ URL.",
    )
    return ""


# ----------------------------------------------------------------- credit safety (§22)


@dataclass
class PendingSubmission:
    """The record that makes a paid Generate click recoverable instead of repeatable."""

    fingerprint: str
    job_id: str
    workspace_url: str
    prompt_sha256: str
    reference_sha256: dict[str, str] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    results_before: int = 0
    results_media: list[str] = field(default_factory=list)
    submitted_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "job_id": self.job_id,
            "workspace_url": self.workspace_url,
            "prompt_sha256": self.prompt_sha256,
            "reference_sha256": dict(self.reference_sha256),
            "settings": dict(self.settings),
            "results_before": self.results_before,
            "results_media": list(self.results_media),
            "submitted_at": self.submitted_at,
        }


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def submission_fingerprint(parts: list[str]) -> str:
    """Stable identity of one paid generation request (kept in sync with the pipeline)."""
    return _sha256_text("\n".join(str(part) for part in parts))[:32]


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def _read_pending(output_dir: Path) -> dict[str, Any] | None:
    marker = output_dir / PENDING_MARKER
    if not marker.is_file():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _record_pending(output_dir: Path, pending: PendingSubmission) -> None:
    _write_json_atomic(output_dir / PENDING_MARKER, pending.to_dict())


def _clear_pending(output_dir: Path) -> None:
    marker = output_dir / PENDING_MARKER
    try:
        marker.unlink()
    except FileNotFoundError:
        pass


_RESULT_MEDIA_JS = r"""
(() => {
  const urls = Array.from(document.querySelectorAll('video'))
    .map((v) => v.src || v.currentSrc || '')
    .filter((src) => src.includes('media.getMediaUrlRedirect'));
  return JSON.stringify({media: Array.from(new Set(urls))});
})()
"""


def _result_media(tab: ChromeTabRef) -> list[str]:
    """Every result video Flow currently exposes, identified by its media URL.

    The URL carries a stable per-asset ``name``, so "which clip is new" is answered by set
    difference rather than by trusting grid order or file timestamps.
    """
    state = _evaluate(tab, _RESULT_MEDIA_JS) or {}
    return [str(url) for url in (state.get("media") or [])]


def _count_results(tab: ChromeTabRef) -> int:
    return len(_result_media(tab))


def _reconcile_pending(
    tab: ChromeTabRef,
    output_dir: Path,
    fingerprint: str,
    runtime: WorkerRuntime | None,
) -> Path | None:
    """Decide what a leftover pending marker means. Never falls through to a new Generate.

    A marker means credits were already spent for this exact request. Either the video is
    on disk (finish), or the workspace grew a result we can still download (download it),
    or a human has to look (``FLOW_RECONCILIATION_REQUIRED``). Blind resubmission is the
    one outcome that is never allowed (§22, §80).
    """
    pending = _read_pending(output_dir)
    if pending is None:
        return None

    existing = sorted(output_dir.glob("*.mp4"))
    if existing:
        _log(runtime, f"Reconciled: this job's video is already on disk ({existing[0].name})")
        _clear_pending(output_dir)
        return existing[0]

    recorded = str(pending.get("fingerprint") or "")
    results_before = int(pending.get("results_before") or 0)
    results_now = _count_results(tab)
    if recorded != fingerprint:
        _flow_raise(
            ErrorCode.FLOW_RECONCILIATION_REQUIRED,
            "A previous Flow submission for this job is unaccounted for and its request "
            "differs from the current one; a human must decide before spending more credits.",
            f"recorded fingerprint {recorded!r} != current {fingerprint!r}",
        )
    if results_now > results_before:
        known = list(pending.get("results_media") or [])
        fresh = [url for url in _result_media(tab) if url not in set(known)]
        _log(
            runtime,
            f"Reconciled: Flow already produced a result for this submission "
            f"({results_before} -> {results_now} results); downloading instead of regenerating.",
        )
        return _download_flow_video(
            tab,
            output_dir,
            runtime,
            fresh[0] if fresh else None,
            job_id=str(pending.get("job_id") or "flow"),
        )
    _flow_raise(
        ErrorCode.FLOW_RECONCILIATION_REQUIRED,
        "Flow was already asked to generate this clip and no result is visible. "
        "Check the Flow workspace before retrying — a blind retry would spend credits twice.",
        f"results before={results_before}, now={results_now}, fingerprint={fingerprint}",
    )
    return None


# ------------------------------------------------------------------- prompt and submit


_EDITOR_JS = r"""
(() => {
  const e = document.querySelector('div[contenteditable="true"][role="textbox"]');
  if (!e) return JSON.stringify({found: false});
  const r = e.getBoundingClientRect();
  return JSON.stringify({
    found: true,
    x: Math.round(r.x + r.width / 2),
    y: Math.round(r.y + r.height / 2),
    text: (e.innerText || '').trim(),
  });
})()
"""

_SUBMIT_JS = r"""
(() => {
  const target = Array.from(document.querySelectorAll('button')).find(
    (b) => (b.innerHTML || '').includes('arrow_forward')
  );
  if (!target) return JSON.stringify({found: false});
  const r = target.getBoundingClientRect();
  return JSON.stringify({
    found: true,
    enabled: !target.disabled && target.getAttribute('aria-disabled') !== 'true',
    x: Math.round(r.x + r.width / 2),
    y: Math.round(r.y + r.height / 2),
  });
})()
"""


def _type_prompt(tab: ChromeTabRef, prompt: str, runtime: WorkerRuntime | None) -> None:
    """Put exactly ``prompt`` in the composer and confirm the composer holds it."""
    state = _evaluate(tab, _EDITOR_JS) or {}
    if not state.get("found"):
        _flow_raise(ErrorCode.FLOW_UI_CHANGED, "Flow prompt editor was not found.")
    dispatch_mouse_click(tab, state["x"], state["y"])
    time.sleep(0.5)
    execute_javascript(
        tab,
        """
(() => {
  const e = document.querySelector('div[contenteditable="true"][role="textbox"]');
  if (!e) return 'no-editor';
  e.focus();
  document.execCommand('selectAll', false, null);
  document.execCommand('delete', false, null);
  return 'cleared';
})()
""",
    )
    time.sleep(0.3)
    insert_text(tab, prompt)
    for attempt in range(6):
        time.sleep(0.5 + 0.3 * attempt)
        current = (_evaluate(tab, _EDITOR_JS) or {}).get("text") or ""
        if current.strip()[:80] == prompt.strip()[:80]:
            _log(runtime, f"Flow prompt set ({len(prompt)} chars)")
            return
    _flow_raise(
        ErrorCode.FLOW_UI_CHANGED,
        "Flow composer did not accept the prompt text.",
        f"editor shows {(_evaluate(tab, _EDITOR_JS) or {}).get('text', '')[:120]!r}",
    )


_CREDIT_TEXT_JS = r"""
(() => {
  const text = (document.body.innerText || '').toLowerCase();
  const needles = [
    'out of credits',
    'no credits',
    'credits remaining: 0',
    'you have run out',
    'insufficient credits',
    'credit limit',
  ];
  const hit = needles.find((n) => text.includes(n));
  return JSON.stringify({exhausted: !!hit, needle: hit || null});
})()
"""


def _assert_credits_available(tab: ChromeTabRef, runtime: WorkerRuntime | None) -> None:
    state = _evaluate(tab, _CREDIT_TEXT_JS) or {}
    if state.get("exhausted"):
        _flow_raise(
            ErrorCode.FLOW_CREDITS_EXHAUSTED,
            "Flow reports that the account is out of credits; generation was not attempted.",
            f"matched UI text: {state.get('needle')!r}",
        )


def _submit(tab: ChromeTabRef, runtime: WorkerRuntime | None) -> None:
    """Click Generate. Only ever called once per job, after the pending marker is on disk."""
    for attempt in range(10):
        state = _evaluate(tab, _SUBMIT_JS) or {}
        if state.get("found") and state.get("enabled"):
            dispatch_mouse_click(tab, state["x"], state["y"])
            _log(runtime, "Flow Generate clicked")
            time.sleep(2.0)
            return
        time.sleep(1.0 + 0.3 * attempt)
    _flow_raise(
        ErrorCode.SUBMIT_FAILED,
        "Flow's Generate button never became enabled.",
        f"last state: {_evaluate(tab, _SUBMIT_JS)!r}",
    )


# ------------------------------------------------------------------------ generation


_GENERATION_STATE_JS = r"""
(() => {
  const baseline = %d;
  const videos = Array.from(document.querySelectorAll('video'));
  const text = (document.body.innerText || '').toLowerCase();
  const failed =
    text.includes("didn't follow") ||
    text.includes('violates') ||
    text.includes('policy') && text.includes('failed');
  const generating =
    text.includes('generating') || text.includes('in progress') || text.includes('queued');
  return JSON.stringify({
    videos: videos.length,
    newResults: Math.max(0, videos.length - baseline),
    failed: failed,
    generating: generating,
  });
})()
"""


def _wait_for_generation(
    tab: ChromeTabRef,
    baseline: list[str],
    runtime: WorkerRuntime | None,
    timeout_ms: int,
) -> str:
    """Wait for a result that was not in ``baseline`` and return its media URL."""
    known = set(baseline)
    script = _GENERATION_STATE_JS % len(baseline)
    deadline = time.monotonic() + max(60.0, timeout_ms / 1000)
    last = ""
    while time.monotonic() < deadline:
        _runtime_checkpoint(runtime)
        state = _evaluate(tab, script) or {}
        fresh = [url for url in _result_media(tab) if url not in known]
        summary = (
            f"results={state.get('videos')} new={len(fresh)} "
            f"generating={state.get('generating')}"
        )
        if summary != last:
            _log(runtime, f"Flow generation: {summary}")
            last = summary
        _assert_credits_available(tab, runtime)
        if fresh:
            _log(runtime, f"Flow produced a new result: {fresh[0][:90]}")
            # A tile can appear while the clip is still rendering, so settle before download:
            # only a finished asset stops reporting progress.
            quiet = 0
            settle_deadline = time.monotonic() + 120.0
            while quiet < 2 and time.monotonic() < settle_deadline:
                time.sleep(3.0)
                later = _evaluate(tab, script) or {}
                quiet = quiet + 1 if not later.get("generating") else 0
            return fresh[0]
        if state.get("failed") and not state.get("generating"):
            _flow_raise(
                ErrorCode.FLOW_POLICY_VIOLATION,
                "Flow rejected the prompt (content policy) and produced no video.",
            )
        time.sleep(5.0)
    _flow_raise(
        ErrorCode.FLOW_GENERATION_TIMEOUT,
        f"Flow did not produce a result within {int(timeout_ms / 1000)}s.",
        f"no media appeared beyond the {len(baseline)} result(s) present before submitting",
    )
    raise AssertionError("unreachable")


# -------------------------------------------------------------------------- download


_RESULT_TILE_JS = r"""
(() => {
  const needle = %s;
  const media = Array.from(document.querySelectorAll('video,img')).find(
    (el) => (el.src || '').includes(needle)
  );
  if (!media) return JSON.stringify({found: false});
  // The clickable tile is the first ancestor large enough to be a real thumbnail.
  let node = media;
  for (let depth = 0; depth < 8 && node.parentElement; depth += 1) {
    node = node.parentElement;
    const r = node.getBoundingClientRect();
    if (r.width > 60 && r.height > 60) {
      return JSON.stringify({
        found: true,
        x: Math.round(r.x + r.width / 2),
        y: Math.round(r.y + r.height / 2),
        w: Math.round(r.width),
        h: Math.round(r.height),
      });
    }
  }
  return JSON.stringify({found: false});
})()
"""

_DOWNLOAD_BUTTON_JS = r"""
(() => {
  const pick = Array.from(document.querySelectorAll('button,[role="menuitem"],a'))
    .map((el) => ({el: el, text: (el.innerText || '').trim().toLowerCase()}))
    .find((c) => c.text.includes('download') && !c.el.disabled);
  if (!pick) return JSON.stringify({found: false});
  const r = pick.el.getBoundingClientRect();
  return JSON.stringify({
    found: true,
    label: pick.text.slice(0, 40),
    x: Math.round(r.x + r.width / 2),
    y: Math.round(r.y + r.height / 2),
  });
})()
"""

_DOWNLOAD_OPTION_JS = r"""
(() => {
  const wrapper = document.querySelector('[data-radix-popper-content-wrapper]');
  if (!wrapper) return JSON.stringify({found: false});
  const pick = Array.from(wrapper.querySelectorAll('[role="menuitem"],button'))
    .map((el) => ({el: el, text: (el.innerText || '').trim()}))
    .find((c) => c.text && !c.el.disabled && /original|720|1080|mp4|video/i.test(c.text));
  if (!pick) return JSON.stringify({found: false});
  const r = pick.el.getBoundingClientRect();
  return JSON.stringify({
    found: true,
    label: pick.text.slice(0, 40),
    x: Math.round(r.x + r.width / 2),
    y: Math.round(r.y + r.height / 2),
  });
})()
"""

_LEAVE_EDIT_JS = r"""
(() => {
  const done = Array.from(document.querySelectorAll('button')).find(
    (b) => (b.innerText || '').trim().toLowerCase() === 'done'
  );
  if (!done) return JSON.stringify({found: false});
  const r = done.getBoundingClientRect();
  return JSON.stringify({
    found: true,
    x: Math.round(r.x + r.width / 2),
    y: Math.round(r.y + r.height / 2),
  });
})()
"""


#: Bookkeeping this worker writes into the job directory itself; never a download.
_WORKER_ARTIFACTS = {PENDING_MARKER, CAPABILITY_SNAPSHOT, RECEIPT_SNAPSHOT}


def _download_candidates(output_dir: Path) -> list[Path]:
    """Files Chrome put here. ``allowAndName`` writes bare GUIDs, so extensions can't filter."""
    return [
        path
        for path in output_dir.iterdir()
        if path.is_file()
        and path.name not in _WORKER_ARTIFACTS
        and not path.name.endswith((".crdownload", ".part"))
    ]


def _settled_download(output_dir: Path, timeout_s: float) -> Path | None:
    """Return the finished download in this job's own directory, or None.

    ``output_dir`` is created per job and Chrome is told to name downloads by GUID inside it,
    so whatever appears here belongs to this job — there is nothing to disambiguate.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        partial = list(output_dir.glob("*.crdownload"))
        finished = _download_candidates(output_dir)
        if finished and not partial:
            candidate = max(finished, key=lambda p: p.stat().st_mtime)
            first = candidate.stat().st_size
            time.sleep(1.5)
            if candidate.stat().st_size == first and first >= MIN_VIDEO_BYTES:
                return candidate
        if time.monotonic() >= deadline:
            return None
        time.sleep(1.5)


def _media_id(media_url: str) -> str:
    """The asset id inside a Flow media URL, used to find that asset's tile."""
    _, _, tail = media_url.partition("name=")
    return (tail.split("&")[0] or media_url)[:64]


def _open_result(tab: ChromeTabRef, media_url: str, runtime: WorkerRuntime | None) -> bool:
    """Open the detail view of the exact result we identified, not "whatever is first"."""
    script = _RESULT_TILE_JS % json.dumps(_media_id(media_url))
    for attempt in range(6):
        tile = _evaluate(tab, script) or {}
        if tile.get("found"):
            dispatch_mouse_click(tab, tile["x"], tile["y"])
            time.sleep(3.0)
            if (_evaluate(tab, _DOWNLOAD_BUTTON_JS) or {}).get("found"):
                _log(runtime, f"Opened the result tile for {_media_id(media_url)}")
                return True
        time.sleep(1.5 + 0.5 * attempt)
    return False


def _leave_result_view(tab: ChromeTabRef) -> None:
    """Return to the project composer so the next job starts from a known screen."""
    state = _evaluate(tab, _LEAVE_EDIT_JS) or {}
    if state.get("found"):
        dispatch_mouse_click(tab, state["x"], state["y"])
        time.sleep(2.0)


def _download_flow_video(
    tab: ChromeTabRef,
    output_dir: Path,
    runtime: WorkerRuntime | None,
    media_url: str | None = None,
    *,
    job_id: str = "flow",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = _settled_download(output_dir, 0.1)
    if existing is not None:
        return existing

    if media_url and not _open_result(tab, media_url, runtime):
        _flow_raise(
            ErrorCode.FLOW_RESULT_NOT_FOUND,
            "Flow produced a result but its tile could not be opened for download.",
            f"media={media_url[:120]}",
        )

    # The download routing only holds while this DevTools session is open, so the click and
    # the wait both happen inside the block.
    with download_to(tab, output_dir):
        _log(runtime, f"Flow downloads routed to {output_dir}")
        for attempt in range(3):
            button = _evaluate(tab, _DOWNLOAD_BUTTON_JS) or {}
            if not button.get("found"):
                time.sleep(2.0)
                continue
            dispatch_mouse_click(tab, button["x"], button["y"])
            _log(runtime, f"Flow download control clicked ({button.get('label')})")
            time.sleep(1.5)
            option = _evaluate(tab, _DOWNLOAD_OPTION_JS) or {}
            if option.get("found"):
                dispatch_mouse_click(tab, option["x"], option["y"])
                _log(runtime, f"Flow download option chosen: {option.get('label')}")
            settled = _settled_download(output_dir, 180.0)
            if settled is not None:
                _log(
                    runtime,
                    f"Flow video downloaded: {settled.name} ({settled.stat().st_size} bytes)",
                )
                _leave_result_view(tab)
                return settled
            _log(runtime, f"Download attempt {attempt + 1} produced no file yet", "warning")
    _flow_raise(
        ErrorCode.FLOW_DOWNLOAD_FAILED,
        "Flow produced a result but no video file arrived in this job's download directory.",
        f"watched {output_dir}",
    )
    raise AssertionError("unreachable")


# ------------------------------------------------------------------------ validation


def probe_video(path: Path) -> dict[str, Any]:
    """ffprobe facts about a produced clip. Raises INVALID_VIDEO_OUTPUT if unusable."""
    source = Path(path)
    if not source.is_file() or source.stat().st_size < MIN_VIDEO_BYTES:
        _flow_raise(
            ErrorCode.INVALID_VIDEO_OUTPUT,
            f"Flow output is missing or too small to be a video: {source}",
        )
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        _flow_raise(
            ErrorCode.INVALID_VIDEO_OUTPUT,
            "ffprobe is not installed, so the Flow output cannot be validated.",
        )
        raise AssertionError("unreachable")
    if completed.returncode != 0:
        _flow_raise(
            ErrorCode.INVALID_VIDEO_OUTPUT,
            "ffprobe could not decode the Flow output.",
            completed.stderr.strip()[:400],
        )
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        _flow_raise(ErrorCode.INVALID_VIDEO_OUTPUT, "Flow output has no video stream.")
    duration = float((payload.get("format") or {}).get("duration") or 0.0)
    if duration <= 0.2:
        _flow_raise(
            ErrorCode.INVALID_VIDEO_OUTPUT,
            f"Flow output duration is {duration:.3f}s, which cannot be a real clip.",
        )
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    return {
        "path": str(source),
        "bytes": source.stat().st_size,
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "codec": video.get("codec_name"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "aspect_ratio": f"{width}:{height}" if width and height else None,
    }


def _normalized_aspect(width: int, height: int) -> str | None:
    """Reduce pixel dimensions to the nearest aspect Flow actually offers."""
    if not width or not height:
        return None
    ratio = width / height
    for label, value in (("9:16", 9 / 16), ("16:9", 16 / 9), ("1:1", 1.0)):
        if abs(ratio - value) <= 0.03:
            return label
    return f"{width}:{height}"


def _validate_against_request(
    probe: dict[str, Any],
    applied: flow_settings.AppliedSettings,
    runtime: WorkerRuntime | None,
) -> list[str]:
    """Compare the produced file with the settings Flow confirmed. Mismatch = failure.

    The settings were verified in the UI before generating; if the file still disagrees,
    the file is not what was ordered and must not be accepted as the clip (§21, §32).
    """
    notes: list[str] = []
    wanted_duration = flow_settings.normalize_duration(applied.duration)
    if wanted_duration:
        target = float(wanted_duration.rstrip("s"))
        actual = float(probe["duration_seconds"])
        # Flow trims to whole seconds but encoders drift by a frame or two.
        if abs(actual - target) > 1.0:
            _flow_raise(
                ErrorCode.INVALID_VIDEO_OUTPUT,
                f"Flow returned a {actual:.2f}s clip but {target:.0f}s was confirmed in the UI.",
            )
        notes.append(f"duration {actual:.2f}s vs requested {target:.0f}s")

    observed_aspect = _normalized_aspect(probe["width"], probe["height"])
    wanted_aspect = flow_settings.normalize_aspect(applied.aspect_ratio)
    if wanted_aspect and observed_aspect and observed_aspect != wanted_aspect:
        _flow_raise(
            ErrorCode.INVALID_VIDEO_OUTPUT,
            f"Flow returned a {observed_aspect} clip but {wanted_aspect} was confirmed in the UI.",
            f"{probe['width']}x{probe['height']}",
        )

    wanted_resolution = flow_settings.normalize_resolution(applied.resolution)
    if wanted_resolution:
        target_lines = int(wanted_resolution.rstrip("p") or 0)
        short_side = min(probe["width"], probe["height"])
        if target_lines and short_side and short_side < target_lines * 0.9:
            _flow_raise(
                ErrorCode.INVALID_VIDEO_OUTPUT,
                f"Flow returned {probe['width']}x{probe['height']} but {wanted_resolution} "
                "was confirmed in the UI.",
            )
        notes.append(f"resolution {probe['width']}x{probe['height']} vs {wanted_resolution}")
    _log(runtime, "Flow output validated: " + "; ".join(notes) if notes else "Flow output validated")
    return notes


# ------------------------------------------------------------------------- entrypoint


def _requested_contract(job: AutomationJobRequest) -> tuple[str, str, int, str]:
    """Read the explicit generation contract; refuse to guess a paid parameter (§5)."""
    generation = job.generation
    duration = generation.duration_seconds if generation else None
    if not duration:
        _flow_raise(
            ErrorCode.MODEL_FEATURE_INCOMPATIBLE,
            "A Flow video job must state duration_seconds; guessing it would change the "
            "credit cost and the narration sync budget.",
        )
    model = (generation.model if generation and generation.model else DEFAULT_MODEL)
    aspect = (generation.aspect_ratio if generation and generation.aspect_ratio else DEFAULT_ASPECT)
    resolution = (
        generation.resolution if generation and generation.resolution else DEFAULT_RESOLUTION
    )
    return str(model), str(aspect), int(duration), str(resolution)


def _reference_pairs(job: AutomationJobRequest) -> list[tuple[str, Path]]:
    """Normalized ``(role, path)`` pairs, policy-checked one more time at the boundary."""
    pairs = [(str(role), Path(path)) for role, path in (job.references or [])]
    if job.uploads and not pairs:
        _flow_raise(
            ErrorCode.FLOW_REFERENCE_POLICY_VIOLATION,
            "Flow uploads arrived without reference roles, so their purpose is unknown.",
            f"{len(job.uploads)} upload(s) with no role",
        )
    if not pairs:
        return []
    roles = validate_references([(role, path) for role, path in pairs])
    return list(zip(roles, [path for _, path in pairs]))


def _reference_mode_for(roles: list[str]) -> str:
    """Frames mode is required by first/last frame roles and excluded otherwise (§10)."""
    if any(role in flow_references.FRAME_SLOTS for role in roles):
        return "Frames"
    return "Ingredients"


def _build_receipt(
    job: AutomationJobRequest,
    applied: flow_settings.AppliedSettings,
    attached: flow_references.AttachedReferences,
    probe: dict[str, Any],
    workspace_url: str,
    fingerprint: str,
    notes: list[str],
) -> GenerationReceipt:
    """Everything in here came from the UI or ffprobe — nothing is echoed from the request."""
    requested_model, requested_aspect, requested_duration, requested_resolution = (
        _requested_contract(job)
    )
    roles = list(attached.frames.keys()) + list(attached.ingredients)
    return GenerationReceipt(
        provider="flow",
        requested_model=requested_model,
        actual_model_label=applied.model_label,
        model_verified=bool(
            applied.model
            and flow_settings.normalize_model(requested_model) == applied.model
        ),
        pro_regeneration_used=False,
        requested_aspect_ratio=requested_aspect,
        actual_aspect_ratio=applied.aspect_ratio,
        requested_duration_seconds=requested_duration,
        actual_duration_seconds=int(round(float(probe["duration_seconds"]))),
        requested_resolution=requested_resolution,
        actual_resolution=applied.resolution,
        workspace_url=workspace_url,
        submission_fingerprint=fingerprint,
        reference_roles=roles,
        notes=[
            f"credits_required={applied.credits_required}",
            f"outputs={applied.outputs}",
            f"reference_mode={applied.reference_mode}",
            f"pixels={probe['width']}x{probe['height']}",
            f"bytes={probe['bytes']}",
            *notes,
        ],
    )


def run_flow_job(
    job_id: str,
    job: AutomationJobRequest,
    runtime: WorkerRuntime | None = None,
    app_settings: Settings | None = None,
) -> str:
    resolved = app_settings or settings
    output_dir = Path(getattr(resolved, "browser_output_dir", Path("/tmp"))) / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        return _run_flow_job_inner(job_id, job, runtime, resolved, output_dir)
    except OrdaKError as exc:
        error = _as_automation_error(exc)
        if runtime is not None:
            runtime.save_error(error.message, error.status, exc.code.value)
            runtime.append_log(f"Flow job failed [{exc.code.value}]: {error.message}", "error")
        raise error from exc
    except GeminiAutomationError:
        raise
    except Exception as exc:  # pragma: no cover - unexpected browser faults
        if runtime is not None:
            runtime.save_error(str(exc), "failed", ErrorCode.PROVIDER_UI_CHANGED.value)
        raise GeminiAutomationError(
            str(exc), error_code=ErrorCode.PROVIDER_UI_CHANGED.value
        ) from exc


def _run_flow_job_inner(
    job_id: str,
    job: AutomationJobRequest,
    runtime: WorkerRuntime | None,
    resolved: Settings,
    output_dir: Path,
) -> str:
    adapter = get_provider_adapter(job.provider)
    model, aspect, duration, resolution = _requested_contract(job)
    references = _reference_pairs(job)
    reference_mode = _reference_mode_for([role for role, _ in references])

    if runtime is not None:
        runtime.update_status("checking_browser")
    _log(runtime, "Checking Chrome for Flow")
    _ensure_linux_browser_ready(resolved, job.provider)
    if not is_google_chrome_running():
        raise OrdaKError(
            code=ErrorCode.CHROME_NOT_OPEN,
            message="Chrome is not running, so Flow cannot be driven.",
        )

    if runtime is not None:
        runtime.update_status("opening_provider_tab")
    target = getattr(resolved, "flow_url", None) or FLOW_BASE_URL
    opened = adapter.open_tab(target_url=target)
    tab = opened.ref if hasattr(opened, "ref") else opened
    workspace_url = _ensure_flow_project(tab, runtime, resolved)

    if runtime is not None:
        runtime.update_status("checking_login")
    _map_login_error(job.provider, adapter.detect_login_state(tab))

    if runtime is not None:
        runtime.update_status("finding_input")
    wait_for_prompt_input(tab, provider="flow", timeout_ms=60_000)
    _assert_credits_available(tab, runtime)

    if runtime is not None:
        runtime.update_status("submitting_prompt")
    capabilities = flow_settings.read_capabilities(tab, project_url=workspace_url)
    _write_json_atomic(output_dir / CAPABILITY_SNAPSHOT, capabilities.to_dict())
    _log(
        runtime,
        "Flow capability snapshot: "
        f"model={capabilities.model_label!r} groups={sorted(capabilities.groups)}",
    )

    applied = flow_settings.apply_settings(
        tab,
        model=model,
        aspect_ratio=aspect,
        duration_seconds=duration,
        resolution=resolution,
        reference_mode=reference_mode,
        outputs=FLOW_OUTPUT_COUNT,
        runtime=runtime,
    )

    attached = flow_references.attach_references(tab, reference_mode, references, runtime=runtime)
    _log(runtime, f"Flow references attached: {json.dumps(attached.to_dict())}")

    _type_prompt(tab, job.question, runtime)

    fingerprint = submission_fingerprint(
        [
            "flow",
            applied.model or model,
            applied.aspect_ratio or aspect,
            applied.resolution or resolution,
            applied.duration or f"{duration}s",
            applied.reference_mode or reference_mode,
            FLOW_OUTPUT_COUNT,
            _sha256_text(job.question),
            *[f"{role}:{_sha256_file(path)}" for role, path in references],
        ]
    )

    reconciled = _reconcile_pending(tab, output_dir, fingerprint, runtime)
    if reconciled is None:
        results_media = _result_media(tab)
        results_before = len(results_media)
        pending = PendingSubmission(
            fingerprint=fingerprint,
            job_id=job_id,
            workspace_url=workspace_url,
            prompt_sha256=_sha256_text(job.question),
            reference_sha256={role: _sha256_file(path) for role, path in references},
            settings=applied.to_dict(),
            results_before=results_before,
            results_media=results_media,
            submitted_at=time.time(),
        )
        # Persist first: after this line a crash costs a reconciliation, not a second charge.
        _record_pending(output_dir, pending)
        _log(
            runtime,
            f"Credit guard armed (fingerprint {fingerprint}, {results_before} existing results, "
            f"{applied.credits_required} credits)",
        )
        _submit(tab, runtime)
        if runtime is not None:
            runtime.update_status("waiting_for_response")
        media_url = _wait_for_generation(
            tab,
            results_media,
            runtime,
            int(getattr(resolved, "flow_response_timeout_ms", 240_000)),
        )
        if runtime is not None:
            runtime.update_status("extracting_answer")
        video_path = _download_flow_video(
            tab, output_dir, runtime, media_url, job_id=job_id
        )
    else:
        video_path = reconciled

    final_path = video_path
    if final_path.name != f"{job_id}.mp4":
        renamed = output_dir / f"{job_id}.mp4"
        final_path = final_path.replace(renamed)

    probe = probe_video(final_path)
    notes = _validate_against_request(probe, applied, runtime)
    _clear_pending(output_dir)

    receipt = _build_receipt(
        job, applied, attached, probe, workspace_url, fingerprint, notes
    )
    _write_json_atomic(
        output_dir / RECEIPT_SNAPSHOT,
        {
            "receipt": receipt.model_dump(),
            "applied_settings": applied.to_dict(),
            "attached_references": attached.to_dict(),
            "probe": probe,
        },
    )
    if runtime is not None and runtime.attach_generation_receipt is not None:
        runtime.attach_generation_receipt(receipt)

    if runtime is not None:
        runtime.save_answer(
            f"Flow generated {probe['duration_seconds']}s "
            f"{probe['width']}x{probe['height']} video: {final_path}"
        )
        try:
            if runtime.attach_output_video is not None:
                runtime.attach_output_video(final_path)
            else:
                runtime.attach_output_image(final_path)
        except Exception as exc:  # pragma: no cover - artifact bookkeeping only
            _log(runtime, f"Could not attach the Flow output artifact: {exc}", "warning")
        # The job record is only terminal once the worker says so; without this the manager's
        # finally-block sees a non-terminal status and records a completed job as failed.
        runtime.update_status("completed")
    return f"Flow video saved to {final_path}"


__all__ = [
    "CAPABILITY_SNAPSHOT",
    "PENDING_MARKER",
    "PendingSubmission",
    "RECEIPT_SNAPSHOT",
    "probe_video",
    "run_flow_job",
    "submission_fingerprint",
]
