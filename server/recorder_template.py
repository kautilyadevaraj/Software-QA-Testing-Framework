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
session is preserved across all scenarios for this project. Click 'Launch'
on any scenario in the Web UI — the daemon picks it up within ~1 second.
Press Ctrl+C when you have finished a scenario.
"""

from __future__ import annotations

import asyncio
import base64
import signal
import sys
import time
from pathlib import Path
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

ACTION_CAPTURE_JS = """
(function () {
  if (window.__sqat_installed__) return;
  window.__sqat_installed__ = true;

  function buildSelector(el) {
    if (!el || !el.tagName) return null;
    const tag = el.tagName.toLowerCase();
    if (el.dataset && el.dataset.testid)
      return { selector: `[data-testid="${el.dataset.testid}"]`, stability: 'high' };
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel)
      return { selector: `${tag}[aria-label="${ariaLabel}"]`, stability: 'high' };
    if (el.id && !el.id.match(/^[0-9a-f-]{20,}$/i))
      return { selector: `#${CSS.escape(el.id)}`, stability: 'high' };
    if (el.name)
      return { selector: `${tag}[name="${el.name}"]`, stability: 'medium' };
    const role = el.getAttribute('role');
    const text = (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 60);
    if (role && text)
      return { selector: `[role="${role}"]`, stability: 'medium', playwrightLocator: `getByRole('${role}', { name: '${text.replace(/'/g, "\\'")}' })` };
    if (el.placeholder)
      return { selector: `${tag}[placeholder="${el.placeholder}"]`, stability: 'medium' };
    return { selector: tag, stability: 'low' };
  }

  document.addEventListener('click', function (e) {
    const el = e.target;
    if (!el || el.tagName === 'HTML' || el.tagName === 'BODY') return;
    const sel = buildSelector(el);
    if (!sel) return;
    window.__sqat_action__({
      type: 'click', ...sel,
      text: (el.textContent || '').trim().substring(0, 120),
      elementType: el.tagName.toLowerCase(),
      url: window.location.href,
    }).catch(() => {});
  }, true);

  document.addEventListener('change', function (e) {
    const el = e.target;
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    if (tag !== 'input' && tag !== 'textarea' && tag !== 'select') return;
    const sel = buildSelector(el);
    if (!sel) return;
    window.__sqat_action__({
      type: tag === 'select' ? 'select' : 'fill', ...sel,
      value: el.type === 'password' ? '***REDACTED***' : (el.value || '').substring(0, 500),
      elementType: tag,
      url: window.location.href,
    }).catch(() => {});
  }, true);

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== 'Tab') return;
    const el = e.target;
    if (!el || !el.tagName) return;
    const sel = buildSelector(el);
    if (!sel) return;
    window.__sqat_action__({
      type: 'keypress', ...sel,
      value: e.key,
      elementType: el.tagName.toLowerCase(),
      url: window.location.href,
    }).catch(() => {});
  }, true);
})();

