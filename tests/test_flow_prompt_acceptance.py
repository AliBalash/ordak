"""Regression coverage for the current Flow editor's visible command prefix."""
from app.automation.flow_worker import _composer_has_prompt


def test_flow_edit_prefix_does_not_hide_an_accepted_prompt() -> None:
    prompt = "Animate the supplied magical storybook exactly as designed."
    assert _composer_has_prompt("Edit\n\n\n" + prompt, prompt)


def test_flow_prompt_verification_rejects_unrelated_stale_text() -> None:
    prompt = "Animate the supplied magical storybook exactly as designed."
    assert not _composer_has_prompt("Old prompt\n" + prompt, prompt)
    assert not _composer_has_prompt("Edit\npartial prompt", prompt)
