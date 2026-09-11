"""A receipt may only assert what the UI actually showed (§8, §18)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.automation import gemini_worker, image_validation
from app.automation.gemini_pro import ProControl, ProOutcome, ResultIdentity
from app.automation.gemini_worker import GeminiJobRequest
from app.schemas import GenerationOptions, GenerationReceipt
from imagefixtures import write_png


def test_model_verified_without_an_observed_label_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerationReceipt(provider="gemini", requested_model="nano_banana_pro", model_verified=True)


def test_model_verified_with_an_observed_label_is_accepted() -> None:
    receipt = GenerationReceipt(
        provider="gemini",
        requested_model="nano_banana_pro",
        actual_model_label="Nano Banana Pro",
        model_verified=True,
    )
    assert receipt.model_verified is True


def test_pro_regeneration_claim_without_a_distinction_note_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerationReceipt(
            provider="gemini",
            actual_model_label="Nano Banana Pro",
            model_verified=True,
            pro_regeneration_used=True,
        )


def _job() -> GeminiJobRequest:
    return GeminiJobRequest(
        question="a portrait still",
        provider="gemini",
        mode="image_generate",
        generation=GenerationOptions(model="nano_banana_pro", aspect_ratio="9:16", quality="best"),
        upload_paths=[Path("/tmp/char.png")],
        references=[("character_sheet", Path("/tmp/char.png"))],
    )


def test_provider_receives_the_image_writer_prompt_verbatim_with_references() -> None:
    """Uploads must never cause the worker to mutate ChatGPT's final art direction."""
    prompt = gemini_worker._effective_prompt(_job())

    assert prompt == "a portrait still"


def test_provider_receives_the_image_writer_prompt_verbatim_with_generation_options() -> None:
    prompt = gemini_worker._effective_prompt(
        GeminiJobRequest(
            question="a landscape, exactly 9:16",
            provider="gemini",
            mode="image_generate",
            generation=GenerationOptions(aspect_ratio="9:16"),
        )
    )

    assert prompt == "a landscape, exactly 9:16"


def _validation(tmp_path: Path) -> image_validation.ImageValidation:
    path = write_png(tmp_path / "out.png", 1080, 1920, seed=21)
    return image_validation.validate_generated_image(path, expected_aspect_ratio="9:16")


def test_receipt_records_the_pro_path_when_it_really_ran(tmp_path: Path) -> None:
    outcome = ProOutcome(
        used=True,
        control=ProControl(label="redo with pro", selector_source="button", matched_verb="redo"),
        baseline=[ResultIdentity("nb2", "nb2", 1080, 1920, "a" * 64)],
        result=ResultIdentity("pro", "pro", 2160, 3840, "b" * 64),
        distinction="new_asset_id+distinct_sha256",
        notes=["pro_control=redo with pro", "pro_distinction=new_asset_id+distinct_sha256"],
    )
    receipt = gemini_worker._build_gemini_receipt(
        _job(),
        model_evidence={"label": "Nano Banana Pro", "source": "model-control"},
        thinking_evidence={"label": "Open mode picker, currently Flash Extended", "source": "mode-picker"},
        pro_outcome=outcome,
        validations=[_validation(tmp_path)],
        workspace_url="https://gemini.google.com/app/abc",
        artifact_source="download",
    )
    assert receipt.model_verified is True
    assert receipt.pro_regeneration_used is True
    assert receipt.actual_model_label == "redo with pro"
    assert receipt.reference_roles == ["character_sheet"]
    assert any("sha256=" in note for note in receipt.notes)


def test_receipt_refuses_to_verify_a_label_that_is_a_different_model(tmp_path: Path) -> None:
    receipt = gemini_worker._build_gemini_receipt(
        _job(),
        model_evidence={"label": "Nano Banana 2", "source": "model-control"},
        thinking_evidence={"label": "Open mode picker, currently Flash Extended", "source": "mode-picker"},
        pro_outcome=None,
        validations=[_validation(tmp_path)],
        workspace_url=None,
        artifact_source="download",
    )
    assert receipt.model_verified is False
    assert receipt.pro_regeneration_used is False


def test_receipt_refuses_to_verify_without_a_label_source(tmp_path: Path) -> None:
    """A label with no source is a guess — body text, not the model control."""
    receipt = gemini_worker._build_gemini_receipt(
        _job(),
        model_evidence={"label": "Nano Banana Pro", "source": ""},
        thinking_evidence={"label": "Open mode picker, currently Flash Extended", "source": "mode-picker"},
        pro_outcome=None,
        validations=[_validation(tmp_path)],
        workspace_url=None,
        artifact_source="download",
    )
    assert receipt.model_verified is False


def test_receipt_refuses_to_verify_an_unnamed_provider_default(tmp_path: Path) -> None:
    receipt = gemini_worker._build_gemini_receipt(
        _job(),
        model_evidence={"label": "Create image (provider-selected)", "source": "image-tool-enabled"},
        thinking_evidence={"label": "Open mode picker, currently Flash Extended", "source": "mode-picker"},
        pro_outcome=None,
        validations=[_validation(tmp_path)],
        workspace_url=None,
        artifact_source="download",
    )
    assert receipt.model_verified is False


def test_worker_rejects_a_downloaded_image_that_is_our_own_reference(tmp_path: Path) -> None:
    from app.errors import ErrorCode, OrdaKError

    reference = write_png(tmp_path / "char.png", 1080, 1920, seed=31)
    download = tmp_path / "download.png"
    download.write_bytes(reference.read_bytes())
    job = GeminiJobRequest(
        question="still",
        provider="gemini",
        mode="image_generate",
        generation=GenerationOptions(model="nano_banana_pro", aspect_ratio="9:16"),
        upload_paths=[reference],
        references=[("character_sheet", reference)],
    )
    with pytest.raises(OrdaKError) as excinfo:
        gemini_worker._validate_gemini_images(
            [download],
            job=job,
            model_label="Nano Banana Pro",
            stale_hashes=[],
            runtime=None,
        )
    assert excinfo.value.code is ErrorCode.RESULT_NOT_EXTRACTABLE
    assert "matches_uploaded_reference" in str(excinfo.value)


def test_worker_rejects_the_superseded_nano_banana_2_download(tmp_path: Path) -> None:
    from app.errors import OrdaKError

    nb2 = write_png(tmp_path / "nb2.png", 1080, 1920, seed=41)
    job = GeminiJobRequest(
        question="still",
        provider="gemini",
        mode="image_generate",
        generation=GenerationOptions(model="nano_banana_pro", aspect_ratio="9:16"),
    )
    with pytest.raises(OrdaKError) as excinfo:
        gemini_worker._validate_gemini_images(
            [nb2],
            job=job,
            model_label="Nano Banana Pro",
            stale_hashes=[image_validation.sha256_file(nb2)],
            runtime=None,
        )
    assert "stale_result" in str(excinfo.value)
