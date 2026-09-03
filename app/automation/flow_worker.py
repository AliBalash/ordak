from __future__ import annotations
import json
import time
import base64
import mimetypes
import hashlib
import subprocess
from pathlib import Path
from typing import Callable

from app.automation.existing_chrome import (
    ChromeTabRef,
    get_tab_info,
    execute_javascript,
    is_google_chrome_running,
    wait_for_prompt_input,
)
from app.automation.gemini_worker import WorkerRuntime, AutomationJobRequest, GeminiAutomationError, _provider_name, _map_login_error, _ensure_linux_browser_ready, _runtime_checkpoint
from app.config import Settings, settings
from app.errors import ErrorCode, OrdaKError
from app.providers import get_provider_adapter

# Flow constants — dynamic per settings, no hardcoded project (§11, §60)
FLOW_BASE_URL = "https://labs.google/fx/tools/flow"
FLOW_PROJECT_URL_PREFIX = "https://labs.google/fx/tools/flow/project/"
# Legacy hardcoded for backward compat only; prefer settings.flow_url
FLOW_MAIN_URL_LEGACY = "https://labs.google/fx/tools/flow/project/36400b0f-605e-484b-95c5-48e727479dfc"

def _flow_raise(code: ErrorCode, message: str):
    raise GeminiAutomationError(message, status="failed", error_code=code.value)


