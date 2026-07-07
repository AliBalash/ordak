from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal

from app.artifacts import slugify_filename


ProviderName = Literal["gemini", "chatgpt"]


PROVIDER_LABELS: dict[ProviderName, str] = {
    "gemini": "Gemini",
    "chatgpt": "ChatGPT",
}

GENERATED_IMAGE_SELECTORS = [
    "img",
    "picture img",
    "generated-image img",
    ".generated-images-container img",
    ".image-gallery img",
]

PROMPT_SELECTORS: dict[ProviderName, list[str]] = {
    "gemini": [
        '.ql-editor[contenteditable="true"][role="textbox"][aria-label*="Gemini"]',
        '[data-test-id="textarea-inner"] .ql-editor[contenteditable="true"]',
        "rich-textarea .ql-editor[contenteditable=\"true\"]",
        '[role="textbox"][aria-label*="Gemini"]',
        'div[contenteditable="true"][role="textbox"]',
        "textarea",
    ],
    "chatgpt": [
        "#prompt-textarea",
        'div#prompt-textarea[contenteditable="true"]',
        '[data-testid="prompt-textarea"]',
        'div[contenteditable="true"][role="textbox"][aria-label*="ChatGPT"]',
        'textarea[placeholder*="Message"]',
        "textarea",
    ],
}


def _assistant_root_selectors(provider: ProviderName) -> list[str]:
    if provider == "chatgpt":
        return [
            '[data-message-author-role="assistant"]',
            'article[data-testid^="conversation-turn-"] [data-message-author-role="assistant"]',
            'main [data-message-author-role="assistant"]',
            "main article",
            "main .markdown",
            "main .prose",
        ]
    return [
        "[data-response-id]",
        '[data-message-author-role="model"]',
        "main article",
        "main .model-response",
        "main .markdown",
        "main .prose",
    ]


def _image_loading_selectors(provider: ProviderName) -> list[str]:
    if provider == "chatgpt":
        return [
            '[aria-busy="true"]',
            '[role="progressbar"]',
            '[role="status"]',
            ".animate-spin",
            '[data-testid*="loading" i]',
            '[data-testid*="generating" i]',
            '[data-testid*="spinner" i]',
        ]
    return [
        '[aria-busy="true"]',
        '[role="progressbar"]',
        '[role="status"]',
        "mat-spinner",
        "md-progress-circular",
        '[data-test-id*="loading" i]',
        '[data-test-id*="spinner" i]',
    ]


@dataclass(slots=True)
class ChromeTabRef:
    window_id: int
    tab_id: int


@dataclass(slots=True)
class ChromeTabInfo:
    window_id: int
    tab_id: int
    url: str
    title: str
    active: bool = False

    @property
    def ref(self) -> ChromeTabRef:
        return ChromeTabRef(window_id=self.window_id, tab_id=self.tab_id)


def _run_osascript(script: str, *args: str) -> str:
    command = ["osascript", "-"] + list(args)
    result = subprocess.run(
        command,
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "osascript failed").strip()
        if "Executing JavaScript through AppleScript is turned off" in message:
            raise RuntimeError(
                "Google Chrome blocks JavaScript from Apple Events. In Chrome enable View > Developer > Allow JavaScript from Apple Events, then retry."
            )
        raise RuntimeError(message)
    return result.stdout.strip()


def is_google_chrome_running() -> bool:
    script = """
on run argv
    tell application "System Events"
        if exists process "Google Chrome" then
            return "true"
        end if
        return "false"
    end tell
end run
"""
    return _run_osascript(script) == "true"


def list_google_chrome_tabs() -> list[ChromeTabInfo]:
    script = """
on run argv
    tell application "Google Chrome"
        if not running then return "[]"
        set tabItems to {}
        repeat with targetWindow in windows
            set windowIdText to (id of targetWindow) as string
            set activeIndex to active tab index of targetWindow
            set tabCounter to 0
            repeat with targetTab in tabs of targetWindow
                set tabCounter to tabCounter + 1
                set tabIdText to (id of targetTab) as string
                set tabUrl to URL of targetTab
                set tabTitle to title of targetTab
                set isActive to (tabCounter is activeIndex)
                set end of tabItems to windowIdText & "||" & tabIdText & "||" & tabUrl & "||" & tabTitle & "||" & (isActive as string)
            end repeat
        end repeat
        return my joinLines(tabItems)
    end tell
end run

on joinLines(itemsList)
    set AppleScript's text item delimiters to linefeed
    set joinedText to itemsList as string
    set AppleScript's text item delimiters to ""
    return joinedText
end joinLines
"""
    raw = _run_osascript(script)
    if not raw.strip():
        return []
    tabs: list[ChromeTabInfo] = []
    for line in raw.splitlines():
        parts = line.split("||", 4)
        if len(parts) != 5:
            continue
        window_id, tab_id, url, title, active = parts
        tabs.append(
            ChromeTabInfo(
                window_id=int(window_id),
                tab_id=int(tab_id),
                url=url,
                title=title,
                active=active.strip().lower() == "true",
            )
        )
    return tabs


def get_tab_info(tab: ChromeTabRef) -> ChromeTabInfo | None:
    for candidate in list_google_chrome_tabs():
        if candidate.window_id == tab.window_id and candidate.tab_id == tab.tab_id:
            return candidate
    return None


