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
import re
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

FLOW_BASE_URL = "https://flow.google.com/"

#: Flow answers on two hosts: the original ``labs.google/fx/tools/flow`` and the newer
#: ``flow.google.com``, which the former now redirects to. Both are accepted so a redirect
#: does not read as a lost tab, and so an account on either host works unchanged.
FLOW_HOST_MARKERS = ("labs.google/fx/tools/flow", "flow.google.com")

#: A project URL on either host. The composer only exists inside a project.
FLOW_PROJECT_URL_MARKERS = (
    "labs.google/fx/tools/flow/project/",
    "flow.google.com/project/",
)
FLOW_PROJECT_URL_PREFIX = "flow.google.com/project/"


def _is_flow_host(url: str) -> bool:
    lowered = (url or "").lower()
    return any(marker in lowered for marker in FLOW_HOST_MARKERS)


def _is_flow_project_url(url: str) -> bool:
    lowered = (url or "").lower()
    return any(marker in lowered for marker in FLOW_PROJECT_URL_MARKERS)

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


def _current_url(tab: ChromeTabRef, *, attempts: int = 3) -> str:
    """The tab's URL, read again when the answer comes back empty.

    An empty answer means "could not read it" — a DevTools reply that raced a navigation, or a
    target id that has just been replaced — not "this tab left Flow". Treating the first empty
    read as fact is what produced ``FLOW_TAB_LOST: observed url: ''`` for a tab that was sitting
    on the project page, and it cost a resume every time (observed 2026-09-05).
    """
    for attempt in range(max(1, attempts)):
        info = get_tab_info(tab)
        url = (info.url or "") if info is not None else ""
        if url:
            return url
        if attempt + 1 < attempts:
            time.sleep(1.0)
    return ""


#: How Flow announces a blocked location, in the URL and on the page.
FLOW_REGION_BLOCK_URL_MARKERS = ("/unsupported-country", "flow.google.com/unsupported")
FLOW_REGION_BLOCK_TEXT = "not available in your country"


def _flow_region_blocked(tab: ChromeTabRef, url: str) -> bool:
    """True when Flow has answered with its unsupported-country page."""
    lowered = (url or "").lower()
    if any(marker in lowered for marker in FLOW_REGION_BLOCK_URL_MARKERS):
        return True
    try:
        from app.automation.existing_chrome import execute_javascript

        text = execute_javascript(
            tab,
            "(() => (document.body ? document.body.innerText : '').slice(0, 4000))()",
        )
    except Exception:
        return False
    return FLOW_REGION_BLOCK_TEXT in str(text or "").lower()