window.__sqat_get_elements__ = function () {
  const results = [];
  const seen = new Set();
  const SELECTORS = [
    'button:not([disabled])','a[href]','input:not([type="hidden"])',
    'select','textarea','[role="button"]','[role="link"]','[role="tab"]',
    '[role="checkbox"]','[role="radio"]','[role="menuitem"]','[role="combobox"]',
    '[role="switch"]','[role="option"]','[tabindex]:not([tabindex="-1"])',
  ];
  function buildSel(el) {
    const tag = el.tagName.toLowerCase();
    if (el.dataset && el.dataset.testid) return { selector: `[data-testid="${el.dataset.testid}"]`, stability: 'high' };
    const al = el.getAttribute('aria-label');
    if (al) return { selector: `${tag}[aria-label="${al}"]`, stability: 'high' };
    if (el.id && !el.id.match(/^[0-9a-f-]{20,}$/i)) return { selector: `#${CSS.escape(el.id)}`, stability: 'high' };
    if (el.name) return { selector: `${tag}[name="${el.name}"]`, stability: 'medium' };
    if (el.placeholder) return { selector: `${tag}[placeholder="${el.placeholder}"]`, stability: 'medium' };
    const text = (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 60);
    const role = el.getAttribute('role') || tag;
    if (text) return { selector: tag, stability: 'low', playwrightLocator: `getByRole('${role}', { name: '${text.replace(/'/g, "\\'")}' })` };
    return { selector: tag, stability: 'low' };
  }
  SELECTORS.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      if (seen.has(el)) return;
      seen.add(el);
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      if (getComputedStyle(el).display === 'none') return;
      const selInfo = buildSel(el);
      results.push({
        tag: el.tagName.toLowerCase(), type: el.type || null,
        text: (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 120),
        placeholder: el.placeholder || null, name: el.name || null, id: el.id || null,
        'data-testid': el.dataset ? el.dataset.testid || null : null,
        'aria-label': el.getAttribute('aria-label'),
        'aria-expanded': el.getAttribute('aria-expanded'),
        'aria-selected': el.getAttribute('aria-selected'),
        role: el.getAttribute('role'),
        href: el.tagName.toLowerCase() === 'a' ? el.getAttribute('href') : null,
        disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
        required: el.required || false,
        ...selInfo,
        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
      });
    });
  });
  return results;
};

window.__sqat_clean_html__ = function () {
  const clone = document.documentElement.cloneNode(true);
  clone.querySelectorAll('style,script,link[rel="stylesheet"],link[rel="preload"],noscript,meta,template,[aria-hidden="true"]').forEach(el => el.remove());
  clone.querySelectorAll('svg *').forEach(el => {
    ['d','points','transform','viewBox','fill','stroke','clip-path','filter','mask','opacity'].forEach(a => el.removeAttribute(a));
  });
  const KEEP = new Set([
    'id','name','type','href','src','alt','placeholder','role','for','action','method',
    'value','checked','selected','disabled','required','readonly','multiple','tabindex','target',
    'colspan','rowspan','scope',
    'aria-label','aria-labelledby','aria-describedby','aria-expanded','aria-selected',
    'aria-checked','aria-disabled','aria-controls','aria-current','aria-invalid','aria-required',
    'data-testid','data-test','data-cy','data-qa',
  ]);
  clone.querySelectorAll('*').forEach(el => {
    el.removeAttribute('style');
    el.removeAttribute('class');
    const toRemove = [];
    for (const attr of el.attributes) { if (!KEEP.has(attr.name)) toRemove.push(attr.name); }
    toRemove.forEach(a => el.removeAttribute(a));
  });
  return clone.outerHTML;
};

