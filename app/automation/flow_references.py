"""Google Flow reference attachment — Ingredients chips and Start/End frames.

Live DOM facts, verified 2026-09-04 against an authenticated Flow project view:

* **Frames mode** puts a slot row in the composer. The row is the parent of the
  ``Swap first and last frames`` button and its children are
  ``[start slot, swap button, end slot]``. An empty slot reads ``Start`` / ``End`` and
  holds no ``<img>``; a filled slot holds exactly one ``<img>`` whose ``src`` is a
  ``media.getMediaUrlRedirect`` URL, and its label becomes ``cancel`` (clicking it clears
  the slot).
* **Ingredients mode** replaces that row with a single ``add_2`` button; attached
  ingredients render as ``<img>`` chips inside the composer box.
* Either target opens the same ``[role="dialog"]`` asset picker. A real upload is
  ``DOM.setFileInputFiles`` against the page's single hidden
  ``input[type="file"][accept*="image"]``; the new asset then appears as the topmost row
  in the picker list, and clicking that row attaches it. Some picker states instead
  require the explicit ``Add to Prompt`` button, so both are handled.

Every attachment is verified from the UI afterwards. A reference that cannot be confirmed
raises ``FLOW_FRAME_UPLOAD_FAILED`` / ``FLOW_UPLOAD_FAILED`` rather than letting the job
generate with a missing or stale reference (master_prompt §14-16, §19).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.automation.existing_chrome import (
    ChromeTabRef,
    dispatch_key,
    dispatch_mouse_click,
    execute_javascript,
    set_file_input_files,
)
from app.errors import ErrorCode, OrdaKError

#: The page keeps exactly one hidden uploader shared by the asset picker.
FILE_INPUT_SELECTOR = 'input[type="file"][accept*="image"]'

#: Frame slots in order; index 0 is the first frame, index 1 the last.
FRAME_SLOTS = ("first_frame", "last_frame")

_SLOT_INDEX = {"first_frame": 0, "last_frame": 1}


_FRAME_ROW_JS = r"""
(() => {
  const swap = Array.from(document.querySelectorAll('button')).find(
    (b) => (b.innerText || '').includes('Swap first and last')
  );
  if (!swap || !swap.parentElement) return JSON.stringify({found: false});
  const row = swap.parentElement;
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x + r.width / 2),
      y: Math.round(r.y + r.height / 2),
      w: Math.round(r.width),
      h: Math.round(r.height),
    };
  };
  const slots = Array.from(row.children)
    .filter((child) => child !== swap)
    .map((el) => {
      const image = el.querySelector('img');
      return Object.assign(
        {
          label: (el.innerText || '').trim(),
          filled: !!image,
          src: image ? image.src || null : null,
        },
        box(el)
      );
    });
  return JSON.stringify({found: true, slots: slots});
})()
"""


_COMPOSER_JS = r"""
(() => {
  const editor = document.querySelector('div[contenteditable="true"][role="textbox"]');
  const add = Array.from(document.querySelectorAll('button')).find(
    (b) => (b.innerText || '').includes('add_2')
  );
  if (!editor) return JSON.stringify({found: false, reason: 'no-editor'});
  let box = editor;
  while (box && add && !box.contains(add)) box = box.parentElement;
  if (!box) box = editor.parentElement;
  const chips = Array.from(box.querySelectorAll('img')).map((im) => {
    const r = im.getBoundingClientRect();
    return {
      src: im.src || null,
      alt: im.alt || null,
      x: Math.round(r.x + r.width / 2),
      y: Math.round(r.y + r.height / 2),
      w: Math.round(r.width),
      h: Math.round(r.height),
    };
  });
  const addBox = add ? add.getBoundingClientRect() : null;
  return JSON.stringify({
    found: true,
    chips: chips,
    add: addBox
      ? {x: Math.round(addBox.x + addBox.width / 2), y: Math.round(addBox.y + addBox.height / 2)}
      : null,
  });
})()
"""


_PICKER_ROW_JS = r"""
(() => {
  const dialog = document.querySelector('[role="dialog"]');
  if (!dialog) return JSON.stringify({open: false});
  const needle = %s;
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
  };
  const labels = Array.from(dialog.querySelectorAll('*')).filter(
    (el) => el.children.length === 0 && (el.innerText || '').trim().toLowerCase() === needle
  );
  labels.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  let row = null;
  if (labels.length) {
    row = labels[0];
    for (let depth = 0; depth < 6 && row.parentElement; depth += 1) {
      row = row.parentElement;
      if (row.querySelector('img')) break;
    }
  }
  // A selected asset row carries an explicit selection attribute, which is the only
  // trustworthy answer to "which asset would Add to Prompt attach right now?".
  const selected = Array.from(
    dialog.querySelectorAll('[aria-selected="true"],[data-selected="true"],[data-state="selected"]')
  )
    .map((el) => (el.innerText || '').trim().toLowerCase())
    .filter((text) => text.includes(needle));
  const confirm = Array.from(dialog.querySelectorAll('button')).find(
    (b) => (b.innerText || '').trim().toLowerCase() === 'add to prompt'
  );
  return JSON.stringify({
    open: true,
    matches: labels.length,
    row: row ? box(row) : null,
    selectedMatches: selected.length,
    confirm: confirm ? box(confirm) : null,
    confirmEnabled: !!confirm && !confirm.disabled,
  });
})()
"""


def _evaluate(tab: ChromeTabRef, script: str) -> Any:
    raw = execute_javascript(tab, script)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _log(runtime: Any, message: str, level: str = "info") -> None:
    if runtime is not None:
        runtime.append_log(message, level)


@dataclass
class AttachedReferences:
    """What Flow actually shows attached, for the receipt and the job log (§8, §23)."""

    mode: str = "unknown"
    ingredients: list[str] = field(default_factory=list)
    frames: dict[str, str | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ingredients": list(self.ingredients),
            "frames": dict(self.frames),
        }


def read_frame_slots(tab: ChromeTabRef) -> list[dict[str, Any]]:
    """Return the two frame slots in order, or raise if Frames mode is not active."""
    state = _evaluate(tab, _FRAME_ROW_JS) or {}
    if not state.get("found"):
        raise OrdaKError(
            code=ErrorCode.FLOW_UI_CHANGED,
            message="Flow is not showing the Start/End frame slots.",
            technical_details=(
                "The 'Swap first and last frames' control was not found; reference mode is "
                "probably still Ingredients."
            ),
        )
    slots = list(state.get("slots") or [])
    if len(slots) != 2:
        raise OrdaKError(
            code=ErrorCode.FLOW_UI_CHANGED,
            message=f"Flow frame row has {len(slots)} slots; expected exactly 2.",
        )
    return slots


def read_composer(tab: ChromeTabRef) -> dict[str, Any]:
    """Return the composer's ingredient chips and the position of its add control."""
    state = _evaluate(tab, _COMPOSER_JS) or {}
    if not state.get("found"):
        raise OrdaKError(
            code=ErrorCode.FLOW_UI_CHANGED,
            message="Flow composer (prompt editor) was not found.",
            technical_details=str(state.get("reason") or "no state"),
        )
    return state