def _bind_flow_tab(adapter: Any, target: str, runtime: WorkerRuntime | None) -> ChromeTabRef:
    """Return a tab reference that actually resolves to the live Flow tab.

    ``open_tab`` hands back a reference built from a DevTools target id, and that id does not
    survive everything Flow does to its own page — opening a result and leaving it replaces the
    target. A dead reference reads back as an empty URL, which surfaced as
    ``FLOW_TAB_LOST: observed url: ''`` even though the Flow tab was sitting right there, and it
    cost a resume every time (observed 2026-09-05).

    So a reference that resolves to nothing is re-resolved against the live tab list, and only a
    Chrome with no Flow tab at all is treated as a lost tab. Matching requires a Flow host, so
    no other provider's tab can be bound by mistake.
    """
    opened = adapter.open_tab(target_url=target)
    tab = opened.ref if hasattr(opened, "ref") else opened
    if _current_url(tab):
        return tab

    from app.automation.existing_chrome import list_google_chrome_tabs

    for info in list_google_chrome_tabs():
        url = str(getattr(info, "url", "") or "")
        if _is_flow_host(url):
            _log(runtime, f"Rebound a stale Flow tab reference to the live tab ({url[:80]})")
            return info.ref
    # Nothing in this Chrome is on Flow any more: ask for the tab once more before giving up.
    opened = adapter.open_tab(target_url=target)
    tab = opened.ref if hasattr(opened, "ref") else opened
    if not _current_url(tab):
        _flow_raise(
            ErrorCode.FLOW_TAB_LOST,
            "Chrome has no usable Google Flow tab.",
            f"reopening {target!r} produced a tab whose URL could not be read",
        )
    return tab


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
    # Flow is geo-restricted and answers a blocked location by redirecting the workspace to
    # its unsupported-country page. Without naming that, the run fails later with "could not
    # find the Flow input box", which sends the operator hunting for a DOM change that did
    # not happen. Checked before anything is uploaded or generated, so it costs no credits.
    if _flow_region_blocked(tab, url):
        _flow_raise(
            ErrorCode.FLOW_REGION_BLOCKED,
            "Google Flow is not available in this country.",
            f"observed url: {url!r}",
        )
    if _is_flow_project_url(url):
        # The block can arrive a moment after the project URL loads, so the settled URL is
        # what decides. Checked here, still before any upload or Generate.
        time.sleep(1.5)
        settled = _current_url(tab)
        if _flow_region_blocked(tab, settled):
            _flow_raise(
                ErrorCode.FLOW_REGION_BLOCKED,
                "Google Flow is not available in this country.",
                f"observed url: {settled!r}",
            )
        _log(runtime, f"Flow project ready: {settled}")
        return settled
    if not _is_flow_host(url):
        _flow_raise(
            ErrorCode.FLOW_TAB_LOST,
            "The Flow tab is no longer on a Google Flow host.",
            f"observed url: {url!r}",
        )
    for attempt in range(8):
        state = _evaluate(tab, _NEW_PROJECT_JS) or {}
        if state.get("found"):
            dispatch_mouse_click(tab, state["x"], state["y"])
            for _ in range(10):
                time.sleep(1.5)
                url = _current_url(tab)
                if _is_flow_project_url(url):
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


#: A result's media URL has two shapes: the older
#: ``labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=<id>`` and the current
#: ``flow-content.google/video/<id>``. Both carry a stable per-asset id, which is what makes
#: "which clip is new" a set difference rather than a guess about grid order. Blob and data
#: URLs are excluded: they are previews, not addressable assets.
#: A finished clip shows up in the project grid as ``flow-video-tile > img.thumbnail``, not as
#: a ``<video>`` — only the detail view mounts one (verified 2026-09-05). Waiting for a
#: ``<video>`` therefore made a finished generation look like no result at all, and the job
#: timed out while its clip sat in the grid. Both shapes are read here, newest first, because
#: the grid lists the most recent asset first; each URL carries a stable per-asset id, so
#: "which clip is new" stays a set difference rather than a guess about order.
_RESULT_MEDIA_JS = r"""
(() => {
  const seen = new Set();
  const out = [];
  const add = (src) => {
    if (!src || /^(blob|data):/i.test(src)) return;
    const id = src.split('?')[0];
    if (seen.has(id)) return;
    seen.add(id);
    out.push(src);
  };
  document.querySelectorAll('video').forEach((v) => {
    const src = v.src || v.currentSrc || '';
    if (/media\.getMediaUrlRedirect|flow-content\.google\/video\/|\/video\/[0-9a-f-]{8,}/i.test(src)) {
      add(src);
    }
  });
  // Video tiles only: an image tile carries `img.image`, a video tile `img.thumbnail`, so a
  // still is never counted as a generated clip.
  document.querySelectorAll('flow-video-tile img.thumbnail, flow-video-tile img')
    .forEach((im) => add(im.src || ''));
  return JSON.stringify({media: out});
})()
"""


#: What a result is addressed by once it has been produced. Flow re-signs the grid's
#: thumbnail URLs while the page is open, so the same clip is served under a different URL
#: minutes apart; a set difference over those URLs therefore reported clips from earlier jobs
#: as "new" and downloaded one of them (observed 2026-09-05: a 6s clip A was downloaded for a
#: 4s clip B request, and only the duration check caught it). The grid lists the most recent
#: asset first, so the clip a submission produced is the first tile once the count has grown.
NEWEST_TILE = "tile:newest"

