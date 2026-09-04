"""Behaviour of the Nano Banana Pro regeneration path (§6, §43).

The browser is stubbed at the JavaScript boundary: each test scripts what the page
reports, and asserts what the module *does* with it.  A Nano Banana 2 result must never
survive as an accepted Pro result, so every negative case is checked too.
"""

from __future__ import annotations

import json

import pytest

from app.automation import gemini_pro
from app.errors import ErrorCode


class FakePage:
    """Answers the three scripts ``gemini_pro`` runs, in the order the page would."""

    def __init__(
        self,
        *,
        results: list[list[dict]],
        control: dict | None,
        clickable: bool = True,
    ) -> None:
        self.result_frames = list(results)
        self.control = control
        self.clickable = clickable
        self.identity_reads = 0
        self.click_calls = 0

    def __call__(self, tab, script: str) -> str:
        if "crypto.subtle" in script:
            frame = self.result_frames[min(self.identity_reads, len(self.result_frames) - 1)]
            self.identity_reads += 1
            return json.dumps({"results": frame})
        if "quality.some" in script:
            if self.control is None:
                return json.dumps({"found": False})
            return json.dumps({"found": True, **self.control})
        if "match.click()" in script:
            self.click_calls += 1
            return json.dumps({"clicked": self.clickable})
        raise AssertionError(f"unexpected script: {script[:80]}")


def _result(src: str, *, width: int = 1080, height: int = 1920, sha: str | None = None) -> dict:
    return {"src": src, "width": width, "height": height, "alt": "Generated image", "sha256": sha}


def _install(monkeypatch: pytest.MonkeyPatch, page: FakePage) -> FakePage:
    monkeypatch.setattr("app.automation.existing_chrome.execute_javascript", page)
    return page


def test_pro_path_accepts_a_positively_distinct_result(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _install(
        monkeypatch,
        FakePage(
            results=[
                [_result("https://x/asset/nb2.png", sha="a" * 64)],
                [
                    _result("https://x/asset/nb2.png", sha="a" * 64),
                    _result("https://x/asset/pro.png", width=2160, height=3840, sha="b" * 64),
                ],
            ],
            control={"label": "redo with nano banana pro", "verb": "redo", "source": "button"},
        ),
    )

    outcome = gemini_pro.run_pro_regeneration(object(), timeout_ms=5_000, sleep=lambda _s: None)

    assert outcome.used is True
    assert page.click_calls == 1
    assert outcome.result is not None
    assert outcome.result.sha256 == "b" * 64
    assert "new_asset_id" in outcome.distinction
    assert "distinct_sha256" in outcome.distinction
    assert "distinct_dimensions" in outcome.distinction
    assert any(note.startswith("pro_distinction=") for note in outcome.notes)


def test_pro_path_refuses_when_no_pro_control_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        FakePage(results=[[_result("https://x/asset/nb2.png", sha="a" * 64)]], control=None),
    )

    with pytest.raises(gemini_pro.ProPathUnavailable):
        gemini_pro.run_pro_regeneration(object(), timeout_ms=1_000, sleep=lambda _s: None)


def test_model_picker_is_never_mistaken_for_the_pro_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """A label of just "Nano Banana Pro" is the model picker, not a regeneration action."""

    class PickerOnlyPage(FakePage):
        def __call__(self, tab, script: str) -> str:
            if "quality.some" in script:
                # Mirror the real predicate: quality token present, no action verb.
                text = "nano banana pro"
                verbs = [v for v in gemini_pro.PRO_ACTION_VERBS if v in text]
                if not verbs:
                    return json.dumps({"found": False})
                return json.dumps({"found": True, "label": text, "verb": verbs[0], "source": "button"})
            return super().__call__(tab, script)

    _install(
        monkeypatch,
        PickerOnlyPage(results=[[_result("https://x/asset/nb2.png", sha="a" * 64)]], control=None),
    )

    with pytest.raises(gemini_pro.ProPathUnavailable):
        gemini_pro.run_pro_regeneration(object(), timeout_ms=1_000, sleep=lambda _s: None)


def test_recycled_bytes_under_a_new_url_are_not_a_new_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        FakePage(
            results=[
                [_result("https://x/asset/nb2.png", sha="a" * 64)],
                [_result("https://x/asset/copy.png", sha="a" * 64)],
            ],
            control={"label": "redo with pro", "verb": "redo", "source": "button"},
        ),
    )

    with pytest.raises(gemini_pro.ProResultNotDistinct):
        gemini_pro.run_pro_regeneration(object(), timeout_ms=900, sleep=lambda _s: None)


def test_same_asset_served_with_new_query_params_is_not_a_new_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        FakePage(
            results=[
                [_result("https://x/asset/nb2.png?sz=w512", sha=None)],
                [_result("https://x/asset/nb2.png?sz=w2048&token=2", sha=None)],
            ],
            control={"label": "regenerate with pro", "verb": "regenerate", "source": "button"},
        ),
    )

    with pytest.raises(gemini_pro.ProResultNotDistinct):
        gemini_pro.run_pro_regeneration(object(), timeout_ms=900, sleep=lambda _s: None)


def test_unclickable_control_is_reported_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        FakePage(
            results=[[_result("https://x/asset/nb2.png", sha="a" * 64)]],
            control={"label": "redo with pro", "verb": "redo", "source": "button"},
            clickable=False,
        ),
    )

    with pytest.raises(gemini_pro.ProPathUnavailable):
        gemini_pro.run_pro_regeneration(object(), timeout_ms=900, sleep=lambda _s: None)


def test_worker_maps_a_missing_pro_control_to_model_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.automation import gemini_worker
    from app.errors import OrdaKError

    monkeypatch.setattr(
        gemini_pro,
        "run_pro_regeneration",
        lambda *a, **k: (_ for _ in ()).throw(gemini_pro.ProPathUnavailable("no control")),
    )
    monkeypatch.setattr(
        "app.automation.existing_chrome.inspect_generated_image_state",
        lambda tab, provider="gemini": {},
    )

    with pytest.raises(OrdaKError) as excinfo:
        gemini_worker._run_gemini_pro_path(object(), runtime=None, timeout_ms=1_000)
    assert excinfo.value.code is ErrorCode.MODEL_NOT_AVAILABLE


def test_worker_maps_an_indistinct_result_to_model_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.automation import gemini_worker
    from app.errors import OrdaKError

    monkeypatch.setattr(
        gemini_pro,
        "run_pro_regeneration",
        lambda *a, **k: (_ for _ in ()).throw(gemini_pro.ProResultNotDistinct("same image")),
    )
    monkeypatch.setattr(
        "app.automation.existing_chrome.inspect_generated_image_state",
        lambda tab, provider="gemini": {},
    )

    with pytest.raises(OrdaKError) as excinfo:
        gemini_worker._run_gemini_pro_path(object(), runtime=None, timeout_ms=1_000)
    assert excinfo.value.code is ErrorCode.MODEL_NOT_AVAILABLE
