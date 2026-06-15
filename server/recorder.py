#!/usr/bin/env python3
"""
SQAT UI Discovery Recorder
--------------------------
Generated for project: 019ea315-4dbe-79cf-a060-f8ba49219c95
Server:                http://localhost:8000

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

PROJECT_ID: str = "019ea315-4dbe-79cf-a060-f8ba49219c95"
SERVER_URL: str = "http://localhost:8000"
RECORDER_TOKEN: str = "292c50df-36d1-4ee1-bdc7-2267faf5bf85"
STORE_PASSWORD_VALUES: bool = str("False") == "True"
SCREENSHOT_INDICATOR: bool = str("True") == "True"

BROWSER_DATA_DIR = Path.home() / ".sqat" / PROJECT_ID
BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── JavaScript injected into every page ───────────────────────────────────

ACTION_CAPTURE_JS = '(function () {\n  if (window.__sqat_installed__) return;\n  window.__sqat_installed__ = true;\n\n  const STORE_PASSWORD_VALUES = window.__sqat_store_password_values__ === true;\n  const focusState = new WeakMap();\n  const dirtyInputs = new WeakSet();\n  const lastInputCommit = new WeakMap();\n  const BARE = new Set([\'a\', \'button\', \'input\', \'select\', \'textarea\', \'div\', \'span\']);\n\n  function text(v, n = 200) {\n    return (v || \'\').toString().replace(/\\s+/g, \' \').trim().substring(0, n);\n  }\n  function esc(v) {\n    return String(v || \'\').replace(/["\\\\]/g, \'\\\\$&\');\n  }\n  function css(v) {\n    return window.CSS && CSS.escape ? CSS.escape(v) : esc(v);\n  }\n  function attr(el, name) {\n    return el && el.getAttribute ? el.getAttribute(name) : null;\n  }\n  function visible(el) {\n    if (!el || !el.getBoundingClientRect) return false;\n    const r = el.getBoundingClientRect();\n    const s = window.getComputedStyle(el);\n    return r.width > 0 && r.height > 0 && s.display !== \'none\' && s.visibility !== \'hidden\';\n  }\n  function cleanHref(el) {\n    const href = attr(el, \'href\');\n    return href ? href.trim() : \'\';\n  }\n  function meaningfulHref(el) {\n    const href = cleanHref(el).toLowerCase();\n    return !!href && href !== \'#\' && href !== \'javascript:void(0)\' && href !== \'javascript:;\';\n  }\n  function adLike(el) {\n    if (!el || !el.tagName) return false;\n    const bits = [\n      attr(el, \'aria-label\'),\n      attr(el, \'title\'),\n      attr(el, \'id\'),\n      attr(el, \'name\'),\n      attr(el, \'src\'),\n      attr(el, \'class\'),\n    ].join(\' \').toLowerCase();\n    return /advertisement|googleads|doubleclick|pagead|adsystem|adservice|googlesyndication/.test(bits);\n  }\n  function stableId(id) {\n    return !!id && !/^[0-9a-f-]{16,}$/i.test(id) && !/^(ember|react|radix|headlessui|mui|chakra|mantine|rc|auto|generated)[-_]?[0-9]/i.test(id);\n  }\n  function role(el) {\n    const explicit = attr(el, \'role\');\n    if (explicit) return explicit;\n    const tag = (el.tagName || \'\').toLowerCase();\n    const type = (el.type || \'\').toLowerCase();\n    if (tag === \'button\' || type === \'button\' || type === \'submit\') return \'button\';\n    if (tag === \'a\' && attr(el, \'href\')) return \'link\';\n    if (tag === \'select\') return \'combobox\';\n    if (tag === \'textarea\') return \'textbox\';\n    if (tag === \'input\') {\n      if (type === \'checkbox\') return \'checkbox\';\n      if (type === \'radio\') return \'radio\';\n      if (type === \'range\') return \'slider\';\n      return \'textbox\';\n    }\n    return tag || null;\n  }\n  function label(el) {\n    const labelledBy = attr(el, \'aria-labelledby\');\n    if (labelledBy) {\n      const t = labelledBy.split(/\\s+/).map(id => text(document.getElementById(id)?.textContent)).filter(Boolean).join(\' \');\n      if (t) return t;\n    }\n    if (el.id) {\n      const l = document.querySelector(`label[for="${esc(el.id)}"]`);\n      if (l) return text(l.textContent);\n    }\n    const wrap = el.closest ? el.closest(\'label\') : null;\n    return wrap ? text(wrap.textContent) : \'\';\n  }\n  function nameOf(el) {\n    return text(attr(el, \'aria-label\')) || label(el) || text(attr(el, \'alt\')) || text(attr(el, \'title\')) || text(el.placeholder) || text(el.innerText || el.textContent || el.value, 120);\n  }\n  function locator(el, accessible, r) {\n    const safe = String(accessible || \'\').replace(/[.*+?^${}()|[\\]\\\\]/g, \'\\\\$&\').replace(/\'/g, "\\\\\'");\n    if (r && safe) return `page.getByRole(\'${r}\', { name: /${safe}/i })`;\n    const l = label(el).replace(/\'/g, "\\\\\'");\n    if (l && [\'input\', \'textarea\', \'select\'].includes((el.tagName || \'\').toLowerCase())) return `page.getByLabel(\'${l}\')`;\n    return null;\n  }\n  function selector(el) {\n    if (!el || !el.tagName) return null;\n    const tag = el.tagName.toLowerCase();\n    for (const a of [\'data-testid\', \'data-test\', \'data-cy\', \'data-qa\']) {\n      const v = attr(el, a);\n      if (v) return { selector: `[${a}="${esc(v)}"]`, stability: \'high\', quality_reason: \'data_attr\' };\n    }\n    const aria = attr(el, \'aria-label\');\n    if (aria) return { selector: `${tag}[aria-label="${esc(aria)}"]`, stability: \'high\', quality_reason: \'role_name\' };\n    if (el.id && stableId(el.id)) return { selector: `#${css(el.id)}`, stability: \'high\', quality_reason: \'stable_id\' };\n    if (el.name) return { selector: `${tag}[name="${esc(el.name)}"]`, stability: \'medium\', quality_reason: \'placeholder\' };\n    if (el.placeholder) return { selector: `${tag}[placeholder="${esc(el.placeholder)}"]`, stability: \'medium\', quality_reason: \'placeholder\' };\n    if (tag === \'a\' && meaningfulHref(el)) return { selector: `${tag}[href="${esc(cleanHref(el))}"]`, stability: \'medium\', quality_reason: \'href\' };\n    const r = role(el);\n    const n = nameOf(el);\n    if (r && n && ![\'div\', \'span\', \'i\', \'svg\', \'path\'].includes(tag)) return { selector: domPath(el) || tag, stability: \'low\', quality_reason: \'role_name_fallback\' };\n    return { selector: domPath(el) || tag, stability: \'low\', quality_reason: \'structural_fallback\' };\n  }\n  function selectorCandidates(el) {\n    if (!el || !el.tagName) return [];\n    const tag = el.tagName.toLowerCase();\n    const out = [];\n    const push = v => {\n      if (v && !out.includes(v)) out.push(v);\n    };\n    for (const a of [\'data-testid\', \'data-test\', \'data-cy\', \'data-qa\']) {\n      const v = attr(el, a);\n      if (v) push(`[${a}="${esc(v)}"]`);\n    }\n    if (attr(el, \'aria-label\')) push(`${tag}[aria-label="${esc(attr(el, \'aria-label\'))}"]`);\n    if (el.id && stableId(el.id)) push(`#${css(el.id)}`);\n    if (el.name) push(`${tag}[name="${esc(el.name)}"]`);\n    if (el.placeholder) push(`${tag}[placeholder="${esc(el.placeholder)}"]`);\n    if (tag === \'a\' && meaningfulHref(el)) push(`${tag}[href="${esc(cleanHref(el))}"]`);\n    const l = label(el);\n    if (l && [\'input\', \'textarea\', \'select\'].includes(tag)) push(`label:${l}`);\n    const r = role(el);\n    const n = nameOf(el);\n    if (r && n) push(`role:${r}[name="${n}"]`);\n    push(domPath(el));\n    return out.slice(0, 8);\n  }\n  function domPath(el) {\n    const parts = [];\n    let node = el;\n    while (node && node.nodeType === 1 && parts.length < 8) {\n      const tag = node.tagName.toLowerCase();\n      if (node.id && stableId(node.id)) {\n        parts.unshift(`${tag}#${css(node.id)}`);\n        break;\n      }\n      let i = 1;\n      let p = node.previousElementSibling;\n      while (p) {\n        if (p.tagName === node.tagName) i += 1;\n        p = p.previousElementSibling;\n      }\n      parts.unshift(`${tag}:nth-of-type(${i})`);\n      node = node.parentElement;\n    }\n    return parts.join(\' > \');\n  }\n  function state(el) {\n    if (!el) return null;\n    const type = (el.type || \'\').toLowerCase();\n    const redacted = type === \'password\' && !STORE_PASSWORD_VALUES;\n    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;\n    const opt = el.tagName?.toLowerCase() === \'select\' ? el.options?.[el.selectedIndex] : null;\n    return {\n      value: redacted ? null : (el.value == null ? null : String(el.value).substring(0, 500)),\n      value_redacted: redacted,\n      checked: typeof el.checked === \'boolean\' ? el.checked : null,\n      selected_value: el.tagName?.toLowerCase() === \'select\' ? el.value : null,\n      selected_label: opt ? text(opt.label || opt.textContent) : null,\n      aria_expanded: attr(el, \'aria-expanded\'),\n      aria_selected: attr(el, \'aria-selected\'),\n      aria_checked: attr(el, \'aria-checked\'),\n      aria_pressed: attr(el, \'aria-pressed\'),\n      aria_invalid: attr(el, \'aria-invalid\'),\n      required: el.required === true || attr(el, \'aria-required\') === \'true\',\n      disabled: el.disabled === true || attr(el, \'aria-disabled\') === \'true\',\n      min: attr(el, \'min\'),\n      max: attr(el, \'max\'),\n      step: attr(el, \'step\'),\n      rect: r ? {\n        x: Math.round(r.x),\n        y: Math.round(r.y),\n        width: Math.round(r.width),\n        height: Math.round(r.height),\n        page_x: Math.round(r.x + window.scrollX),\n        page_y: Math.round(r.y + window.scrollY),\n      } : null,\n    };\n  }\n  function options(el) {\n    if (el && el.tagName?.toLowerCase() === \'select\') {\n      return Array.from(el.options || []).map((o, i) => ({\n        index: i,\n        value: o.value,\n        label: text(o.label || o.textContent),\n        selected: o.selected,\n        disabled: o.disabled,\n      }));\n    }\n    const controlled = el ? document.getElementById(attr(el, \'aria-controls\')) : null;\n    const root = controlled || document;\n    return Array.from(root.querySelectorAll(\'[role="listbox"] [role="option"],[role="option"],[role="menu"] [role="menuitem"],[role="menuitem"]\'))\n      .filter(visible)\n      .slice(0, 80)\n      .map((o, i) => ({\n        index: i,\n        text: nameOf(o) || text(o.textContent),\n        value: attr(o, \'data-value\') || attr(o, \'value\'),\n        role: role(o),\n        selected: attr(o, \'aria-selected\') === \'true\' || attr(o, \'aria-checked\') === \'true\',\n        disabled: o.disabled === true || attr(o, \'aria-disabled\') === \'true\',\n        selector: selector(o)?.selector || null,\n      }));\n  }\n  function group(el) {\n    const type = (el.type || \'\').toLowerCase();\n    if (el.name && [\'checkbox\', \'radio\'].includes(type)) {\n      return {\n        name: el.name,\n        role: type === \'radio\' ? \'radiogroup\' : \'checkbox-group\',\n        options: Array.from(document.querySelectorAll(`input[type="${type}"][name="${esc(el.name)}"]`)).map((n, i) => ({\n          index: i,\n          label: nameOf(n),\n          value: n.value || null,\n          checked: n.checked === true,\n          selector: selector(n)?.selector || null,\n        })),\n      };\n    }\n    const g = el.closest?.(\'[role="radiogroup"],[role="group"],fieldset\');\n    if (!g) return null;\n    return {\n      label: nameOf(g) || text(g.querySelector(\'legend\')?.textContent),\n      role: attr(g, \'role\') || g.tagName.toLowerCase(),\n      options: Array.from(g.querySelectorAll(\'[role="radio"],[role="checkbox"],[role="switch"],input[type="radio"],input[type="checkbox"]\')).map((n, i) => ({\n        index: i,\n        label: nameOf(n),\n        checked: n.checked === true || attr(n, \'aria-checked\') === \'true\',\n        selector: selector(n)?.selector || null,\n      })),\n    };\n  }\n  function parent(el) {\n    const p = el.closest?.(\'form,nav,header,main,section,article,[role="dialog"],[role="navigation"],[role="main"],[role="group"],fieldset\') || el.parentElement;\n    return p ? {\n      tag: p.tagName.toLowerCase(),\n      role: role(p),\n      label: nameOf(p) || text(p.querySelector?.(\'legend,h1,h2,h3\')?.textContent),\n      text: text(p.textContent, 300),\n      selector: selector(p)?.selector || null,\n    } : null;\n  }\n  function actionableTarget(el) {\n    if (!ok(el)) return null;\n    const target = el.closest?.([\n      \'button\',\n      \'a[href]\',\n      \'input[type="button"]\',\n      \'input[type="submit"]\',\n      \'input[type="reset"]\',\n      \'label\',\n      \'[role="button"]\',\n      \'[role="link"]\',\n      \'[role="menuitem"]\',\n      \'[role="menuitemcheckbox"]\',\n      \'[role="menuitemradio"]\',\n      \'[role="tab"]\',\n      \'[role="option"]\',\n      \'[role="checkbox"]\',\n      \'[role="radio"]\',\n      \'[role="switch"]\',\n      \'[tabindex]:not([tabindex="-1"])\',\n    ].join(\',\'));\n    if (target && ok(target) && visible(target) && !adLike(target)) return target;\n    return adLike(el) ? null : el;\n  }\n  function snapshot(el) {\n    const s = selector(el);\n    const r = role(el);\n    const n = nameOf(el);\n    const loc = locator(el, n, r);\n    return {\n      selector: s?.selector || null,\n      selector_candidates: selectorCandidates(el),\n      selector_stability: (loc && (loc.includes(\'getByRole\') || loc.includes(\'getByLabel\'))) ? \'high\' : (s?.stability || \'low\'),\n      playwright_locator: loc,\n      accessible_name: n || null,\n      visible_text: text(el.innerText || el.textContent, 160),\n      label: label(el) || null,\n      role: r,\n      tag: el.tagName?.toLowerCase() || null,\n      type: el.type || null,\n      id: el.id || null,\n      name: el.name || null,\n      class: attr(el, \'class\'),\n      href: attr(el, \'href\'),\n      dom_path: domPath(el),\n      parent_context: parent(el),\n      state: state(el),\n      options: options(el),\n      group: group(el),\n      attributes: {\n        \'data-testid\': attr(el, \'data-testid\'),\n        \'data-test\': attr(el, \'data-test\'),\n        \'data-cy\': attr(el, \'data-cy\'),\n        \'data-qa\': attr(el, \'data-qa\'),\n        \'aria-label\': attr(el, \'aria-label\'),\n        \'aria-controls\': attr(el, \'aria-controls\'),\n      },\n    };\n  }\n  function ok(el) {\n    return el && el.tagName && ![\'HTML\', \'BODY\'].includes(el.tagName) && !(el.closest && el.closest(\'#__sqat_capture_indicator__\')) && !adLike(el);\n  }\n  function action(type, el, extra = {}) {\n    const snap = snapshot(el);\n    return {\n      type,\n      selector: snap.selector,\n      selectorCandidates: snap.selector_candidates,\n      stability: snap.selector_stability,\n      playwrightLocator: snap.playwright_locator,\n      text: snap.visible_text,\n      accessibleName: snap.accessible_name,\n      role: snap.role,\n      label: snap.label,\n      inputType: snap.type,\n      elementType: snap.tag,\n      value: extra.value,\n      inputValueKind: extra.inputValueKind,\n      url: window.location.href,\n      urlBefore: window.location.href,\n      beforeState: extra.beforeState || snap.state,\n      afterState: extra.afterState || null,\n      semanticContext: {\n        element: snap,\n        parent_context: snap.parent_context,\n        options: snap.options || [],\n        group: snap.group || null,\n        event: extra.event || null,\n        low_selector_reason: BARE.has(String(snap.selector || \'\').toLowerCase()) ? \'semantic CSS selector unavailable; use playwright_locator or parent_context\' : null,\n      },\n    };\n  }\n  function inputKind(el) {\n    const tag = (el.tagName || \'\').toLowerCase();\n    const t = (el.type || \'\').toLowerCase();\n    if (tag === \'select\') return \'select\';\n    if (t === \'range\') return \'slide\';\n    if ([\'checkbox\', \'radio\'].includes(t)) return el.checked ? \'check\' : \'uncheck\';\n    return \'fill\';\n  }\n  function recordableInput(el) {\n    if (!ok(el)) return false;\n    const tag = (el.tagName || \'\').toLowerCase();\n    const t = (el.type || \'\').toLowerCase();\n    if (![\'input\', \'textarea\', \'select\'].includes(tag)) return false;\n    return ![\'button\', \'submit\', \'reset\', \'image\', \'hidden\', \'file\'].includes(t);\n  }\n  function inputValue(el) {\n    const t = (el.type || \'\').toLowerCase();\n    const redacted = t === \'password\' && !STORE_PASSWORD_VALUES;\n    return redacted ? null : (el.value || \'\').substring(0, 500);\n  }\n  function inputValueKind(el) {\n    const t = (el.type || \'\').toLowerCase();\n    const hints = `${t} ${el.name || \'\'} ${el.id || \'\'} ${attr(el, \'autocomplete\') || \'\'} ${label(el) || \'\'} ${el.placeholder || \'\'}`.toLowerCase();\n    if (t === \'password\' || /password|username|email|login|credential/.test(hints)) return \'credential\';\n    if ((el.value || \'\') === \'\') return \'empty\';\n    if ((el.tagName || \'\').toLowerCase() === \'select\') return \'option_value\';\n    return \'literal\';\n  }\n  function inputCommitKey(el) {\n    const t = (el.type || \'\').toLowerCase();\n    const rawValue = t === \'password\'\n      ? String(el.value || \'\').substring(0, 500)\n      : String(inputValue(el) ?? \'\');\n    return `${inputKind(el)}|${rawValue}|${el.checked === true}`;\n  }\n  function commitInput(el, event = {}) {\n    if (!recordableInput(el)) return;\n    const force = event.force === true || event.source === \'change\';\n    if (!force && !dirtyInputs.has(el)) return;\n    const key = inputCommitKey(el);\n    if (lastInputCommit.get(el) === key) return;\n    lastInputCommit.set(el, key);\n    window.__sqat_action__(action(inputKind(el), el, {\n      value: inputValue(el),\n      inputValueKind: inputValueKind(el),\n      beforeState: focusState.get(el) || null,\n      afterState: state(el),\n      event,\n    })).catch(() => {});\n  }\n  function flushActiveInput(source) {\n    const active = document.activeElement;\n    if (active) commitInput(active, { committed: true, source });\n  }\n\n  document.addEventListener(\'focusin\', e => {\n    if (ok(e.target)) focusState.set(e.target, state(e.target));\n  }, true);\n  document.addEventListener(\'pointerdown\', e => {\n    if (ok(e.target) && e.target !== document.activeElement) flushActiveInput(\'pointerdown\');\n    if (ok(e.target)) focusState.set(e.target, state(e.target));\n  }, true);\n  document.addEventListener(\'focusout\', e => {\n    commitInput(e.target, { committed: true, source: \'blur\' });\n  }, true);\n  document.addEventListener(\'click\', e => {\n    if (!ok(e.target)) return;\n    flushActiveInput(\'click\');\n    const el = actionableTarget(e.target);\n    if (!el) return;\n    const tag = el.tagName.toLowerCase();\n    const nativeType = (el.type || \'\').toLowerCase();\n    if ([\'input\', \'textarea\', \'select\'].includes(tag) && ![\'button\', \'submit\', \'reset\'].includes(nativeType)) return;\n    let type = \'click\';\n    const r = role(el);\n    if (r === \'button\' && ((el.type || \'\').toLowerCase() === \'submit\' || text(el.innerText || el.value).toLowerCase() === \'submit\')) type = \'submit\';\n    window.__sqat_action__(action(type, el, {\n      beforeState: focusState.get(el) || state(el),\n      event: { x: Math.round(e.clientX), y: Math.round(e.clientY), button: e.button },\n    })).catch(() => {});\n  }, true);\n  document.addEventListener(\'change\', e => {\n    if (!ok(e.target)) return;\n    const el = e.target;\n    const tag = el.tagName.toLowerCase();\n    if (![\'input\', \'textarea\', \'select\'].includes(tag)) return;\n    commitInput(el, { committed: true, source: \'change\', force: true });\n  }, true);\n  document.addEventListener(\'input\', e => {\n    if (!ok(e.target)) return;\n    const el = e.target;\n    const tag = el.tagName.toLowerCase();\n    const t = (el.type || \'\').toLowerCase();\n    if (![\'input\', \'textarea\'].includes(tag) || [\'checkbox\', \'radio\', \'range\', \'button\', \'submit\', \'reset\'].includes(t)) return;\n    dirtyInputs.add(el);\n  }, true);\n  document.addEventListener(\'keydown\', e => {\n    if (![\'Enter\', \'Tab\'].includes(e.key) || !ok(e.target)) return;\n    commitInput(e.target, { committed: true, source: \'keydown\', key: e.key });\n    window.__sqat_action__(action(\'keypress\', e.target, {\n      value: e.key,\n      beforeState: focusState.get(e.target) || state(e.target),\n      event: { key: e.key },\n    })).catch(() => {});\n  }, true);\n})();\n\nwindow.__sqat_enrich_after_action__ = function (action) {\n  const text = (v, n = 200) => (v || \'\').toString().replace(/\\s+/g, \' \').trim().substring(0, n);\n  const attr = (el, name) => el && el.getAttribute ? el.getAttribute(name) : null;\n  let el = null;\n  try {\n    const sel = String(action?.selector || \'\');\n    if (sel && ![\'a\', \'button\', \'input\', \'select\', \'textarea\', \'div\', \'span\'].includes(sel.toLowerCase())) el = document.querySelector(sel);\n  } catch (_) {}\n  const visible = node => {\n    if (!node || !node.getBoundingClientRect) return false;\n    const r = node.getBoundingClientRect();\n    const s = getComputedStyle(node);\n    return r.width > 0 && r.height > 0 && s.display !== \'none\' && s.visibility !== \'hidden\';\n  };\n  const optionRoot = el && attr(el, \'aria-controls\') ? document.getElementById(attr(el, \'aria-controls\')) : document;\n  const visibleOptions = el && el.tagName?.toLowerCase() === \'select\'\n    ? Array.from(el.options || []).map((o, i) => ({ index: i, value: o.value, label: text(o.label || o.textContent), selected: o.selected, disabled: o.disabled }))\n    : Array.from(optionRoot.querySelectorAll(\'[role="listbox"] [role="option"],[role="option"],[role="menu"] [role="menuitem"],[role="menuitem"]\')).filter(visible).slice(0, 80).map((o, i) => ({\n        index: i,\n        text: text(attr(o, \'aria-label\') || o.textContent),\n        value: attr(o, \'data-value\') || attr(o, \'value\'),\n        role: attr(o, \'role\') || o.tagName.toLowerCase(),\n        selected: attr(o, \'aria-selected\') === \'true\' || attr(o, \'aria-checked\') === \'true\',\n        disabled: o.disabled === true || attr(o, \'aria-disabled\') === \'true\',\n      }));\n  return {\n    url: window.location.href,\n    title: document.title,\n    after_state: el ? {\n      value: el.type === \'password\' && !window.__sqat_store_password_values__ ? null : (el.value ?? null),\n      value_redacted: el.type === \'password\' && !window.__sqat_store_password_values__,\n      checked: typeof el.checked === \'boolean\' ? el.checked : null,\n      selected_value: el.tagName?.toLowerCase() === \'select\' ? el.value : null,\n      selected_label: el.tagName?.toLowerCase() === \'select\' ? text(el.options?.[el.selectedIndex]?.label || el.options?.[el.selectedIndex]?.textContent) : null,\n      aria_expanded: attr(el, \'aria-expanded\'),\n      aria_selected: attr(el, \'aria-selected\'),\n      aria_checked: attr(el, \'aria-checked\'),\n      aria_pressed: attr(el, \'aria-pressed\'),\n    } : null,\n    visible_options: visibleOptions,\n  };\n};\n\nwindow.__sqat_show_capture_indicator__ = function () {\n  let el = document.getElementById(\'__sqat_capture_indicator__\');\n  if (!el) {\n    el = document.createElement(\'div\');\n    el.id = \'__sqat_capture_indicator__\';\n    el.setAttribute(\'aria-hidden\', \'true\');\n    el.innerHTML = \'\';\n    const style = document.createElement(\'style\');\n    style.id = \'__sqat_capture_indicator_style__\';\n    style.textContent = \'#__sqat_capture_indicator__{position:fixed;inset:0;pointer-events:none;z-index:2147483647;box-shadow:inset 0 0 0 3px rgba(125,211,252,.95),inset 0 0 18px rgba(125,211,252,.45);opacity:0;transition:opacity 80ms ease}#__sqat_capture_indicator__.on{opacity:1}\';\n    document.documentElement.appendChild(style);\n    document.documentElement.appendChild(el);\n  }\n  requestAnimationFrame(() => el.classList.add(\'on\'));\n};\nwindow.__sqat_hide_capture_indicator__ = function () {\n  const el = document.getElementById(\'__sqat_capture_indicator__\');\n  if (el) el.classList.remove(\'on\');\n};\n\nwindow.__sqat_get_elements__ = function () {\n  const out = [];\n  const seen = new Set();\n  const selectors = [\n    \'button:not([disabled])\',\n    \'a[href]\',\n    \'input:not([type="hidden"])\',\n    \'select\',\n    \'textarea\',\n    \'[role="button"]\',\n    \'[role="link"]\',\n    \'[role="tab"]\',\n    \'[role="checkbox"]\',\n    \'[role="radio"]\',\n    \'[role="switch"]\',\n    \'[role="combobox"]\',\n    \'[role="option"]\',\n    \'[role="menuitem"]\',\n    \'[tabindex]:not([tabindex="-1"])\',\n  ];\n  const text = (v, n = 160) => (v || \'\').toString().replace(/\\s+/g, \' \').trim().substring(0, n);\n  const attr = (el, name) => el && el.getAttribute ? el.getAttribute(name) : null;\n  const esc = v => String(v || \'\').replace(/["\\\\]/g, \'\\\\$&\');\n  const stableId = id => !!id && !/^[0-9a-f-]{16,}$/i.test(id);\n  const role = el => attr(el, \'role\') || (el.tagName || \'\').toLowerCase();\n  const name = el => text(attr(el, \'aria-label\')) || text(el.innerText || el.textContent || el.value);\n  const meaningfulHref = el => {\n    const href = String(attr(el, \'href\') || \'\').trim().toLowerCase();\n    return !!href && href !== \'#\' && href !== \'javascript:void(0)\' && href !== \'javascript:;\';\n  };\n  const adLike = el => /advertisement|googleads|doubleclick|pagead|adsystem|adservice|googlesyndication/.test([\n    attr(el, \'aria-label\'),\n    attr(el, \'title\'),\n    attr(el, \'id\'),\n    attr(el, \'name\'),\n    attr(el, \'src\'),\n    attr(el, \'class\'),\n  ].join(\' \').toLowerCase());\n  const path = el => {\n    const parts = [];\n    let node = el;\n    while (node && node.nodeType === 1 && parts.length < 8) {\n      const tag = node.tagName.toLowerCase();\n      if (node.id && stableId(node.id)) {\n        parts.unshift(`${tag}#${window.CSS && CSS.escape ? CSS.escape(node.id) : esc(node.id)}`);\n        break;\n      }\n      let i = 1;\n      let p = node.previousElementSibling;\n      while (p) {\n        if (p.tagName === node.tagName) i += 1;\n        p = p.previousElementSibling;\n      }\n      parts.unshift(`${tag}:nth-of-type(${i})`);\n      node = node.parentElement;\n    }\n    return parts.join(\' > \');\n  };\n  const candidates = el => {\n    const tag = el.tagName.toLowerCase();\n    const out = [];\n    const push = v => {\n      if (v && !out.includes(v)) out.push(v);\n    };\n    for (const a of [\'data-testid\', \'data-test\', \'data-cy\', \'data-qa\']) {\n      const v = attr(el, a);\n      if (v) push(`[${a}="${esc(v)}"]`);\n    }\n    if (attr(el, \'aria-label\')) push(`${tag}[aria-label="${esc(attr(el, \'aria-label\'))}"]`);\n    if (el.id && stableId(el.id)) push(`#${window.CSS && CSS.escape ? CSS.escape(el.id) : esc(el.id)}`);\n    if (el.name) push(`${tag}[name="${esc(el.name)}"]`);\n    if (el.placeholder) push(`${tag}[placeholder="${esc(el.placeholder)}"]`);\n    if (tag === \'a\' && meaningfulHref(el)) push(`${tag}[href="${esc(attr(el, \'href\'))}"]`);\n    const labelled = el.id ? text(document.querySelector(`label[for="${esc(el.id)}"]`)?.textContent) : \'\';\n    if (labelled && [\'input\', \'textarea\', \'select\'].includes(tag)) push(`label:${labelled}`);\n    const r = role(el);\n    const n = name(el);\n    if (r && n) push(`role:${r}[name="${n}"]`);\n    push(path(el));\n    return out.slice(0, 8);\n  };\n  const best = el => {\n    const tag = el.tagName.toLowerCase();\n    for (const a of [\'data-testid\', \'data-test\', \'data-cy\', \'data-qa\']) {\n      const v = attr(el, a);\n      if (v) return { selector: `[${a}="${esc(v)}"]`, selector_stability: \'high\' };\n    }\n    if (attr(el, \'aria-label\')) return { selector: `${tag}[aria-label="${esc(attr(el, \'aria-label\'))}"]`, selector_stability: \'high\' };\n    if (el.id && stableId(el.id)) return { selector: `#${window.CSS && CSS.escape ? CSS.escape(el.id) : esc(el.id)}`, selector_stability: \'high\' };\n    if (el.name) return { selector: `${tag}[name="${esc(el.name)}"]`, selector_stability: \'medium\' };\n    if (el.placeholder) return { selector: `${tag}[placeholder="${esc(el.placeholder)}"]`, selector_stability: \'medium\' };\n    if (tag === \'a\' && meaningfulHref(el)) return { selector: `${tag}[href="${esc(attr(el, \'href\'))}"]`, selector_stability: \'medium\' };\n    return { selector: candidates(el).at(-1) || tag, selector_stability: \'low\' };\n  };\n  const optionList = el => {\n    if (el.tagName?.toLowerCase() !== \'select\') return [];\n    return Array.from(el.options || []).map((o, i) => ({ index: i, value: o.value, label: text(o.label || o.textContent), selected: o.selected, disabled: o.disabled }));\n  };\n\n  selectors.forEach(sel => {\n    document.querySelectorAll(sel).forEach(el => {\n      if (seen.has(el)) return;\n      seen.add(el);\n      const rect = el.getBoundingClientRect();\n      const style = getComputedStyle(el);\n      if (rect.width === 0 || rect.height === 0 || style.display === \'none\' || style.visibility === \'hidden\' || adLike(el)) return;\n      const b = best(el);\n      out.push({\n        tag: el.tagName.toLowerCase(),\n        type: el.type || null,\n        role: role(el),\n        text: text(el.innerText || el.textContent),\n        accessible_name: name(el),\n        selector_candidates: candidates(el),\n        label: text(document.querySelector(`label[for="${esc(el.id)}"]`)?.textContent),\n        placeholder: el.placeholder || null,\n        name: el.name || null,\n        id: el.id || null,\n        class: attr(el, \'class\'),\n        href: el.tagName.toLowerCase() === \'a\' ? attr(el, \'href\') : null,\n        disabled: el.disabled === true || attr(el, \'aria-disabled\') === \'true\',\n        required: el.required === true || attr(el, \'aria-required\') === \'true\',\n        checked: typeof el.checked === \'boolean\' ? el.checked : null,\n        value_redacted: (el.type || \'\').toLowerCase() === \'password\',\n        selected_value: el.tagName.toLowerCase() === \'select\' ? el.value : null,\n        selected_label: el.tagName.toLowerCase() === \'select\' ? text(el.options?.[el.selectedIndex]?.label || el.options?.[el.selectedIndex]?.textContent) : null,\n        aria_expanded: attr(el, \'aria-expanded\'),\n        aria_selected: attr(el, \'aria-selected\'),\n        aria_checked: attr(el, \'aria-checked\'),\n        options: optionList(el),\n        ...b,\n        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },\n      });\n    });\n  });\n  return out;\n};\n\nwindow.__sqat_clean_html__ = function () {\n  const clone = document.documentElement.cloneNode(true);\n  clone.querySelectorAll(\'style,script,link[rel="stylesheet"],link[rel="preload"],noscript,meta,template,[aria-hidden="true"],#__sqat_capture_indicator__,#__sqat_capture_indicator_style__\').forEach(el => el.remove());\n  clone.querySelectorAll(\'iframe[aria-label*="Advertisement" i],iframe[title*="Advertisement" i],iframe[src*="googleads"],iframe[src*="doubleclick"],iframe[src*="pagead"],iframe[src*="googlesyndication"],ins[id*="aswift"],div[id*="aswift"],iframe[name*="googlefc"]\').forEach(el => el.remove());\n  clone.querySelectorAll(\'svg *\').forEach(el => {\n    [\'d\', \'points\', \'transform\', \'viewBox\', \'fill\', \'stroke\', \'clip-path\', \'filter\', \'mask\', \'opacity\'].forEach(a => el.removeAttribute(a));\n  });\n  const keep = new Set([\n    \'id\', \'name\', \'type\', \'href\', \'src\', \'alt\', \'placeholder\', \'role\', \'for\', \'action\', \'method\',\n    \'value\', \'checked\', \'selected\', \'disabled\', \'required\', \'readonly\', \'multiple\', \'tabindex\', \'target\',\n    \'aria-label\', \'aria-labelledby\', \'aria-describedby\', \'aria-expanded\', \'aria-selected\',\n    \'aria-checked\', \'aria-disabled\', \'aria-controls\', \'aria-current\', \'aria-invalid\', \'aria-required\',\n    \'data-testid\', \'data-test\', \'data-cy\', \'data-qa\',\n  ]);\n  clone.querySelectorAll(\'*\').forEach(el => {\n    el.removeAttribute(\'style\');\n    el.removeAttribute(\'class\');\n    Array.from(el.attributes).forEach(a => {\n      if (!keep.has(a.name)) el.removeAttribute(a.name);\n    });\n  });\n  return clone.outerHTML;\n};\n\nwindow.__sqat_get_page_context__ = function () {\n  const text = (el, n = 220) => el ? (el.textContent || \'\').replace(/\\s+/g, \' \').trim().substring(0, n) : \'\';\n  const best = el => {\n    if (!el) return null;\n    const tag = el.tagName.toLowerCase();\n    for (const a of [\'data-testid\', \'data-test\', \'data-cy\', \'data-qa\']) {\n      const v = el.getAttribute(a);\n      if (v) return `[${a}="${String(v).replace(/["\\\\]/g, \'\\\\$&\')}"]`;\n    }\n    if (el.getAttribute(\'aria-label\')) return `${tag}[aria-label="${String(el.getAttribute(\'aria-label\')).replace(/["\\\\]/g, \'\\\\$&\')}"]`;\n    if (el.id && !/^[0-9a-f-]{16,}$/i.test(el.id)) return `#${window.CSS && CSS.escape ? CSS.escape(el.id) : el.id}`;\n    if (el.name) return `${tag}[name="${String(el.name).replace(/["\\\\]/g, \'\\\\$&\')}"]`;\n    return null;\n  };\n  const headings = Array.from(document.querySelectorAll(\'h1,h2,h3,h4\')).map(h => ({ level: parseInt(h.tagName[1], 10), text: text(h) })).filter(h => h.text);\n  const forms = Array.from(document.querySelectorAll(\'form\')).map(form => ({\n    action: form.getAttribute(\'action\'),\n    method: form.method || \'get\',\n    submit_text: text(form.querySelector(\'[type="submit"],button:not([type="button"])\')),\n    fields: Array.from(form.querySelectorAll(\'input:not([type="hidden"]),select,textarea\')).map(f => ({\n      tag: f.tagName.toLowerCase(),\n      type: f.type || null,\n      name: f.name || null,\n      label: text(document.querySelector(`label[for="${String(f.id || \'\').replace(/["\\\\]/g, \'\\\\$&\')}"]`)),\n      placeholder: f.placeholder || null,\n      required: f.required || false,\n      selector: best(f),\n      options: f.tagName.toLowerCase() === \'select\' ? Array.from(f.options || []).map(o => ({ value: o.value, label: text(o), selected: o.selected })) : [],\n    })),\n  }));\n  const nav_links = Array.from(document.querySelectorAll(\'nav a,[role="navigation"] a,header a\')).map(a => ({ text: text(a), href: a.getAttribute(\'href\'), selector: best(a) })).filter(l => l.text && l.href).slice(0, 50);\n  const buttons = Array.from(document.querySelectorAll(\'button,[role="button"]\')).filter(b => {\n    const r = b.getBoundingClientRect();\n    return r.width > 0 && r.height > 0 && !b.disabled;\n  }).map(b => ({ text: text(b), selector: best(b), aria_label: b.getAttribute(\'aria-label\') })).filter(b => b.text || b.aria_label).slice(0, 50);\n  const dialogs = Array.from(document.querySelectorAll(\'[role="dialog"],[role="alertdialog"],dialog[open]\')).filter(el => {\n    const r = el.getBoundingClientRect();\n    return r.width > 0 && r.height > 0;\n  }).map(el => ({ title: text(el.querySelector(\'[aria-labelledby],h1,h2,h3\')), text: text(el, 350) }));\n  return { url: window.location.href, title: document.title, headings, forms, nav_links, buttons, dialogs };\n};\n\nwindow.__sqat_classify_snapshot_kind__ = function (actionType) {\n  // Called by Python push_step() after an action to determine semantic snapshot_kind.\n  const hasValidationError = !!(\n    document.querySelector(\'[role="alert"]\') ||\n    document.querySelector(\'[aria-invalid="true"]\') ||\n    document.querySelector(\'.error, .is-error, .has-error, [class*="error"]\')\n  );\n  const hasModal = !!document.querySelector(\'[role="dialog"]:not([aria-hidden="true"])\');\n  if (hasValidationError && actionType === \'submit\') return \'validation_error\';\n  if (hasModal) return \'modal_state\';\n  return \'after_action\';\n};\n\nwindow.__sqat_get_assertion_candidates__ = function () {\n  const out = [];\n  const seen = new Set();\n  const text = (el, n = 260) => el ? (el.textContent || \'\').replace(/\\s+/g, \' \').trim().substring(0, n) : \'\';\n  const attr = (el, name) => el && el.getAttribute ? el.getAttribute(name) : null;\n  const esc = v => String(v || \'\').replace(/["\\\\]/g, \'\\\\$&\');\n  const stableId = id => !!id && !/^[0-9a-f-]{16,}$/i.test(id);\n  const visible = el => {\n    if (!el || !el.getBoundingClientRect) return false;\n    const r = el.getBoundingClientRect();\n    const s = getComputedStyle(el);\n    return r.width > 0 && r.height > 0 && s.display !== \'none\' && s.visibility !== \'hidden\';\n  };\n  // Elements to exclude: body-level containers and giant layout elements (>60% viewport)\n  const EXCLUDED_IDS = new Set([\'__next\', \'__nuxt\', \'app\', \'root\', \'__app\']);\n  const EXCLUDED_TAGS = new Set([\'body\', \'html\', \'main\']);\n  const viewportArea = window.innerWidth * window.innerHeight;\n  const isExcluded = el => {\n    if (!el || !el.tagName) return true;\n    const tag = el.tagName.toLowerCase();\n    if (EXCLUDED_TAGS.has(tag)) return true;\n    const id = (el.id || \'\').toLowerCase().replace(/^#/, \'\');\n    if (EXCLUDED_IDS.has(id)) return true;\n    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;\n    if (r && (r.width * r.height) > viewportArea * 0.6) return true;\n    return false;\n  };\n  const selector = el => {\n    if (!el || !el.tagName) return null;\n    const tag = el.tagName.toLowerCase();\n    for (const a of [\'data-testid\', \'data-test\', \'data-cy\', \'data-qa\']) {\n      const v = attr(el, a);\n      if (v) return `[${a}="${esc(v)}"]`;\n    }\n    if (attr(el, \'aria-label\')) return `${tag}[aria-label="${esc(attr(el, \'aria-label\'))}"]`;\n    if (el.id && stableId(el.id)) return `#${window.CSS && CSS.escape ? CSS.escape(el.id) : esc(el.id)}`;\n    if (el.name) return `${tag}[name="${esc(el.name)}"]`;\n    return null;\n  };\n  const hasStableSelector = el => selector(el) !== null;\n  const push = (kind, el, confidence = 0.75, extra = {}) => {\n    if (!visible(el) || isExcluded(el)) return;\n    const value = text(el);\n    if (!value) return;\n    const sel = selector(el);\n    // Set confidence=0.0 for any candidate without a stable selector\n    const adjustedConfidence = sel ? confidence : 0.0;\n    const key = `${kind}|${sel || \'\'}|${value}`;\n    if (seen.has(key)) return;\n    seen.add(key);\n    out.push({ kind, selector: sel, text: value, confidence: adjustedConfidence, ...extra });\n  };\n\n  // ui_text: headings\n  document.querySelectorAll(\'h1,h2,h3,h4,[role="heading"]\').forEach(el => push(\'ui_text\', el, 0.9, { source: \'heading\' }));\n  // error_message: alerts and aria-invalid messages\n  document.querySelectorAll(\'[role="alert"],[role="status"],[aria-live]\').forEach(el => push(\'error_message\', el, 0.95, { source: \'alert\' }));\n  document.querySelectorAll(\'[aria-invalid="true"]\').forEach(el => push(\'error_message\', el, 0.85, { source: \'aria_invalid\' }));\n  // element_visible: stable data-testid elements\n  document.querySelectorAll(\'[data-testid],[data-test],[data-cy],[data-qa]\').forEach(el => {\n    const key = `${attr(el, \'data-testid\') || attr(el, \'data-test\') || attr(el, \'data-cy\') || attr(el, \'data-qa\')}`.toLowerCase();\n    if (/error|alert|success|complete|confirm|message|badge|count|total|title|name|price|summary|empty/.test(key)) {\n      push(\'element_visible\', el, 0.9, { source: \'stable_attr\', data_key: key });\n    }\n  });\n  // count_check: badge/count elements\n  document.querySelectorAll(\'.badge,.count,[class*="badge"],[class*="count"]\').forEach(el => push(\'count_check\', el, 0.7, { source: \'badge\' }));\n  // list_items\n  document.querySelectorAll(\'ul,ol,[role="list"]\').forEach((list, listIndex) => {\n    if (isExcluded(list)) return;\n    const items = Array.from(list.querySelectorAll(\'li,[role="listitem"]\')).filter(visible).map(item => text(item, 180)).filter(Boolean).slice(0, 40);\n    if (items.length) {\n      const sel = selector(list);\n      const key = `list_items|${sel || \'\'}|${items.slice(0, 3).join(\'|\')}`;\n      if (!seen.has(key)) {\n        seen.add(key);\n        out.push({ kind: \'list_items\', selector: sel, text: items.join(\' | \'), confidence: sel ? 0.75 : 0.0, source: \'list\', item_count: items.length, list_index: listIndex, items });\n      }\n    }\n  });\n  // table_rows\n  document.querySelectorAll(\'table,[role="table"],[role="grid"]\').forEach((table, tableIndex) => {\n    if (isExcluded(table)) return;\n    const rows = Array.from(table.querySelectorAll(\'tr,[role="row"]\')).filter(visible).map(row => text(row, 220)).filter(Boolean).slice(0, 40);\n    if (rows.length) {\n      const sel = selector(table);\n      const key = `table_rows|${sel || \'\'}|${rows.slice(0, 2).join(\'|\')}`;\n      if (!seen.has(key)) {\n        seen.add(key);\n        out.push({ kind: \'table_rows\', selector: sel, text: rows.join(\' | \'), confidence: sel ? 0.8 : 0.0, source: \'table\', row_count: rows.length, table_index: tableIndex, rows });\n      }\n    }\n  });\n  // url_match\n  const urlPath = window.location.pathname + window.location.search;\n  if (urlPath) {\n    out.push({ kind: \'url_match\', selector: null, text: urlPath, confidence: 0.8, source: \'url\' });\n  }\n  return out.slice(0, 120);\n};\n'

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
            # Classify the snapshot kind semantically using the JS classifier
            action_type_str = action.get("type", "")
            try:
                snapshot_kind = await page.evaluate(
                    "(actionType) => window.__sqat_classify_snapshot_kind__ ? window.__sqat_classify_snapshot_kind__(actionType) : 'after_action'",
                    action_type_str,
                )
            except Exception:
                snapshot_kind = "after_action"
            # Check if URL changed after action for success_state detection
            url_before = action.get("urlBefore") or action.get("url")
            url_after = action.get("urlAfter")
            if url_before and url_after and url_before != url_after and action_type_str == "submit":
                snapshot_kind = "success_state"
            snapshot = await self.upsert_route(
                session_id,
                scenario_id,
                flow_id,
                page,
                network_calls,
                snapshot_index=self._next_snapshot_index(),
                snapshot_kind=snapshot_kind,
                metadata={
                    "step_index": step_index,
                    "action_type": action_type_str,
                    "url_before": url_before,
                    "url_after": url_after,
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
                        loc = action.get("playwrightLocator")
                        sel = (loc if loc else action.get("selector", ""))[:70]
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