_NEWEST_TILE_JS = r"""
(() => {
  const tiles = [...document.querySelectorAll('flow-video-tile')];
  if (!tiles.length) return JSON.stringify({found: false, count: 0});
  const tile = tiles[0];
  const holder = tile.closest('flow-grid-tile-container') || tile;
  const target = holder.getBoundingClientRect().width > 60 ? holder : tile;
  const r = target.getBoundingClientRect();
  const img = tile.querySelector('img');
  const thumb = img ? String(img.src || '') : '';
  // A tile appears the moment a generation is accepted, before the clip exists. Readiness is
  // therefore positive evidence, not the absence of the word "generating" in the page text:
  // a real poster frame, a play affordance, and no progress indicator anywhere in the tile.
  const busy = holder.querySelectorAll(
    '[role=progressbar],mat-progress-bar,mat-spinner,.mat-mdc-progress-bar,.mat-mdc-progress-spinner,.spinner'
  ).length > 0 || /generating|processing|queued|in progress|\d{1,3}\s?%/i.test(holder.innerText || '');
  const playable = /play_circle/i.test(tile.innerText || '') || !!tile.querySelector('video');
  return JSON.stringify({
    found: r.width > 60 && r.height > 60,
    count: tiles.length,
    title: (holder.getAttribute('aria-label') || '').slice(0, 120),
    thumb: thumb ? thumb.split('?')[0] : null,
    busy: busy,
    playable: playable,
    ready: /^https?:/i.test(thumb) && !busy && playable,
    x: Math.round(r.x + r.width / 2),
    y: Math.round(r.y + r.height / 2),
  });
})()
"""


def _newest_tile(tab: ChromeTabRef) -> dict[str, Any]:
    """The first (most recent) video tile in the project grid, with its click point."""
    state = _evaluate(tab, _NEWEST_TILE_JS)
    return state if isinstance(state, dict) else {"found": False, "count": 0}


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
        _log(
            runtime,
            f"Reconciled: Flow already produced a result for this submission "
            f"({results_before} -> {results_now} results); downloading instead of regenerating.",
        )
        # The grid is most-recent-first, so the clip that submission produced is its first
        # tile; the download is still validated against the requested duration and aspect.
        return _download_flow_video(
            tab,
            output_dir,
            runtime,
            NEWEST_TILE,
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


#: Flow's composer is a ProseMirror contenteditable. It carries no ``role="textbox"``, so
#: requiring that role finds nothing; the search-box input and the reCAPTCHA textarea are
#: the other editable nodes on the page, hence the visibility and class preference.
_EDITOR_FINDER = r"""
  const editorNode = () => {
    const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const candidates = [
      'div.ProseMirror[contenteditable="true"]',
      'div[contenteditable="true"][role="textbox"]',
      'div[contenteditable="true"]',
      '[contenteditable="true"]',
    ];
    for (const selector of candidates) {
      const hit = [...document.querySelectorAll(selector)].filter(vis)
        .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
      if (hit) return hit;
    }
    return null;
  };
"""

_EDITOR_JS = r"""
(() => {
%(finder)s
  const e = editorNode();
  if (!e) return JSON.stringify({found: false});
  const r = e.getBoundingClientRect();
  return JSON.stringify({
    found: true,
    x: Math.round(r.x + r.width / 2),
    y: Math.round(r.y + r.height / 2),
    text: (e.innerText || '').trim(),
  });
})()
""" % {"finder": _EDITOR_FINDER}

_SUBMIT_JS = r"""
(() => {
  // The generate control's aria-label is the stable part; the icon font text is the fallback.
  const buttons = Array.from(document.querySelectorAll('button,[role=button]'));
  const target = buttons.find(b => /start generation|generate/i.test(b.getAttribute('aria-label') || ''))
    || buttons.find(b => (b.innerHTML || '').includes('arrow_forward'));
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


def _composer_has_prompt(editor_text: str, prompt: str) -> bool:
    """Verify Flow retained our whole prompt, allowing its current ``Edit`` prefix.

    Flow's September 2026 composer renders an ``Edit`` command line above the
    actual ProseMirror text.  It is part of the visible editor's ``innerText``
    even after select-all/delete, so an exact prefix comparison reports a
    false UI-change failure although the full prompt is present.  Permit only
    that known one-line UI prefix; arbitrary stale text is still rejected.
    """
    expected = re.sub(r"\s+", " ", prompt).strip()
    observed = re.sub(r"\s+", " ", editor_text).strip()
    if observed == expected:
        return True
    prefix = "Edit "
    return observed.startswith(prefix) and observed[len(prefix):] == expected


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
%(finder)s
  const e = editorNode();
  if (!e) return 'no-editor';
  e.focus();
  document.execCommand('selectAll', false, null);
  document.execCommand('delete', false, null);
  return 'cleared';
})()
""" % {"finder": _EDITOR_FINDER},
    )
    time.sleep(0.3)
    insert_text(tab, prompt)
    for attempt in range(6):
        time.sleep(0.5 + 0.3 * attempt)
        current = (_evaluate(tab, _EDITOR_JS) or {}).get("text") or ""
        if _composer_has_prompt(current, prompt):
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
  // Count what the grid actually shows for a clip (see _RESULT_MEDIA_JS): the detail view's
  // <video> plus the grid's video-tile thumbnails.
  const videos = Array.from(document.querySelectorAll(
    'video, flow-video-tile img.thumbnail, flow-video-tile img'
  ));
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


