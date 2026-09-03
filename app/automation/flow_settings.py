"""Google Flow generation settings — read, apply, verify.

Every Flow setting lives in one Radix menu behind a summary button that reads like
``"Video · 720p · 6s crop_16_9 x2"``. Inside it, each setting is a ``div[role="tablist"]``
of ``button[role="tab"]`` elements where the active option carries
``aria-selected="true"`` / ``data-state="active"``, plus a model dropdown button.

That gives a genuine read-back for master_prompt §18-21: select, re-read the control's own
state, compare requested against actual, and refuse to generate on a mismatch. Nothing here
falls back to ``document.body.innerText``, which also matches the option names listed inside
a closed menu.

Observed live vocabulary (2026-09-03, authenticated Flow):
    media type      Image | Video
    reference mode  Frames | Ingredients      (mutually exclusive)
    aspect ratio    9:16 | 16:9
    model           Omni 1.1 Flash | Veo 3.1 - Lite | Veo 3.1 - Fast | Veo 3.1 - Quality
    resolution      360p | 720p
    duration        4s | 6s | 8s | 10s
    outputs         x1 | x2 | x3 | x4
The menu also states the cost, e.g. "Generating will use 20 credits".
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.errors import ErrorCode, OrdaKError

#: Internal model id -> exact visible label in the Flow model dropdown.
FLOW_MODEL_LABELS = {
    "gemini_omni_1_1_flash": "Omni 1.1 Flash",
    "veo_3_1_lite": "Veo 3.1 - Lite",
    "veo_3_1_fast": "Veo 3.1 - Fast",
    "veo_3_1_quality": "Veo 3.1 - Quality",
}

#: Setting groups are identified by the options they contain, never by index.
GROUP_SIGNATURES: dict[str, frozenset[str]] = {
    "media_type": frozenset({"image", "video"}),
    "reference_mode": frozenset({"frames", "ingredients"}),
    "aspect_ratio": frozenset({"9:16", "16:9"}),
    "resolution": frozenset({"360p", "720p"}),
    "duration": frozenset({"4s", "6s", "8s", "10s"}),
    "outputs": frozenset({"x1", "x2", "x3", "x4"}),
}

SETTINGS_TRIGGER_PATTERN = r"(Video|Image)\s*·"


def normalize_model(value: str | None) -> str | None:
    """Map any spelling of a Flow model (internal id or UI label) to its internal id."""
    if not value:
        return None
    # Collapse every separator so "Veo 3.1 - Lite", "veo-3.1-lite" and "veo_3_1_lite" agree.
    key = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    aliases = {
        "omni_1_1_flash": "gemini_omni_1_1_flash",
        "gemini_omni_1_1_flash": "gemini_omni_1_1_flash",
        "veo_3_1_lite": "veo_3_1_lite",
        "veo_3_1_fast": "veo_3_1_fast",
        "veo_3_1_quality": "veo_3_1_quality",
    }
    return aliases.get(key)


def identify_model(label: str | None) -> str | None:
    """Reverse-map an observed dropdown label to an internal model id."""
    text = " ".join(str(label or "").lower().split())
    if not text:
        return None
    if "omni" in text:
        return "gemini_omni_1_1_flash"
    if "veo" in text:
        for suffix, model in (
            ("quality", "veo_3_1_quality"),
            ("fast", "veo_3_1_fast"),
            ("lite", "veo_3_1_lite"),
        ):
            if suffix in text:
                return model
    return None


def normalize_duration(value: int | str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().rstrip("s")
    if not text:
        return None
    try:
        return f"{int(float(text))}s"
    except ValueError:
        return None


def normalize_resolution(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().lower().replace(" ", "")
    text = text.replace("draft", "")
    if text.endswith("p"):
        return text
    if text.isdigit():
        return f"{text}p"
    return text or None


def normalize_aspect(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().lower().replace("_", ":").replace("x", ":").replace(" ", "")
    return text or None


@dataclass
class FlowCapabilities:
    """A live snapshot of what the Flow composer currently offers (§11)."""

    groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_label: str | None = None
    model: str | None = None
    available_models: list[str] = field(default_factory=list)
    credits_required: int | None = None
    project_url: str | None = None

    def active(self, group: str) -> str | None:
        return (self.groups.get(group) or {}).get("active")

    def options(self, group: str) -> list[str]:
        return list((self.groups.get(group) or {}).get("options") or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": self.groups,
            "model_label": self.model_label,
            "model": self.model,
            "available_models": self.available_models,
            "credits_required": self.credits_required,
            "project_url": self.project_url,
        }


# ---------------------------------------------------------------------------
# Browser primitives
# ---------------------------------------------------------------------------

_READ_SETTINGS_JS = r"""
(() => {
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const wrap = document.querySelector('[data-radix-popper-content-wrapper]');
  if (!wrap) return JSON.stringify({open: false});
  const groups = Array.from(wrap.querySelectorAll('[role="tablist"]')).map(list => {
    const options = Array.from(list.querySelectorAll('[role="tab"]')).map(tab => {
      const words = clean(tab.innerText).split(' ');
      return {
        label: words[words.length - 1] || '',
        raw: clean(tab.innerText),
        active: tab.getAttribute('aria-selected') === 'true'
             || tab.getAttribute('data-state') === 'active'
      };
    });
    return options;
  });
  let modelLabel = null;
  for (const button of Array.from(wrap.querySelectorAll('button'))) {
    const text = clean(button.innerText);
    if (/arrow_drop_down/.test(text)) {
      modelLabel = clean(text.replace('arrow_drop_down', ''));
      break;
    }
  }
  const credits = clean(wrap.innerText).match(/use\s+(\d+)\s+credits?/i);
  return JSON.stringify({
    open: true,
    groups,
    modelLabel,
    credits: credits ? Number(credits[1]) : null
  });
})()
"""

_MODEL_OPTIONS_JS = r"""
(() => {
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const wraps = Array.from(document.querySelectorAll('[data-radix-popper-content-wrapper]'));
  // The model list is the popper that mentions a model name but no tablist.
  for (const wrap of wraps) {
    if (wrap.querySelector('[role="tablist"]')) continue;
    const text = clean(wrap.innerText);
    if (!/omni|veo/i.test(text)) continue;
    const items = Array.from(wrap.querySelectorAll('[role="menuitem"],[role="menuitemradio"],[role="option"],button'))
      .filter(el => el.offsetWidth > 0)
      .map(el => clean(el.innerText).replace(/^volume_up\s*/i, ''))
      .filter(Boolean);
    return JSON.stringify({found: true, items});
  }
  return JSON.stringify({found: false, items: []});
})()
"""


def _evaluate(tab, script: str) -> Any:
    from app.automation.existing_chrome import execute_javascript

    raw = execute_javascript(tab, script)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


def _click_by_text(tab, selectors: str, needle: str, *, exact: bool = False) -> bool:
    """CDP-click the first visible element matching ``selectors`` whose text matches.

    A plain ``element.click()`` does not open Radix menus (they listen for pointer
    events), so the click is dispatched through CDP Input at the element's centre.
    """
    from app.automation.existing_chrome import execute_javascript, dispatch_mouse_click

    locate = """
    (() => {
      const needle = %s;
      const exact = %s;
      const clean = s => String(s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
      const nodes = Array.from(document.querySelectorAll(%s));
      const match = nodes.find(el => {
        const text = clean((el.getAttribute('aria-label') || '') + ' ' + (el.innerText || ''));
        if (!text) return false;
        const hit = exact
          ? text.split(' ').includes(needle) || text === needle
          : text.includes(needle);
        if (!hit) return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      });
      if (!match) return null;
      match.scrollIntoView({block: 'center'});
      const rect = match.getBoundingClientRect();
      return JSON.stringify({
        x: rect.x + rect.width / 2,
        y: rect.y + rect.height / 2,
        text: clean(match.innerText)
      });
    })()
    """ % (json.dumps(needle.lower()), "true" if exact else "false", json.dumps(selectors))

    raw = execute_javascript(tab, locate)
    if not raw:
        return False
    try:
        info = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    if not info:
        return False
    dispatch_mouse_click(tab, float(info["x"]), float(info["y"]))
    return True


def _press_escape(tab) -> None:
    from app.automation.existing_chrome import dispatch_key

    dispatch_key(tab, "Escape", code="Escape", key_code=27)


# ---------------------------------------------------------------------------
# Read / apply / verify
# ---------------------------------------------------------------------------


def _classify_groups(raw_groups: list[list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Name each tablist by the options it holds, so index drift cannot mislabel it."""
    classified: dict[str, dict[str, Any]] = {}
    for options in raw_groups:
        labels = {str(option.get("label") or "").strip().lower() for option in options}
        labels.discard("")
        name: str | None = None
        for candidate, signature in GROUP_SIGNATURES.items():
            if labels & signature:
                name = candidate
                break
        if name is None or name in classified:
            continue
        active = next(
            (str(option.get("label")) for option in options if option.get("active")),
            None,
        )
        classified[name] = {
            "options": [str(option.get("label")) for option in options if option.get("label")],
            "active": active,
        }
    return classified


def open_settings_menu(tab, *, attempts: int = 3) -> None:
    """Open the Flow generation-settings menu, or raise FLOW_UI_CHANGED."""
    for attempt in range(attempts):
        state = _evaluate(tab, _READ_SETTINGS_JS) or {}
        if isinstance(state, dict) and state.get("open"):
            return
        if not _click_by_text(tab, "button,[role=button]", "720p") and not _click_by_text(
            tab, "button,[role=button]", " · "
        ):
            # Fall back to matching the media-type word in the summary button.
            _click_by_text(tab, "button,[role=button]", "video ·")
        time.sleep(0.9 + 0.4 * attempt)
    state = _evaluate(tab, _READ_SETTINGS_JS) or {}
    if isinstance(state, dict) and state.get("open"):
        return
    raise OrdaKError(
        code=ErrorCode.FLOW_UI_CHANGED,
        message="Could not open the Flow generation-settings menu.",
        technical_details="No [data-radix-popper-content-wrapper] with a tablist appeared.",
    )


def close_settings_menu(tab) -> None:
    try:
        _press_escape(tab)
        time.sleep(0.3)
    except Exception:
        pass


def read_capabilities(tab, *, project_url: str | None = None) -> FlowCapabilities:
    """Read the live capability/state snapshot from the open settings menu (§11)."""
    open_settings_menu(tab)
    state = _evaluate(tab, _READ_SETTINGS_JS)
    if not isinstance(state, dict) or not state.get("open"):
        raise OrdaKError(
            code=ErrorCode.FLOW_UI_CHANGED,
            message="Flow settings menu did not report its state.",
        )
    groups = _classify_groups(state.get("groups") or [])
    model_label = state.get("modelLabel")
    capabilities = FlowCapabilities(
        groups=groups,
        model_label=model_label,
        model=identify_model(model_label),
        credits_required=state.get("credits"),
        project_url=project_url,
    )
    # Which groups exist depends on the selected model: Veo 3.1 - Lite, for example, drops
    # the resolution and duration controls entirely. That is a capability fact to report,
    # not a UI regression, so only the always-present controls are treated as required.
    if "media_type" not in groups:
        raise OrdaKError(
            code=ErrorCode.FLOW_UI_CHANGED,
            message="Flow settings menu has no media-type control.",
            technical_details=f"observed groups: {sorted(groups)}",
        )
    return capabilities


def read_model_options(tab) -> list[str]:
    """Open the model dropdown and list the model labels it offers."""
    open_settings_menu(tab)
    if not _click_by_text(tab, "button", "arrow_drop_down"):
        raise OrdaKError(
            code=ErrorCode.FLOW_UI_CHANGED,
            message="Could not open the Flow model dropdown.",
        )
    time.sleep(1.2)
    payload = _evaluate(tab, _MODEL_OPTIONS_JS)
    labels: list[str] = []
    if isinstance(payload, dict) and payload.get("found"):
        seen: set[str] = set()
        for item in payload.get("items") or []:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                labels.append(text)
    _press_escape(tab)
    time.sleep(0.4)
    return labels


def _select_tab_option(tab, group: str, wanted: str, *, runtime=None) -> str:
    """Click one option inside a tablist and verify the control reports it active."""
    capabilities = read_capabilities(tab)
    if group not in capabilities.groups:
        raise OrdaKError(
            code=ErrorCode.MODEL_FEATURE_INCOMPATIBLE,
            message=(
                f"The selected Flow model ({capabilities.model_label or 'unknown'}) does not "
                f"expose a {group} control, so {wanted!r} cannot be requested."
            ),
            technical_details=f"available controls: {sorted(capabilities.groups)}",
        )
    current = capabilities.active(group)
    available = capabilities.options(group)
    if current and current.lower() == wanted.lower():
        return current
    if not any(option.lower() == wanted.lower() for option in available):
        raise OrdaKError(
            code=ErrorCode.MODEL_FEATURE_INCOMPATIBLE,
            message=(
                f"Flow does not offer {group}={wanted!r} for the selected model."
            ),
            technical_details=f"available {group}: {available}",
        )
    if not _click_by_text(tab, '[role="tab"]', wanted, exact=True):
        raise OrdaKError(
            code=ErrorCode.FLOW_UI_CHANGED,
            message=f"Could not click the Flow {group} option {wanted!r}.",
        )
    for attempt in range(4):
        time.sleep(0.5 + 0.3 * attempt)
        verified = read_capabilities(tab).active(group)
        if verified and verified.lower() == wanted.lower():
            if runtime is not None:
                runtime.append_log(f"Flow {group} verified: {verified}")
            return verified
    raise OrdaKError(
        code=ErrorCode.MODEL_SELECTION_FAILED,
        message=f"Flow {group} did not change to {wanted!r} after selection.",
        technical_details=f"still reports {read_capabilities(tab).active(group)!r}",
    )


def select_model(tab, model: str, *, runtime=None) -> str:
    """Select and positively verify the requested Flow video model (§18)."""
    normalized = normalize_model(model)
    if normalized is None:
        raise OrdaKError(
            code=ErrorCode.MODEL_NOT_AVAILABLE,
            message=f"Unknown Flow video model {model!r}.",
            technical_details=f"allowed: {sorted(FLOW_MODEL_LABELS)}",
        )
    label = FLOW_MODEL_LABELS[normalized]

    capabilities = read_capabilities(tab)
    if capabilities.model == normalized:
        if runtime is not None:
            runtime.append_log(f"Flow model already selected: {capabilities.model_label}")
        return capabilities.model_label or label

    options = read_model_options(tab)
    if not any(identify_model(option) == normalized for option in options):
        raise OrdaKError(
            code=ErrorCode.MODEL_NOT_AVAILABLE,
            message=f"Flow does not currently offer {label!r}.",
            technical_details=f"offered: {options}",
        )

    open_settings_menu(tab)
    if not _click_by_text(tab, "button", "arrow_drop_down"):
        raise OrdaKError(
            code=ErrorCode.FLOW_UI_CHANGED,
            message="Could not reopen the Flow model dropdown to select a model.",
        )
    time.sleep(1.0)
    if not _click_by_text(
        tab,
        '[role="menuitem"],[role="menuitemradio"],[role="option"],button',
        label.lower(),
    ):
        raise OrdaKError(
            code=ErrorCode.MODEL_SELECTION_FAILED,
            message=f"Could not click the Flow model option {label!r}.",
            technical_details=f"offered: {options}",
        )
    time.sleep(1.4)

    for attempt in range(4):
        verified = read_capabilities(tab)
        if verified.model == normalized:
            if runtime is not None:
                runtime.append_log(f"Flow model verified: {verified.model_label}")
            return verified.model_label or label
        time.sleep(0.8)
    raise OrdaKError(
        code=ErrorCode.MODEL_SELECTION_FAILED,
        message=f"Selected {label!r} but Flow still reports a different model.",
        technical_details=f"observed: {read_capabilities(tab).model_label!r}",
    )


@dataclass
class AppliedSettings:
    """What Flow confirmed after every control was selected and re-read."""

    model: str | None = None
    model_label: str | None = None
    media_type: str | None = None
    reference_mode: str | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    duration: str | None = None
    outputs: str | None = None
    credits_required: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "model_label": self.model_label,
            "media_type": self.media_type,
            "reference_mode": self.reference_mode,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "duration": self.duration,
            "outputs": self.outputs,
            "credits_required": self.credits_required,
        }


