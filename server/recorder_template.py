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
STORE_PASSWORD_VALUES: bool = __STORE_PASSWORD_VALUES__
SCREENSHOT_INDICATOR: bool = __SCREENSHOT_INDICATOR__

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
        self._current_flow_id: str | None = None
        self._step_index: int = 0
        self._snapshot_index: int = 0
        self._last_snapshot_id: str | None = None
        self._browser_dead = False
        self._shutdown_requested = False
        self._action_handler = None

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

    async def _dispatch_action(self, source, action_data: dict) -> None:
        handler = self._action_handler
        if handler is not None:
            await handler(source, action_data)

    # ── Network tracking ───────────────────────────────────────────────────

    def _on_response(self, response) -> None:
        try:
            resource_type = getattr(response.request, "resource_type", None)
            parsed = urlparse(response.url)
            path = parsed.path.lower()
            static_ext = (
                ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
                ".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".otf", ".map",
            )
            is_static_asset = bool(resource_type in {"image", "stylesheet", "font", "script"} or path.endswith(static_ext))
            is_api_call = bool(resource_type in {"xhr", "fetch"} or "/api/" in path or response.request.method.upper() != "GET")
            self.network_buffer.append({
                "ts": round(time.time() - self._capture_start, 3),
                "method": response.request.method,
                "url": response.url,
                "status": response.status,
                "resource_type": resource_type,
                "is_static_asset": is_static_asset,
                "is_api_call": is_api_call,
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

    async def create_session(self, scenario_id: str) -> dict:
        data = await self._post(
            f"/api/v1/recorder/{PROJECT_ID}/sessions",
            json={"scenario_id": scenario_id},
        )
        return data

    def _next_snapshot_index(self) -> int:
        current = self._snapshot_index
        self._snapshot_index += 1
        return current

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

    async def _capture_png(self, page: Page, *, full_page: bool = False) -> bytes:
        try:
            return await page.screenshot(
                full_page=full_page,
                type="png",
                animations="disabled",
            )
        except TypeError:
            return await page.screenshot(
                full_page=full_page,
                type="png",
                animations="disabled",
            )

    async def _enrich_action_after_delay(self, page: Page, action: dict) -> dict:
        before_url = action.get("urlBefore") or action.get("url")
        try:
            after = await page.evaluate(
                "(action) => window.__sqat_enrich_after_action__ ? window.__sqat_enrich_after_action__(action) : null",
                action,
            )
        except Exception:
            after = None

        after_url = page.url
        if isinstance(after, dict) and after.get("url"):
            after_url = after.get("url")

        caused_navigation = bool(before_url and after_url and before_url != after_url)
        semantic = dict(action.get("semanticContext") or {})
        semantic["page"] = {
            "url_before": before_url,
            "url_current": action.get("url"),
            "url_after": after_url,
            "title_after": after.get("title") if isinstance(after, dict) else None,
        }
        semantic["navigation"] = {
            "caused_navigation": caused_navigation,
            "from": before_url,
            "to": after_url if caused_navigation else None,
        }
        if isinstance(after, dict):
            if after.get("after_state") is not None:
                action["afterState"] = after.get("after_state")
                semantic["after_state"] = after.get("after_state")
            if after.get("visible_options"):
                semantic["visible_options_after"] = after.get("visible_options")

        action["urlAfter"] = after_url
        action["causedNavigation"] = caused_navigation
        action["semanticContext"] = semantic
        return action

    async def _ensure_idle_project_page(self, context: BrowserContext, project_url: str | None) -> Page | None:
        if not project_url:
            return None

        for page in context.pages:
            if not page.is_closed():
                try:
                    if page.url != "about:blank":
                        return page
                except Exception:
                    pass

        page = context.pages[0] if context.pages else await context.new_page()
        try:
            if page.url != project_url:
                await page.goto(project_url, wait_until="domcontentloaded")
            print(f"  Opened project URL: {project_url}")
            return page
        except Exception as e:
            print(f"  [WARN] Could not open project URL {project_url}: {e}")
            return page

    # ── Route capture ──────────────────────────────────────────────────────

    async def upsert_route(
        self,
        session_id: str,
        scenario_id: str,
        flow_id: str | None,
        page: Page,
        network_calls: list[dict],
        *,
        snapshot_index: int,
        snapshot_kind: str,
        metadata: dict | None = None,
    ) -> dict:
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
            assertion_candidates = await page.evaluate("window.__sqat_get_assertion_candidates__ ? window.__sqat_get_assertion_candidates__() : []")
        except Exception:
            assertion_candidates = []

        try:
            png = await self._capture_png(page, full_page=True)
            screenshot_b64 = base64.b64encode(png).decode()
        except Exception:
            screenshot_b64 = None

        payload = {
            "session_id": session_id,
            "scenario_id": scenario_id,
            "flow_id": flow_id,
            "snapshot_index": snapshot_index,
            "snapshot_kind": snapshot_kind,
            "url": url,
            "title": title,
            "html_base64": html_b64,
            "accessibility_tree": {"accessibility_tree": a11y, "page_context": page_context},
            "interactive_elements": interactive,
            "assertion_candidates": assertion_candidates,
            "screenshot_base64": screenshot_b64,
            "network_calls": network_calls,
            "metadata_json": metadata or {},
        }
        return await self._post(f"/api/v1/recorder/{PROJECT_ID}/routes", json=payload)

    # ── Step capture ───────────────────────────────────────────────────────

    async def push_step(
        self,
        session_id: str,
        scenario_id: str,
        flow_id: str | None,
        step_index: int,
        action: dict,
        page: Page,
        network_calls: list[dict],
    ) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass
        await asyncio.sleep(0.3)

        before_snapshot_id = self._last_snapshot_id
        after_snapshot_id = None
        try:
            action = await self._enrich_action_after_delay(page, action)
            snapshot = await self.upsert_route(
                session_id,
                scenario_id,
                flow_id,
                page,
                network_calls,
                snapshot_index=self._next_snapshot_index(),
                snapshot_kind=f"after_step_{step_index:04d}",
                metadata={
                    "step_index": step_index,
                    "action_type": action.get("type"),
                    "url_before": action.get("urlBefore") or action.get("url"),
                    "url_after": action.get("urlAfter"),
                },
            )
            after_snapshot_id = snapshot.get("variant_id")
            self._last_snapshot_id = after_snapshot_id
            screenshot_b64 = None
        except Exception:
            screenshot_b64 = None

        payload = {
            "step_index": step_index,
            "flow_id": flow_id,
            "action_type": action.get("type", "click"),
            "url": action.get("url"),
            "selector": action.get("selector"),
            "selector_candidates": action.get("selectorCandidates"),
            "value": action.get("value"),
            "input_value_kind": action.get("inputValueKind"),
            "element_text": action.get("text"),
            "element_type": action.get("elementType"),
            "playwright_locator": action.get("playwrightLocator"),
            "selector_stability": action.get("stability"),
            "accessible_name": action.get("accessibleName"),
            "role": action.get("role"),
            "label": action.get("label"),
            "input_type": action.get("inputType"),
            "url_before": action.get("urlBefore") or action.get("url"),
            "url_after": action.get("urlAfter"),
            "caused_navigation": action.get("causedNavigation"),
            "route_variant_before_id": before_snapshot_id,
            "route_variant_after_id": after_snapshot_id,
            "semantic_context": action.get("semanticContext"),
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
            page = next((p for p in context.pages if not p.is_closed()), None)
            if page is None:
                page = await context.new_page()
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

        session_info = await self.create_session(scenario_id)
        session_id = session_info["id"]
        flow_id = session_info.get("flow_id")
        self._current_session_id = session_id
        self._current_flow_id = flow_id
        self._step_index = 0
        self._snapshot_index = 0
        self._last_snapshot_id = None
        self._stop_event.clear()
        self.network_buffer.clear()
        self._capture_start = time.time()

        page.on("response", self._on_response)

        pending_actions: list[dict] = []

        async def on_action(source, action_data: dict) -> None:
            pending_actions.append(action_data)

        self._action_handler = on_action
        try:
            await page.expose_binding("__sqat_action__", self._dispatch_action)
        except Exception as e:
            if "already" not in str(e).lower() and "registered" not in str(e).lower():
                raise
        await page.add_init_script(
            f"window.__sqat_store_password_values__ = {str(STORE_PASSWORD_VALUES).lower()};\n"
            + ACTION_CAPTURE_JS
        )
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

        async def capture_current_route(snapshot_kind: str, metadata: dict | None = None) -> dict | None:
            await self._wait_for_page_ready(page)
            current_url = page.url
            network = self._drain_network()
            try:
                result = await self.upsert_route(
                    session_id,
                    scenario_id,
                    flow_id,
                    page,
                    network,
                    snapshot_index=self._next_snapshot_index(),
                    snapshot_kind=snapshot_kind,
                    metadata=metadata,
                )
                self._last_snapshot_id = result.get("variant_id")
                path = urlparse(current_url).path or "/"
                tag = "NEW" if result.get("is_new_route") else "UPD"
                print(f"  [{tag}] Route captured: {path}")
                return result
            except Exception as e:
                print(f"  [ERR] Failed to capture route: {e}")
                return None

        await capture_current_route("initial", {"source": "recording_start"})

        async def action_processor() -> None:
            while not self._stop_event.is_set():
                if pending_actions:
                    action = pending_actions.pop(0)
                    await asyncio.sleep(1.0)
                    network = self._drain_network()
                    try:
                        await self.push_step(session_id, scenario_id, flow_id, self._step_index, action, page, network)
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
                        break
                except Exception:
                    pass
                await asyncio.sleep(2)

        async def all_pages_closed() -> None:
            while not self._stop_event.is_set():
                try:
                    if not [p for p in context.pages if not p.is_closed()]:
                        self._stop_event.set()
                        break
                except Exception:
                    self._browser_dead = True
                    self._stop_event.set()
                    break
                await asyncio.sleep(0.5)

        processor_task = asyncio.create_task(action_processor())
        poller_task = asyncio.create_task(status_poller())
        pages_closed_task = asyncio.create_task(all_pages_closed())

        stop_task = asyncio.create_task(self._stop_event.wait())
        try:
            await asyncio.wait(
                {pages_closed_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            pages_closed_task.cancel()
            stop_task.cancel()
            self._stop_event.set()
            processor_task.cancel()
            poller_task.cancel()
            try:
                await asyncio.gather(processor_task, poller_task, pages_closed_task, return_exceptions=True)
            except asyncio.CancelledError:
                pass

        try:
            await self.complete_session(session_id)
            print(f"\n  ✅ Session completed — {self._step_index} steps captured")
        except Exception as e:
            print(f"\n  ⚠  Failed to mark session complete: {e}")

        self._current_session_id = None
        self._current_flow_id = None
        self._action_handler = None

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

        idle_project_url = info.get("project_url")
        pending_scenario = None

        # Outer restart loop — re-enters async_playwright if driver connection dies
        while not self._shutdown_requested:
            self._browser_dead = False
            try:
                async with async_playwright() as pw:
                    if self._shutdown_requested:
                        break
                    print("\n  Launching browser (persistent session)...")
                    context = await pw.chromium.launch_persistent_context(
                        str(BROWSER_DATA_DIR),
                        headless=False,
                        args=["--no-sandbox", "--disable-dev-shm-usage"],
                        slow_mo=50,
                    )
                    print("  ✓ Browser ready. Log in to the application if needed.")
                    print(f"    Session saved at: {BROWSER_DATA_DIR}")
                    await self._ensure_idle_project_page(context, idle_project_url)
                    print()
                    print("  ┌─────────────────────────────────────────────────────┐")
                    print("  │  Idle — waiting for launch signal from Web UI...    │")
                    print("  │  Click 'Launch' on any scenario in the dashboard.   │")
                    print("  │  Press Ctrl+C to exit.                              │")
                    print("  └─────────────────────────────────────────────────────┘\n")

                    while not self._shutdown_requested:
                        if pending_scenario:
                            scenario_id = pending_scenario["id"]
                            scenario_title = pending_scenario["title"]
                            project_url = pending_scenario.get("url")
                            pending_scenario = None
                        else:
                            try:
                                pulse = await self._get(f"/api/v1/recorder/{PROJECT_ID}/pulse")
                            except Exception as e:
                                if self._shutdown_requested:
                                    break
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

                        if self._shutdown_requested:
                            break

                        if not self._browser_dead:
                            print("\n  Idle — waiting for next launch signal from Web UI...\n")

            except KeyboardInterrupt:
                print("\n  Recorder closed. Goodbye!\n")
                return
            except Exception as e:
                print(f"\n  [ERR] Playwright crashed: {e}")

            if self._shutdown_requested:
                break

            if self._browser_dead or pending_scenario:
                print("  Restarting browser in 2 seconds...\n")
                await asyncio.sleep(2)

        print("\n  Recorder closed. Goodbye!\n")


# ── Entry point ────────────────────────────────────────────────────────────

async def main() -> None:
    recorder = Recorder()
    loop = asyncio.get_event_loop()

    def _sigint_handler(sig, frame):
        recorder._shutdown_requested = True
        if recorder._current_session_id:
            print("\n\n  Ctrl+C detected — marking current session as complete...")
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(recorder.complete_session(recorder._current_session_id))
            )
        else:
            print("\n\n  Ctrl+C detected â€” shutting down recorder...")
        loop.call_soon_threadsafe(recorder._stop_event.set)

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        await recorder.run()
    except KeyboardInterrupt:
        pass
    finally:
        await recorder.client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