#: How Flow words a refusal on the page. An abuse flag and a content rejection both end
#: with no video and no charge, but they need different answers from the operator.
_REFUSAL_JS = r"""
(() => {
  const text = ((document.body ? document.body.innerText : '') || '')
      .replace(/\s+/g, ' ').toLowerCase();
  if (/unusual activity/.test(text)) return 'unusual_activity';
  if (/violat|content polic|not allowed|cannot generate/.test(text)) return 'policy';
  return '';
})()
"""


def _refusal_reason(tab: ChromeTabRef) -> str:
    """``"unusual_activity"``, ``"policy"`` or ``""`` — read from the page, not inferred."""
    try:
        from app.automation.existing_chrome import execute_javascript

        return str(execute_javascript(tab, _REFUSAL_JS) or "").strip()
    except Exception:
        return ""


def _wait_for_generation(
    tab: ChromeTabRef,
    baseline: list[str],
    runtime: WorkerRuntime | None,
    timeout_ms: int,
) -> str:
    """Wait until the grid holds one more clip than ``baseline`` and return its locator.

    The returned value is :data:`NEWEST_TILE`, not a URL: Flow re-signs the grid thumbnails
    while the page is open, so URL identity cannot answer "which clip is new" (see
    :data:`NEWEST_TILE`). Position can — the grid is most-recent-first — and the downloaded
    file is still validated against the requested duration and aspect before it is accepted.
    """
    baseline_count = len(baseline)
    script = _GENERATION_STATE_JS % baseline_count
    deadline = time.monotonic() + max(60.0, timeout_ms / 1000)
    last = ""
    while time.monotonic() < deadline:
        _runtime_checkpoint(runtime)
        state = _evaluate(tab, script) or {}
        newest = _newest_tile(tab)
        count = int(newest.get("count") or 0)
        summary = (
            f"results={count} new={max(0, count - baseline_count)} "
            f"generating={state.get('generating')}"
        )
        if summary != last:
            _log(runtime, f"Flow generation: {summary}")
            last = summary
        _assert_credits_available(tab, runtime)
        if count > baseline_count and newest.get("found"):
            if not newest.get("ready"):
                # The tile exists but the clip does not yet: keep waiting rather than opening
                # a half-rendered asset and asking it to export (observed 2026-09-05 —
                # downloading here produced a failed export, not a video).
                time.sleep(5.0)
                continue
            _log(
                runtime,
                "Flow produced a new result: "
                f"{newest.get('title') or newest.get('thumb') or 'the newest tile'}",
            )
            # Readiness must hold, not merely occur: three consecutive confirmations, and the
            # same asset each time, before the export is asked for.
            stable = 1
            thumb = newest.get("thumb")
            settle_deadline = time.monotonic() + 180.0
            while stable < 3 and time.monotonic() < settle_deadline:
                time.sleep(3.0)
                later = _newest_tile(tab)
                if later.get("ready") and later.get("thumb") == thumb:
                    stable += 1
                else:
                    stable = 0
                    thumb = later.get("thumb")
            if stable < 3:
                _flow_raise(
                    ErrorCode.FLOW_GENERATION_TIMEOUT,
                    "Flow's newest result never settled into a finished clip.",
                    f"last seen: {json.dumps(_newest_tile(tab))[:200]}",
                )
            _log(runtime, "Flow result confirmed finished; exporting")
            return NEWEST_TILE
        # Flow can refuse a generation outright, and the reason matters: an abuse flag is
        # not a content-policy rejection and not a timeout. Both say "no charge", so
        # neither should be retried blindly, and polling on for the full timeout only
        # hides what happened.
        refusal = _refusal_reason(tab)
        if refusal == "unusual_activity":
            _flow_raise(
                ErrorCode.FLOW_UNUSUAL_ACTIVITY,
                "Flow refused the generation as unusual activity and produced no video.",
                "the page states the account was not charged for this generation",
            )
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