def apply_settings(
    tab,
    *,
    model: str,
    aspect_ratio: str,
    duration_seconds: int,
    resolution: str,
    reference_mode: str,
    outputs: str = "x1",
    runtime=None,
) -> AppliedSettings:
    """Select every generation setting and verify each one from the UI (§18-21).

    Raises rather than generating whenever a control cannot be confirmed. ``outputs``
    defaults to ``x1`` because every extra output multiplies the credit cost.
    """
    wanted_aspect = normalize_aspect(aspect_ratio)
    wanted_resolution = normalize_resolution(resolution)
    wanted_duration = normalize_duration(duration_seconds)
    if not (wanted_aspect and wanted_resolution and wanted_duration):
        raise OrdaKError(
            code=ErrorCode.MODEL_FEATURE_INCOMPATIBLE,
            message=(
                "Flow generation requires aspect_ratio, resolution and duration_seconds; "
                f"got {aspect_ratio!r}, {resolution!r}, {duration_seconds!r}."
            ),
        )

    open_settings_menu(tab)
    media_type = _select_tab_option(tab, "media_type", "Video", runtime=runtime)
    model_label = select_model(tab, model, runtime=runtime)
    mode = _select_tab_option(tab, "reference_mode", reference_mode, runtime=runtime)
    aspect = _select_tab_option(tab, "aspect_ratio", wanted_aspect, runtime=runtime)
    res = _select_tab_option(tab, "resolution", wanted_resolution, runtime=runtime)
    dur = _select_tab_option(tab, "duration", wanted_duration, runtime=runtime)
    out = _select_tab_option(tab, "outputs", outputs, runtime=runtime)

    final = read_capabilities(tab)
    applied = AppliedSettings(
        model=final.model,
        model_label=final.model_label or model_label,
        media_type=media_type,
        reference_mode=mode,
        aspect_ratio=aspect,
        resolution=res,
        duration=dur,
        outputs=out,
        credits_required=final.credits_required,
    )

    # Final cross-check against the request: nothing silently substituted.
    mismatches: list[str] = []
    if normalize_model(model) != applied.model:
        mismatches.append(f"model requested={normalize_model(model)} actual={applied.model}")
    if wanted_aspect != normalize_aspect(applied.aspect_ratio):
        mismatches.append(f"aspect requested={wanted_aspect} actual={applied.aspect_ratio}")
    if wanted_resolution != normalize_resolution(applied.resolution):
        mismatches.append(f"resolution requested={wanted_resolution} actual={applied.resolution}")
    if wanted_duration != normalize_duration(applied.duration):
        mismatches.append(f"duration requested={wanted_duration} actual={applied.duration}")
    if mismatches:
        raise OrdaKError(
            code=ErrorCode.MODEL_SELECTION_FAILED,
            message="Flow settings verification failed: " + "; ".join(mismatches),
        )

    close_settings_menu(tab)
    if runtime is not None:
        runtime.append_log(
            "Flow settings confirmed: "
            f"{applied.model_label} · {applied.resolution} · {applied.duration} · "
            f"{applied.aspect_ratio} · {applied.reference_mode} · {applied.outputs} "
            f"(credits: {applied.credits_required})"
        )
    return applied


__all__ = [
    "AppliedSettings",
    "FLOW_MODEL_LABELS",
    "FlowCapabilities",
    "apply_settings",
    "close_settings_menu",
    "identify_model",
    "normalize_aspect",
    "normalize_duration",
    "normalize_model",
    "normalize_resolution",
    "open_settings_menu",
    "read_capabilities",
    "read_model_options",
    "select_model",
]
