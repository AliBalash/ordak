"""Nano Banana Pro regeneration path for Gemini image jobs (§6, §43, §98).

Gemini's current product behaviour is to answer an image prompt with a Nano Banana 2
result and *then* offer a "Redo with Pro" style affordance.  When the job asks for
``nano_banana_pro`` the initial result is therefore **not** acceptable: this module
locates the Pro affordance, invokes it, waits for a genuinely different result, and
proves the difference before the caller is allowed to download anything.

Nothing here guesses.  Every decision is made from what the page reports:

* asset identity  — the ``src`` of the rendered result (Gemini mints a new asset id
  per generation, so a set difference answers "which result is new")
* bytes           — SHA-256 computed *inside the page* over the fetched asset, so a
  recycled asset served under a new URL cannot masquerade as a new result
* dimensions      — ``naturalWidth``/``naturalHeight`` of the rendered element

If the affordance does not exist, or the new result cannot be positively
distinguished, the caller raises ``MODEL_NOT_AVAILABLE``: Nano Banana 2 is never
silently accepted as Pro.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

#: Text that identifies a Pro regeneration affordance.  A bare "nano banana pro"
#: is deliberately *not* enough — that is also the label of the model picker — so a
#: match needs an action verb as well.
PRO_ACTION_VERBS = (
    "redo",
    "regenerate",
    "re-generate",
    "recreate",
    "upgrade",
    "refine",
    "enhance",
    "improve",
    "retry",
    "try again",
    "switch to",
    "generate with",
    "remake",
)

#: The quality token the affordance must also mention.
PRO_QUALITY_TOKENS = ("pro", "nano banana pro", "highest quality", "best quality")

#: Controls that must never be mistaken for the Pro affordance.
PRO_CONTROL_EXCLUDE_SELECTORS = (
    '[data-test-id*="model"]',
    '[data-testid*="model"]',
    '[class*="model-selector"]',
    '[class*="modelSelector"]',
    "[aria-haspopup]",
    '[role="combobox"]',
)


class ProPathUnavailable(RuntimeError):
    """The UI does not currently offer a Pro regeneration affordance."""


class ProResultNotDistinct(RuntimeError):
    """A Pro regeneration was invoked but produced no positively different result."""


@dataclass(frozen=True, slots=True)
class ResultIdentity:
    """One rendered image result, identified by everything the page can prove."""

    key: str
    src: str
    width: int
    height: int
    sha256: str | None = None
    alt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
            "alt": self.alt,
        }


@dataclass(frozen=True, slots=True)
class ProControl:
    """A located Pro regeneration affordance."""

    label: str
    selector_source: str
    matched_verb: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source": self.selector_source,
            "matched_verb": self.matched_verb,
        }


@dataclass(slots=True)
class ProOutcome:
    """The evidence trail for one Pro regeneration attempt."""

    used: bool
    control: ProControl | None = None
    baseline: list[ResultIdentity] = field(default_factory=list)
    result: ResultIdentity | None = None
    distinction: str = "none"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pro_regeneration_used": self.used,
            "pro_control": self.control.to_dict() if self.control else None,
            "pro_distinction": self.distinction,
            "baseline_results": [item.to_dict() for item in self.baseline],
            "pro_result": self.result.to_dict() if self.result else None,
            "notes": list(self.notes),
        }


def _eval(tab, script: str) -> dict[str, Any]:
    """Run a JSON-returning script in the tab; ``{}`` when the page refuses."""
    from app.automation.existing_chrome import execute_javascript

    try:
        raw = execute_javascript(tab, script)
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


_IDENTITY_SCRIPT = """
(async () => {
  const withHash = %(with_hash)s;
  const maxImages = %(max_images)d;
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0
      && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const isUserUpload = (img) => {
    const labels = [
      img.closest('button')?.getAttribute('aria-label') || '',
      img.closest('[aria-label]')?.getAttribute('aria-label') || '',
      img.getAttribute('alt') || '',
    ].join(' ');
    return /user uploaded image|uploaded by you|your upload/i.test(labels)
      || !!img.closest('[data-message-author-role="user"]');
  };
  const imgs = Array.from(document.querySelectorAll('img'))
    .filter(isVisible)
    .filter((img) => (img.naturalWidth || img.width || 0) >= 160
                  && (img.naturalHeight || img.height || 0) >= 160)
    .filter((img) => !isUserUpload(img))
    .slice(-maxImages);
  const digest = async (src) => {
    if (!withHash) return null;
    try {
      const response = await fetch(src, {cache: 'force-cache'});
      if (!response.ok) return null;
      const buffer = await response.arrayBuffer();
      const hashed = await crypto.subtle.digest('SHA-256', buffer);
      return Array.from(new Uint8Array(hashed))
        .map((b) => b.toString(16).padStart(2, '0')).join('');
    } catch (err) {
      return null;
    }
  };
  const results = [];
  for (const img of imgs) {
    const src = img.currentSrc || img.src || '';
    if (!src) continue;
    results.push({
      src,
      width: img.naturalWidth || img.width || 0,
      height: img.naturalHeight || img.height || 0,
      alt: (img.getAttribute('alt') || '').slice(0, 120),
      sha256: await digest(src),
    });
  }
  return JSON.stringify({results});
})()
"""


def _identity_key(src: str) -> str:
    """Collapse a result URL to the part that identifies the asset.

    Gemini serves the same asset through URLs that differ only in transient query
    parameters (size hints, auth tokens), so those must not read as "a new result".
    """
    text = str(src or "")
    if not text:
        return ""
    if text.startswith("data:"):
        # Inline payloads carry their own identity; hash-length prefix is plenty.
        return "data:" + text[-96:]
    head = text.split("?", 1)[0].split("#", 1)[0]
    return head


def read_result_identities(
    tab,
    *,
    with_hash: bool = True,
    max_images: int = 6,
) -> list[ResultIdentity]:
    """Identify the image results the page is currently rendering."""
    payload = _eval(
        tab,
        _IDENTITY_SCRIPT % {"with_hash": "true" if with_hash else "false", "max_images": int(max_images)},
    )
    identities: list[ResultIdentity] = []
    for item in payload.get("results") or []:
        src = str(item.get("src") or "")
        key = _identity_key(src)
        if not key:
            continue
        identities.append(
            ResultIdentity(
                key=key,
                src=src,
                width=int(item.get("width") or 0),
                height=int(item.get("height") or 0),
                sha256=(str(item["sha256"]) if item.get("sha256") else None),
                alt=str(item.get("alt") or ""),
            )
        )
    return identities


_FIND_CONTROL_SCRIPT = """
(() => {
  const verbs = %(verbs)s;
  const quality = %(quality)s;
  const exclude = %(exclude)s;
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0
      && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const isExcluded = (el) => exclude.some((selector) => {
    try { return el.matches(selector) || !!el.closest(selector); } catch (err) { return false; }
  });
  const nodes = Array.from(document.querySelectorAll(
    'button, a, [role="button"], [role="menuitem"], [role="menuitemradio"], [role="option"]'
  )).filter(isVisible).filter((el) => !isExcluded(el));
  for (const el of nodes) {
    const text = [
      el.getAttribute('aria-label') || '',
      el.getAttribute('title') || '',
      el.innerText || '',
    ].join(' ').toLowerCase().replace(/\\s+/g, ' ').trim();
    if (!text) continue;
    if (!quality.some((token) => text.includes(token))) continue;
    const verb = verbs.find((needle) => text.includes(needle));
    if (!verb) continue;
    if (el.getAttribute('aria-disabled') === 'true' || el.disabled) continue;
    return JSON.stringify({
      found: true,
      label: text.slice(0, 160),
      verb,
      source: el.tagName.toLowerCase() + (el.getAttribute('role') ? '[role=' + el.getAttribute('role') + ']' : ''),
    });
  }
  return JSON.stringify({found: false});
})()
"""


def find_pro_control(tab) -> ProControl | None:
    """Locate the Pro regeneration affordance, or ``None`` when the UI has none."""
    payload = _eval(
        tab,
        _FIND_CONTROL_SCRIPT
        % {
            "verbs": json.dumps(list(PRO_ACTION_VERBS)),
            "quality": json.dumps(list(PRO_QUALITY_TOKENS)),
            "exclude": json.dumps(list(PRO_CONTROL_EXCLUDE_SELECTORS)),
        },
    )
    if not payload.get("found"):
        return None
    return ProControl(
        label=str(payload.get("label") or ""),
        selector_source=str(payload.get("source") or ""),
        matched_verb=str(payload.get("verb") or ""),
    )


_CLICK_CONTROL_SCRIPT = """
(() => {
  const label = %(label)s;
  const exclude = %(exclude)s;
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0
      && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const isExcluded = (el) => exclude.some((selector) => {
    try { return el.matches(selector) || !!el.closest(selector); } catch (err) { return false; }
  });
  const nodes = Array.from(document.querySelectorAll(
    'button, a, [role="button"], [role="menuitem"], [role="menuitemradio"], [role="option"]'
  )).filter(isVisible).filter((el) => !isExcluded(el));
  const match = nodes.find((el) => [
    el.getAttribute('aria-label') || '',
    el.getAttribute('title') || '',
    el.innerText || '',
  ].join(' ').toLowerCase().replace(/\\s+/g, ' ').trim().includes(label));
  if (!match) return JSON.stringify({clicked: false});
  match.scrollIntoView({block: 'center'});
  match.click();
  return JSON.stringify({clicked: true});
})()
"""


def click_pro_control(tab, control: ProControl) -> bool:
    """Click a previously located Pro affordance."""
    needle = control.label.strip().lower()[:80]
    if not needle:
        return False
    payload = _eval(
        tab,
        _CLICK_CONTROL_SCRIPT
        % {
            "label": json.dumps(needle),
            "exclude": json.dumps(list(PRO_CONTROL_EXCLUDE_SELECTORS)),
        },
    )
    return bool(payload.get("clicked"))


def classify_distinction(
    baseline: Iterable[ResultIdentity],
    candidate: ResultIdentity,
) -> str:
    """Return why ``candidate`` is a genuinely new result, or ``""`` when it is not.

    An asset id the page has already shown, or bytes it has already shown under a
    different URL, is the *same* result — accepting either would let Nano Banana 2
    be recorded as Pro.
    """
    baseline = list(baseline)
    baseline_keys = {item.key for item in baseline}
    baseline_hashes = {item.sha256 for item in baseline if item.sha256}
    if candidate.key in baseline_keys:
        return ""
    if candidate.sha256 and candidate.sha256 in baseline_hashes:
        return ""
    reasons = ["new_asset_id"]
    if candidate.sha256 and baseline_hashes:
        reasons.append("distinct_sha256")
    if any(
        (candidate.width, candidate.height) != (item.width, item.height)
        for item in baseline
    ):
        reasons.append("distinct_dimensions")
    return "+".join(reasons)


def wait_for_pro_result(
    tab,
    baseline: Iterable[ResultIdentity],
    *,
    timeout_ms: int,
    poll_seconds: float = 2.0,
    state_reader: Callable[[Any], dict[str, Any]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[ResultIdentity, str]:
    """Wait until the page renders a result that is positively different.

    Raises ``ProResultNotDistinct`` when the timeout expires with nothing new.
    """
    baseline = list(baseline)
    deadline = monotonic() + max(timeout_ms, 0) / 1000
    last_seen: ResultIdentity | None = None
    while monotonic() < deadline:
        if state_reader is not None:
            state = state_reader(tab) or {}
            if state.get("loading") or state.get("busy"):
                sleep(poll_seconds)
                continue
        for candidate in reversed(read_result_identities(tab)):
            reason = classify_distinction(baseline, candidate)
            if reason:
                return candidate, reason
            last_seen = candidate
        sleep(poll_seconds)
    detail = f"last observed result: {last_seen.key if last_seen else 'none'}"
    raise ProResultNotDistinct(
        "Gemini did not render a result distinguishable from the initial one. " + detail
    )


def run_pro_regeneration(
    tab,
    *,
    timeout_ms: int,
    log: Callable[[str], None] | None = None,
    state_reader: Callable[[Any], dict[str, Any]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    settle_seconds: float = 1.5,
) -> ProOutcome:
    """Take the initial result to a verified Pro result.

    Raises ``ProPathUnavailable`` when no Pro affordance exists and
    ``ProResultNotDistinct`` when one exists but yields nothing new.  Either way the
    caller must refuse the job rather than accept Nano Banana 2 (§6).
    """

    def _log(message: str) -> None:
        if log is not None:
            log(message)

    baseline = read_result_identities(tab)
    if not baseline:
        raise ProResultNotDistinct(
            "No initial Gemini image result was visible, so a Pro result cannot be distinguished."
        )
    _log(
        "Initial Gemini result(s): "
        + ", ".join(f"{item.key.rsplit('/', 1)[-1]} {item.width}x{item.height}" for item in baseline)
    )

    control = find_pro_control(tab)
    if control is None:
        raise ProPathUnavailable(
            "Gemini did not offer a Pro regeneration control for this result."
        )
    _log(f"Pro regeneration control found: {control.label!r} ({control.selector_source})")

    if not click_pro_control(tab, control):
        raise ProPathUnavailable(
            f"The Pro regeneration control {control.label!r} could not be invoked."
        )
    sleep(settle_seconds)

    result, distinction = wait_for_pro_result(
        tab,
        baseline,
        timeout_ms=timeout_ms,
        state_reader=state_reader,
        sleep=sleep,
        monotonic=monotonic,
    )
    _log(
        f"Pro result accepted ({distinction}): {result.width}x{result.height} "
        f"sha256={(result.sha256 or 'unavailable')[:12]}"
    )
    notes = [f"pro_control={control.label}", f"pro_distinction={distinction}"]
    if result.sha256 is None:
        notes.append("pro_sha256=unavailable_in_page")
    return ProOutcome(
        used=True,
        control=control,
        baseline=baseline,
        result=result,
        distinction=distinction,
        notes=notes,
    )
