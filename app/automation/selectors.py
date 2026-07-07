from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from playwright.sync_api import Locator, Page


INPUT_SELECTORS = [
    'div[contenteditable="true"]',
    'textarea[aria-label*="prompt" i]',
    'textarea[placeholder*="Enter" i]',
    'textarea',
    '[role="textbox"]',
]

SEND_BUTTON_SELECTORS = [
    'button[aria-label*="send" i]',
    'button[aria-label*="submit" i]',
    'button[data-test-id*="send" i]',
    'button:has(svg)',
]

RESPONSE_BLOCK_SELECTORS = [
    '[data-response-id]',
    '[data-message-author-role="model"]',
    'main article',
    'main [role="article"]',
    'main .markdown',
    'main .model-response',
]

UPLOAD_BUTTON_SELECTORS = [
    'button[aria-label*="Upload" i]',
    'button[aria-label*="tools" i]',
    'button[aria-label*="plus" i]',
    '[data-node-type="input-area"] button',
]

UPLOAD_MENU_SELECTORS = [
    '[role="menuitem"]:has-text("Upload")',
    '[role="menuitem"]:has-text("Photos")',
    '[role="menuitem"]:has-text("Files")',
    'button:has-text("Upload")',
    'button:has-text("Photos")',
    'button:has-text("Files")',
    'button:has-text("From computer")',
]

IMAGE_MODE_SELECTORS = [
    'button:has-text("Create image")',
    '[role="menuitem"]:has-text("Create image")',
    '[role="option"]:has-text("Create image")',
    'button:has-text("Image generation")',
]

LOGIN_REQUIRED_SELECTORS = [
    'text=/Choose an account/i',
    'text=/Continue to Gemini/i',
]

VERIFICATION_SELECTORS = [
    'text=/Verify it.?s you/i',
    'text=/verification required/i',
    'text=/captcha/i',
    'text=/unusual traffic/i',
    'iframe[title*="challenge" i]',
    'input[name="captcha"]',
]

CONSENT_SELECTORS = [
    'text=/Before you continue/i',
    'text=/Review terms/i',
    'button:has-text("I agree")',
    'button:has-text("Accept all")',
]

LOADING_SELECTORS = [
    'button[aria-label*="Stop" i]',
    'button[aria-label*="Cancel" i]',
    '[role="progressbar"]',
    '.loading',
    '.spinner',
]


@dataclass(slots=True)
class PromptCandidate:
    locator: Locator
    source: str
    tag_name: str
    y: float
    width: float


def _iter_role_textboxes(page: Page) -> Iterable[PromptCandidate]:
    locator = page.get_by_role("textbox")
    count = min(locator.count(), 8)
    for index in range(count):
        candidate = locator.nth(index)
        if not candidate.is_visible():
            continue
        box = candidate.bounding_box() or {}
        meta = candidate.evaluate(
            """(element) => ({
                tagName: element.tagName.toLowerCase(),
                contentEditable: element.getAttribute('contenteditable') || '',
            })"""
        )
        yield PromptCandidate(
            locator=candidate,
            source="role=textbox",
            tag_name=meta["tagName"],
            y=box.get("y", 0.0),
            width=box.get("width", 0.0),
        )


def _iter_css_candidates(page: Page) -> Iterable[PromptCandidate]:
    for selector in INPUT_SELECTORS:
        locator = page.locator(selector)
        count = min(locator.count(), 8)
        for index in range(count):
            candidate = locator.nth(index)
            if not candidate.is_visible():
                continue
            box = candidate.bounding_box() or {}
            meta = candidate.evaluate(
                "(element) => ({tagName: element.tagName.toLowerCase()})"
            )
            yield PromptCandidate(
                locator=candidate,
                source=selector,
                tag_name=meta["tagName"],
                y=box.get("y", 0.0),
                width=box.get("width", 0.0),
            )


def find_prompt_input(page: Page) -> PromptCandidate | None:
    candidates = list(_iter_role_textboxes(page)) + list(_iter_css_candidates(page))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.y, item.width), reverse=True)
    return candidates[0]


def find_send_button(page: Page, *, prompt_y: float | None = None) -> Locator | None:
    candidates: list[tuple[float, Locator]] = []
    role_locator = page.get_by_role("button")
    count = min(role_locator.count(), 20)
    for index in range(count):
        candidate = role_locator.nth(index)
        if not candidate.is_visible():
            continue
        label = candidate.inner_text(timeout=500).strip().lower()
        aria = (candidate.get_attribute("aria-label") or "").strip().lower()
        text = f"{label} {aria}"
        if any(token in text for token in ["send", "submit"]):
            box = candidate.bounding_box() or {}
            score = box.get("x", 0.0)
            if prompt_y is not None:
                score -= abs(box.get("y", 0.0) - prompt_y)
            candidates.append((score, candidate))
    for selector in SEND_BUTTON_SELECTORS:
        locator = page.locator(selector)
        count = min(locator.count(), 12)
        for index in range(count):
            candidate = locator.nth(index)
            if candidate.is_visible():
                box = candidate.bounding_box() or {}
                score = box.get("x", 0.0)
                if prompt_y is not None:
                    score -= abs(box.get("y", 0.0) - prompt_y)
                candidates.append((score, candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def find_first_visible(page: Page, selectors: Iterable[str]) -> Locator | None:
    for selector in selectors:
        locator = page.locator(selector)
        count = min(locator.count(), 12)
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
    return None


def has_visible_locator(page: Page, selectors: Iterable[str]) -> bool:
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.first.is_visible():
                return True
        except Exception:
            continue
    return False


def detect_login_or_verification(page: Page) -> str | None:
    prompt_candidate = find_prompt_input(page)
    has_prompt = prompt_candidate is not None
    if has_visible_locator(page, VERIFICATION_SELECTORS):
        return "manual_verification_required"
    if has_visible_locator(page, LOGIN_REQUIRED_SELECTORS):
        return "login_required"
    if has_visible_locator(page, CONSENT_SELECTORS) and not has_prompt:
        return "manual_verification_required"
    if has_visible_locator(page, ['a[href*="/signin"]', 'button:has-text("Sign in")']) and not has_prompt:
        return "login_required"
    return None


def response_is_busy(page: Page) -> bool:
    return has_visible_locator(page, LOADING_SELECTORS)
