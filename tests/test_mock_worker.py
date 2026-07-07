from __future__ import annotations

from app.automation.extraction import clean_answer_text


def test_clean_answer_text_removes_duplicates() -> None:
    dirty = "Gemini said\n\nسلام\n\n\nسلام\n\nجهان"
    assert clean_answer_text(dirty) == "سلام\n\nجهان"