#: The quality the export should choose when Flow offers a choice, best first. Flow only ever
#: *generates* 360p or 720p, so 1080 can only come from its own upscale on export; when the menu
#: offers it, it is taken, and every label seen is logged so the real vocabulary is on record
#: rather than guessed at.
_DOWNLOAD_QUALITY_ORDER = ("1080", "upscale", "original", "highest", "720", "mp4")

_DOWNLOAD_OPTION_JS = r"""
(() => {
  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const vis = (e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  // Angular Material menus and Radix poppers both appear here depending on the view.
  const panes = [...document.querySelectorAll(
    '[data-radix-popper-content-wrapper],.cdk-overlay-pane,[role=menu],mat-menu-panel,.mat-mdc-menu-panel'
  )].filter(vis);
  const items = panes.flatMap((pane) =>
    [...pane.querySelectorAll('[role="menuitem"],button,[role=option]')].filter(vis)
      .filter((el) => !el.disabled)
      .map((el) => {
        const r = el.getBoundingClientRect();
        return {
          label: clean(el.innerText).slice(0, 60),
          x: Math.round(r.x + r.width / 2),
          y: Math.round(r.y + r.height / 2),
        };
      })
      .filter((o) => o.label)
  );
  const order = %(order)s;
  let pick = null;
  for (const needle of order) {
    pick = items.find((o) => o.label.toLowerCase().includes(needle));
    if (pick) break;
  }
  return JSON.stringify({
    found: !!pick,
    label: pick ? pick.label : null,
    x: pick ? pick.x : null,
    y: pick ? pick.y : null,
    offered: items.map((o) => o.label).slice(0, 12),
  });
})()
""" % {"order": json.dumps(list(_DOWNLOAD_QUALITY_ORDER))}

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
    """Open the detail view of the result we identified, never "whatever is first" by luck.

    :data:`NEWEST_TILE` means the clip this submission produced — the grid's first tile, which
    is how a just-generated clip is addressed since its thumbnail URL is not stable. Any other
    value is a media URL and is matched against the tile that carries it.
    """
    newest = media_url == NEWEST_TILE
    script = _NEWEST_TILE_JS if newest else _RESULT_TILE_JS % json.dumps(_media_id(media_url))
    for attempt in range(6):
        tile = _evaluate(tab, script) or {}
        if tile.get("found"):
            dispatch_mouse_click(tab, tile["x"], tile["y"])
            time.sleep(3.0)
            if (_evaluate(tab, _DOWNLOAD_BUTTON_JS) or {}).get("found"):
                label = tile.get("title") if newest else _media_id(media_url)
                _log(runtime, f"Opened the result tile for {label or media_url[:60]}")
                return True
        time.sleep(1.5 + 0.5 * attempt)
    return False


def _leave_result_view(tab: ChromeTabRef) -> None:
    """Return to the project composer so the next job starts from a known screen."""
    state = _evaluate(tab, _LEAVE_EDIT_JS) or {}
    if state.get("found"):
        dispatch_mouse_click(tab, state["x"], state["y"])
        time.sleep(2.0)