def open_url_in_existing_chrome(target_url: str) -> ChromeTabRef:
    script = """
on run argv
    set targetUrl to item 1 of argv
    tell application "Google Chrome"
        if not running then error "Google Chrome is not running."
        if (count of windows) = 0 then error "No Google Chrome window is open."
        activate
        set targetWindow to front window
        tell targetWindow
            make new tab with properties {URL:targetUrl}
            set active tab index to (count of tabs)
            set targetTab to active tab
            return (id as string) & ":" & (id of targetTab as string)
        end tell
    end tell
end run
"""
    raw = _run_osascript(script, target_url)
    window_id, tab_id = raw.split(":")
    return ChromeTabRef(window_id=int(window_id), tab_id=int(tab_id))


def open_provider_tab_in_existing_chrome(provider: ProviderName, target_url: str) -> ChromeTabRef:
    return open_url_in_existing_chrome(target_url)


def open_gemini_tab_in_existing_chrome(gemini_url: str) -> ChromeTabRef:
    return open_url_in_existing_chrome(gemini_url)


def execute_javascript(tab: ChromeTabRef, javascript: str) -> str:
    script = """
on run argv
    set targetWindowId to (item 1 of argv) as integer
    set targetTabId to item 2 of argv
    set jsCode to item 3 of argv
    tell application "Google Chrome"
        set targetWindow to first window whose id is targetWindowId
        tell targetWindow
            repeat with targetTab in tabs
                if ((id of targetTab) as string) is targetTabId then
                    tell targetTab
                        return execute javascript jsCode
                    end tell
                end if
            end repeat
        end tell
    end tell
    error "Could not find the requested Google Chrome tab."
end run
"""
    return _run_osascript(
        script,
        str(tab.window_id),
        str(tab.tab_id),
        javascript,
    )


def wait_for_prompt_input(
    tab: ChromeTabRef,
    timeout_ms: int,
    *,
    provider: ProviderName = "gemini",
) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    probe = """
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const selectors = __SELECTORS__;
  const found = selectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .some((el) => isVisible(el));
  return found ? "ready" : "waiting";
})()
"""
    probe = probe.replace(
        "__SELECTORS__", json.dumps(PROMPT_SELECTORS[provider], ensure_ascii=False)
    )
    while time.monotonic() < deadline:
        if execute_javascript(tab, probe) == "ready":
            return
        time.sleep(1)
    raise TimeoutError(
        f"Could not find {PROVIDER_LABELS[provider]} input box in the current Chrome tab."
    )


