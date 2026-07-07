from __future__ import annotations

import random
import time

from playwright.sync_api import Locator, Page


def random_delay(min_ms: int, max_ms: int) -> None:
    time.sleep(random.uniform(min_ms, max_ms) / 1000)


def human_click(locator: Locator, *, min_ms: int = 60, max_ms: int = 180) -> None:
    locator.scroll_into_view_if_needed()
    random_delay(min_ms, max_ms)
    locator.click()
    random_delay(min_ms, max_ms)


def human_paste_text(page: Page, text: str) -> None:
    page.keyboard.insert_text(text)


def human_type_text(
    locator: Locator,
    text: str,
    min_delay_ms: int,
    max_delay_ms: int,
) -> None:
    locator.focus()
    for char in text:
        locator.type(char, delay=random.randint(min_delay_ms, max_delay_ms))
