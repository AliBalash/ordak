"""Genuine image bytes for tests.

The §32 validator parses container headers and rejects tiny files, so tests cannot
stand in a byte string like ``b"fake-output"`` for a generated image.  These helpers
build real PNG/JPEG payloads with no third-party imaging dependency.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def png_bytes(width: int = 1080, height: int = 1920, *, seed: int = 7) -> bytes:
    """A valid, decodable RGB PNG whose pixel noise depends on ``seed``.

    Noise (rather than a flat colour) keeps the compressed payload comfortably above
    the validator's minimum-size floor and makes two different seeds differ in SHA.
    """
    state = (seed * 2654435761 + 1) & 0xFFFFFFFF
    noise = bytearray()
    for _ in range(width * 3):
        state = (state * 1103515245 + 12345) & 0xFFFFFFFF
        noise.append((state >> 16) & 0xFF)
    rows = bytearray()
    for row in range(height):
        offset = (row * 7 + seed) % max(len(noise), 1)
        rows.append(0)  # PNG filter type: none
        rows += noise[offset:] + noise[:offset]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


def write_png(path: str | Path, width: int = 1080, height: int = 1920, *, seed: int = 7) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(width, height, seed=seed))
    return path