def detect_login_or_verification(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> str | None:
    probe = """
(() => {
  const bodyText = (document.body?.innerText || "").toLowerCase();
  const promptSelectors = __SELECTORS__;
  const prompt = promptSelectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .find((element) => !!element);
  const hasPrompt = !!prompt;
  if (/verify|captcha|unusual traffic|human verification|prove you are human/.test(bodyText)) {
    return "manual_verification_required";
  }
  if (!hasPrompt && /sign in|log in|choose an account|continue to gemini|continue with google|continue with apple|welcome back|sign up/.test(bodyText)) {
    return "login_required";
  }
  return "";
})()
"""
    probe = probe.replace(
        "__SELECTORS__", json.dumps(PROMPT_SELECTORS[provider], ensure_ascii=False)
    )
    state = execute_javascript(tab, probe).strip()
    return state or None


def activate_create_image_mode(
    tab: ChromeTabRef,
    timeout_ms: int = 20_000,
    *,
    provider: ProviderName = "gemini",
) -> bool:
    label_pattern = (
        r"create image|image generation"
        if provider == "gemini"
        else r"create image|generate image|4o image|image"
    )
    menu_selectors = (
        ['[data-test-id="bard-mode-menu-button"]', 'button[aria-haspopup="menu"]']
        if provider == "gemini"
        else ['button[aria-haspopup="menu"]', '#composer-plus-btn', 'button[aria-label*="tools" i]', 'button[aria-label*="more" i]']
    )
    script = """
(() => {
  const isVisible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const clickLikeUser = (element) => {
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {
      element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    });
  };
  const direct = Array.from(document.querySelectorAll('button, [role="button"], [role="menuitem"], [role="option"]'))
    .find((el) => isVisible(el) && /__LABEL_PATTERN__/.test(`${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase()));
  if (direct) {
    clickLikeUser(direct);
    return "clicked";
  }
  const menuSelectors = __MENU_SELECTORS__;
  const modeButton = menuSelectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .find((el) => isVisible(el));
  if (modeButton) {
    clickLikeUser(modeButton);
    return "menu-opened";
  }
  return "not-found";
})()
"""
    script = script.replace("__LABEL_PATTERN__", label_pattern).replace(
        "__MENU_SELECTORS__", json.dumps(menu_selectors, ensure_ascii=False)
    )
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        result = execute_javascript(tab, script).strip()
        if result == "clicked":
            return True
        time.sleep(0.8)
    return False


def upload_local_file(
    tab: ChromeTabRef,
    *,
    file_path: Path,
    file_name: str,
    mime_type: str,
    timeout_ms: int = 90_000,
    provider: ProviderName = "gemini",
) -> None:
    payload_name = json.dumps(file_name, ensure_ascii=False)
    payload_mime = json.dumps(mime_type, ensure_ascii=False)
    payload_provider = json.dumps(provider, ensure_ascii=False)
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    chunk_size = 60_000
    chunks = [encoded[index : index + chunk_size] for index in range(0, len(encoded), chunk_size)]

    execute_javascript(
        tab,
        """
(() => {
  window.__codexUploadChunks = [];
  window.__codexUploadStatus = "preparing";
  return "ok";
})()
""",
    )
    for chunk in chunks:
        execute_javascript(
            tab,
            f"""
(() => {{
  window.__codexUploadChunks = window.__codexUploadChunks || [];
  window.__codexUploadChunks.push({json.dumps(chunk)});
  return "ok";
}})()
""",
        )

    bootstrap = f"""
(() => {{
  const fileName = {payload_name};
  const mimeType = {payload_mime};
  const provider = {payload_provider};
  window.__codexUploadStatus = "pending";
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const getDropTarget = () => {{
    const selectors = provider === "chatgpt"
      ? ["form", "#prompt-textarea", '[data-testid="composer"]', 'main']
      : ['[xapfileselectordropzone]', '.xap-uploader-dropzone', '[file-drop-zone]', '[data-node-type="input-area"]'];
    return selectors
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .find((el) => isVisible(el)) || null;
  }};
  const setFilesOnInput = (file) => {{
    const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
    for (const input of inputs) {{
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(file);
      input.files = dataTransfer.files;
      if (!input.files || input.files.length < 1) continue;
      input.dispatchEvent(new Event('input', {{ bubbles: true, composed: true }}));
      input.dispatchEvent(new Event('change', {{ bubbles: true, composed: true }}));
      const dropTarget = getDropTarget();
      if (provider !== "chatgpt" && dropTarget) {{
        for (const type of ["dragenter", "dragover", "drop"]) {{
          dropTarget.dispatchEvent(new DragEvent(type, {{ bubbles: true, cancelable: true, dataTransfer }}));
        }}
      }}
      return true;
    }}
    return false;
  }};
  const triggerUploadMenu = () => {{
    const trigger = Array.from(document.querySelectorAll('button, [role="button"]')).find((el) => {{
      if (!isVisible(el) || el.disabled) return false;
      const text = `${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase();
      return /upload & tools|upload|add files|files|photos|image/.test(text);
    }});
    if (!trigger) return false;
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {{
      trigger.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window }}));
    }});
    return true;
  }};
  const clickUploadFilesItem = () => {{
    const action = Array.from(document.querySelectorAll('button, [role="button"], [role="menuitem"]')).find((el) => {{
      if (!isVisible(el) || el.disabled) return false;
      const text = `${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase();
      return provider === "chatgpt"
        ? /upload files|upload from computer|photos|files|add photos/.test(text)
        : /upload files/.test(text);
    }});
    if (!action) return false;
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {{
      action.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window }}));
    }});
    return true;
  }};
  const dispatchPaste = (file) => {{
    const target = document.querySelector('.ql-editor[role="textbox"], [role="textbox"], div[contenteditable="true"], textarea') || getDropTarget();
    if (!target) return false;
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    const event = new ClipboardEvent("paste", {{ bubbles: true, cancelable: true }});
    Object.defineProperty(event, "clipboardData", {{ value: dataTransfer }});
    target.dispatchEvent(event);
    return true;
  }};
  const dispatchDrop = async (file) => {{
    const target = getDropTarget();
    if (!target) return false;
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    for (const type of ["dragenter", "dragover", "drop"]) {{
      target.dispatchEvent(new DragEvent(type, {{ bubbles: true, cancelable: true, dataTransfer }}));
    }}
    return true;
  }};
  const uploadLooksAttached = () => {{
    const body = document.body?.innerText || "";
    const fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
    if (fileInputs.some((input) => input.files && input.files.length > 0)) return true;
    if (body.includes(fileName)) return true;
    if (document.querySelector('img[src^="blob:"]')) return true;
    if (document.querySelector('[data-test-id*="upload" i], [data-testid*="upload" i], [data-test-id*="attachment" i], [data-testid*="attachment" i]')) return true;
    if (provider === "chatgpt" && document.querySelector('#prompt-textarea img, form img, [data-testid="composer-plus-btn"] + * img')) return true;
    return false;
  }};
  (async () => {{
    try {{
      const base64 = (window.__codexUploadChunks || []).join("");
      if (!base64) throw new Error("Upload content buffer is empty.");
      const binary = atob(base64);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {{
        bytes[index] = binary.charCodeAt(index);
      }}
      const file = new File([bytes], fileName, {{ type: mimeType || "application/octet-stream" }});
      let attached = setFilesOnInput(file);
      if (!attached) {{
        triggerUploadMenu();
        await wait(800);
        attached = setFilesOnInput(file);
      }}
      if (!attached) {{
        clickUploadFilesItem();
        await wait(800);
        attached = setFilesOnInput(file);
      }}
      if (!attached) {{
        attached = dispatchPaste(file);
        await wait(600);
      }}
      if (!attached) {{
        attached = setFilesOnInput(file);
      }}
      if (!attached) {{
        const dropped = await dispatchDrop(file);
        if (!dropped) throw new Error("Could not find an upload target in the current Chrome tab.");
      }}
      await wait(1500);
      if (!uploadLooksAttached()) {{
        throw new Error("The current chat page did not acknowledge the uploaded image.");
      }}
      window.__codexUploadStatus = "attached";
      window.__codexUploadChunks = [];
    }} catch (error) {{
      const message = error instanceof Error ? error.message : String(error);
      window.__codexUploadStatus = `error:${{message}}`;
      window.__codexUploadChunks = [];
    }}
  }})();
  return "started";
}})()
"""
    result = execute_javascript(tab, bootstrap)
    if result != "started":
        raise RuntimeError("Could not start image upload in the current Chrome tab.")

    deadline = time.monotonic() + timeout_ms / 1000
    ready_checks = 0
    while time.monotonic() < deadline:
        state = execute_javascript(
            tab,
            """
(() => window.__codexUploadStatus || "pending")()
""",
        ).strip()
        if state.startswith("error:"):
            raise RuntimeError(state.removeprefix("error:").strip() or "Image upload failed.")
        if state in {"attached", "done"}:
            upload_state = inspect_upload_state(tab, provider=provider)
            if (
                upload_state.get("attachment")
                and upload_state.get("hasPreview")
                and not upload_state.get("loading")
                and upload_state.get("submitReady", True)
            ):
                ready_checks += 1
                if ready_checks >= 2:
                    execute_javascript(
                        tab,
                        """
(() => {
  window.__codexUploadStatus = "done";
  return "ok";
})()
""",
                    )
                    return
            else:
                ready_checks = 0
        time.sleep(1)
    raise TimeoutError("Timed out while waiting for image upload in the current Chrome tab.")


def inspect_upload_state(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> dict[str, Any]:
    readiness_probe = """
(() => {
  const isVisible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const provider = __PROVIDER__;
  const attachmentSelectors = provider === "chatgpt"
    ? ['img[src^="blob:"]', 'form img', '[data-testid*="attachment" i]', '[data-testid*="composer" i] img']
    : ['gem-media-attachment', '.gem-attachment', 'mat-basic-chip', '[data-test-id*="upload" i]'];
  const loadingSelectors = provider === "chatgpt"
    ? ['[role="status"]', '.animate-spin', '[data-testid*="uploading" i]', '[data-testid="send-button"][disabled]']
    : ['.gem-attachment-content.loading', '.gem-attachment-loading-container', 'mat-spinner[aria-label="Loading image"]'];
  const attachment = attachmentSelectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .find((el) => isVisible(el));
  const loadingBySelector = loadingSelectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .some((el) => {
      if (!isVisible(el)) return false;
      if (provider === "chatgpt" && el.matches('[data-testid="send-button"][disabled]')) return true;
      return /upload|loading|processing/.test(`${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase());
    });
  const loadingByCursor = provider === "chatgpt" && Array.from(document.querySelectorAll('*')).some((el) => {
    if (!isVisible(el)) return false;
    const className = (el.className || '').toString();
    return className.includes('cursor-wait');
  });
  const loading = !!loadingBySelector || !!loadingByCursor;
  const hasPreview = !!Array.from(document.querySelectorAll('img')).find((img) => {
    const src = img.src || '';
    return isVisible(img) && ((src.startsWith('blob:')) || (provider === "chatgpt" && img.naturalWidth >= 48 && img.naturalHeight >= 48));
  });
  const sendButton = Array.from(document.querySelectorAll('button, [role="button"]'))
    .find((el) => {
      if (!isVisible(el)) return false;
      const text = `${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase();
      return /send message|send prompt|send|submit/.test(text) || el.getAttribute("data-testid") === "send-button";
    });
  return JSON.stringify({
    attachment: !!attachment,
    loading,
    hasPreview,
    submitReady: !!sendButton && !sendButton.disabled,
  });
})()
"""
    readiness_probe = readiness_probe.replace(
        "__PROVIDER__", json.dumps(provider, ensure_ascii=False)
    )
    return json.loads(execute_javascript(tab, readiness_probe) or "{}")


def insert_prompt(
    tab: ChromeTabRef,
    prompt: str,
    *,
    provider: ProviderName = "gemini",
) -> None:
    payload = json.dumps(prompt, ensure_ascii=False)
    script = f"""
(() => {{
  const text = {payload};
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const selectors = {json.dumps(PROMPT_SELECTORS[provider], ensure_ascii=False)};
  const candidates = selectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .filter((el, index, arr) => arr.indexOf(el) === index)
    .filter((el) => isVisible(el))
    .sort((a, b) => b.getBoundingClientRect().height - a.getBoundingClientRect().height);
  const target = candidates[0];
  if (!target) return "missing";
  target.focus();
  const dispatch = (type, extra = {{}}) => target.dispatchEvent(new InputEvent(type, {{ bubbles: true, cancelable: true, ...extra }}));
  if (target.tagName === "TEXTAREA" || target.tagName === "INPUT") {{
    target.value = text;
    target.dispatchEvent(new Event("input", {{ bubbles: true }}));
    target.dispatchEvent(new Event("change", {{ bubbles: true }}));
  }} else {{
    const selection = window.getSelection();
    if (selection) {{
      const range = document.createRange();
      range.selectNodeContents(target);
      selection.removeAllRanges();
      selection.addRange(range);
    }}
    document.execCommand("selectAll", false);
    const inserted = document.execCommand("insertText", false, text);
    if (!inserted || (target.innerText || "").trim() !== text.trim()) {{
      target.innerHTML = "";
      const lines = text.split("\\n");
      lines.forEach((line) => {{
        const p = document.createElement("p");
        if (line) {{
          p.textContent = line;
        }} else {{
          p.appendChild(document.createElement("br"));
        }}
        target.appendChild(p);
      }});
    }}
    dispatch("beforeinput", {{ data: text, inputType: "insertText" }});
    dispatch("input", {{ data: text, inputType: "insertText" }});
    target.dispatchEvent(new KeyboardEvent("keyup", {{ key: "Unidentified", bubbles: true }}));
  }}
  const normalize = (value) => (value || "").replace(/\\r\\n/g, "\\n").replace(/\\n+/g, "\\n").trim();
  const finalText = normalize(target.innerText || target.value || "");
  const expected = normalize(text);
  return finalText === expected || finalText.includes(expected) ? "ok" : `mismatch:${{finalText}}`;
}})()
"""
    result = execute_javascript(tab, script)
    if result == "ok":
        return

    verify = execute_javascript(
        tab,
        f"""
(() => {{
  const selectors = {json.dumps(PROMPT_SELECTORS[provider], ensure_ascii=False)};
  const target = selectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .find((element) => !!element);
  if (!target) return "missing";
  const normalize = (value) => (value || "").replace(/\\r\\n/g, "\\n").replace(/\\n+/g, "\\n").trim();
  const current = normalize(target.innerText || target.value || "");
  const expected = normalize({payload});
  return current === expected || current.includes(expected) ? "ok" : current;
}})()
""",
    )
    if verify != "ok":
        raise RuntimeError(
            f"Could not insert prompt into {PROVIDER_LABELS[provider]} in the current Chrome tab."
        )


def submit_prompt(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> None:
    provider_selectors = json.dumps(PROMPT_SELECTORS[provider], ensure_ascii=False)
    script = """
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter((el) => isVisible(el));
  const sendButton = buttons.find((el) => {
    const text = `${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase();
    return /send message|send prompt|send|submit/.test(text) || el.getAttribute("data-testid") === "send-button";
  });
  const selectors = __SELECTORS__;
  const target = selectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .find((element) => !!element);
  const busyBefore = buttons.some((el) => /stop|cancel/.test(`${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase()));
  if (sendButton && !sendButton.disabled) {
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {
      sendButton.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    });
    return "clicked";
  }
  if (!target) return "missing";
  target.focus();
  ["keydown", "keypress", "keyup"].forEach((type) => {
    target.dispatchEvent(new KeyboardEvent(type, {
      key: "Enter",
      code: "Enter",
      keyCode: 13,
      which: 13,
      bubbles: true,
      cancelable: true,
    }));
  });
  const busyAfterEnter = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((el) => isVisible(el))
    .some((el) => /stop|cancel/.test(`${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase()));
  const promptText = (target.innerText || target.value || "").trim();
  if (busyAfterEnter || promptText === "") {
    return "enter";
  }
  return busyBefore ? "busy" : "not_submitted";
})()
"""
    script = script.replace("__SELECTORS__", provider_selectors)
    deadline = time.monotonic() + 12
    last_result = "not_submitted"
    while time.monotonic() < deadline:
        result = execute_javascript(tab, script)
        last_result = result or "not_submitted"
        if last_result in {"clicked", "enter", "busy"}:
            return
        time.sleep(0.75)
    raise RuntimeError(
        f"Could not submit the {PROVIDER_LABELS[provider]} prompt in the current Chrome tab. Last submit state: {last_result}."
    )


def detect_busy_state(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> bool:
    script = """
(() => {
  const isVisible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const busy = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((el) => isVisible(el))
    .some((el) => {
      const text = `${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase().trim();
      if (/stopped thinking/.test(text)) return false;
      return /stop answering|stop generating|stop|cancel/.test(text);
    });
  return busy ? "true" : "false";
})()
"""
    return execute_javascript(tab, script).strip() == "true"


def best_effort_stop(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> bool:
    script = """
(() => {
  const isVisible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const target = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((el) => isVisible(el))
    .find((el) => {
      const text = `${el.innerText || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase().trim();
      if (/stopped thinking/.test(text)) return false;
      return /stop answering|stop generating|stop|cancel/.test(text);
    });
  if (!target) return "false";
  ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {
    target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
  });
  return "true";
})()
"""
    return execute_javascript(tab, script).strip() == "true"


def save_tab_html(tab: ChromeTabRef, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = execute_javascript(
        tab,
        """
(() => document.documentElement?.outerHTML || "")()
""",
    )
    output_path.write_text(html or "", encoding="utf-8")
    return output_path


def inspect_generated_image_state(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
) -> dict[str, Any]:
    script = f"""
(() => {{
  const assistantRootSelectors = {json.dumps(_assistant_root_selectors(provider), ensure_ascii=False)};
  const loadingSelectors = {json.dumps(_image_loading_selectors(provider), ensure_ascii=False)};
  const generatedImageSelectors = {json.dumps(GENERATED_IMAGE_SELECTORS, ensure_ascii=False)};
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const collectAssistantRoots = () => {{
    const candidates = assistantRootSelectors
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((el) => isVisible(el));
    const unique = [];
    const seen = new Set();
    for (const candidate of candidates) {{
      const root = candidate.closest('article[data-testid^="conversation-turn-"]')
        || candidate.closest("article")
        || candidate;
      if (!root || !isVisible(root) || seen.has(root)) continue;
      seen.add(root);
      unique.push(root);
    }}
    return unique.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  }};
  const assistantRoots = collectAssistantRoots();
  const latestAssistantRoot = [...assistantRoots]
    .reverse()
    .find((root) => generatedImageSelectors
      .flatMap((selector) => Array.from(root.querySelectorAll(selector)))
      .some((img) => isVisible(img) && (img.naturalWidth || img.width || 0) >= 96 && (img.naturalHeight || img.height || 0) >= 96))
    || assistantRoots.at(-1)
    || null;
  const scope = latestAssistantRoot || document;
  const rootText = `${{latestAssistantRoot?.innerText || ""}}`.toLowerCase();
  const assistantImages = generatedImageSelectors
    .flatMap((selector) => Array.from(scope.querySelectorAll(selector)))
    .filter((img) => isVisible(img) && (img.naturalWidth || img.width || 0) >= 96 && (img.naturalHeight || img.height || 0) >= 96)
    .filter((img) => !img.closest('[data-message-author-role="user"]'));
  const downloadAffordance = Array.from(scope.querySelectorAll('a, button, [role="button"], [role="menuitem"]'))
    .filter((el) => isVisible(el))
    .some((el) => /download|save image|open image/.test(`${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase()));
  const generatedMarker = downloadAffordance
    || /generated image|created with|open image|download image|image ready/.test(rootText)
    || assistantImages.some((img) => /generated image/.test((img.alt || "").toLowerCase()));
  const loading = loadingSelectors
    .flatMap((selector) => Array.from(scope.querySelectorAll(selector)))
    .some((el) => isVisible(el) && /loading|creating|generating|rendering|processing/.test(`${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase() || "loading"))
    || (!!latestAssistantRoot && latestAssistantRoot.getAttribute("aria-busy") === "true");
  const busy = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((el) => isVisible(el))
    .some((el) => {{
      const text = `${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase().trim();
      if (/stopped thinking/.test(text)) return false;
      return /stop answering|stop generating|stop|cancel/.test(text);
    }});
  return JSON.stringify({{
    assistantImageCount: assistantImages.length,
    generatedMarker,
    downloadAffordance,
    loading,
    busy,
  }});
}})()
"""
    return json.loads(execute_javascript(tab, script) or "{}")


def wait_for_generated_image_ready(
    tab: ChromeTabRef,
    *,
    provider: ProviderName = "gemini",
    timeout_ms: int = 90_000,
) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    stable_checks = 0
    while time.monotonic() < deadline:
        state = inspect_generated_image_state(tab, provider=provider)
        has_images = int(state.get("assistantImageCount") or 0) > 0
        is_ready = has_images and not state.get("loading") and not state.get("busy")
        if is_ready:
            stable_checks += 1
            if stable_checks >= 2:
                return True
        else:
            stable_checks = 0
        time.sleep(1)
    return False


def _read_generated_image_export_payloads(
    tab: ChromeTabRef,
    *,
    output_dir: Path,
    job_id: str,
    timeout_ms: int,
) -> list[Path]:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        state = execute_javascript(
            tab,
            """
(() => window.__codexGeneratedImageExport?.status || "pending")()
""",
        ).strip()
        if state == "done":
            break
        if state.startswith("error:"):
            raise RuntimeError(state.removeprefix("error:").strip() or "Generated image export failed.")
        time.sleep(1)
    else:
        raise TimeoutError("Timed out while exporting generated images from the current Chrome tab.")

    metadata = json.loads(
        execute_javascript(
            tab,
            """
(() => JSON.stringify(window.__codexGeneratedImageExport?.items || []))()
""",
        )
        or "[]"
    )
    saved_paths: list[Path] = []
    chunk_size = 50_000
    seen_payload_hashes: set[bytes] = set()
    for index, item in enumerate(metadata):
        size = int(item.get("size") or 0)
        if size < 1:
            continue
        data_url_parts: list[str] = []
        for start in range(0, size, chunk_size):
            chunk = execute_javascript(
                tab,
                f"""
(() => (window.__codexGeneratedImageExport?.payloads?.[{index}]?.data || "").slice({start}, {start + chunk_size}))()
""",
            )
            if not chunk:
                break
            data_url_parts.append(chunk)
        data_url = "".join(data_url_parts)
        if not data_url.startswith("data:") or "," not in data_url:
            continue
        header, encoded = data_url.split(",", 1)
        mime = item.get("mime") or header.split(";")[0].removeprefix("data:") or "image/png"
        payload_bytes = base64.b64decode(encoded)
        if payload_bytes in seen_payload_hashes:
            continue
        seen_payload_hashes.add(payload_bytes)
        suffix = ".jpg" if "jpeg" in mime else ".webp" if "webp" in mime else ".png"
        file_name = slugify_filename(f"{job_id}_output_{index + 1}{suffix}")
        output_path = output_dir / file_name
        output_path.write_bytes(payload_bytes)
        saved_paths.append(output_path)

    execute_javascript(
        tab,
        """
(() => {
  window.__codexGeneratedImageExport = { status: "cleared", items: [], payloads: [] };
  return "ok";
})()
""",
    )
    return saved_paths


def export_generated_images(
    tab: ChromeTabRef,
    *,
    output_dir: Path,
    job_id: str,
    provider: ProviderName = "gemini",
    max_images: int = 4,
    timeout_ms: int = 90_000,
    strategy: Literal["download", "asset_url", "dom"] = "dom",
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    assistant_root_selectors = _assistant_root_selectors(provider)
    bootstrap = f"""
(() => {{
  window.__codexGeneratedImageExport = {{ status: "pending", items: [], payloads: [] }};
  const strategy = {json.dumps(strategy)};
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const readAsDataUrl = (blob) => new Promise((resolve, reject) => {{
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("Could not read generated image blob."));
    reader.readAsDataURL(blob);
  }});
  const exportUrl = async (source, index, forcedStem = null) => {{
    if (!source) return null;
    if ((strategy === "download" || strategy === "asset_url") && /^(data:|blob:)/.test(source)) return null;
    try {{
      const response = await fetch(source);
      const blob = await response.blob();
      const mime = blob.type || "image/png";
      const ext = mime.includes("jpeg") ? "jpg" : mime.includes("webp") ? "webp" : "png";
      const data = await readAsDataUrl(blob);
      return {{ name: `${{forcedStem || `generated_${{index + 1}}`}}.${{ext}}`, mime, data }};
    }} catch (_error) {{}}
    return null;
  }};
  const exportImage = async (img, index) => {{
    const source = img.currentSrc || img.src || "";
    if (!source) return null;
    const exported = await exportUrl(source, index);
    if (exported) return exported;
    if (strategy !== "dom") return null;
    try {{
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth || img.width || 0;
      canvas.height = img.naturalHeight || img.height || 0;
      const ctx = canvas.getContext("2d");
      if (!ctx || !canvas.width || !canvas.height) return null;
      ctx.drawImage(img, 0, 0);
      const data = canvas.toDataURL("image/png");
      return {{ name: `generated_${{index + 1}}.png`, mime: "image/png", data }};
    }} catch (_error) {{
      return null;
    }}
  }};
  const assistantRootSelectors = {json.dumps(assistant_root_selectors, ensure_ascii=False)};
  const collectAssistantRoots = () => {{
    const candidates = assistantRootSelectors
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((el) => isVisible(el));
    const unique = [];
    const seen = new Set();
    for (const candidate of candidates) {{
      const root = candidate.closest('article[data-testid^="conversation-turn-"]')
        || candidate.closest('article')
        || candidate;
      if (!root || !isVisible(root) || seen.has(root)) continue;
      seen.add(root);
      unique.push(root);
    }}
    return unique.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  }};
  (async () => {{
    try {{
      const generatedImageSelectors = {json.dumps(GENERATED_IMAGE_SELECTORS, ensure_ascii=False)};
      const assistantRoots = collectAssistantRoots();
      const latestAssistantRoot = [...assistantRoots]
        .reverse()
        .find((root) => generatedImageSelectors.some((selector) => Array.from(root.querySelectorAll(selector)).some((img) => isVisible(img))))
        || assistantRoots.at(-1)
        || null;
      const imageScope = latestAssistantRoot || document;
      const rootText = `${{latestAssistantRoot?.innerText || ""}}`.toLowerCase();
      const downloadAffordance = Array.from(imageScope.querySelectorAll('a, button, [role="button"], [role="menuitem"]'))
        .filter((el) => isVisible(el))
        .some((el) => /download|save image|open image/.test(`${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase()));
      const downloadLinks = Array.from(imageScope.querySelectorAll('a[href]'))
        .map((link) => {{
          const href = link.href || "";
          const text = `${{link.innerText || ""}} ${{link.getAttribute("aria-label") || ""}}`.toLowerCase();
          return {{
            href,
            text,
            visible: isVisible(link),
            generatedHint:
              /download|save image|open image|generated image/.test(text)
              || /[?&](download|format)=/i.test(href)
              || /\\.(png|jpe?g|webp)(\\?|#|$)/i.test(href),
          }};
        }})
        .filter((item) => item.href && item.visible)
        .sort((a, b) => Number(b.generatedHint) - Number(a.generatedHint));
      const candidates = generatedImageSelectors
        .flatMap((selector) => Array.from(imageScope.querySelectorAll(selector)))
        .filter((img) => isVisible(img) && img.naturalWidth >= 160 && img.naturalHeight >= 160)
        .filter((img) => !img.closest('[data-message-author-role="user"]'))
        .map((img) => ({{
          img,
          src: img.currentSrc || img.src || "",
          area: (img.naturalWidth || img.width || 0) * (img.naturalHeight || img.height || 0),
          top: img.getBoundingClientRect().top,
          generatedHint:
            /generated image/i.test(img.alt || "")
            || downloadAffordance
            || /generated image|created with|open image|download image|image ready/.test(rootText),
        }}))
        .filter((item) => item.src)
        .sort((a, b) => Number(b.generatedHint) - Number(a.generatedHint) || b.area - a.area || a.top - b.top);
      const payloads = [];
      if (strategy === "download") {{
        const seenLinks = new Set();
        for (const link of downloadLinks) {{
          if (seenLinks.has(link.href)) continue;
          seenLinks.add(link.href);
          const exported = await exportUrl(link.href, payloads.length, `generated_${{payloads.length + 1}}`);
          if (exported?.data) payloads.push(exported);
          if (payloads.length >= {max_images}) break;
        }}
      }} else {{
        const preferredCandidates = candidates.filter((item) => item.generatedHint);
        const rankedCandidates = preferredCandidates.length ? preferredCandidates : candidates;
        const unique = [];
        const seen = new Set();
        for (const item of rankedCandidates) {{
          if (seen.has(item.src)) continue;
          seen.add(item.src);
          unique.push(item.img);
          if (unique.length >= {max_images}) break;
        }}
        for (let index = 0; index < unique.length; index += 1) {{
          const exported = await exportImage(unique[index], index);
          if (exported?.data) payloads.push(exported);
        }}
      }}
      window.__codexGeneratedImageExport = {{
        status: "done",
        items: payloads.map((item) => ({{
          name: item.name,
          mime: item.mime,
          size: item.data.length,
        }})),
        payloads,
      }};
    }} catch (error) {{
      const message = error instanceof Error ? error.message : String(error);
      window.__codexGeneratedImageExport = {{ status: `error:${{message}}`, items: [], payloads: [] }};
    }}
  }})();
  return "started";
}})()
"""
    result = execute_javascript(tab, bootstrap)
    if result != "started":
        raise RuntimeError("Could not start generated image export in the current Chrome tab.")
    return _read_generated_image_export_payloads(
        tab,
        output_dir=output_dir,
        job_id=job_id,
        timeout_ms=timeout_ms,
    )


def save_generated_images(
    tab: ChromeTabRef,
    *,
    output_dir: Path,
    job_id: str,
    provider: ProviderName = "gemini",
    max_images: int = 4,
    timeout_ms: int = 90_000,
) -> list[Path]:
    return export_generated_images(
        tab,
        output_dir=output_dir,
        job_id=job_id,
        provider=provider,
        max_images=max_images,
        timeout_ms=timeout_ms,
        strategy="dom",
    )


def wait_for_response_stable(
    tab: ChromeTabRef,
    *,
    timeout_ms: int,
    stable_seconds: int,
    excluded_text: str,
    expect_images: bool = False,
    provider: ProviderName = "gemini",
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    deadline = time.monotonic() + timeout_ms / 1000
    stable_since = time.monotonic()
    last_text = ""
    payload = json.dumps(excluded_text, ensure_ascii=False)
    assistant_selectors = (
        [
            '[data-response-id]',
            '[data-message-author-role="model"]',
            'main article',
            'main [role="article"]',
            'main .markdown',
            'main .model-response',
        ]
        if provider == "gemini"
        else [
            '[data-message-author-role="assistant"]',
            'article[data-testid^="conversation-turn-"] [data-message-author-role="assistant"]',
            'main [data-message-author-role="assistant"]',
            'main article',
            'main .markdown',
            'main .prose',
        ]
    )
    assistant_root_selectors = (
        [
            '[data-message-author-role="assistant"]',
            'article[data-testid^="conversation-turn-"] [data-message-author-role="assistant"]',
            'main [data-message-author-role="assistant"]',
            'main .markdown',
            'main .prose',
        ]
        if provider == "chatgpt"
        else [
            '[data-response-id]',
            '[data-message-author-role="model"]',
            'main .model-response',
            'main .markdown',
            'main .prose',
        ]
    )
    probe = f"""
(() => {{
  const excluded = {payload};
  const transientPatterns = [/^thinking$/i, /^analyzing$/i, /^searching$/i, /^reasoned for \\d+/i];
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }};
  const clean = (text) => (text || "").replace(/\\r\\n/g, "\\n").trim();
  const selectors = {json.dumps(assistant_selectors, ensure_ascii=False)};
  const assistantRootSelectors = {json.dumps(assistant_root_selectors, ensure_ascii=False)};
  const blocks = selectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .filter((el) => isVisible(el))
    .map((el) => clean(el.innerText))
    .filter(Boolean);
  const lastBlock = [...blocks].reverse()[0] || "";
  const answer = [...blocks]
    .reverse()
    .find((text) => text !== clean(excluded) && !transientPatterns.some((pattern) => pattern.test(text))) || "";
  const assistantRoots = assistantRootSelectors
    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    .filter((el) => isVisible(el))
    .map((candidate) => candidate.closest('article[data-testid^="conversation-turn-"]') || candidate.closest('article') || candidate)
    .filter((root, index, arr) => root && isVisible(root) && arr.indexOf(root) === index)
    .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  const latestAssistantRoot = [...assistantRoots]
    .reverse()
    .find((root) => Array.from(root.querySelectorAll('img, picture img')).some((img) => isVisible(img)))
    || assistantRoots.at(-1)
    || null;
  const allGeneratedImageCandidates = Array.from((latestAssistantRoot || document).querySelectorAll('img, generated-image img, .generated-images-container img, .image-gallery img, picture img'))
    .filter((img) => isVisible(img) && img.naturalWidth >= 96 && img.naturalHeight >= 96)
    .filter((img) => !img.closest('[data-message-author-role="user"]'));
  const generatedHintCandidates = allGeneratedImageCandidates.filter((img) => /generated image/i.test(img.alt || ""));
  const generatedImages = (generatedHintCandidates.length ? generatedHintCandidates : allGeneratedImageCandidates).length;
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter((el) => isVisible(el));
  const busy = buttons.some((el) => {{
    const text = `${{el.innerText || ""}} ${{el.getAttribute("aria-label") || ""}}`.toLowerCase().trim();
    if (/stopped thinking/.test(text)) return false;
    return /stop answering|stop generating|stop|cancel/.test(text);
  }});
  const transientTail = transientPatterns.some((pattern) => pattern.test(lastBlock));
  return JSON.stringify({{ answer, busy, generatedImages, transientTail }});
}})()
"""
    while time.monotonic() < deadline:
        if should_cancel and should_cancel():
            raise TimeoutError("__ORD_CANCELLED__")
        state = json.loads(execute_javascript(tab, probe) or "{}")
        current = (state.get("answer") or "").strip()
        busy = bool(state.get("busy"))
        generated_images = int(state.get("generatedImages") or 0)
        transient_tail = bool(state.get("transientTail"))
        stable_elapsed = time.monotonic() - stable_since
        if current and current != last_text:
            last_text = current
            stable_since = time.monotonic()
            stable_elapsed = 0
        elif current and ((not busy) or transient_tail) and stable_elapsed >= stable_seconds:
            return current
        elif current and provider == "chatgpt" and busy and stable_elapsed >= max(stable_seconds + 2, 6):
            return current
        elif expect_images and generated_images > 0 and not busy and stable_elapsed >= stable_seconds:
            return f"__GENERATED_IMAGES__:{generated_images}"
        elif expect_images and generated_images > 0 and provider == "chatgpt" and stable_elapsed >= max(stable_seconds + 2, 6):
            return f"__GENERATED_IMAGES__:{generated_images}"
        time.sleep(1)
    raise TimeoutError(
        f"{PROVIDER_LABELS[provider]} did not finish response within the timeout in the current Chrome tab."
    )
