#!/usr/bin/env python3
"""
SQAT UI Discovery Recorder
--------------------------
Generated for project: __PROJECT_ID__
Server:                __SERVER_URL__

Run this script on your local machine:
    pip install playwright httpx
    playwright install chromium
    python recorder.py

The recorder opens a persistent Chromium browser. Log in once and your
session is preserved across all scenarios for this project. For each
scenario, navigate the application as a real user would. Press Ctrl+C or
close the browser window when you have finished a scenario.
"""

from __future__ import annotations

import asyncio
import base64
import json
import signal
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from playwright.async_api import async_playwright, BrowserContext, Page
except ImportError:
    print("ERROR: Playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

# ── Configuration (embedded by server) ────────────────────────────────────

PROJECT_ID: str = "__PROJECT_ID__"
SERVER_URL: str = "__SERVER_URL__"
RECORDER_TOKEN: str = "__RECORDER_TOKEN__"

BROWSER_DATA_DIR = Path.home() / ".sqat" / PROJECT_ID
BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── JavaScript injected into every page ───────────────────────────────────
#
# This script intercepts user interactions and calls back into Python via the
# `__sqat_action__` binding exposed by Playwright. It also provides a
# `__sqat_get_elements__` function for capturing interactive elements.

ACTION_CAPTURE_JS = """
(function () {
  if (window.__sqat_installed__) return;
  window.__sqat_installed__ = true;

  function buildSelector(el) {
    if (el.dataset && el.dataset.testid) return `[data-testid="${el.dataset.testid}"]`;
    if (el.id) return `#${CSS.escape(el.id)}`;
    const tag = el.tagName.toLowerCase();
    if (el.name) return `${tag}[name="${el.name}"]`;
    const cls = Array.from(el.classList)
      .filter(c => !c.match(/^(active|focus|hover|selected|disabled|error)$/i))
      .slice(0, 2)
      .join('.');
    return cls ? `${tag}.${cls}` : tag;
  }

  // Click listener
  document.addEventListener('click', function (e) {
    const el = e.target;
    if (!el || el.tagName === 'HTML' || el.tagName === 'BODY') return;
    window.__sqat_action__({
      type: 'click',
      selector: buildSelector(el),
      text: (el.textContent || '').trim().substring(0, 120),
      elementType: el.tagName.toLowerCase(),
      url: window.location.href,
    }).catch(() => {});
  }, true);

  // Input/change listener
  document.addEventListener('change', function (e) {
    const el = e.target;
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      window.__sqat_action__({
        type: tag === 'select' ? 'select' : 'fill',
        selector: buildSelector(el),
        value: el.type === 'password' ? '***REDACTED***' : (el.value || '').substring(0, 500),
        elementType: tag,
        url: window.location.href,
      }).catch(() => {});
    }
  }, true);

  // Key listener (for Enter/Tab on forms)
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== 'Tab') return;
    const el = e.target;
    if (!el || !el.tagName) return;
    window.__sqat_action__({
      type: 'keypress',
      selector: buildSelector(el),
      value: e.key,
      elementType: el.tagName.toLowerCase(),
      url: window.location.href,
    }).catch(() => {});
  }, true);
})();

// Helper: collect all interactive elements on the page
window.__sqat_get_elements__ = function () {
  const results = [];
  const seen = new Set();

  const selectors = [
    'button', 'a[href]', 'input', 'select', 'textarea',
    '[role="button"]', '[role="link"]', '[role="tab"]',
    '[role="checkbox"]', '[role="radio"]', '[role="menuitem"]',
    '[onclick]', '[tabindex]'
  ];

  selectors.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      if (seen.has(el)) return;
      seen.add(el);

      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return;  // invisible

      function buildSel(el) {
        if (el.dataset && el.dataset.testid) return `[data-testid="${el.dataset.testid}"]`;
        if (el.id) return `#${CSS.escape(el.id)}`;
        const tag = el.tagName.toLowerCase();
        if (el.name) return `${tag}[name="${el.name}"]`;
        const cls = Array.from(el.classList)
          .filter(c => !c.match(/^(active|focus|hover|selected|disabled|error)$/i))
          .slice(0, 2).join('.');
        return cls ? `${tag}.${cls}` : tag;
      }

      results.push({
        tag: el.tagName.toLowerCase(),
        type: el.type || null,
        text: (el.textContent || el.innerText || '').trim().substring(0, 120),
        placeholder: el.placeholder || null,
        name: el.name || null,
        id: el.id || null,
        'data-testid': el.dataset ? el.dataset.testid || null : null,
        role: el.getAttribute('role'),
        selector: buildSel(el),
        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
        stability: el.id || (el.dataset && el.dataset.testid) ? 'high' : 'low',
      });
    });
  });

  return results;
};
"""

# ── Recorder class ─────────────────────────────────────────────────────────

class Recorder:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            base_url=SERVER_URL.rstrip("/"),
            headers={"X-Recorder-Token": RECORDER_TOKEN},
            timeout=30.0,
        )
        self.network_buffer: list[dict] = []
        self._capture_start: float = time.time()
        self._stop_event = asyncio.Event()
        self._current_session_id: str | None = None
        self._step_index: int = 0
        self._action_lock = asyncio.Lock()
        self.project_url: str | None = None

    # ── HTTP helpers ───────────────────────────────────────────────────────

    async def _get(self, path: str) -> dict:
        r = await self.client.get(path)
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, **kwargs) -> dict:
        r = await self.client.post(path, **kwargs)
        r.raise_for_status()
        return r.json()

    async def _put(self, path: str) -> dict:
        r = await self.client.put(path)
        r.raise_for_status()
        return r.json()

    # ── Network tracking ───────────────────────────────────────────────────

    def _on_response(self, response) -> None:
        try:
            self.network_buffer.append({
                "ts": round(time.time() - self._capture_start, 3),
                "method": response.request.method,
                "url": response.url,
                "status": response.status,
            })
        except Exception:
            pass

    def _drain_network(self) -> list[dict]:
        buf = list(self.network_buffer)
        self.network_buffer.clear()
        return buf

    # ── Server API calls ───────────────────────────────────────────────────

    async def fetch_project_info(self) -> dict:
        return await self._get(f"/api/v1/recorder/{PROJECT_ID}/info")

    async def create_session(self, scenario_id: str) -> str:
        data = await self._post(
            f"/api/v1/recorder/{PROJECT_ID}/sessions",
            json={"scenario_id": scenario_id},
        )
        return data["id"]

    async def start_session(self, session_id: str) -> None:
        await self._put(f"/api/v1/recorder/{PROJECT_ID}/sessions/{session_id}/start")

    async def complete_session(self, session_id: str) -> None:
        await self._put(f"/api/v1/recorder/{PROJECT_ID}/sessions/{session_id}/complete")

    async def fail_session(self, session_id: str) -> None:
        await self._put(f"/api/v1/recorder/{PROJECT_ID}/sessions/{session_id}/fail")

    async def upsert_route(
        self,
        session_id: str,
        scenario_id: str,
        page: Page,
        network_calls: list[dict],
    ) -> dict:
        url = page.url

        # Capture page data
        try:
            html = await page.content()
            html_b64 = base64.b64encode(html.encode()).decode()
        except Exception:
            html_b64 = None

        try:
            client = await page.context.new_cdp_session(page)
            a11y = await client.send("Accessibility.getFullAXTree")
        except Exception:
            a11y = None

        try:
            interactive = await page.evaluate("window.__sqat_get_elements__ ? window.__sqat_get_elements__() : []")
        except Exception:
            interactive = []

        try:
            png = await page.screenshot(full_page=True, type="png")
            screenshot_b64 = base64.b64encode(png).decode()
        except Exception:
            screenshot_b64 = None

        try:
            title = await page.title()
        except Exception:
            title = None

        payload = {
            "session_id": session_id,
            "scenario_id": scenario_id,
            "url": url,
            "title": title,
            "html_base64": html_b64,
            "accessibility_tree": a11y,
            "interactive_elements": interactive,
            "screenshot_base64": screenshot_b64,
            "network_calls": network_calls,
        }

        return await self._post(f"/api/v1/recorder/{PROJECT_ID}/routes", json=payload)

    async def push_step(
        self,
        session_id: str,
        step_index: int,
        action: dict,
        page: Page,
        network_calls: list[dict],
    ) -> None:
        try:
            png = await page.screenshot(type="png")
            screenshot_b64 = base64.b64encode(png).decode()
        except Exception:
            screenshot_b64 = None

        payload = {
            "step_index": step_index,
            "action_type": action.get("type", "click"),
            "url": action.get("url"),
            "selector": action.get("selector"),
            "value": action.get("value"),
            "element_text": action.get("text"),
            "element_type": action.get("elementType"),
            "screenshot_base64": screenshot_b64,
            "network_calls": network_calls,
        }

        await self._post(
            f"/api/v1/recorder/{PROJECT_ID}/sessions/{session_id}/steps",
            json=payload,
        )

    # ── Recording loop ─────────────────────────────────────────────────────

    async def record_scenario(
        self,
        scenario: dict,
        pw: Any,
        context: BrowserContext,
    ) -> BrowserContext:
        scenario_id: str = scenario["id"]
        session_id = await self.create_session(scenario_id)
        self._current_session_id = session_id
        self._step_index = 0
        self._stop_event.clear()
        self.network_buffer.clear()
        self._capture_start = time.time()

        try:
            page = context.pages[0] if context.pages else await context.new_page()
        except Exception:
            print("  [INFO] Browser was closed. Relaunching...")
            context = await pw.chromium.launch_persistent_context(
                str(BROWSER_DATA_DIR),
                headless=False,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
                slow_mo=50,
            )
            page = context.pages[0] if context.pages else await context.new_page()

        # Attach network listener
        page.on("response", self._on_response)

        # Expose action callback to JS
        pending_actions: list[dict] = []
        
        # Navigate to the project URL if we are on a blank tab
        if self.project_url and page.url == "about:blank":
            try:
                await page.goto(self.project_url)
            except Exception:
                pass

        async def on_action(source, action_data: dict) -> None:
            # This is called from JS on every click/fill/keypress
            pending_actions.append(action_data)

        await page.expose_binding("__sqat_action__", on_action)
        await page.add_init_script(ACTION_CAPTURE_JS)

        await self.start_session(session_id)

        print(f"\n  ┌─ Recording: \"{scenario['title']}\"")
        print(f"  │  Navigate the application as a user would.")
        print(f"  │  Close the browser tab (or press Ctrl+C) when done.")
        print(f"  └─ Waiting for browser activity...\n")

        # Track last navigation URL so we don't double-capture same-page events
        last_captured_url: str = ""

        async def on_frame_navigated(frame) -> None:
            nonlocal last_captured_url
            if frame != page.main_frame:
                return
            await asyncio.sleep(0.8)  # let page settle
            current_url = page.url
            if current_url == last_captured_url:
                return
            last_captured_url = current_url
            network = self._drain_network()
            try:
                result = await self.upsert_route(session_id, scenario_id, page, network)
                path = urlparse(current_url).path or "/"
                tag = "NEW" if result.get("is_new_route") else "UPD"
                print(f"  [{tag}] Route captured: {path}")
            except Exception as e:
                print(f"  [ERR] Failed to capture route: {e}")

        page.on("framenavigated", lambda f: asyncio.ensure_future(on_frame_navigated(f)))

        # Process pending actions in a background task
        async def action_processor() -> None:
            while not self._stop_event.is_set():
                if pending_actions:
                    action = pending_actions.pop(0)
                    await asyncio.sleep(1.0)  # wait ~1s to capture correlated network
                    network = self._drain_network()
                    try:
                        await self.push_step(
                            session_id, self._step_index, action, page, network
                        )
                        self._step_index += 1
                        atype = action.get("type", "?")
                        sel = action.get("selector", "")[:40]
                        print(f"  [ACT] {atype:8s} → {sel}")
                    except Exception as e:
                        print(f"  [ERR] Failed to push step: {e}")
                else:
                    await asyncio.sleep(0.2)

        processor_task = asyncio.create_task(action_processor())

        # Wait for user to close the page or press Ctrl+C
        try:
            close_task = asyncio.create_task(page.wait_for_event("close", timeout=0))
            stop_task = asyncio.create_task(self._stop_event.wait())
            await asyncio.wait([close_task, stop_task], return_when=asyncio.FIRST_COMPLETED)
        except Exception:
            pass
        finally:
            self._stop_event.set()
            processor_task.cancel()
            try:
                await processor_task
            except asyncio.CancelledError:
                pass

        try:
            await self.complete_session(session_id)
            print(f"\n  ✅ Session completed — {self._step_index} steps captured")
        except Exception as e:
            print(f"\n  ⚠  Failed to mark session complete: {e}")

        self._current_session_id = None
        return context

    # ── Main loop ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        print("\n" + "═" * 50)
        print("  SQAT UI Discovery Recorder")
        print("  Project:", PROJECT_ID)
        print("  Server: ", SERVER_URL)
        print("═" * 50)

        print("\n  Connecting to server...")
        try:
            info = await self.fetch_project_info()
            self.project_url = info.get("project_url")
        except Exception as e:
            print(f"\n  ✗ Cannot reach server: {e}")
            print(f"    Make sure the server is running at {SERVER_URL}")
            print(f"    and your network can reach it.\n")
            return

        print(f"  ✓ Connected — Project: {info['project_name']}")

        async with async_playwright() as pw:
            print("\n  Launching browser (persistent session)...")
            context = await pw.chromium.launch_persistent_context(
                str(BROWSER_DATA_DIR),
                headless=False,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
                slow_mo=50,  # slight slow-down makes captures more reliable
            )
            print("  ✓ Browser ready. Log in to the application if needed.")
            print(f"    Your session will be saved at: {BROWSER_DATA_DIR}\n")

            while True:
                # Refresh scenario list each loop iteration
                try:
                    info = await self.fetch_project_info()
                except Exception:
                    print("  ⚠  Could not refresh scenario list from server")

                scenarios = info.get("scenarios", [])
                unrecorded = [s for s in scenarios if s["status"] != "completed"]

                if not unrecorded:
                    print("\n  🎉 All scenarios have been recorded!")
                    break

                print("\n  Scenarios to record:")
                for i, s in enumerate(unrecorded, 1):
                    desc = f" — {s['description'][:60]}" if s.get("description") else ""
                    print(f"    {i:2d}. {s['title']}{desc}")
                print("     q. Quit")

                choice = input("\n  Select a scenario to record: ").strip().lower()

                if choice == "q":
                    print("\n  Exiting recorder.")
                    break

                try:
                    idx = int(choice) - 1
                    if not (0 <= idx < len(unrecorded)):
                        raise ValueError
                    scenario = unrecorded[idx]
                except ValueError:
                    print("  Invalid choice, try again.")
                    continue

                try:
                    context = await self.record_scenario(scenario, pw, context)
                except Exception as e:
                    if "Target closed" in str(e) or "Browser has been closed" in str(e):
                        print("\n  Browser was closed manually. Ending recording session.")
                        continue
                    print(f"\n  [ERR] Recording interrupted: {e}")

            await context.close()
            await self.client.aclose()
            print("\n  Recorder closed. Goodbye!\n")


# ── Entry point ────────────────────────────────────────────────────────────

async def main() -> None:
    recorder = Recorder()

    # Handle Ctrl+C gracefully: complete the current session if one is open
    original_sigint = signal.getsignal(signal.SIGINT)

    def _sigint_handler(sig, frame):
        if recorder._current_session_id:
            print("\n\n  Ctrl+C detected — marking current session as complete...")
            asyncio.ensure_future(
                recorder.complete_session(recorder._current_session_id)
            )
            recorder._stop_event.set()
        else:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        await recorder.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())