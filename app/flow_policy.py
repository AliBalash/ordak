"""Google Flow reference policy — enforced at the upload boundary.

This is the last line of defence before bytes reach the Flow UI. The pipeline has its own
guard (``scripts/flow_reference_policy.py`` in the parent repository) and
``tests/test_ordak_flow_policy_parity.py`` there asserts both vocabularies stay identical.

Absolute rules (master_prompt §12-16, §41, §61):

  * Flow NEVER receives a style sheet — no world style anchor, no home/environment style
    sheet, no mood board, no book style board, no previous image used as a style reference.
  * Flow receives exactly one canonical reference sheet per clip:
        Clip A (question spark) -> character_sheet
        Clip B (book -> world)  -> book_design_sheet
  * Frame inputs are job content, not style references, and remain allowed:
        first_frame -> the composed book spread
        last_frame  -> the world keyframe

There is no provider fallback and no "warn and continue" path: a violation fails the job.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from app.errors import ErrorCode, OrdaKError

#: Canonical recurring reference sheet per clip.
CLIP_CANONICAL_ROLE: dict[str, str] = {
    "A": "character_sheet",
    "B": "book_design_sheet",
}

ALLOWED_CANONICAL_ROLES = frozenset(CLIP_CANONICAL_ROLE.values())
ALLOWED_FRAME_ROLES = frozenset({"first_frame", "last_frame"})
ALLOWED_ROLES = ALLOWED_CANONICAL_ROLES | ALLOWED_FRAME_ROLES

FORBIDDEN_ROLES = frozenset({
    "style",
    "style_sheet",
    "style_anchor",
    "style_board",
    "home_style",
    "home_style_sheet",
    "home_style_anchor",
    "home_world_style_anchor",
    "world_style",
    "world_style_sheet",
    "world_style_anchor",
    "environment_sheet",
    "environment_style",
    "book_anchor",
    "book_style",
    "book_style_sheet",
    "mood_board",
    "moodboard",
    "visual_style_anchor",
    "previous_image_style",
})

FORBIDDEN_NAME_TOKENS = (
    "style_anchor",
    "style_sheet",
    "stylesheet",
    "world_style",
    "home_style",
    "book_anchor",
    "book_style",
    "mood_board",
    "moodboard",
    "style_board",
)


def normalize_role(role: str | None) -> str:
    return str(role or "").strip().lower().replace(" ", "_").replace("-", "_")


def _reject(message: str) -> None:
    raise OrdaKError(
        code=ErrorCode.FLOW_REFERENCE_POLICY_VIOLATION,
        message=message,
        technical_details=(
            "Flow may only receive character_sheet (Clip A) or book_design_sheet (Clip B), "
            "plus first_frame/last_frame scene inputs."
        ),
    )


def _looks_like_style(role: str) -> bool:
    return any(token in role for token in ("style", "anchor", "board", "mood"))


def validate_roles(roles: Sequence[str | None]) -> list[str]:
    """Validate declared reference roles for a Flow job. Returns normalized roles."""
    normalized = [normalize_role(role) for role in roles]

    forbidden = [role for role in normalized if role in FORBIDDEN_ROLES]
    if forbidden:
        _reject(f"Flow style-sheet upload is forbidden; rejected roles: {forbidden}")

    unspecified = [role for role in normalized if role in ("", "unspecified")]
    if unspecified:
        _reject(
            "Every Flow reference must declare an explicit role "
            f"({len(unspecified)} upload(s) arrived without one)"
        )

    for role in normalized:
        if role in ALLOWED_ROLES:
            continue
        if _looks_like_style(role):
            _reject(f"Flow reference role {role!r} looks like a style sheet")
        _reject(f"Flow reference role {role!r} is not allowed")

    duplicates = sorted({role for role in normalized if normalized.count(role) > 1})
    if duplicates:
        _reject(f"Duplicate Flow reference roles are not allowed: {duplicates}")

    canonical = [role for role in normalized if role in ALLOWED_CANONICAL_ROLES]
    if len(canonical) > 1:
        _reject(f"Flow may receive only one canonical reference sheet; got {canonical}")

    return normalized


def validate_paths(paths: Iterable[Path | str]) -> None:
    """Catch a style sheet smuggled in under an allowed role by inspecting filenames."""
    for path in paths:
        name = Path(str(path)).name.lower()
        for token in FORBIDDEN_NAME_TOKENS:
            if token in name:
                _reject(f"Upload {name!r} looks like a style sheet and must not reach Flow")


def validate_references(references: Sequence[tuple[str | None, Path | str]]) -> list[str]:
    """Validate ``(role, path)`` pairs. Returns the normalized role list."""
    roles = validate_roles([role for role, _ in references])
    validate_paths([path for _, path in references])
    return roles


def frame_role_map(references: Sequence[tuple[str, Path]]) -> dict[str, Path]:
    """Return ``{role: path}`` so the worker can drive the right Flow control per role."""
    return {normalize_role(role): Path(path) for role, path in references}