def close_picker(tab: ChromeTabRef) -> None:
    try:
        dispatch_key(tab, "Escape", code="Escape", key_code=27)
        time.sleep(0.4)
    except Exception:
        pass


def _wait_for_picker(tab: ChromeTabRef, *, timeout_s: float = 12.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (execute_javascript(tab, "!!document.querySelector('[role=\"dialog\"]')") or "").strip() == "true":
            return
        time.sleep(0.5)
    raise OrdaKError(
        code=ErrorCode.FLOW_UI_CHANGED,
        message="Flow asset picker did not open.",
        technical_details="No [role=dialog] appeared after clicking the reference control.",
    )


def _select_uploaded_asset(
    tab: ChromeTabRef,
    filename: str,
    *,
    timeout_s: float = 120.0,
) -> None:
    """Wait for the freshly uploaded asset to list, confirm it is the selected one, attach it.

    Flow auto-selects a new upload and enables ``Add to Prompt``. Clicking the row in that
    state would *deselect* it, so the row is only clicked when nothing matching is selected.
    """
    script = _PICKER_ROW_JS % json.dumps(filename.lower())
    deadline = time.monotonic() + timeout_s
    row_clicks = 0
    while time.monotonic() < deadline:
        state = _evaluate(tab, script) or {}
        if not state.get("open"):
            raise OrdaKError(
                code=ErrorCode.FLOW_UPLOAD_FAILED,
                message=f"Flow's asset picker closed before {filename!r} could be attached.",
            )
        if state.get("selectedMatches") and state.get("confirmEnabled") and state.get("confirm"):
            dispatch_mouse_click(tab, state["confirm"]["x"], state["confirm"]["y"])
            for _ in range(10):
                time.sleep(0.5)
                if not (_evaluate(tab, script) or {}).get("open"):
                    return
            return
        if state.get("row") and row_clicks < 2 and not state.get("selectedMatches"):
            row_clicks += 1
            dispatch_mouse_click(tab, state["row"]["x"], state["row"]["y"])
            time.sleep(1.5)
            continue
        time.sleep(1.5)
    raise OrdaKError(
        code=ErrorCode.FLOW_UPLOAD_FAILED,
        message=f"Flow never offered the uploaded asset {filename!r} for attachment.",
    )


def attach_frame(
    tab: ChromeTabRef,
    role: str,
    path: Path,
    *,
    runtime: Any = None,
) -> str:
    """Upload ``path`` into the Start (``first_frame``) or End (``last_frame``) slot.

    Returns the media URL Flow shows in the slot, which is the positive evidence that the
    frame is really attached — a filled slot is the only accepted proof (§14, §19).
    """
    index = _SLOT_INDEX.get(role)
    if index is None:
        raise OrdaKError(
            code=ErrorCode.FLOW_FRAME_UPLOAD_FAILED,
            message=f"{role!r} is not a Flow frame slot; expected one of {list(FRAME_SLOTS)}.",
        )
    source = Path(path)
    if not source.is_file():
        raise OrdaKError(
            code=ErrorCode.FLOW_FRAME_UPLOAD_FAILED,
            message=f"Frame source for {role} is missing on disk: {source}",
        )

    slots = read_frame_slots(tab)
    target = slots[index]
    if target.get("filled"):
        clear_frame(tab, role, runtime=runtime)
        target = read_frame_slots(tab)[index]

    dispatch_mouse_click(tab, target["x"], target["y"])
    _wait_for_picker(tab)
    set_file_input_files(tab, FILE_INPUT_SELECTOR, [source])
    _log(runtime, f"Flow {role}: uploaded {source.name}, waiting for the picker to list it")
    _select_uploaded_asset(tab, source.name)

    for attempt in range(8):
        time.sleep(0.6 + 0.4 * attempt)
        refreshed = read_frame_slots(tab)[index]
        if refreshed.get("filled") and refreshed.get("src"):
            _log(runtime, f"Flow {role} verified attached: {source.name}")
            return str(refreshed["src"])
    close_picker(tab)
    raise OrdaKError(
        code=ErrorCode.FLOW_FRAME_UPLOAD_FAILED,
        message=f"Flow did not show {source.name} in its {role} slot after upload.",
    )


def clear_frame(tab: ChromeTabRef, role: str, *, runtime: Any = None) -> None:
    """Empty one frame slot. A filled slot's own control is its remove button."""
    index = _SLOT_INDEX[role]
    for _ in range(3):
        slots = read_frame_slots(tab)
        if not slots[index].get("filled"):
            return
        dispatch_mouse_click(tab, slots[index]["x"], slots[index]["y"])
        time.sleep(1.0)
        if _evaluate(tab, "JSON.stringify(!!document.querySelector('[role=\"dialog\"]'))"):
            close_picker(tab)
    if read_frame_slots(tab)[index].get("filled"):
        raise OrdaKError(
            code=ErrorCode.FLOW_FRAME_UPLOAD_FAILED,
            message=f"Could not clear the stale reference out of Flow's {role} slot.",
        )
    _log(runtime, f"Flow {role} slot cleared")


def attach_ingredient(
    tab: ChromeTabRef,
    role: str,
    path: Path,
    *,
    runtime: Any = None,
) -> str:
    """Upload ``path`` as an Ingredients-mode reference chip and verify it attached."""
    source = Path(path)
    if not source.is_file():
        raise OrdaKError(
            code=ErrorCode.FLOW_UPLOAD_FAILED,
            message=f"Reference source for {role} is missing on disk: {source}",
        )
    before = read_composer(tab)
    if not before.get("add"):
        raise OrdaKError(
            code=ErrorCode.FLOW_UI_CHANGED,
            message="Flow composer has no add-reference control in Ingredients mode.",
        )
    baseline = {chip.get("src") for chip in before.get("chips") or []}
    dispatch_mouse_click(tab, before["add"]["x"], before["add"]["y"])
    _wait_for_picker(tab)
    set_file_input_files(tab, FILE_INPUT_SELECTOR, [source])
    _log(runtime, f"Flow {role}: uploaded {source.name}, waiting for the picker to list it")
    _select_uploaded_asset(tab, source.name)

    for attempt in range(8):
        time.sleep(0.6 + 0.4 * attempt)
        after = read_composer(tab)
        fresh = [
            chip.get("src")
            for chip in after.get("chips") or []
            if chip.get("src") and chip.get("src") not in baseline
        ]
        if fresh:
            _log(runtime, f"Flow {role} verified attached: {source.name}")
            return str(fresh[0])
    close_picker(tab)
    raise OrdaKError(
        code=ErrorCode.FLOW_UPLOAD_FAILED,
        message=f"Flow did not show {source.name} as an attached reference after upload.",
    )


def clear_references(tab: ChromeTabRef, mode: str, *, runtime: Any = None) -> None:
    """Start from an empty reference state so no stale asset can leak into a paid run.

    A leftover ingredient or frame from a previous job would silently change the output
    while every setting still verified correctly, so this runs before every attachment.
    """
    if mode.strip().lower() == "frames":
        for role in FRAME_SLOTS:
            clear_frame(tab, role, runtime=runtime)
        return
    for _ in range(6):
        state = read_composer(tab)
        chips = [chip for chip in state.get("chips") or [] if (chip.get("w") or 0) >= 12]
        if not chips:
            return
        chip = chips[0]
        # The chip's remove affordance sits at its top-right corner.
        dispatch_mouse_click(
            tab,
            chip["x"] + max(6, (chip.get("w") or 24) // 2 - 4),
            chip["y"] - max(6, (chip.get("h") or 24) // 2 - 4),
        )
        time.sleep(0.8)
    remaining = [c for c in (read_composer(tab).get("chips") or []) if (c.get("w") or 0) >= 12]
    if remaining:
        raise OrdaKError(
            code=ErrorCode.FLOW_UPLOAD_FAILED,
            message=f"Flow still shows {len(remaining)} stale reference(s) that could not be removed.",
        )


def attach_references(
    tab: ChromeTabRef,
    mode: str,
    references: list[tuple[str, Path]],
    *,
    runtime: Any = None,
) -> AttachedReferences:
    """Attach every ``(role, path)`` pair using the control that matches its role.

    ``first_frame`` / ``last_frame`` drive the Start/End slots (Frames mode); every other
    allowed role becomes an Ingredients chip. The caller has already run the reference
    policy, so nothing here decides *whether* a reference is allowed — only *where* it goes.
    """
    normalized = mode.strip().lower()
    attached = AttachedReferences(mode=normalized)
    frame_refs = [(role, path) for role, path in references if role in _SLOT_INDEX]
    other_refs = [(role, path) for role, path in references if role not in _SLOT_INDEX]

    if normalized == "frames":
        if other_refs:
            raise OrdaKError(
                code=ErrorCode.FLOW_REFERENCE_POLICY_VIOLATION,
                message=(
                    "Frames mode accepts only first_frame/last_frame; "
                    f"got {[role for role, _ in other_refs]}."
                ),
            )
    elif frame_refs:
        raise OrdaKError(
            code=ErrorCode.MODEL_FEATURE_INCOMPATIBLE,
            message=(
                "first_frame/last_frame require Flow's Frames reference mode, "
                f"but {normalized!r} is active."
            ),
        )

    clear_references(tab, normalized, runtime=runtime)
    for role, path in frame_refs:
        attached.frames[role] = attach_frame(tab, role, Path(path), runtime=runtime)
    for role, path in other_refs:
        attach_ingredient(tab, role, Path(path), runtime=runtime)
        attached.ingredients.append(role)
    return attached


__all__ = [
    "AttachedReferences",
    "FILE_INPUT_SELECTOR",
    "FRAME_SLOTS",
    "attach_frame",
    "attach_ingredient",
    "attach_references",
    "clear_frame",
    "clear_references",
    "close_picker",
    "read_composer",
    "read_frame_slots",
]
