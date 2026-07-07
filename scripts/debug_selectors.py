from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.automation.browser import close_browser_context, create_browser_context, take_screenshot
from app.automation.extraction import dump_debug_dom
from app.automation.selectors import INPUT_SELECTORS
from app.config import settings


def collect_nodes(page, selector: str, limit: int = 10) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []
    locator = page.locator(selector)
    count = min(locator.count(), limit)
    for index in range(count):
        item = locator.nth(index)
        if not item.is_visible():
            continue
        box = item.bounding_box() or {}
        nodes.append(
            {
                "selector": selector,
                "text": item.inner_text(timeout=500).strip()[:200],
                "aria_label": item.get_attribute("aria-label"),
                "placeholder": item.get_attribute("placeholder"),
                "box": box,
            }
        )
    return nodes


def main() -> None:
    session = create_browser_context(settings, job_id="debug-selectors")
    try:
        context = session.context
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(settings.gemini_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            page.wait_for_timeout(2_000)

        debug_payload = {
            "url": page.url,
            "textboxes": collect_nodes(page, '[role="textbox"]'),
            "buttons": collect_nodes(page, "button"),
            "contenteditable": collect_nodes(page, '[contenteditable="true"]'),
            "input_selectors": INPUT_SELECTORS,
        }
        target = settings.browser_log_dir / "debug_dom.json"
        dump_debug_dom(target, debug_payload)
        screenshot_path = take_screenshot(page, "debug", "selector_debug", settings)

        print(f"Saved simplified DOM info to {target}")
        print(f"Saved screenshot to {screenshot_path}")
        print("Visible textboxes:")
        for entry in debug_payload["textboxes"]:
            print(entry)
        print("Visible buttons:")
        for entry in debug_payload["buttons"]:
            print(entry)
    finally:
        close_browser_context(session)


if __name__ == "__main__":
    main()
