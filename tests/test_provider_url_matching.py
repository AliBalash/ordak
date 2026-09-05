"""A Flow tab must be recognised as Flow, and never as Gemini.

Before this was fixed the ``flow`` provider fell through to the Gemini branch,
so ``labs.google/fx/tools/flow`` matched nothing while a Gemini tab was
reported as the live Flow session.
"""
from __future__ import annotations

from app.providers.existing_chrome import ExistingChromeProviderAdapter

FLOW_HOME = "https://flow.google.com/"
FLOW_PROJECT = "https://flow.google.com/project/c706a1e6-6246-4050-8ea5-443e2d2fa5a0"
FLOW_LEGACY = "https://labs.google/fx/tools/flow/project/36400b0f-605e-484b-95c5-48e727479dfc"
GEMINI = "https://gemini.google.com/app"
CHATGPT = "https://chatgpt.com/"


def test_flow_matches_its_own_tabs() -> None:
    """Flow answers on flow.google.com now, and labs.google/fx/tools/flow redirects there."""
    adapter = ExistingChromeProviderAdapter("flow")
    assert adapter._matches_provider_url(FLOW_HOME) is True
    assert adapter._matches_provider_url(FLOW_PROJECT) is True
    assert adapter._matches_provider_url(FLOW_LEGACY) is True


def test_flow_does_not_claim_gemini_or_chatgpt_tabs() -> None:
    adapter = ExistingChromeProviderAdapter("flow")
    assert adapter._matches_provider_url(GEMINI) is False
    assert adapter._matches_provider_url(CHATGPT) is False


def test_gemini_does_not_claim_flow_tabs() -> None:
    adapter = ExistingChromeProviderAdapter("gemini")
    assert adapter._matches_provider_url(GEMINI) is True
    assert adapter._matches_provider_url(FLOW_PROJECT) is False
    assert adapter._matches_provider_url(FLOW_LEGACY) is False


def test_chatgpt_matching_unchanged() -> None:
    adapter = ExistingChromeProviderAdapter("chatgpt")
    assert adapter._matches_provider_url(CHATGPT) is True
    assert adapter._matches_provider_url(GEMINI) is False
    assert adapter._matches_provider_url(FLOW_PROJECT) is False


def test_flow_default_url_points_at_flow() -> None:
    assert ExistingChromeProviderAdapter("flow").default_url == FLOW_HOME
    assert ExistingChromeProviderAdapter("gemini").default_url == GEMINI
    assert ExistingChromeProviderAdapter("chatgpt").default_url == CHATGPT


def test_other_labs_google_tools_are_not_flow() -> None:
    adapter = ExistingChromeProviderAdapter("flow")
    assert adapter._matches_provider_url("https://labs.google/fx/tools/whisk") is False
    assert adapter._matches_provider_url("https://labs.google/") is False