_PLAYABLE_JS = r"""
(() => {
  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const vis = (e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const video = [...document.querySelectorAll('video')].filter(vis)
    .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
  const duration = video && Number.isFinite(video.duration)
    ? Math.round(video.duration * 100) / 100
    : null;
  // Flow's result view is the scene editor, and it is a canvas player: it mounts no <video>
  // at all (verified 2026-09-05). What it does state is the clip it holds — "Total duration:
  // 00:08" — so that line is the proof the asset exists, and requiring a <video> made every
  // export fail with "never became a playable clip" for clips that were sitting right there.
  const stated = clean(document.body.innerText)
    .match(/total duration:\s*(?:(\d+):)?(\d{1,2}):(\d{2})/i);
  const statedSeconds = stated
    ? Number(stated[1] || 0) * 3600 + Number(stated[2]) * 60 + Number(stated[3])
    : null;
  const downloadReady = [...document.querySelectorAll('button,[role=button],a')].filter(vis)
    .some((b) => /download/i.test(clean(b.innerText) + ' ' + clean(b.getAttribute('aria-label')))
              && !b.disabled);
  // The grid also mounts a <video> for the tile it is previewing, so a playable video alone
  // does not mean we are where the export control lives: that is the scene view, /edit/<id>.
  const sceneView = /\/edit\//.test(location.href);
  return JSON.stringify({
    found: !!video || statedSeconds !== null || downloadReady,
    sceneView,
    duration,
    statedSeconds,
    downloadReady,
    readyState: video ? video.readyState : null,
    width: video ? video.videoWidth : null,
    height: video ? video.videoHeight : null,
    src: video ? String(video.src || video.currentSrc || '').split('?')[0].slice(0, 120) : null,
  });
})()
"""


