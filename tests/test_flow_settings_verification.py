"""What the Flow settings verifiers actually do against a scripted UI (§18-21, T6.1).

A mock Flow composer answers the real scripts ``flow_settings`` sends, so these tests
exercise the shipped selection and read-back logic rather than restating a constant.
Every silent-downgrade path is checked: a control that refuses to change, a model the UI
does not offer, and a resolution the model cannot do must all raise, never return.
"""

from __future__ import annotations

import json

import pytest

from app.automation import flow_settings
from app.errors import ErrorCode, OrdaKError


class FlowComposerMock:
    """A Flow settings menu with real option lists and a click that can be made to fail."""

    def __init__(
        self,
        *,
        groups: dict[str, tuple[list[str], str]] | None = None,
        model_label: str = "Omni 1.1 Flash",
        model_options: list[str] | None = None,
        credits: int | None = 7,
        clicks_take_effect: bool = True,
        clickable: bool = True,
    ) -> None:
        self.groups = dict(
            groups
            or {
                "media_type": (["Image", "Video"], "Video"),
                "reference_mode": (["Frames", "Ingredients"], "Ingredients"),
                "aspect_ratio": (["16:9", "9:16"], "9:16"),
                "resolution": (["360p", "720p"], "720p"),
                "duration": (["4s", "6s", "8s"], "4s"),
                "outputs": (["x1", "x2", "x3", "x4"], "x1"),
            }
        )
        self.model_label = model_label
        self.model_options = list(
            model_options or ["Omni 1.1 Flash", "Veo 3.1 - Quality", "Veo 3.1 - Fast"]
        )
        self.credits = credits
        self.clicks_take_effect = clicks_take_effect
        self.clickable = clickable
        self.clicked: list[str] = []

    # -- the two read scripts -------------------------------------------------
    def _settings_payload(self) -> str:
        return json.dumps(
            {
                "open": True,
                "groups": [
                    [
                        {"label": option, "raw": option, "active": option == active}
                        for option in options
                    ]
                    for options, active in self.groups.values()
                ],
                "modelLabel": self.model_label,
                "credits": self.credits,
            }
        )

    def execute_javascript(self, tab, script: str) -> str:
        # The model-list script also mentions tablists (it skips them), so match it first.
        if "omni|veo" in script:
            return json.dumps({"found": True, "items": list(self.model_options)})
        if "data-radix-popper-content-wrapper" in script and "tablist" in script:
            return self._settings_payload()
        if "getBoundingClientRect" in script:
            needle = json.loads(script.split("const needle = ", 1)[1].split(";", 1)[0])
            if not self.clickable:
                return ""
            self.clicked.append(needle)
            if self.clicks_take_effect:
                self._apply(needle)
            return json.dumps({"x": 10.0, "y": 20.0, "text": needle})
        raise AssertionError(f"unexpected script: {script[:70]}")

    def _apply(self, needle: str) -> None:
        for name, (options, _active) in self.groups.items():
            for option in options:
                if option.lower() == needle:
                    self.groups[name] = (options, option)
                    return
        for label in self.model_options:
            if label.lower() == needle:
                self.model_label = label
                return


@pytest.fixture
def composer(monkeypatch: pytest.MonkeyPatch):
    mock = FlowComposerMock()

    def _install(instance: FlowComposerMock) -> FlowComposerMock:
        monkeypatch.setattr(
            "app.automation.existing_chrome.execute_javascript", instance.execute_javascript
        )
        monkeypatch.setattr(
            "app.automation.existing_chrome.dispatch_mouse_click", lambda *a, **k: None
        )
        monkeypatch.setattr("app.automation.existing_chrome.dispatch_key", lambda *a, **k: None)
        monkeypatch.setattr(flow_settings.time, "sleep", lambda _s: None)
        return instance

    mock.install = _install  # type: ignore[attr-defined]
    return _install(mock)