def _click_at(tab: ChromeTabRef, x: float, y: float):
    """Dispatch mouse click via CDP Input."""
    try:
        from app.automation.existing_chrome import get_tab_info, list_google_chrome_tabs
        import websocket, json
        info = get_tab_info(tab)
        ws_url = None
        if info and hasattr(info, 'target_id'):
            # Try to get ws_url from list
            try:
                tabs = list_google_chrome_tabs()
                for tb in tabs:
                    if tb.get('target_id') == getattr(info, 'target_id', None) or tb.get('id') == getattr(info, 'target_id', None):
                        ws_url = tb.get('webSocketDebuggerUrl') or tb.get('websocket_debugger_url')
                        break
            except:
                pass
            if not ws_url:
                ws_url = getattr(info, 'websocket_debugger_url', None)
        if not ws_url:
            # Fallback: try to get via tabs list by url
            try:
                tabs = list_google_chrome_tabs()
                for tb in tabs:
                    if 'flow' in (tb.get('url') or '').lower():
                        ws_url = tb.get('webSocketDebuggerUrl') or tb.get('websocket_debugger_url')
                        if ws_url:
                            break
            except:
                pass
        if ws_url:
            w = websocket.create_connection(ws_url, timeout=5)
            payload = {"id": 1, "method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}}
            w.send(json.dumps(payload))
            w.recv()
            payload2 = {"id": 2, "method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}}
            w.send(json.dumps(payload2))
            w.recv()
            w.close()
            return True
    except Exception:
        pass
    # Fallback to JS click
    try:
        execute_javascript(tab, f"document.elementFromPoint({x}, {y})?.click()")
        return True
    except:
        return False


def _ensure_flow_project(tab: ChromeTabRef, runtime: WorkerRuntime | None, app_settings: Settings) -> ChromeTabRef:
    """If on main Flow page, click New project to create a project."""
    from urllib.parse import urlparse
    info = get_tab_info(tab)
    if info is None:
        return tab
    url = info.url or ""
    # If already in a project, return
    if "/project/" in url:
        if runtime:
            runtime.append_log(f"Already in Flow project: {url}")
        return tab
    # If on main flow page, click New project at 955,868 (center)
    if "labs.google/fx/tools/flow" in url and "/project/" not in url:
        if runtime:
            runtime.append_log("Flow main page detected, clicking New project")
        # Wait for page load and button to appear
        for attempt in range(10):
            try:
                # Check if button exists
                js_check = "Array.from(document.querySelectorAll('button')).some(b => b.innerText.includes('New project')) ? 'found' : 'not found'"
                result = execute_javascript(tab, js_check)
                if 'found' in result:
                    if runtime:
                        runtime.append_log(f"New project button found on attempt {attempt+1}")
                    break
            except Exception as e:
                if runtime:
                    runtime.append_log(f"Check attempt {attempt+1} failed: {e}", level="warning")
            time.sleep(2)
        else:
            if runtime:
                runtime.append_log("New project button not found after waiting", level="warning")
        # Use JS to find and click New project
        js = """
        (() => {
          const btns = Array.from(document.querySelectorAll('button'));
          const target = btns.find(b => b.innerText.includes('New project'));
          if (!target) return 'no new project';
          const r = target.getBoundingClientRect();
          return JSON.stringify({cx: r.x+r.width/2, cy: r.y+r.height/2});
        })()
        """
        try:
            result = execute_javascript(tab, js)
            import json as js2
            info2 = js2.loads(result) if result else {}
            if 'cx' in info2:
                # Use CDP Input via execute_javascript with mouse event simulation
                js_click = f"""
                (() => {{
                  const el = document.elementFromPoint({info2['cx']}, {info2['cy']});
                  if (el) el.click();
                  return 'clicked';
                }})()
                """
                # Use _click_at helper for reliable click
                _click_at(tab, info2['cx'], info2['cy'])
                time.sleep(5)
                # Poll for project URL change
                for _ in range(6):
                    new_info = get_tab_info(tab)
                    if new_info and "/project/" in (new_info.url or ""):
                        if runtime:
                            runtime.append_log(f"New Flow project created: {new_info.url}")
                        return tab
                    time.sleep(2)
                # Fallback try direct button click via JS
                js2_click = """
                (() => {
                  const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('New project'));
                  if (btn) { btn.click(); return 'clicked2'; }
                  return 'not found2';
                })()
                """
                execute_javascript(tab, js2_click)
                time.sleep(5)
                for _ in range(6):
                    new_info2 = get_tab_info(tab)
                    if new_info2 and "/project/" in (new_info2.url or ""):
                        if runtime:
                            runtime.append_log(f"New Flow project created via fallback: {new_info2.url}")
                        return tab
                    time.sleep(2)
                raise RuntimeError("Failed to create new Flow project after retries")
            else:
                raise RuntimeError(f"New project button not found: {info2}")
        except Exception as e:
            raise GeminiAutomationError(f"Flow New project failed: {e}", error_code=ErrorCode.PROVIDER_UI_CHANGED.value)
    return tab

def _flow_display_label(model: str) -> str:
    """Map internal flow model to visible UI label (§10)."""
    mapping = {
        "gemini_omni_1_1_flash": "Omni 1.1 Flash",
        "veo_3_1_quality": "Veo 3.1 Quality",
        "veo_3_1_fast": "Veo 3.1 Fast",
        "veo_3_1_lite": "Veo 3.1 Lite",
        "Omni 1.1 Flash": "Omni 1.1 Flash",
        "Veo 3.1 Quality": "Veo 3.1 Quality",
    }
    return mapping.get(model, model)

def _select_flow_video_settings(tab: ChromeTabRef, runtime: WorkerRuntime | None, aspect="9:16", model="Omni 1.1 Flash", resolution="720p", duration="6s"):
    """Select Video mode and verify settings — optimized: check current value first, no redundant open/close (§91-92)."""
    display_model = _flow_display_label(model)
    # Fast check: is requested model already selected? (§5 verification)
    try:
        js_current = f"""
        (() => {{
          const selected = document.body.innerText;
          // Check if display_model already visible as selected (heuristic: button with aria-selected or active tab)
          const active = Array.from(document.querySelectorAll('button[aria-selected=\"true\"], button[aria-pressed=\"true\"]')).map(b=>b.innerText).join(' ');
          return JSON.stringify({{selected: selected.includes('{display_model}'), active: active.slice(0,200)}});
        }})()
        """
        cur = execute_javascript(tab, js_current)
        import json as js
        cur_info = js.loads(cur) if cur else {}
        if cur_info.get('selected'):
            if runtime:
                runtime.append_log(f"Flow model {display_model} already selected — skipping dropdown")
            # still need to verify aspect/resolution/duration but skip model dropdown
            need_model_open = False
        else:
            need_model_open = True
    except Exception:
        need_model_open = True

    try:
        if need_model_open:
            # Click model selector to open popup — ONLY for video models (Omni/Veo), not Nano Banana (§10)
            js_model = """
            (() => {
              const b = Array.from(document.querySelectorAll('button')).find(e=>e.innerText.includes('Omni') || e.innerText.includes('Veo'));
              if (!b) return JSON.stringify({error: 'no model btn'});
              const r = b.getBoundingClientRect();
              return JSON.stringify({cx: r.x+r.width/2, cy: r.y+r.height/2});
            })()
            """
            result = execute_javascript(tab, js_model)
            import json as js
            info = js.loads(result) if result else {}
            if 'cx' in info:
                js_click = f"""
                (() => {{
                  const el = document.elementFromPoint({info['cx']}, {info['cy']});
                  if (el) el.click();
                  return 'clicked';
                }})()
                """
                execute_javascript(tab, js_click)
                time.sleep(0.6)
                if runtime:
                    runtime.append_log(f"Opened Flow model selector at {info['cx']},{info['cy']}")
                # Now select requested display_model inside dropdown
                js_pick = f"""
                (() => {{
                  const opts = Array.from(document.querySelectorAll('button, [role=\"option\"]'));
                  const target = opts.find(e => e.innerText.trim().includes('{display_model}'));
                  if (!target) return JSON.stringify({{error: 'no opt {display_model}'}});
                  const r = target.getBoundingClientRect();
                  return JSON.stringify({{cx: r.x+r.width/2, cy: r.y+r.height/2}});
                }})()
                """
                pick = execute_javascript(tab, js_pick)
                pick_info = js.loads(pick) if pick else {}
                if 'cx' in pick_info:
                    js_pick_click = f"""
                    (() => {{
                      const el = document.elementFromPoint({pick_info['cx']}, {pick_info['cy']});
                      if (el) el.click();
                      return 'picked {display_model}';
                    }})()
                    """
                    execute_javascript(tab, js_pick_click)
                    time.sleep(0.5)
                    if runtime:
                        runtime.append_log(f"Selected Flow model {display_model}")
                else:
                    if runtime:
                        runtime.append_log(f"Flow model option {display_model} not found: {pick}", level="warning")
                # small wait for UI to stabilize
                time.sleep(0.4)
        # Verify Video tab is active — only click if not already
        js_video_check = """
        (() => {
          const active = document.querySelector('button[role="tab"][aria-selected="true"]');
          const txt = active ? active.innerText : '';
          return JSON.stringify({active: txt, isVideo: txt.includes('Video')});
        })()
        """
        vc = execute_javascript(tab, js_video_check)
        vc_info = js.loads(vc) if vc else {}
        if not vc_info.get('isVideo'):
            js_video = """
            (() => {
              const btns = Array.from(document.querySelectorAll('button'));
              const target = btns.find(e => e.innerText.includes('Video') && e.getAttribute('role')==='tab');
              if (!target) return JSON.stringify({error: 'no video tab'});
              const r = target.getBoundingClientRect();
              return JSON.stringify({cx: r.x+r.width/2, cy: r.y+r.height/2});
            })()
            """
            result2 = execute_javascript(tab, js_video)
            info2 = js.loads(result2) if result2 else {}
            if 'cx' in info2:
                js_click2 = f"""
                (() => {{
                  const el = document.elementFromPoint({info2['cx']}, {info2['cy']});
                  if (el) el.click();
                  return 'clicked video';
                }})()
                """
                execute_javascript(tab, js_click2)
                time.sleep(0.5)
                if runtime:
                    runtime.append_log("Selected Video tab")
        else:
            if runtime:
                runtime.append_log("Video tab already active — skipping")
        # Select aspect 9:16 — only if not already active (check aria-selected)
        js_aspect_check = f"""
        (() => {{
          const active = Array.from(document.querySelectorAll('button[aria-selected="true"], button[aria-pressed="true"]')).map(b=>b.innerText).join(' ');
          if (active.includes('{aspect}')) return JSON.stringify({{already:true}});
          const btns = Array.from(document.querySelectorAll('button'));
          const target = btns.find(e => e.innerText.includes('{aspect}'));
          if (!target) return JSON.stringify({{error: 'no aspect {aspect}'}});
          const r = target.getBoundingClientRect();
          return JSON.stringify({{cx: r.x+r.width/2, cy: r.y+r.height/2}});
        }})()
        """
        result3 = execute_javascript(tab, js_aspect_check)
        info3 = js.loads(result3) if result3 else {}
        if info3.get('already'):
            if runtime:
                runtime.append_log(f"Aspect {aspect} already selected — skipping")
        elif 'cx' in info3:
            js_click3 = f"""
            (() => {{
              const el = document.elementFromPoint({info3['cx']}, {info3['cy']});
              if (el) el.click();
              return 'clicked';
            }})()
            """
            execute_javascript(tab, js_click3)
            time.sleep(0.4)
            if runtime:
                runtime.append_log(f"Selected aspect {aspect}")
        # Select resolution — check first
        js_res_check = f"""
        (() => {{
          const active = Array.from(document.querySelectorAll('button[aria-selected="true"], button[aria-pressed="true"]')).map(b=>b.innerText).join(' ');
          if (active.includes('{resolution}')) return JSON.stringify({{already:true}});
          const btns = Array.from(document.querySelectorAll('button'));
          const target = btns.find(e => e.innerText.trim() === '{resolution}');
          if (!target) return JSON.stringify({{error: 'no res'}});
          const r = target.getBoundingClientRect();
          return JSON.stringify({{cx: r.x+r.width/2, cy: r.y+r.height/2}});
        }})()
        """
        result4 = execute_javascript(tab, js_res_check)
        info4 = js.loads(result4) if result4 else {}
        if info4.get('already'):
            if runtime:
                runtime.append_log(f"Resolution {resolution} already selected — skipping")
        elif 'cx' in info4:
            js_click4 = f"""
            (() => {{
              const el = document.elementFromPoint({info4['cx']}, {info4['cy']});
              if (el) el.click();
              return 'clicked';
            }})()
            """
            execute_javascript(tab, js_click4)
            time.sleep(0.4)
            if runtime:
                runtime.append_log(f"Selected resolution {resolution}")
        # Select duration — check already selected
        js_dur_check = f"""
        (() => {{
          const active = Array.from(document.querySelectorAll('button[aria-selected="true"], button[aria-pressed="true"]')).map(b=>b.innerText).join(' ');
          if (active.includes('{duration}')) return JSON.stringify({{already:true}});
          const btns = Array.from(document.querySelectorAll('button'));
          const target = btns.find(e => e.innerText.trim() === '{duration}');
          if (!target) return JSON.stringify({{error: 'no dur'}});
          const r = target.getBoundingClientRect();
          return JSON.stringify({{cx: r.x+r.width/2, cy: r.y+r.height/2}});
        }})()
        """
        result5 = execute_javascript(tab, js_dur_check)
        info5 = js.loads(result5) if result5 else {}
        if info5.get('already'):
            if runtime:
                runtime.append_log(f"Duration {duration} already selected — skipping")
        elif 'cx' in info5:
            js_click5 = f"""
            (() => {{
              const el = document.elementFromPoint({info5['cx']}, {info5['cy']});
              if (el) el.click();
              return 'clicked';
            }})()
            """
            execute_javascript(tab, js_click5)
            time.sleep(0.4)
            if runtime:
                runtime.append_log(f"Selected duration {duration}")
        # Verify model selection — efficient check without reopening dropdown (§5 verification)
        if model:
            js_model2 = f"""
            (() => {{
              const bodyText = document.body.innerText;
              const foundSelected = bodyText.includes('{display_model}');
              // Also check active button text
              const activeBtns = Array.from(document.querySelectorAll('button[aria-selected="true"]')).map(b=>b.innerText).join(' | ');
              return JSON.stringify({{found: foundSelected, active: activeBtns.slice(0,300)}});
            }})()
            """
            result6 = execute_javascript(tab, js_model2)
            if runtime:
                runtime.append_log(f"Model verification for {display_model}: {result6[:300]}")
            # If requested model not found in UI, raise structured error per §5
            try:
                v = js.loads(result6) if result6 else {}
                if not v.get('found'):
                    raise GeminiAutomationError(f"Flow model {display_model} not found/selected in UI — MODEL_SELECTION_FAILED", error_code="MODEL_SELECTION_FAILED")
            except GeminiAutomationError:
                raise
            except Exception:
                pass
        # Close popup via Escape only if a popup is likely open (check for dialog/overlay)
        try:
            has_popup = execute_javascript(tab, "document.querySelector('[role=\"dialog\"], [aria-modal=\"true\"]') ? 'yes' : 'no'")
            if 'yes' in has_popup:
                execute_javascript(tab, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape'})); document.dispatchEvent(new KeyboardEvent('keyup', {key: 'Escape', code: 'Escape'})); 'escaped'")
                time.sleep(0.3)
        except Exception:
            pass
        time.sleep(0.2)
    except Exception as e:
        if runtime:
            runtime.append_log(f"Flow video settings selection warning: {e}", level="warning")

def _type_flow_prompt(tab: ChromeTabRef, prompt: str, runtime: WorkerRuntime | None):
    # Use reliable click + Input.insertText method
    try:
        js_editor = """
        (() => {
          const e = document.querySelector('div[contenteditable="true"][role="textbox"]');
          if (!e) return JSON.stringify({error: 'no editor'});
          const r = e.getBoundingClientRect();
          return JSON.stringify({x: r.x+r.width/2, y: r.y+r.height/2});
        })()
        """
        result = execute_javascript(tab, js_editor)
        import json as js
        info = js.loads(result) if result else {}
        if 'x' in info:
            _click_at(tab, info['x'], info['y'])
            time.sleep(1)
            # Select all and delete via Input
            from app.automation.existing_chrome import get_tab_info
            import websocket, json as js2
            info2 = get_tab_info(tab)
            ws_url = None
            if info2:
                ws_url = getattr(info2, 'websocket_debugger_url', None)
            if not ws_url:
                from app.automation.existing_chrome import list_google_chrome_tabs
                tabs = list_google_chrome_tabs()
                for tb in tabs:
                    if tb.get('target_id') == getattr(info2, 'target_id', None):
                        ws_url = tb.get('webSocketDebuggerUrl')
                        break
            if ws_url:
                w = websocket.create_connection(ws_url, timeout=5)
                w.send(js2.dumps({"id": 1, "method": "Input.dispatchKeyEvent", "params": {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2}}))
                w.recv()
                w.send(js2.dumps({"id": 2, "method": "Input.dispatchKeyEvent", "params": {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2}}))
                w.recv()
                time.sleep(0.3)
                w.send(js2.dumps({"id": 3, "method": "Input.dispatchKeyEvent", "params": {"type": "keyDown", "key": "Backspace", "code": "Backspace"}}))
                w.recv()
                w.send(js2.dumps({"id": 4, "method": "Input.dispatchKeyEvent", "params": {"type": "keyUp", "key": "Backspace", "code": "Backspace"}}))
                w.recv()
                time.sleep(0.5)
                w.send(js2.dumps({"id": 5, "method": "Input.insertText", "params": {"text": prompt}}))
                w.recv()
                w.close()
                if runtime:
                    runtime.append_log(f"Typed via Input.insertText: {prompt[:100]}")
            else:
                raise RuntimeError("no ws for typing")
        else:
            raise RuntimeError("editor not found")
    except Exception as e:
        if runtime:
            runtime.append_log(f"Typed via fallback execCommand: {e}", level="warning")
        prompt_esc = prompt.replace("\\", "\\\\").replace("`", "\\`")
        js_fallback = f"""
        (() => {{
          const editor = document.querySelector('div[contenteditable="true"][role="textbox"]');
          if (!editor) return 'no editor2';
          editor.focus();
          document.execCommand('selectAll', false, null);
          document.execCommand('insertText', false, `{prompt_esc}`);
          return editor.innerText.slice(0,800);
        }})()
        """
        execute_javascript(tab, js_fallback)
    time.sleep(1)
    for attempt in range(6):
        js3 = """
        (() => {
          const btns = Array.from(document.querySelectorAll('button'));
          const target = btns.find(e => e.innerHTML.includes('arrow_forward'));
          if (!target) return JSON.stringify({error: 'no create'});
          return JSON.stringify({disabled: target.disabled, aria: target.getAttribute('aria-disabled')});
        })()
        """
        result3 = execute_javascript(tab, js3)
        if runtime:
            runtime.append_log(f"Create button state attempt {attempt+1}: {result3[:400]}")
        if '"aria":"false"' in result3 and '"disabled":false' in result3:
            break
        time.sleep(1)
    return result3


def _submit_flow(tab: ChromeTabRef, runtime: WorkerRuntime | None):
    js = """
    (() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const target = btns.find(e => e.innerHTML.includes('arrow_forward') && e.getAttribute('aria-disabled') !== 'true' && !e.disabled);
      if (!target) return JSON.stringify({error: 'no create enabled'});
      const r = target.getBoundingClientRect();
      return JSON.stringify({cx: r.x+r.width/2, cy: r.y+r.height/2});
    })()
    """
    result = execute_javascript(tab, js)
    import json as js2
    info = js2.loads(result) if result else {}
    if 'cx' not in info:
        raise GeminiAutomationError(f"Flow Create button not enabled: {result}", error_code=ErrorCode.SUBMIT_FAILED.value)
    cx = info['cx']; cy = info['cy']
    # Click via JS
    js_click = f"""
    (() => {{
      const el = document.elementFromPoint({cx}, {cy});
      if (el) el.click();
      return 'clicked';
    }})()
    """
    execute_javascript(tab, js_click)
    if runtime:
        runtime.append_log(f"Clicked Flow Create at {cx},{cy}")
    time.sleep(2)

def _wait_for_flow_generation(tab: ChromeTabRef, runtime: WorkerRuntime | None, timeout_ms: int = 300000):
    deadline = time.time() + timeout_ms / 1000
    last_status = ""
    # Get initial video count and also track initial download button state
    try:
        initial = int(execute_javascript(tab, "document.querySelectorAll('video').length") or "0")
    except:
        initial = 0
    if runtime:
        runtime.append_log(f"Initial video count: {initial}, waiting for new video and download")
    while time.time() < deadline:
        js_status = """
        (() => {
          const vcount = document.querySelectorAll('video').length;
          const hasDownload = !!Array.from(document.querySelectorAll('button')).find(b => b.innerText.toLowerCase().includes('download'));
          const txt = document.body.innerText;
          // Prioritize new video + download as ready
          if (hasDownload && vcount > %d) return 'READY';
          if (vcount > %d) {
            // Check if new video is actually ready (has src and not generating)
            const videos = Array.from(document.querySelectorAll('video'));
            const ready = videos.some(v => v.src && !v.paused);
            // Also check for Failed that is recent (ignore old)
            if (txt.includes('Failed') && txt.includes('policies')) {
              // Only consider policy failed if it appears near new video area
              const recentFailed = Array.from(document.querySelectorAll('*')).filter(e => e.innerText && e.innerText.includes('Failed') && e.getBoundingClientRect().width > 100);
              if (recentFailed.length > 2) return 'POLICY_FAILED';
            }
            return 'VIDEO_READY';
          }
          if (txt.includes('Generating')) return 'GENERATING';
          return 'UNKNOWN';
        })()
        """ % (initial, initial)
        try:
            result = execute_javascript(tab, js_status)
            status = result.strip() if result else "UNKNOWN"
        except:
            status = "UNKNOWN"
        js_vid = "document.querySelectorAll('video').length + ' videos'"
        result2 = execute_javascript(tab, js_vid)
        if runtime and status != last_status:
            runtime.append_log(f"Flow generation status: {status} {result2}")
            last_status = status
        if status == "POLICY_FAILED":
            raise GeminiAutomationError("Flow prompt violated policy", error_code="FLOW_POLICY_VIOLATION")
        if status in ("READY", "VIDEO_READY"):
            if runtime:
                runtime.append_log("Flow generation appears ready (new video detected)")
            return "READY"
        # Also check for download button as additional signal
        js_dl = """
        (() => {
          const btns = Array.from(document.querySelectorAll('button'));
          const dl = btns.find(b => b.innerText.toLowerCase().includes('download'));
          return dl ? 'has_download' : 'no_dl';
        })()
        """
        result_dl = execute_javascript(tab, js_dl)
        if "has_download" in result_dl:
            # Only consider download if we have new video
            try:
                vcount = int(execute_javascript(tab, "document.querySelectorAll('video').length") or "0")
                if vcount > initial:
                    if runtime:
                        runtime.append_log("Found Download button for new video")
                    return "READY"
            except:
                pass
        time.sleep(5)
    raise GeminiAutomationError("Flow generation timeout", error_code=ErrorCode.RESPONSE_TIMEOUT.value)

def _set_flow_download_behavior(tab: ChromeTabRef, download_path: str = "/tmp"):
    """Set Chrome download behavior to allow downloads to specified path."""
    try:
        from app.automation.existing_chrome import get_tab_info
        info = get_tab_info(tab)
        if info and hasattr(info, 'target_id'):
            # Use websocket to set download behavior
            import websocket, json
            ws_url = getattr(info, 'websocket_debugger_url', None)
            if not ws_url:
                # Try to get via tabs list
                from app.automation.existing_chrome import list_google_chrome_tabs
                tabs = list_google_chrome_tabs()
                for tb in tabs:
                    if tb.get('target_id') == getattr(info, 'target_id', None):
                        ws_url = tb.get('webSocketDebuggerUrl') or tb.get('websocket_debugger_url')
                        break
            if ws_url:
                w = websocket.create_connection(ws_url, timeout=5)
                payload = {"id": 1, "method": "Browser.setDownloadBehavior", "params": {"behavior": "allow", "downloadPath": download_path, "eventsEnabled": True}}
                w.send(json.dumps(payload))
                w.recv()
                w.close()
    except Exception:
        pass

def _download_flow_video(tab: ChromeTabRef, job_id: str, output_dir: Path, runtime: WorkerRuntime | None) -> Path:
    _set_flow_download_behavior(tab, "/tmp")
    # First, if we are in project view, click the newest video to open edit view where Download is available
    try:
        js_click_newest = """
        (() => {
          const videos = Array.from(document.querySelectorAll('video'));
          if (videos.length === 0) return JSON.stringify({error: 'no videos'});
          // The newest video is likely the first in the grid (most recent) - check its parent button
          const firstVideo = videos[0];
          let el = firstVideo;
          for (let i=0;i<6;i++) {
            if (el && el.getBoundingClientRect().width > 100) break;
            if (el && el.parentElement) el = el.parentElement;
          }
          if (!el) return JSON.stringify({error: 'no el'});
          const r = el.getBoundingClientRect();
          return JSON.stringify({cx: r.x+r.width/2, cy: r.y+r.height/2});
        })()
        """
        result = execute_javascript(tab, js_click_newest)
        import json as js
        info = js.loads(result) if result else {}
        if 'cx' in info:
            _click_at(tab, info['cx'], info['cy'])
            time.sleep(4)
            if runtime:
                runtime.append_log(f"Clicked newest video at {info['cx']},{info['cy']} to open edit view")
            # Wait for edit view to load (check for Download button)
            for _ in range(10):
                js_dl_check = "Array.from(document.querySelectorAll('button')).some(b => b.innerText.toLowerCase().includes('download')) ? 'has' : 'no'"
                if 'has' in execute_javascript(tab, js_dl_check):
                    break
                time.sleep(1)
    except Exception as e:
        if runtime:
            runtime.append_log(f"Click newest video warning: {e}", level="warning")
    # Try to get video src and download via fetch inside browser, or click Download
    # First try to click Download button and let Browser.download handle it
    # Set download behavior via CDP is already set globally, but we need to ensure download path
    # For now, try to fetch via JS and save via base64 if download via click fails
    # Try download button click
    js_find = """
    (() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const target = btns.find(b => b.innerText.toLowerCase().includes('download'));
      if (!target) return JSON.stringify({error: 'no download'});
      const r = target.getBoundingClientRect();
      return JSON.stringify({cx: r.x+r.width/2, cy: r.y+r.height/2});
    })()
    """
    result = execute_javascript(tab, js_find)
    import json as js
    info = js.loads(result) if result else {}
    output_dir.mkdir(parents=True, exist_ok=True)
    if 'cx' in info:
        # Try click download
        cx = info['cx']; cy = info['cy']
        js_click = f"""
        (() => {{
          const el = document.elementFromPoint({cx}, {cy});
          if (el) el.click();
          return 'clicked download';
        }})()
        """
        execute_javascript(tab, js_click)
        if runtime:
            runtime.append_log(f"Clicked Flow Download at {cx},{cy}")
        time.sleep(3)
        # Check for downloaded file in /tmp (Browser.setDownloadBehavior was to /tmp, but we want output_dir)
        # The download will go to /tmp with suggested filename like Watering_garden_scene...mp4
        # We need to find latest mp4 in /tmp
        # Wait for download to complete (check every 2s for up to 30s) - check both /tmp and /root/Downloads
        dest = None
        for wait_attempt in range(15):
            time.sleep(2)
            candidates = []
            for base in [Path("/tmp"), Path("/root/Downloads"), Path.home() / "Downloads"]:
                try:
                    if base.is_dir():
                        candidates.extend(sorted(base.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True))
                except:
                    pass
            # Also check via glob for any recent mp4
            if candidates:
                # Find most recent that is stable
                for latest in candidates[:5]:
                    try:
                        if time.time() - latest.stat().st_mtime < 180:
                            size1 = latest.stat().st_size
                            time.sleep(1)
                            try:
                                size2 = latest.stat().st_size
                            except:
                                continue
                            if size1 == size2 and size1 > 100000:
                                dest_path = output_dir / f"{job_id}.mp4"
                                import shutil
                                shutil.copy(str(latest), str(dest_path))
                                if runtime:
                                    runtime.append_log(f"Downloaded via click to {dest_path} from {latest} ({size1} bytes)")
                                return dest_path
                    except:
                        continue
            if runtime and wait_attempt % 5 == 0:
                runtime.append_log(f"Waiting for download... attempt {wait_attempt+1}/15, checked {len(candidates)} candidates")
        if runtime:
            runtime.append_log("Download via click did not produce file in /tmp after 30s, trying fetch method", level="warning")
    # Fallback: try to get video src and download via browser's download instead of fetch (to avoid 1MB websocket limit)
    # The fetch via websocket fails for large videos (>1MB) due to frame limit, so we avoid it
    # Instead, try to trigger download again or use direct video src with curl and Chrome cookies
    try:
        # Get video src
        js_src = "document.querySelector('video') ? document.querySelector('video').src : 'no'"
        result_src = execute_javascript(tab, js_src)
        if runtime:
            runtime.append_log(f"Video src for fallback: {result_src[:200]}")
        # Try to use Chrome's Network to get cookies and curl
        # For now, just wait a bit more for download file to appear
        time.sleep(5)
        candidates = []
        for base in [Path("/tmp"), Path("/root/Downloads")]:
            try:
                candidates.extend(sorted(base.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True))
            except:
                pass
        if candidates:
            latest = candidates[0]
            if time.time() - latest.stat().st_mtime < 180:
                dest_path = output_dir / f"{job_id}.mp4"
                import shutil
                shutil.copy(str(latest), str(dest_path))
                if runtime:
                    runtime.append_log(f"Downloaded via fallback check to {dest_path} from {latest}")
                return dest_path
    except Exception as e:
        if runtime:
            runtime.append_log(f"Fallback check failed: {e}", level="warning")
    # Another fallback: try to get video src and download via curl with current Chrome's cookies via CDP
    # Get video src
    js_src = "document.querySelector('video') ? document.querySelector('video').src : 'no'"
    result_src = execute_javascript(tab, js_src)
    if runtime:
        runtime.append_log(f"Video src: {result_src[:300]}")
    # If we have src, try to download via python requests using the same URL but we need auth; instead we can use `curl` with --cookie from Chrome?
    # As last resort, raise error
    raise GeminiAutomationError("Flow video download failed: no file in /tmp and fetch not implemented", error_code=ErrorCode.RESULT_NOT_EXTRACTABLE.value)

def run_flow_job(job_id: str, job: AutomationJobRequest, runtime: WorkerRuntime | None = None, app_settings: Settings | None = None) -> str:
    resolved = app_settings or settings
    adapter = get_provider_adapter(job.provider)
    tab = None
    try:
        if runtime:
            runtime.update_status("checking_browser")
            runtime.append_log("Checking Chrome for Flow")
        _ensure_linux_browser_ready(resolved, job.provider)
        if not is_google_chrome_running():
            raise GeminiAutomationError("Chrome not open", error_code=ErrorCode.CHROME_NOT_OPEN.value)
        # Open Flow project tab
        if runtime:
            runtime.update_status("opening_provider_tab")
            runtime.append_log("Opening Flow in Chrome")
        # Use adapter to open tab — prefer settings.flow_url, no hardcoded project
        target = getattr(resolved, 'flow_url', None) or FLOW_BASE_URL
        # If settings still points to base without project, _ensure_flow_project will create one
        opened = adapter.open_tab(target_url=target)
        tab = opened.ref if hasattr(opened, 'ref') else opened
        # Ensure project
        tab = _ensure_flow_project(tab, runtime, resolved)
        # Check login
        if runtime:
            runtime.update_status("checking_login")
            runtime.append_log("Checking Flow login")
        login_state = adapter.detect_login_state(tab)
        _map_login_error(job.provider, login_state)
        # Find prompt input
        if runtime:
            runtime.update_status("finding_input")
            runtime.append_log("Finding Flow prompt input")
        wait_for_prompt_input(tab, provider="flow", timeout_ms=60000)
        # Select video settings
        if runtime:
            runtime.update_status("submitting_prompt")
            runtime.append_log("Selecting Flow video settings")
        # Determine aspect/duration from job metadata if available
        # For now, use defaults from settings or job
        aspect = "9:16"
        duration = "6s"
        # Try to parse from job.question if contains duration? For now defaults
        _select_flow_video_settings(tab, runtime, aspect=aspect, duration=duration)
        # Handle uploads (character sheet etc)
        # §61 guard: Flow must NEVER receive a style sheet — validate before upload
        if job.uploads:
            for _p in job.uploads:
                name = Path(str(_p)).name.lower()
                if any(tok in name for tok in ("style", "home_style", "world_style", "book_anchor", "mood_board")):
                    raise GeminiAutomationError(
                        f"Flow reference validation failed: style sheet detected in upload {name!r} — Flow may only receive character_sheet (+ first/last frame) (§61)",
                        error_code=ErrorCode.UPLOAD_INCOMPLETE.value,
                    )
            if runtime:
                runtime.append_log(f"Uploading {len(job.uploads)} reference(s) to Flow")
            # Use existing upload helper
            from app.automation.existing_chrome import upload_local_file
            for p in job.uploads:
                # Flow's file input is <input type="file" accept="image/*">
                js_input = "document.querySelector('input[type=\"file\"][accept*=\"image\"]') ? 'found' : 'not found'"
                result = execute_javascript(tab, js_input)
                if runtime:
                    runtime.append_log(f"Flow file input check: {result}")
                # Use upload helper
                try:
                    upload_local_file(tab, str(p), provider="flow")
                    time.sleep(2)
                    if runtime:
                        runtime.append_log(f"Uploaded {p.name} to Flow")
                except Exception as e:
                    if runtime:
                        runtime.append_log(f"Flow upload failed for {p}: {e}", level="warning")
                    # Verify upload complete
                    state = adapter.verify_upload_complete(tab)
                    if not state.get("hasPreview"):
                        raise GeminiAutomationError(f"Flow upload failed: {p}", error_code=ErrorCode.UPLOAD_INCOMPLETE.value)
        # Type prompt
        effective = job.question
        _type_flow_prompt(tab, effective, runtime)
        # Submit
        _submit_flow(tab, runtime)
        if runtime:
            runtime.update_status("waiting_for_response")
            runtime.append_log("Waiting for Flow video generation")
        _wait_for_flow_generation(tab, runtime, timeout_ms=resolved.flow_response_timeout_ms if hasattr(resolved, 'flow_response_timeout_ms') else 300000)
        # Download
        if runtime:
            runtime.update_status("extracting_answer")
            runtime.append_log("Downloading Flow video")
        output_dir = resolved.browser_output_dir / job_id if hasattr(resolved, 'browser_output_dir') else Path("/tmp") / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        video_path = _download_flow_video(tab, job_id, output_dir, runtime)
        # Validate via ffprobe
        try:
            result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name", "-of", "default=noprint_wrappers=1", str(video_path)], capture_output=True, text=True, timeout=20)
            if result.returncode != 0:
                raise GeminiAutomationError(f"Invalid video output: {result.stderr}", error_code=ErrorCode.RESULT_NOT_EXTRACTABLE.value)
            if runtime:
                runtime.append_log(f"Flow video validated: {result.stdout[:300]}")
        except FileNotFoundError:
            if runtime:
                runtime.append_log("ffprobe not found, skipping validation", level="warning")
        # Save answer
        if runtime:
            runtime.save_answer(f"Flow generated video: {video_path}")
            # Attach output video properly (handles Path -> relative)
            try:
                if hasattr(runtime, "attach_output_video") and runtime.attach_output_video is not None:
                    runtime.attach_output_video(video_path)  # type: ignore
                else:
                    runtime.attach_output_image(video_path)
            except Exception:
                pass
        return f"Flow video saved to {video_path}"
    except GeminiAutomationError:
        raise
    except Exception as e:
        if runtime:
            runtime.save_error(str(e), "failed", ErrorCode.PROVIDER_UI_CHANGED.value)
        raise GeminiAutomationError(str(e), error_code=ErrorCode.PROVIDER_UI_CHANGED.value)
