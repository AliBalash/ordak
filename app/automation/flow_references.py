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
#: Flow's hidden upload input is always in the DOM, but its ``accept`` lists file
#: extensions (".png,.jpg,…") rather than MIME types, so ``[accept*="image"]`` alone matches
#: nothing on the current UI. Both shapes are covered, with a bare file input as the last
#: resort — the composer only ever exposes one.
FILE_INPUT_SELECTOR = (
    'input[type="file"][accept*="image"],'
    'input[type="file"][accept*=".png"],'
    'input[type="file"][accept*=".jpg"],'
    'input[type="file"]'
)

#: Frame slots in order; index 0 is the first frame, index 1 the last.
FRAME_SLOTS = ("first_frame", "last_frame")

_SLOT_INDEX = {"first_frame": 0, "last_frame": 1}


_FRAME_ROW_JS = r"""
(() => {
  // The swap control is an icon button: its visible text is the ligature ('swap_horiz')
  // and the phrase lives in aria-label (verified 2026-09-05). Matching the text alone
  // found nothing, which read as "Frames mode is not active" for a row that was there.
  const swap = Array.from(document.querySelectorAll('button,[role=button]')).find(
    (b) => /swap first and last/i.test(b.getAttribute('aria-label') || '')
        || /swap first and last/i.test(b.innerText || '')
        || (b.innerText || '').trim() === 'swap_horiz'
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


#: The composer is a ProseMirror contenteditable and carries no ``role="textbox"``, so
#: requiring that role finds nothing. The page's other editable nodes are the search input
#: and a hidden reCAPTCHA textarea, hence the visibility filter and the widest-first order.
_EDITOR_FINDER = r"""
  const editorNode = () => {
    const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    for (const selector of [
      'div.ProseMirror[contenteditable="true"]',
      'div[contenteditable="true"][role="textbox"]',
      'div[contenteditable="true"]',
      '[contenteditable="true"]',
    ]) {
      const hit = [...document.querySelectorAll(selector)].filter(vis)
        .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
      if (hit) return hit;
    }
    return null;
  };