window.__sqat_get_page_context__ = function () {
  function text(el) { return el ? (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 200) : ''; }
  function bestSel(el) {
    if (!el) return null;
    const tag = el.tagName.toLowerCase();
    if (el.dataset && el.dataset.testid) return `[data-testid="${el.dataset.testid}"]`;
    const al = el.getAttribute('aria-label');
    if (al) return `${tag}[aria-label="${al}"]`;
    if (el.id && !el.id.match(/^[0-9a-f-]{20,}$/i)) return `#${CSS.escape(el.id)}`;
    if (el.name) return `${tag}[name="${el.name}"]`;
    return null;
  }
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4')).map(h => ({ level: parseInt(h.tagName[1]), text: text(h) })).filter(h => h.text);
  const forms = Array.from(document.querySelectorAll('form')).map(form => {
    const fields = Array.from(form.querySelectorAll('input:not([type="hidden"]),select,textarea')).map(f => ({
      tag: f.tagName.toLowerCase(), type: f.type || null, name: f.name || null,
      placeholder: f.placeholder || null, 'aria-label': f.getAttribute('aria-label'),
      'data-testid': f.dataset ? f.dataset.testid || null : null,
      required: f.required || false, selector: bestSel(f),
    }));
    const sub = form.querySelector('[type="submit"],button:not([type="button"])');
    return { action: form.getAttribute('action'), method: form.method || 'get', submit_text: sub ? text(sub) : null, fields };
  });
  const navLinks = Array.from(document.querySelectorAll('nav a,[role="navigation"] a,header a')).map(a => ({ text: text(a), href: a.getAttribute('href'), selector: bestSel(a) })).filter(l => l.text && l.href);
  const buttons = Array.from(document.querySelectorAll('button:not(form button),[role="button"]')).filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && !b.disabled; }).map(b => ({ text: text(b), selector: bestSel(b), 'aria-label': b.getAttribute('aria-label') })).filter(b => b.text || b['aria-label']).slice(0, 30);
  const tabs = Array.from(document.querySelectorAll('[role="tab"]')).map(t => ({ text: text(t), selected: t.getAttribute('aria-selected') === 'true', selector: bestSel(t) }));
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[role="alertdialog"],dialog[open]')).filter(el => { const r = el.getBoundingClientRect(); return r.width > 0; }).map(el => ({ title: text(el.querySelector('[aria-labelledby],h1,h2,h3')), text: text(el).substring(0, 300) }));
  return { url: window.location.href, title: document.title, headings, forms, nav_links: navLinks.slice(0, 40), buttons, tabs, dialogs };
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
        self._browser_dead = False

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

    # ── Page readiness ─────────────────────────────────────────────────────

    @staticmethod
    async def _wait_for_page_ready(page: Page, timeout: float = 12.0) -> None:
        ms = int(timeout * 1000)
        try:
            await page.wait_for_load_state("networkidle", timeout=ms)
        except Exception:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
        try:
            await page.evaluate(
                "() => Promise.all(Array.from(document.images).filter(i => !i.complete).map(i => new Promise(r => { i.onload = i.onerror = r; setTimeout(r, 5000); })))"
            )
        except Exception:
            pass
        try:
            await page.evaluate("() => document.fonts.ready")
        except Exception:
            pass
        for sel in ('[aria-busy="true"]', '.animate-spin', '.animate-pulse', '[data-loading="true"]'):
            try:
                await page.wait_for_function(f"() => !document.querySelector('{sel}')", timeout=4000)
                break
            except Exception:
                continue
        await asyncio.sleep(0.6)

    # ── Route capture ──────────────────────────────────────────────────────

    async def upsert_route(self, session_id: str, scenario_id: str, page: Page, network_calls: list[dict]) -> dict:
        await self._wait_for_page_ready(page)
        url = page.url

        try:
            title = await page.title()
        except Exception:
            title = None

        try:
            clean_html = await page.evaluate("window.__sqat_clean_html__ ? window.__sqat_clean_html__() : document.documentElement.outerHTML")
            html_b64 = base64.b64encode(clean_html.encode()).decode()
        except Exception:
            html_b64 = None

        try:
            a11y = await page.accessibility.snapshot(interesting_only=True)
        except Exception:
            a11y = None

        try:
            interactive = await page.evaluate("window.__sqat_get_elements__ ? window.__sqat_get_elements__() : []")
        except Exception:
            interactive = []

        try:
            page_context = await page.evaluate("window.__sqat_get_page_context__ ? window.__sqat_get_page_context__() : null")
        except Exception:
            page_context = None

        try:
            png = await page.screenshot(full_page=True, type="png", animations="disabled")
            screenshot_b64 = base64.b64encode(png).decode()
        except Exception:
            screenshot_b64 = None

        payload = {
            "session_id": session_id,
            "scenario_id": scenario_id,
            "url": url,
            "title": title,
            "html_base64": html_b64,
            "accessibility_tree": {"accessibility_tree": a11y, "page_context": page_context},
            "interactive_elements": interactive,
            "screenshot_base64": screenshot_b64,
            "network_calls": network_calls,
        }
        return await self._post(f"/api/v1/recorder/{PROJECT_ID}/routes", json=payload)

    # ── Step capture ───────────────────────────────────────────────────────

    async def push_step(self, session_id: str, step_index: int, action: dict, page: Page, network_calls: list[dict]) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass
        await asyncio.sleep(0.3)

        try:
            png = await page.screenshot(type="png", animations="disabled")
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
            "playwright_locator": action.get("playwrightLocator"),
            "selector_stability": action.get("stability"),
            "screenshot_base64": screenshot_b64,
            "network_calls": network_calls,
        }
        await self._post(f"/api/v1/recorder/{PROJECT_ID}/sessions/{session_id}/steps", json=payload)

    # ── Recording loop ─────────────────────────────────────────────────────

    async def record_scenario(self, scenario: dict, context: BrowserContext) -> None:
        scenario_id: str = scenario["id"]
        target_url = scenario.get("url")

        # Guard: if the browser context is dead, signal the outer loop to restart
        try:
            # Create a fresh page to avoid expose_binding conflicts across sessions
            page = await context.new_page()
            # Close any old pages
            for p in context.pages:
                if p != page:
                    try:
                        await p.close()
                    except Exception:
                        pass
        except Exception:
            print("  [INFO] Browser context is gone — will relaunch on next trigger.")
            self._browser_dead = True
            raise RuntimeError("browser_dead")

        session_id = await self.create_session(scenario_id)
        self._current_session_id = session_id
        self._step_index = 0
        self._stop_event.clear()
        self.network_buffer.clear()
        self._capture_start = time.time()

        page.on("response", self._on_response)

        pending_actions: list[dict] = []

        async def on_action(source, action_data: dict) -> None:
            pending_actions.append(action_data)

        await page.expose_binding("__sqat_action__", on_action)
        await page.add_init_script(ACTION_CAPTURE_JS)
        await self.start_session(session_id)

        if target_url:
            try:
                await page.goto(target_url, wait_until="domcontentloaded")
            except Exception:
                pass

        print(f"\n  ┌─ Recording: \"{scenario['title']}\"")
        print(f"  │  Navigate the application as a user would.")
        print(f"  │  Close the browser tab (or press Ctrl+C) when done.")
        print(f"  └─ Waiting for browser activity...\n")

        last_captured_url: str = ""

        async def on_frame_navigated(frame) -> None:
            nonlocal last_captured_url
            if frame != page.main_frame:
                return
            await self._wait_for_page_ready(page)
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

        async def action_processor() -> None:
            while not self._stop_event.is_set():
                if pending_actions:
                    action = pending_actions.pop(0)
                    await asyncio.sleep(1.0)
                    network = self._drain_network()
                    try:
                        await self.push_step(session_id, self._step_index, action, page, network)
                        self._step_index += 1
                        atype = action.get("type", "?")
                        sel = action.get("selector", "")[:40]
                        print(f"  [ACT] {atype:8s} → {sel}")
                    except Exception as e:
                        print(f"  [ERR] Failed to push step: {e}")
                else:
                    await asyncio.sleep(0.2)

        async def status_poller() -> None:
            while not self._stop_event.is_set():
                try:
                    res = await self._get(f"/api/v1/recorder/{PROJECT_ID}/sessions/{session_id}/status")
                    if res.get("status") in ("completed", "failed"):
                        print(f"\n  [INFO] Stop signal received from Web UI.")
                        self._stop_event.set()
                        try:
                            await page.close()
                        except Exception:
                            pass
                        break
                except Exception:
                    pass
                await asyncio.sleep(2)

        processor_task = asyncio.create_task(action_processor())
        poller_task = asyncio.create_task(status_poller())

        try:
            await page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        finally:
            self._stop_event.set()
            processor_task.cancel()
            poller_task.cancel()
            try:
                await processor_task
                await poller_task
            except asyncio.CancelledError:
                pass
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass

        try:
            await self.complete_session(session_id)
            print(f"\n  ✅ Session completed — {self._step_index} steps captured")
        except Exception as e:
            print(f"\n  ⚠  Failed to mark session complete: {e}")

        self._current_session_id = None

    # ── Main loop (pulse-based) ────────────────────────────────────────────

    async def run(self) -> None:
        print("\n" + "═" * 50)
        print("  SQAT UI Discovery Recorder")
        print("  Project:", PROJECT_ID)
        print("  Server: ", SERVER_URL)
        print("═" * 50)

        print("\n  Connecting to server...")
        try:
            info = await self.fetch_project_info()
        except Exception as e:
            print(f"\n  ✗ Cannot reach server: {e}")
            print(f"    Make sure the server is running at {SERVER_URL}\n")
            return

        print(f"  ✓ Connected — Project: {info['project_name']}")

        pending_scenario = None

        # Outer restart loop — re-enters async_playwright if driver connection dies
        while True:
            self._browser_dead = False
            try:
                async with async_playwright() as pw:
                    print("\n  Launching browser (persistent session)...")
                    context = await pw.chromium.launch_persistent_context(
                        str(BROWSER_DATA_DIR),
                        headless=False,
                        args=["--no-sandbox", "--disable-dev-shm-usage"],
                        slow_mo=50,
                    )
                    print("  ✓ Browser ready. Log in to the application if needed.")
                    print(f"    Session saved at: {BROWSER_DATA_DIR}")
                    print()
                    print("  ┌─────────────────────────────────────────────────────┐")
                    print("  │  Idle — waiting for launch signal from Web UI...    │")
                    print("  │  Click 'Launch' on any scenario in the dashboard.   │")
                    print("  │  Press Ctrl+C to exit.                              │")
                    print("  └─────────────────────────────────────────────────────┘\n")

                    while True:
                        if pending_scenario:
                            scenario_id = pending_scenario["id"]
                            scenario_title = pending_scenario["title"]
                            project_url = pending_scenario.get("url")
                            pending_scenario = None
                        else:
                            try:
                                pulse = await self._get(f"/api/v1/recorder/{PROJECT_ID}/pulse")
                            except Exception as e:
                                print(f"  [WARN] Pulse check failed: {e} — retrying...")
                                await asyncio.sleep(3)
                                continue

                            scenario_id = pulse.get("scenario_id")
                            if not scenario_id:
                                await asyncio.sleep(1)
                                continue

                            scenario_title = pulse.get("scenario_title") or scenario_id
                            project_url = pulse.get("project_url")
                            print(f"\n  ⚡ Launch signal received → \"{scenario_title}\"")

                        try:
                            await self.record_scenario(
                                {"id": scenario_id, "title": scenario_title, "url": project_url, "description": ""},
                                context,
                            )
                        except RuntimeError as e:
                            if str(e) == "browser_dead":
                                pending_scenario = {"id": scenario_id, "title": scenario_title, "url": project_url}
                                break  # exit inner loop → re-enter async_playwright
                            print(f"\n  [ERR] Recording interrupted: {e}")
                        except Exception as e:
                            dead = ("Target closed", "Browser has been closed", "Connection closed", "driver")
                            if any(s in str(e) for s in dead):
                                print("\n  Browser connection lost — restarting playwright...")
                                pending_scenario = {"id": scenario_id, "title": scenario_title, "url": project_url}
                                break
                            print(f"\n  [ERR] Recording interrupted: {e}")

                        if not self._browser_dead:
                            print("\n  Idle — waiting for next launch signal from Web UI...\n")

            except KeyboardInterrupt:
                print("\n  Recorder closed. Goodbye!\n")
                return
            except Exception as e:
                print(f"\n  [ERR] Playwright crashed: {e}")

            if self._browser_dead or pending_scenario:
                print("  Restarting browser in 2 seconds...\n")
                await asyncio.sleep(2)


# ── Entry point ────────────────────────────────────────────────────────────

async def main() -> None:
    recorder = Recorder()
    loop = asyncio.get_event_loop()

    def _sigint_handler(sig, frame):
        if recorder._current_session_id:
            print("\n\n  Ctrl+C detected — marking current session as complete...")
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(recorder.complete_session(recorder._current_session_id))
            )
            loop.call_soon_threadsafe(recorder._stop_event.set)
        else:
            loop.call_soon_threadsafe(loop.stop)

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        await recorder.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())