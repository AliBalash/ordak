"""The Gemini image model is read from the UI, never assumed.

Gemini's web UI has no image-model dropdown. Image generation is the ``Create image``
entry of the ``Upload & tools`` menu, and the composer's zero-state line
``Create with <model>.`` is the only place the UI names the image model. A request for
a model that line does not name must fail rather than quietly run on another one.
"""
from __future__ import annotations

import pytest

from app.automation import gemini_worker as gw
from app.errors import ErrorCode, OrdaKError


class Runtime:
    def __init__(self) -> None:
        self.logs: list[str] = []

    def append_log(self, message: str, level: str = "info") -> None:
        self.logs.append(message)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch) -> None:
    monkeypatch.setattr(gw.time, "sleep", lambda _s: None)


def fake_state(monkeypatch, *, active: bool, attribution: list[str]) -> None:
    monkeypatch.setattr(
        gw,
        "_read_image_tool_state",
        lambda tab: {"imageToolActive": active, "toolsButton": {"x": 1.0, "y": 2.0},
                     "attribution": attribution},
    )


def test_nano_banana_2_is_confirmed_from_the_attribution(monkeypatch) -> None:
    fake_state(monkeypatch, active=True, attribution=["Create with Nano Banana 2."])
    runtime = Runtime()
    evidence = gw._select_gemini_image_model(object(), "nano_banana_2", runtime)
    assert evidence["label"] == "Create with Nano Banana 2."
    assert evidence["source"] == "composer-attribution"
    assert any("confirmed by the UI" in line for line in runtime.logs)


def test_gemini_app_flash_mode_is_verified_as_nano_banana_2(monkeypatch) -> None:
    fake_state(monkeypatch, active=True, attribution=[])
    monkeypatch.setattr(
        gw,
        "_read_gemini_selected_model",
        lambda tab: {"label": "Open mode picker, currently 3.8 Flash", "source": "popup-button"},
    )

    evidence = gw._select_gemini_image_model(object(), "nano_banana_2", Runtime())

    assert evidence == {
        "label": "Open mode picker, currently 3.8 Flash",
        "source": "mode-picker",
    }


def test_gemini_app_pro_mode_is_verified_as_nano_banana_2(monkeypatch) -> None:
    fake_state(monkeypatch, active=True, attribution=[])
    monkeypatch.setattr(
        gw,
        "_read_gemini_selected_model",
        lambda tab: {"label": "Open mode picker, currently 3.1 Pro Extended", "source": "popup-button"},
    )

    evidence = gw._select_gemini_image_model(object(), "nano_banana_2", Runtime())

    assert evidence["source"] == "mode-picker"


def test_flash_lite_is_not_accepted_for_the_nano_banana_2_contract(monkeypatch) -> None:
    fake_state(monkeypatch, active=True, attribution=[])
    monkeypatch.setattr(
        gw,
        "_read_gemini_selected_model",
        lambda tab: {"label": "Open mode picker, currently 3.5 Flash-Lite", "source": "popup-button"},
    )

    with pytest.raises(OrdaKError) as excinfo:
        gw._select_gemini_image_model(object(), "nano_banana_2", Runtime())
    assert excinfo.value.code is ErrorCode.MODEL_SELECTION_FAILED


def test_pro_request_allows_nb2_initial_step_but_does_not_claim_pro(monkeypatch) -> None:
    fake_state(monkeypatch, active=True, attribution=["Create with Nano Banana 2."])
    evidence = gw._select_gemini_image_model(object(), "nano_banana_pro", Runtime())
    assert evidence["label"] == "Create with Nano Banana 2."


def test_hidden_named_model_fails_instead_of_using_a_provider_selected_fallback(monkeypatch) -> None:
    fake_state(monkeypatch, active=True, attribution=["Chat with Gemini"])
    with pytest.raises(OrdaKError) as excinfo:
        gw._select_gemini_image_model(object(), "nano_banana_2", Runtime())
    assert excinfo.value.code is ErrorCode.MODEL_SELECTION_FAILED


def test_auto_best_also_fails_when_gemini_hides_its_model_name(monkeypatch) -> None:
    fake_state(monkeypatch, active=True, attribution=["Chat with Gemini"])
    with pytest.raises(OrdaKError) as excinfo:
        gw._select_gemini_image_model(object(), "auto_best", Runtime())
    assert excinfo.value.code is ErrorCode.MODEL_SELECTION_FAILED


def test_unknown_model_is_rejected_before_touching_the_browser(monkeypatch) -> None:
    def boom(tab):  # pragma: no cover - must not run
        raise AssertionError("the browser was touched for an unknown model")

    monkeypatch.setattr(gw, "_read_image_tool_state", boom)
    with pytest.raises(OrdaKError) as excinfo:
        gw._select_gemini_image_model(object(), "dall_e_3", Runtime())
    assert excinfo.value.code is ErrorCode.MODEL_NOT_AVAILABLE


def test_the_tool_is_switched_on_when_the_chip_is_absent(monkeypatch) -> None:
    states = [
        {"imageToolActive": False, "toolsButton": {"x": 5.0, "y": 6.0}, "attribution": []},
        {"imageToolActive": True, "toolsButton": None,
         "attribution": ["Create with Nano Banana 2."]},
    ]
    monkeypatch.setattr(gw, "_read_image_tool_state", lambda tab: states.pop(0) if states else states)
    clicks: list[tuple[float, float]] = []
    monkeypatch.setattr(
        "app.automation.existing_chrome.dispatch_mouse_click",
        lambda tab, x, y: clicks.append((x, y)),
    )
    monkeypatch.setattr(gw, "_click_menu_item", lambda tab, label: label == "Create image")
    runtime = Runtime()
    state = gw._activate_gemini_image_tool(object(), runtime)
    assert state["imageToolActive"] is True
    assert clicks == [(5.0, 6.0)]
    assert any("image tool activated" in line for line in runtime.logs)


def test_a_tool_that_never_turns_on_is_a_ui_change(monkeypatch) -> None:
    monkeypatch.setattr(
        gw,
        "_read_image_tool_state",
        lambda tab: {"imageToolActive": False, "toolsButton": {"x": 1.0, "y": 1.0}, "attribution": []},
    )
    monkeypatch.setattr(
        "app.automation.existing_chrome.dispatch_mouse_click", lambda tab, x, y: None
    )
    monkeypatch.setattr(gw, "_click_menu_item", lambda tab, label: False)
    with pytest.raises(OrdaKError) as excinfo:
        gw._activate_gemini_image_tool(object(), Runtime())
    assert excinfo.value.code is ErrorCode.PROVIDER_UI_CHANGED


def test_image_tool_waits_for_a_late_mounted_menu_before_retrying(monkeypatch) -> None:
    states = [
        {"imageToolActive": False, "toolsButton": {"x": 5.0, "y": 6.0}, "attribution": []},
        {"imageToolActive": True, "toolsButton": None, "attribution": ["Create with Nano Banana 2."]},
    ]
    monkeypatch.setattr(gw, "_read_image_tool_state", lambda tab: states.pop(0) if states else {"imageToolActive": True})
    attempts = iter([False, False, True])
    monkeypatch.setattr(gw, "_click_menu_item", lambda tab, label: next(attempts))
    monkeypatch.setattr("app.automation.existing_chrome.dispatch_mouse_click", lambda *args: None)
    monkeypatch.setattr("app.automation.existing_chrome.dispatch_key", lambda *args, **kwargs: None)
    monkeypatch.setattr(gw.time, "sleep", lambda _seconds: None)
    state = gw._activate_gemini_image_tool(object(), Runtime())
    assert state["imageToolActive"] is True
