"""Validation every accepted Gemini image must pass (§32).

The checks answer one question: *is this file really the image the provider just
generated for this job?*  A download that is truncated, that is the user's own
uploaded reference thumbnail, that is byte-identical to an image we already accepted,
or that is a stale result from an earlier turn, is rejected — the pipeline never
silently substitutes one image for another.

Dimensions come from parsing the container header rather than a third-party imaging
library, so the service venv stays dependency-free and a file that merely *claims* to
be a PNG cannot pass.  When ``ffprobe`` is present it is used as a second, independent
decode check.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

#: A generated 9:16 or 16:9 still is tens of kilobytes at the very least; anything
#: smaller is a spinner, a placeholder, or a truncated download.
MIN_IMAGE_BYTES = 16_384

#: Below this on either axis the file cannot be a production still.
MIN_IMAGE_DIMENSION = 320

#: Fractional tolerance when comparing an observed aspect ratio to the requested one.
DEFAULT_ASPECT_TOLERANCE = 0.06


class ImageValidationError(ValueError):
    """An accepted-image check failed.  ``reason`` is a stable machine token."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class ImageInfo:
    image_format: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ImageValidation:
    """Everything §32 says must be stored about an accepted image."""

    path: Path
    sha256: str
    size_bytes: int
    width: int
    height: int
    image_format: str
    aspect_ratio: float
    provider: str = "gemini"
    model: str | None = None
    references: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "dimensions": [self.width, self.height],
            "format": self.image_format,
            "aspect_ratio": round(self.aspect_ratio, 6),
            "provider": self.provider,
            "model": self.model,
            "references": list(self.references),
            "notes": list(self.notes),
        }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _png_size(blob: bytes) -> ImageInfo | None:
    if not blob.startswith(b"\x89PNG\r\n\x1a\n") or len(blob) < 24:
        return None
    if blob[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", blob[16:24])
    return ImageInfo("png", int(width), int(height))


def _gif_size(blob: bytes) -> ImageInfo | None:
    if not (blob.startswith(b"GIF87a") or blob.startswith(b"GIF89a")) or len(blob) < 10:
        return None
    width, height = struct.unpack("<HH", blob[6:10])
    return ImageInfo("gif", int(width), int(height))


def _webp_size(blob: bytes) -> ImageInfo | None:
    if len(blob) < 30 or blob[0:4] != b"RIFF" or blob[8:12] != b"WEBP":
        return None
    chunk = blob[12:16]
    if chunk == b"VP8X":
        width = int.from_bytes(blob[24:27], "little") + 1
        height = int.from_bytes(blob[27:30], "little") + 1
        return ImageInfo("webp", width, height)
    if chunk == b"VP8 ":
        if blob[23:26] != b"\x9d\x01\x2a":
            return None
        width = int.from_bytes(blob[26:28], "little") & 0x3FFF
        height = int.from_bytes(blob[28:30], "little") & 0x3FFF
        return ImageInfo("webp", width, height)
    if chunk == b"VP8L":
        bits = int.from_bytes(blob[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return ImageInfo("webp", width, height)
    return None


def _jpeg_size(blob: bytes) -> ImageInfo | None:
    if not blob.startswith(b"\xff\xd8"):
        return None
    index = 2
    total = len(blob)
    while index + 9 < total:
        if blob[index] != 0xFF:
            index += 1
            continue
        marker = blob[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if marker == 0xD9:
            return None
        length = int.from_bytes(blob[index + 2 : index + 4], "big")
        if length < 2:
            return None
        # SOF0..SOF15 except the DHT/JPG/DAC markers carry the frame dimensions.
        if marker in (
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        ):
            height = int.from_bytes(blob[index + 5 : index + 7], "big")
            width = int.from_bytes(blob[index + 7 : index + 9], "big")
            return ImageInfo("jpeg", int(width), int(height))
        index += 2 + length
    return None


def read_image_header(path: str | Path, *, probe_bytes: int = 1_048_576) -> ImageInfo:
    """Extract format and dimensions from the container header.

    Raises ``ImageValidationError('undecodable', ...)`` when the bytes do not describe
    a supported still image.
    """
    path = Path(path)
    with path.open("rb") as handle:
        blob = handle.read(probe_bytes)
    for parser in (_png_size, _jpeg_size, _webp_size, _gif_size):
        info = parser(blob)
        if info is not None:
            if info.width <= 0 or info.height <= 0:
                raise ImageValidationError(
                    "dimensions_unknown",
                    f"{path.name} is a {info.image_format} whose header reports no usable size.",
                )
            return info
    raise ImageValidationError(
        "undecodable",
        f"{path.name} is not a decodable PNG/JPEG/WebP/GIF image.",
    )


def ffprobe_dimensions(path: str | Path) -> tuple[int, int] | None:
    """Second opinion from ffprobe; ``None`` when ffprobe is unavailable."""
    binary = shutil.which("ffprobe")
    if not binary:
        return None
    try:
        raw = subprocess.run(
            [
                binary, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        streams = (json.loads(raw or "{}").get("streams") or [])
        if not streams:
            return None
        return int(streams[0].get("width") or 0), int(streams[0].get("height") or 0)
    except Exception:
        return None


def parse_aspect_ratio(text: str | None) -> float | None:
    """``"9:16"`` → ``0.5625``.  Returns ``None`` for anything unparseable."""
    if not text:
        return None
    body = str(text).strip().lower().replace(" ", "")
    for separator in (":", "x", "/"):
        if separator in body:
            left, _, right = body.partition(separator)
            try:
                width = float(left)
                height = float(right)
            except ValueError:
                return None
            if width <= 0 or height <= 0:
                return None
            return width / height
    try:
        value = float(body)
    except ValueError:
        return None
    return value if value > 0 else None


def validate_generated_image(
    path: str | Path,
    *,
    provider: str = "gemini",
    model: str | None = None,
    references: Sequence[str] = (),
    expected_aspect_ratio: str | None = None,
    aspect_tolerance: float = DEFAULT_ASPECT_TOLERANCE,
    min_bytes: int = MIN_IMAGE_BYTES,
    min_dimension: int = MIN_IMAGE_DIMENSION,
    forbidden_hashes: Iterable[str] = (),
    reference_hashes: Iterable[str] = (),
    stale_hashes: Iterable[str] = (),
    expected_sha256: str | None = None,
) -> ImageValidation:
    """Run every §32 check and return the record to persist.

    ``forbidden_hashes``  — images already accepted in this video; a repeat means the
    download picked up the previous result instead of the new one.
    ``reference_hashes``  — SHA-256 of the files *we uploaded*; a match means the
    download grabbed the user-upload thumbnail rather than the generated image.
    ``stale_hashes``      — results the page had already shown before this turn (for
    example the Nano Banana 2 image that preceded a Pro regeneration); a match means
    the download picked up the superseded asset.
    ``expected_sha256``   — the hash of the result the page identified as current; a
    mismatch means the download raced a stale asset.  Only pass this when the download
    is known to be byte-identical to the rendered asset.
    """
    path = Path(path)
    notes: list[str] = []

    if not path.is_file():
        raise ImageValidationError("missing_file", f"{path} does not exist.")
    size_bytes = path.stat().st_size
    if size_bytes < min_bytes:
        raise ImageValidationError(
            "too_small",
            f"{path.name} is {size_bytes} bytes, below the {min_bytes}-byte floor for a real image.",
        )

    info = read_image_header(path)
    if min(info.width, info.height) < min_dimension:
        raise ImageValidationError(
            "dimension_too_small",
            f"{path.name} is {info.width}x{info.height}; both axes must be at least {min_dimension}px.",
        )

    probed = ffprobe_dimensions(path)
    if probed is None:
        notes.append("ffprobe_unavailable")
    elif probed != (info.width, info.height):
        if 0 in probed:
            raise ImageValidationError(
                "undecodable",
                f"{path.name} could not be decoded by ffprobe.",
            )
        notes.append(f"ffprobe_dimensions={probed[0]}x{probed[1]}")

    digest = sha256_file(path)

    reference_set = {str(item).lower() for item in reference_hashes if item}
    if digest.lower() in reference_set:
        raise ImageValidationError(
            "matches_uploaded_reference",
            f"{path.name} is byte-identical to a reference we uploaded, so it is a thumbnail, not a result.",
        )

    forbidden_set = {str(item).lower() for item in forbidden_hashes if item}
    if digest.lower() in forbidden_set:
        raise ImageValidationError(
            "duplicate_of_previous",
            f"{path.name} is byte-identical to an image already accepted for this video.",
        )

    stale_set = {str(item).lower() for item in stale_hashes if item}
    if digest.lower() in stale_set:
        raise ImageValidationError(
            "stale_result",
            f"{path.name} is the superseded result the page showed before this turn, not the current one.",
        )

    if expected_sha256 and digest.lower() != str(expected_sha256).lower():
        raise ImageValidationError(
            "stale_result",
            f"{path.name} does not match the result the page identified as current "
            f"(expected {str(expected_sha256)[:12]}…, downloaded {digest[:12]}…).",
        )

    observed_ratio = info.width / info.height
    expected_ratio = parse_aspect_ratio(expected_aspect_ratio)
    if expected_ratio is not None:
        drift = abs(observed_ratio - expected_ratio) / expected_ratio
        if drift > aspect_tolerance:
            raise ImageValidationError(
                "aspect_mismatch",
                f"{path.name} is {info.width}x{info.height} (ratio {observed_ratio:.4f}) but "
                f"{expected_aspect_ratio} was requested (ratio {expected_ratio:.4f}).",
            )
        notes.append(f"aspect_drift={drift:.4f}")

    return ImageValidation(
        path=path,
        sha256=digest,
        size_bytes=size_bytes,
        width=info.width,
        height=info.height,
        image_format=info.image_format,
        aspect_ratio=observed_ratio,
        provider=provider,
        model=model,
        references=[str(item) for item in references],
        notes=notes,
    )