def _wait_for_playable(
    tab: ChromeTabRef,
    runtime: WorkerRuntime | None,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Wait until the opened result really is a playable clip, and say what it is.

    The detail view mounts a ``<video>``; a finished asset reports a finite duration and has
    loaded its metadata. Asking for the export before that is what produced a failed export
    with no file (observed 2026-09-05), so the wait is a precondition of the download rather
    than a retry around it.
    """
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = _evaluate(tab, _PLAYABLE_JS) or {}
        last = state if isinstance(state, dict) else {}
        duration = last.get("duration")
        stated = last.get("statedSeconds")
        proof = ""
        if last.get("downloadReady"):
            proof = "an enabled export control on the opened scene"
        elif isinstance(stated, (int, float)) and stated > 0:
            proof = f"the player's own total duration of {int(stated)}s"
        elif (
            last.get("sceneView")
            and isinstance(duration, (int, float))
            and duration > 0.2
            and int(last.get("readyState") or 0) >= 1
        ):
            proof = f"a playable <video> of {duration}s {last.get('width')}x{last.get('height')}"
        if proof:
            _log(runtime, f"Flow result confirmed by {proof}")
            return last
        time.sleep(2.0)
    _flow_raise(
        ErrorCode.FLOW_RESULT_NOT_FOUND,
        "Flow opened the result but it never became a playable clip, so nothing was exported.",
        f"last observed: {json.dumps(last)[:200]}",
    )
    raise AssertionError("unreachable")


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
    if media_url:
        # Only a clip that plays can be exported; this is the check that keeps the export from
        # being asked for while the asset is still being rendered.
        _wait_for_playable(tab, runtime)

    # The download routing only holds while this DevTools session is open, so the click and
    # the wait both happen inside the block.
    with download_to(tab, output_dir):
        _log(runtime, f"Flow downloads routed to {output_dir}")
        for attempt in range(3):
            button = _evaluate(tab, _DOWNLOAD_BUTTON_JS) or {}
            if not button.get("found"):
                # The grid has no export control; only the scene view does. Leaving the scene
                # (or never having opened it) is a recoverable state, so the result is opened
                # again rather than the attempts being spent clicking nothing.
                if media_url:
                    _log(runtime, "No export control here; opening the result view again")
                    if _open_result(tab, media_url, runtime):
                        _wait_for_playable(tab, runtime, timeout_s=60.0)
                time.sleep(2.0)
                continue
            dispatch_mouse_click(tab, button["x"], button["y"])
            _log(runtime, f"Flow download control clicked ({button.get('label')})")
            time.sleep(1.5)
            time.sleep(1.0)
            option = _evaluate(tab, _DOWNLOAD_OPTION_JS) or {}
            offered = option.get("offered") or []
            if offered:
                _log(runtime, f"Flow export options offered: {json.dumps(offered)[:200]}")
            if option.get("found"):
                dispatch_mouse_click(tab, option["x"], option["y"])
                _log(
                    runtime,
                    f"Flow export quality chosen: {option.get('label')} "
                    f"(preference order {'/'.join(_DOWNLOAD_QUALITY_ORDER)})",
                )
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


def _reclassify_region_block(exc: OrdaKError) -> OrdaKError:
    """Rename any Flow failure that is really the unsupported-country page.

    The block can land at any point — while the project loads, while the composer is being
    waited for, or between two steps — and each of those paths has its own error. Rather
    than teaching every one of them about geography, the live tab is checked once here, so
    the operator is never told to look for a DOM change that did not happen.
    """
    if exc.code is ErrorCode.FLOW_REGION_BLOCKED:
        return exc
    try:
        from app.automation.existing_chrome import list_google_chrome_tabs

        for info in list_google_chrome_tabs():
            url = str(getattr(info, "url", "") or "").lower()
            if any(marker in url for marker in FLOW_REGION_BLOCK_URL_MARKERS):
                return OrdaKError(
                    code=ErrorCode.FLOW_REGION_BLOCKED,
                    message="Google Flow is not available in this country.",
                    technical_details=(
                        f"observed url: {getattr(info, 'url', '')!r}; "
                        f"original failure: {exc.code.value}: {exc.message}"
                    ),
                )
    except Exception:
        return exc
    return exc


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
        exc = _reclassify_region_block(exc)
        error = _as_automation_error(exc)
        if runtime is not None:
            runtime.save_error(error.message, error.status, exc.code.value)
            runtime.append_log(f"Flow job failed [{exc.code.value}]: {error.message}", "error")
        raise error from exc
    except GeminiAutomationError:
        raise
    except Exception as exc:  # pragma: no cover - unexpected browser faults
        replacement = _reclassify_region_block(
            OrdaKError(code=ErrorCode.PROVIDER_UI_CHANGED, message=str(exc))
        )
        if runtime is not None:
            runtime.save_error(replacement.message, "failed", replacement.code.value)
            runtime.append_log(
                f"Flow job failed [{replacement.code.value}]: {replacement.message}", "error"
            )
        raise GeminiAutomationError(
            replacement.message, error_code=replacement.code.value
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
    # Binding and opening the project are one operation: if the reference goes stale between
    # them, rebinding is the fix, not failing the job.
    tab = _bind_flow_tab(adapter, target, runtime)
    workspace_url = ""
    for attempt in range(3):
        try:
            workspace_url = _ensure_flow_project(tab, runtime, resolved)
            break
        except OrdaKError as exc:
            if exc.code is not ErrorCode.FLOW_TAB_LOST or attempt == 2:
                raise
            _log(
                runtime,
                "Flow tab reference went stale while opening the project; rebinding and retrying",
                "warning",
            )
            time.sleep(2.0)
            tab = _bind_flow_tab(adapter, target, runtime)

    if runtime is not None:
        runtime.update_status("checking_login")
    _map_login_error(job.provider, adapter.detect_login_state(tab))

    if runtime is not None:
        runtime.update_status("finding_input")
    try:
        wait_for_prompt_input(tab, provider="flow", timeout_ms=60_000)
    except (TimeoutError, RuntimeError):
        # A regional block redirects the workspace *after* the project URL has loaded, so
        # the composer is simply absent. Saying "the input box is missing" would send the
        # operator looking for a DOM change; name the real cause when it is the real cause.
        if _flow_region_blocked(tab, _current_url(tab)):
            _flow_raise(
                ErrorCode.FLOW_REGION_BLOCKED,
                "Google Flow is not available in this country.",
                f"observed url: {_current_url(tab)!r}",
            )
        raise
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