"""


_COMPOSER_JS = r"""
(() => {
%(finder)s
  const editor = editorNode();
  // The add control is an icon button; its aria-label is the stable part, while the icon
  // font text has been both 'add_2' and 'add'.
  // The add control is an icon button whose font text has been both 'add_2' and 'add', and
  // the header carries a similarly labelled "Add media menu". The one that matters is the
  // composer's, so candidates are ranked by how close they sit to the editor.
  const buttons = Array.from(document.querySelectorAll('button,[role=button]'));
  const editorY = editor ? editor.getBoundingClientRect().y : 0;
  const add = buttons
    .filter(b => /add ingredients to the prompt/i.test(b.getAttribute('aria-label') || '')
              || ['add_2', 'add'].includes((b.innerText || '').trim()))
    .sort((a, b) => Math.abs(a.getBoundingClientRect().y - editorY)
                  - Math.abs(b.getBoundingClientRect().y - editorY))[0];
  if (!editor) return JSON.stringify({found: false, reason: 'no-editor'});
  let box = editor;
  while (box && add && !box.contains(add)) box = box.parentElement;
  if (!box) box = editor.parentElement;
  // A zero-size <img> is a layout placeholder, not an attached reference: counting it
  // would make an empty composer look like it already carries an ingredient.
  const chips = Array.from(box.querySelectorAll('img')).filter((im) => {
    const r = im.getBoundingClientRect();
    return r.width > 4 && r.height > 4;
  }).map((im) => {
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
""" % {"finder": _EDITOR_FINDER}


_PICKER_ROW_JS = r"""
(() => {
  const cleanText = s => String(s || '').replace(/\s+/g, ' ').trim();
  const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  // The picker is an Angular Material overlay, not a [role=dialog]; it is identified by
  // its own asset rail so a settings popper is never mistaken for it.
  const panes = [...document.querySelectorAll(
    '[role=dialog],.cdk-overlay-pane,mat-dialog-container,[data-radix-popper-content-wrapper]'
  )].filter(visible);
  const dialog = panes.find(p => /upload media/i.test(cleanText(p.innerText)))
    || panes.find(p => /add to prompt/i.test(cleanText(p.innerText)));
  if (!dialog) return JSON.stringify({open: false});
  const needle = %s;
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x + r.width / 2),
      y: Math.round(r.y + r.height / 2),
      w: Math.round(r.width),
      h: Math.round(r.height),
    };
  };
  // Each asset is a button ('asset-item') whose text reads "<file name> <kind>". The button
  // is what carries the layout box: walking up from the file-name text node lands on an
  // Angular custom element with `display: contents`, whose rect is all zeros — that is why
  // the row was never clickable and the code fell through to the confirm button instead.
  const rows = Array.from(dialog.querySelectorAll('button,[role=button],[role=option]'))
    .filter(visible)
    .filter((el) => cleanText(el.innerText).toLowerCase().includes(needle))
    .map((el) => Object.assign(
      {active: /asset-item-active|selected/i.test(String(el.className || ''))},
      box(el)
    ))
    .filter((o) => o.w > 20 && o.h > 10);
  rows.sort((a, b) => a.y - b.y);
  const confirm = Array.from(dialog.querySelectorAll('button')).find(
    (b) => cleanText(b.innerText).toLowerCase() === 'add to prompt'
  );
  return JSON.stringify({
    open: true,
    matches: rows.length,
    row: rows.length ? rows[0] : null,
    confirm: confirm ? box(confirm) : null,
    confirmEnabled: !!confirm && !confirm.disabled,
  });
})()
"""


#: Flow's asset picker is an Angular Material overlay, not a ``[role=dialog]``. It is
#: recognised by its own content — the asset-source rail and its upload entry — so a
#: settings popper or a tooltip is never mistaken for it.
PICKER_SELECTORS = "[role=dialog],.cdk-overlay-pane,mat-dialog-container,[data-radix-popper-content-wrapper]"

_PICKER_OPEN_JS = r"""
(() => {
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const panes = [...document.querySelectorAll(%(selectors)s)].filter(vis);
  // Two different overlays list assets: the Ingredients picker (which carries the only
  // 'Upload media' entry Flow exposes) and the frame dialog, titled 'Select a frame image',
  // which can merely pick from the library. Both are the picker for our purposes; only the
  // first can upload, which is why 'upload' is reported separately (verified 2026-09-05).
  const picker = panes.find(p => {
    const text = clean(p.innerText).toLowerCase();
    return /upload media/.test(text)
      || /select a frame image/.test(text)
      || (/uploads?/.test(text) && /characters|avatars|images/.test(text))
      || (/add to prompt/.test(text) && /recent/.test(text));
  });
  if (!picker) return JSON.stringify({open: false, paneCount: panes.length});
  const upload = [...picker.querySelectorAll('button,[role=button],[role=menuitem]')]
    .filter(vis).find(b => /upload media/i.test(clean(b.innerText)));
  const box = upload ? upload.getBoundingClientRect() : null;
  return JSON.stringify({
    open: true,
    text: clean(picker.innerText).slice(0, 240),
    canUpload: !!upload,
    upload: box ? {x: Math.round(box.x + box.width / 2), y: Math.round(box.y + box.height / 2)} : null,
  });
})()
""" % {"selectors": json.dumps(PICKER_SELECTORS)}


def picker_state(tab: ChromeTabRef) -> dict[str, Any]:
    """Whether the asset picker is open, and where its upload entry is."""
    state = _evaluate(tab, _PICKER_OPEN_JS)
    return state if isinstance(state, dict) else {"open": False}


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


def ensure_file_input(tab: ChromeTabRef, *, runtime: Any = None) -> None:
    """Make sure the picker's hidden upload input exists before files are attached.

    Flow creates that input only when its ``Upload media`` entry is clicked, and it then
    persists for the rest of the page's life. The click is made with the native file chooser
    intercepted, so no OS dialog can appear — the bytes still go in through
    ``DOM.setFileInputFiles``, never through a file manager.
    """
    from app.automation.existing_chrome import file_input_count, intercepted_file_chooser

    if file_input_count(tab, FILE_INPUT_SELECTOR):
        return
    state = picker_state(tab)
    upload = state.get("upload") if isinstance(state, dict) else None
    if not upload:
        raise OrdaKError(
            code=ErrorCode.FLOW_UI_CHANGED,
            message="Flow's asset picker has no 'Upload media' entry, so no upload input exists.",
            technical_details=str(state)[:200],
        )
    with intercepted_file_chooser(tab):
        dispatch_mouse_click(tab, float(upload["x"]), float(upload["y"]))
        for attempt in range(10):
            time.sleep(0.4 + 0.2 * attempt)
            if file_input_count(tab, FILE_INPUT_SELECTOR):
                _log(runtime, "Flow upload input ready")
                return
    raise OrdaKError(
        code=ErrorCode.FLOW_UPLOAD_FAILED,
        message="Flow did not create an upload input after its 'Upload media' entry was used.",
    )


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
        if picker_state(tab).get("open"):
            return
        time.sleep(0.5)
    raise OrdaKError(
        code=ErrorCode.FLOW_UI_CHANGED,
        message="Flow asset picker did not open.",
        technical_details=(
            "No asset picker overlay appeared after clicking the reference control. "
            "The picker is an Angular Material overlay containing an 'Upload media' entry."
        ),
    )


def _select_uploaded_asset(
    tab: ChromeTabRef,
    filename: str,
    *,
    timeout_s: float = 120.0,
) -> None:
    """Wait for the uploaded asset to list, then attach it by clicking its own row.

    Clicking the row is what attaches an asset: the picker closes as soon as the chip or the
    frame is added (verified 2026-09-05). ``Add to prompt`` belongs to the detail pane and is
    enabled even when nothing is selected, so clicking it first attached nothing and closed
    the picker — which the caller could only report as "Flow did not show the reference".
    It stays as the fallback for the states where a row click merely selects.

    The caller verifies the chip or the filled slot, so a closed picker is reported as done
    rather than as success: nothing here claims an attachment it has not seen.
    """
    script = _PICKER_ROW_JS % json.dumps(filename.lower())
    deadline = time.monotonic() + timeout_s
    attempts = 0
    while time.monotonic() < deadline:
        state = _evaluate(tab, script) or {}
        if not state.get("open"):
            return
        row = state.get("row")
        if not row:
            # The upload is still travelling; the row appears when Flow has stored it.
            time.sleep(1.5)
            continue
        if attempts >= 3:
            break
        attempts += 1
        dispatch_mouse_click(tab, row["x"], row["y"])
        for _ in range(8):
            time.sleep(0.6)
            if not (_evaluate(tab, script) or {}).get("open"):
                return
        # Still open: this picker state only selected the row, so confirm explicitly.
        state = _evaluate(tab, script) or {}
        confirm = state.get("confirm") if state.get("confirmEnabled") else None
        if confirm:
            dispatch_mouse_click(tab, confirm["x"], confirm["y"])
            for _ in range(8):
                time.sleep(0.6)
                if not (_evaluate(tab, script) or {}).get("open"):
                    return
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
    """Put ``path`` into the Start (``first_frame``) or End (``last_frame``) slot.

    The frame dialog can only *pick* from the project library — it has no upload entry of its
    own (verified 2026-09-05) — so the caller must have put the file in the library first;
    :func:`upload_to_library` is what does that.

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
    _log(runtime, f"Flow {role}: selecting {source.name} in the frame dialog")
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


def library_lists_asset(tab: ChromeTabRef, filename: str) -> bool:
    """True when the open picker already lists an asset with this file name."""
    state = _evaluate(tab, _PICKER_ROW_JS % json.dumps(filename.lower())) or {}
    return bool(state.get("open")) and int(state.get("matches") or 0) > 0


def upload_to_library(
    tab: ChromeTabRef,
    paths: list[Path],
    *,
    runtime: Any = None,
) -> None:
    """Upload files into the project's asset library without attaching them to the prompt.

    Only the Ingredients picker exposes an upload entry that creates a real
    ``<input type="file">``; the frame dialog can merely pick from the library, and the
    project's own ``Upload`` menu item opens a native chooser that no automation can fill
    (all verified 2026-09-05). So Frames-mode clips upload here first, in Ingredients mode,
    and then select the asset in the frame dialog.

    The picker is dismissed once each file is listed, so nothing is attached as a side effect.
    """
    for source in paths:
        path = Path(source)
        if not path.is_file():
            raise OrdaKError(
                code=ErrorCode.FLOW_UPLOAD_FAILED,
                message=f"Reference source is missing on disk: {path}",
            )
        composer = read_composer(tab)
        if not composer.get("add"):
            raise OrdaKError(
                code=ErrorCode.FLOW_UI_CHANGED,
                message="Flow composer has no add-reference control, so nothing can be uploaded.",
            )
        dispatch_mouse_click(tab, composer["add"]["x"], composer["add"]["y"])
        _wait_for_picker(tab)
        ensure_file_input(tab, runtime=runtime)
        set_file_input_files(tab, FILE_INPUT_SELECTOR, [path])
        _log(runtime, f"Flow library: uploaded {path.name}, waiting for it to list")
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            if library_lists_asset(tab, path.name):
                break
            time.sleep(1.5)
        else:
            close_picker(tab)
            raise OrdaKError(
                code=ErrorCode.FLOW_UPLOAD_FAILED,
                message=f"Flow never listed {path.name} in its asset library after upload.",
            )
        close_picker(tab)
        time.sleep(0.8)
        _log(runtime, f"Flow library holds {path.name}")


def clear_frame(tab: ChromeTabRef, role: str, *, runtime: Any = None) -> None:
    """Empty one frame slot. A filled slot's own control is its remove button."""
    index = _SLOT_INDEX[role]
    for _ in range(3):
        slots = read_frame_slots(tab)
        if not slots[index].get("filled"):
            return
        dispatch_mouse_click(tab, slots[index]["x"], slots[index]["y"])
        time.sleep(1.0)
        if picker_state(tab).get("open"):
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
    ensure_file_input(tab, runtime=runtime)
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
    if frame_refs:
        # Frames can only be picked from the library, and only Ingredients mode can upload
        # into it, so the files go in first and the mode is put back before anything is
        # selected. The mode is re-verified by flow_settings on the way back.
        from app.automation import flow_settings

        _log(runtime, "Flow frames: uploading the frame sources through the Ingredients picker")
        flow_settings.select_reference_mode(tab, "ingredients", runtime=runtime)
        try:
            upload_to_library(tab, [Path(path) for _, path in frame_refs], runtime=runtime)
        finally:
            flow_settings.select_reference_mode(tab, "frames", runtime=runtime)
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
    "library_lists_asset",
    "read_frame_slots",
    "upload_to_library",
]
