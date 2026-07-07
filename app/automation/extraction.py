from __future__ import annotations

import json
import re
import time
from pathlib import Path

from playwright.sync_api import Locator, Page

from app.automation import selectors


def get_candidate_response_blocks(page: Page) -> list[Locator]:
    candidates: list[Locator] = []
    for selector in selectors.RESPONSE_BLOCK_SELECTORS:
        locator = page.locator(selector)
        count = min(locator.count(), 12)
        for index in range(count):
            block = locator.nth(index)
            try:
                if block.is_visible() and block.inner_text(timeout=500).strip():
                    candidates.append(block)
            except Exception:
                continue
    return candidates


def clean_answer_text(text: str) -> str:
    text = text.replace("\r\n", "\n").strip()
    text = re.sub(r"^\s*Gemini\s+said\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines: list[str] = []
    previous = None
    last_non_empty = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line and previous == "":
            continue
        if line and (line == previous or line == last_non_empty):
            continue
        lines.append(line)
        previous = line
        if line:
            last_non_empty = line
    return "\n".join(lines).strip()


def get_last_response_text(page: Page, excluded_text: str | None = None) -> str:
    candidates = get_candidate_response_blocks(page)
    for block in reversed(candidates):
        try:
            text = clean_answer_text(block.inner_text(timeout=1_000))
        except Exception:
            continue
        if not text:
            continue
        if excluded_text and clean_answer_text(excluded_text) == text:
            continue
        return text
    raise ValueError("No assistant response block could be extracted.")


def wait_until_response_stable(
    page: Page,
    timeout_ms: int,
    stable_seconds: int,
    excluded_text: str | None = None,
) -> str:
    deadline = time.monotonic() + timeout_ms / 1000
    last_text = ""
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        current_text = ""
        try:
            current_text = get_last_response_text(page, excluded_text=excluded_text)
        except Exception:
            current_text = ""
        if current_text and current_text != last_text:
            last_text = current_text
            stable_since = time.monotonic()
        elif current_text and not selectors.response_is_busy(page):
            if time.monotonic() - stable_since >= stable_seconds:
                return current_text
        time.sleep(1)
    raise TimeoutError("Gemini did not finish response within the timeout.")


def dump_failure_html(page: Page, target: Path) -> Path:
    target.write_text(page.content(), encoding="utf-8")
    return target


def dump_debug_dom(target: Path, payload: dict[str, object]) -> Path:
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
