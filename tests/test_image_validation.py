"""The §32 checks every accepted Gemini image has to pass."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.automation.image_validation import (
    ImageValidationError,
    read_image_header,
    sha256_file,
    validate_generated_image,
)
from imagefixtures import png_bytes, write_png


def test_header_parser_reads_real_png_dimensions(tmp_path: Path) -> None:
    path = write_png(tmp_path / "a.png", 1080, 1920, seed=1)
    info = read_image_header(path)
    assert (info.image_format, info.width, info.height) == ("png", 1080, 1920)


def test_a_file_that_only_claims_to_be_an_image_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "not-really.png"
    path.write_bytes(b"x" * 40_000)
    with pytest.raises(ImageValidationError) as excinfo:
        validate_generated_image(path)
    assert excinfo.value.reason == "undecodable"


def test_truncated_download_is_rejected_on_size(tmp_path: Path) -> None:
    path = tmp_path / "truncated.png"
    path.write_bytes(png_bytes(1080, 1920, seed=1)[:2048])
    with pytest.raises(ImageValidationError) as excinfo:
        validate_generated_image(path)
    assert excinfo.value.reason == "too_small"


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ImageValidationError) as excinfo:
        validate_generated_image(tmp_path / "nope.png")
    assert excinfo.value.reason == "missing_file"


def test_thumbnail_of_our_own_upload_is_rejected(tmp_path: Path) -> None:
    reference = write_png(tmp_path / "character_sheet.png", 1080, 1920, seed=5)
    downloaded = tmp_path / "downloaded.png"
    downloaded.write_bytes(reference.read_bytes())
    with pytest.raises(ImageValidationError) as excinfo:
        validate_generated_image(downloaded, reference_hashes=[sha256_file(reference)])
    assert excinfo.value.reason == "matches_uploaded_reference"


def test_repeat_of_an_already_accepted_image_is_rejected(tmp_path: Path) -> None:
    first = write_png(tmp_path / "first.png", 1080, 1920, seed=9)
    second = tmp_path / "second.png"
    second.write_bytes(first.read_bytes())
    with pytest.raises(ImageValidationError) as excinfo:
        validate_generated_image(second, forbidden_hashes=[sha256_file(first)])
    assert excinfo.value.reason == "duplicate_of_previous"


def test_superseded_pre_regeneration_result_is_rejected_as_stale(tmp_path: Path) -> None:
    nb2 = write_png(tmp_path / "nb2.png", 1080, 1920, seed=2)
    downloaded = tmp_path / "downloaded.png"
    downloaded.write_bytes(nb2.read_bytes())
    with pytest.raises(ImageValidationError) as excinfo:
        validate_generated_image(downloaded, stale_hashes=[sha256_file(nb2)])
    assert excinfo.value.reason == "stale_result"


def test_aspect_ratio_outside_tolerance_is_rejected(tmp_path: Path) -> None:
    landscape = write_png(tmp_path / "landscape.png", 1920, 1080, seed=3)
    with pytest.raises(ImageValidationError) as excinfo:
        validate_generated_image(landscape, expected_aspect_ratio="9:16")
    assert excinfo.value.reason == "aspect_mismatch"


def test_requested_aspect_ratio_within_tolerance_passes(tmp_path: Path) -> None:
    path = write_png(tmp_path / "portrait.png", 1080, 1920, seed=4)
    record = validate_generated_image(path, expected_aspect_ratio="9:16", model="Nano Banana Pro")
    assert record.width == 1080 and record.height == 1920
    assert record.model == "Nano Banana Pro"
    assert record.sha256 == sha256_file(path)


def test_tiny_but_valid_image_is_rejected_on_dimension(tmp_path: Path) -> None:
    path = write_png(tmp_path / "tiny.png", 96, 96, seed=6)
    # min_bytes is relaxed so the failure can only come from the dimension check.
    with pytest.raises(ImageValidationError) as excinfo:
        validate_generated_image(path, min_bytes=0)
    assert excinfo.value.reason == "dimension_too_small"


def test_download_that_is_not_the_identified_result_is_rejected(tmp_path: Path) -> None:
    path = write_png(tmp_path / "other.png", 1080, 1920, seed=8)
    with pytest.raises(ImageValidationError) as excinfo:
        validate_generated_image(path, expected_sha256="f" * 64)
    assert excinfo.value.reason == "stale_result"