def test_capabilities_are_read_from_the_live_menu(composer: FlowComposerMock) -> None:
    capabilities = flow_settings.read_capabilities(object())
    assert capabilities.model == "gemini_omni_1_1_flash"
    assert capabilities.model_label == "Omni 1.1 Flash"
    assert capabilities.active("resolution") == "720p"
    assert capabilities.options("duration") == ["4s", "6s", "8s"]
    assert capabilities.credits_required == 7


def test_selecting_an_option_verifies_the_control_reports_it(composer: FlowComposerMock) -> None:
    assert flow_settings._select_tab_option(object(), "duration", "6s") == "6s"
    assert composer.groups["duration"][1] == "6s"


def test_a_control_that_does_not_change_raises_instead_of_returning(
    composer: FlowComposerMock,
) -> None:
    """The verifier must not accept 'clicked it' as 'it is selected'."""
    composer.clicks_take_effect = False
    with pytest.raises(OrdaKError) as excinfo:
        flow_settings._select_tab_option(object(), "duration", "6s")
    assert excinfo.value.code is ErrorCode.MODEL_SELECTION_FAILED


def test_a_720p_request_is_refused_when_the_model_only_offers_360p(
    composer: FlowComposerMock,
) -> None:
    composer.groups["resolution"] = (["360p"], "360p")
    with pytest.raises(OrdaKError) as excinfo:
        flow_settings._select_tab_option(object(), "resolution", "720p")
    assert excinfo.value.code is ErrorCode.MODEL_FEATURE_INCOMPATIBLE
    assert composer.groups["resolution"][1] == "360p", "no silent downgrade, and no change"


def test_a_missing_control_is_reported_as_a_capability_fact(composer: FlowComposerMock) -> None:
    composer.groups.pop("resolution")
    with pytest.raises(OrdaKError) as excinfo:
        flow_settings._select_tab_option(object(), "resolution", "720p")
    assert excinfo.value.code is ErrorCode.MODEL_FEATURE_INCOMPATIBLE


def test_an_already_correct_control_is_not_clicked_again(composer: FlowComposerMock) -> None:
    assert flow_settings._select_tab_option(object(), "resolution", "720p") == "720p"
    assert composer.clicked == []


def test_model_selection_verifies_the_label_after_switching(composer: FlowComposerMock) -> None:
    label = flow_settings.select_model(object(), "veo_3_1_quality")
    assert label == "Veo 3.1 - Quality"
    assert flow_settings.identify_model(composer.model_label) == "veo_3_1_quality"


def test_a_model_the_ui_does_not_offer_is_refused(composer: FlowComposerMock) -> None:
    composer.model_options = ["Omni 1.1 Flash"]
    with pytest.raises(OrdaKError) as excinfo:
        flow_settings.select_model(object(), "veo_3_1_quality")
    assert excinfo.value.code is ErrorCode.MODEL_NOT_AVAILABLE
    assert composer.model_label == "Omni 1.1 Flash"


def test_an_unknown_model_never_reaches_the_ui(composer: FlowComposerMock) -> None:
    with pytest.raises(OrdaKError) as excinfo:
        flow_settings.select_model(object(), "best_available")
    assert excinfo.value.code is ErrorCode.MODEL_NOT_AVAILABLE
    assert composer.clicked == []


def test_a_model_that_will_not_switch_raises_selection_failed(composer: FlowComposerMock) -> None:
    composer.clicks_take_effect = False
    with pytest.raises(OrdaKError) as excinfo:
        flow_settings.select_model(object(), "veo_3_1_quality")
    assert excinfo.value.code is ErrorCode.MODEL_SELECTION_FAILED


def test_a_menu_that_never_opens_is_a_ui_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.automation.existing_chrome.execute_javascript",
        lambda tab, script: json.dumps({"open": False}) if "tablist" in script else "",
    )
    monkeypatch.setattr("app.automation.existing_chrome.dispatch_mouse_click", lambda *a, **k: None)
    monkeypatch.setattr(flow_settings.time, "sleep", lambda _s: None)
    with pytest.raises(OrdaKError) as excinfo:
        flow_settings.read_capabilities(object())
    assert excinfo.value.code is ErrorCode.FLOW_UI_CHANGED
