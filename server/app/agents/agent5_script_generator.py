"""Agent A5 — Script Generator.

Takes a ContextObject from A4 and generates a runnable Playwright .spec.ts.
Every generated script includes four preamble helpers:
  - smartFind()             — selector resolution with fallbacks
  - NetworkMonitor          — captures all 4xx/5xx responses for A6 to classify
  - navigateWithFallback()  — retry navigation through goto on action failure
  - env()                   — safe process.env resolver (fail-fast on missing vars)

Entry points:
  generate_script(context)                              -> str | None  (single test)
  generate_grouped_script(contexts, hls_id, hls_title)  -> str | None  (serial describe)
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.services import mcp_server
from app.services.state_store import update_state
from app.utils.llm import call_llm

logger = logging.getLogger(__name__)

_MAX_LLM_RETRIES = 3

# ── Preamble injected at top of every generated script ───────────────────────

_PREAMBLE = '''\
import { test, expect, Page } from "@playwright/test";

// ── smartFind ──────────────────────────────────────────────────────────────
function smartFind(page: Page, selector: string) {
  try { return page.locator(selector); } catch {}
  try { return page.getByText(selector, { exact: false }); } catch {}
  return page.getByRole("button", { name: selector });
}

// ── NetworkMonitor ─────────────────────────────────────────────────────────
// IMPORTANT: instantiate BEFORE any page actions so all responses are captured.
class NetworkMonitor {
  failures: { url: string; method: string; status: number; resourceType: string }[] = [];
  private origin: string;
  private staticAsset = /\.(jpg|jpeg|png|gif|svg|ico|webp|woff|woff2|ttf|eot|css|js|map)(\?.*)?$/i;
  private ignoredResourceTypes = new Set(["image", "font", "stylesheet", "script"]);
  constructor(private page: Page) {
    const base = process.env.BASE_URL ?? "";
    try { this.origin = new URL(base).origin; } catch { this.origin = base; }
    page.on("response", (res) => {
      const url = res.url();
      const resourceType = res.request().resourceType();
      if (
        res.status() >= 400 &&
        url.startsWith(this.origin) &&
        !this.staticAsset.test(url) &&
        !this.ignoredResourceTypes.has(resourceType)
      ) {
        this.failures.push({ url, method: res.request().method(), status: res.status(), resourceType });
      }
    });
  }
  hasFailures() { return this.failures.length > 0; }
}

// ── navigateWithFallback ───────────────────────────────────────────────────
async function navigateWithFallback(page: Page, action: () => Promise<unknown>, route: string) {
  try { await action(); } catch { await page.goto(route); }
}

// ── env() ─────────────────────────────────────────────────────────────────
// Safe process.env resolver — throws immediately if the var is missing
// so tests fail fast instead of silently passing with empty strings.
function env(name: string): string {
  const val = process.env[name];
  if (val === undefined || val === "") throw new Error(`Missing env var: ${name}`);
  return val;
}

'''

# ── Single-test prompt ────────────────────────────────────────────────────────

_SCRIPT_PROMPT = """\
You are Agent A5, a Playwright TypeScript test script generator.

Generate ONE complete, runnable Playwright test() block for the test case below.
The preamble (imports, smartFind, NetworkMonitor, navigateWithFallback, env) is
already written above your block — do NOT repeat it.

═══ CRITICAL RULES ═══

NETWORK MONITOR — MUST be first line inside the test body:
  const monitor = new NetworkMonitor(page);
  Reason: it attaches a response listener. Any action before this line will not
  be captured. This is NOT optional.
  Use this signature so the test can attach network evidence:
    test("title", async ({{ page }}, testInfo) => {{ ... }})

WAITING — NEVER use waitForTimeout(). Use instead:
  - await page.waitForURL('**/path**')    after navigation actions
  - await page.waitForLoadState('networkidle')   after form submits
  - await expect(locator).toBeVisible()   to wait for elements

SELECTORS — NEVER use bare tag names ('div', 'span', 'input', 'button', 'a', 'select'):
  - Use: page.getByRole('button', {{ name: 'Submit' }})
  - Use: page.getByPlaceholder('Search')
  - Use: page.getByLabel('Reference')
  - Use: page.getByText('Continue')
  - Use: page.locator('#specific-id') or page.locator('[data-testid="x"]')
  - **PREFER selectors from the "RECORDED SELECTORS" section below verbatim** —
    those were captured during a real Phase-2 recording run. If a step matches
    a recorded action, copy the exact selector string.
  - If step is ambiguous like "click div", skip it silently

selectOption — NEVER pass a bare string. Always use an object form:
  - locator.selectOption({{ value: 'target-value' }})       ← match by <option value>
  - locator.selectOption({{ label: 'Visible Option' }})     ← match by visible text
  Bare strings cause label/value/index ambiguity and Playwright strict-mode failures.

INVALID SYNTAX — NEVER use these:
  - page.locator('role=button', {{ name: '...' }})   ← second arg is ignored; throws
  - Use:  page.getByRole('button', {{ name: '...' }}) instead.

URLS — NEVER hardcode http://… or https://… literals. Always:
  - await page.goto(env('BASE_URL') + '/path');
  - Comparing URLs: page.url().endsWith('/path')   (NOT '== "https://site.com/path"')

AUTH / LOGIN STEPS:
  Follow the provided test-case Steps exactly. If the Steps include login,
  sign-in, logout, registration, password reset, or credential entry, generate
  those actions even when the broader title is a business workflow.
  If this test starts unauthenticated, add this line BEFORE the test() block:
    test.use({{ storageState: {{ cookies: [], origins: [] }} }});
  Use env('USER_EMAIL') and env('USER_PASSWORD') for credentials.
  If an auth_state_path/session is already loaded and the Steps do NOT include
  login/authentication, do not invent login steps.

AUTH TESTS — if the title contains Login, Sign In, Logout, Register, or Password:
  Add this line BEFORE the test() block (it clears session state):
    test.use({{ storageState: {{ cookies: [], origins: [] }} }});
  Then use env('USER_EMAIL') and env('USER_PASSWORD') for credentials.

LOGIN NAVIGATION — for login/signup tests, the canonical login page path is
  whichever path the RECORDED SELECTORS section shows as the first navigate
  action. Use that path verbatim. Without a session, navigating directly to
  protected paths typically redirects or hides the form selectors, causing
  30s hangs that blow the suite's timeout budget.
  CORRECT:  await page.goto(env('BASE_URL') + '<recorded-login-path>');
  WRONG:    Hardcoding any guessed post-login path.

ASSERTIONS — at least one expect() per meaningful step.

FINAL NETWORK EVIDENCE — always attach failures before asserting:
  await testInfo.attach('network_logs', {{ body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' }});
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);

RETURN — ONLY the test() block (and optional test.use() line before it).
  No imports. No preamble. No describe wrapper.

═══ FEW-SHOT EXAMPLE ═══
{few_shot_example}

═══ TEST CASE ═══
Title: {title}
Target Page: {target_page}

Steps:
{steps}

Acceptance Criteria:
{acceptance_criteria}

RECORDED SELECTORS (Phase-2 ground truth — prefer these verbatim):
{recorded_steps}

Recorded variant elements (real DOM captured during recording):
{variant_elements}

Known route map (path → link/button text that navigates there):
{route_map}

Interactive elements on {target_page}:
{interactive_elements}

ENV Placeholders — use env('NAME'), NOT process.env.NAME:
{env_placeholders}

DOM excerpt (use selectors from this):
{dom_html}
"""

# ── Grouped-test prompt ───────────────────────────────────────────────────────

_GROUPED_TEST_BLOCK_PROMPT = """\
You are Agent A5 generating ONE test() block for a Playwright test.describe.serial() suite.

The suite uses a shared browser page called `sharedPage` that persists across all tests.
This means earlier tests have already navigated — later tests continue from where they left off.

═══ CRITICAL RULES ═══

NETWORK MONITOR — MUST be the first line inside the test body:
  const monitor = new NetworkMonitor(sharedPage);
  Do this BEFORE any action. This is NOT optional.

PAGE VARIABLE — use `sharedPage` everywhere. NEVER use `page` or create new pages.

SIGNATURE — grouped tests do not use page fixture, but do receive testInfo:
  test("title", async ({{}}, testInfo) => {{ ... }})   ← correct
  test("title", async () => {{ ... }})                 ← missing evidence attachment
  test("title", async ({{ page }}) => {{ ... }})       ← WRONG

NAVIGATION — only call sharedPage.goto() if this is step_index 0 (first test in suite).
  For later tests the browser is already on the right page. Use waitForURL if needed.

WAITING — NEVER use waitForTimeout(). Use:
  - await sharedPage.waitForURL('**/path**')
  - await sharedPage.waitForLoadState('networkidle')
  - await expect(locator).toBeVisible()

SELECTORS — NEVER bare tag names ('select', 'div', 'span', 'input', 'button', 'a'):
  - sharedPage.getByRole('button', {{ name: '...' }})
  - sharedPage.getByPlaceholder('...')
  - sharedPage.getByText('...')
  - sharedPage.locator('#id') or sharedPage.locator('[data-testid="x"]')
  - **PREFER selectors from "RECORDED SELECTORS" below verbatim** when the step
    matches a recorded action.

selectOption — NEVER pass a bare string. Use object form:
  - locator.selectOption({{ value: 'target-value' }}) or {{ label: 'Visible Option' }}.

INVALID SYNTAX — NEVER use these:
  - sharedPage.locator('role=button', {{ name: '...' }})   ← throws; second arg ignored
  - Use: sharedPage.getByRole('button', {{ name: '...' }})

URLS — NEVER hardcode http://… / https://… literals. Use env('BASE_URL') + '/path'.

LOGIN NAVIGATION — for login-flow / signup tests, the canonical login page
  path comes from the RECORDED SELECTORS below (the first navigate captured
  during Phase-2). Use that path verbatim — do NOT guess common login or
  post-login paths. If no recording is available, default to env('BASE_URL').

AUTH / LOGIN STEPS:
  Follow the provided test-case Steps exactly. If the Steps include login,
  sign-in, logout, registration, password reset, or credential entry, generate
  those actions in this test block. If the Steps do not include authentication,
  do not invent it.

ASSERTIONS — at least one expect() per meaningful step.

FINAL NETWORK EVIDENCE — always attach failures before asserting:
  await testInfo.attach('network_logs', {{ body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' }});
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);

RETURN — ONLY the test() block. No imports, no preamble, no describe wrapper.

═══ FEW-SHOT EXAMPLE ═══
{few_shot_example}

═══ TEST CASE ═══
Step index (0 = first in suite, navigate here): {step_index}
Title: {title}
Target Page: {target_page}

Steps:
{steps}

Acceptance Criteria:
{acceptance_criteria}

RECORDED SELECTORS (Phase-2 ground truth — prefer these verbatim):
{recorded_steps}

Recorded variant elements (real DOM captured during recording):
{variant_elements}

Known route map (path → link/button text that navigates there):
{route_map}

Interactive elements on {target_page}:
{interactive_elements}

ENV Placeholders — use env('NAME') helper:
{env_placeholders}

DOM excerpt:
{dom_html}
"""


# ── Utilities ─────────────────────────────────────────────────────────────────

def _format_recorded_steps(recorded: list[dict[str, Any]]) -> str:
    """Render Phase-2 ScenarioStep rows into a compact, prompt-friendly block.

    Output is one line per step with the verbatim selector — A5 is told to
    prefer these strings over anything it might infer from the DOM excerpt.
    """
    if not recorded:
        return "  (no recorded steps for this scenario)"
    lines: list[str] = []
    for s in recorded:
        sel = s.get("selector") or ""
        action = s.get("action") or ""
        value = s.get("value") or ""
        text = s.get("element_text") or ""
        url = s.get("url") or ""
        parts = [f"#{s.get('step_index', '?')}", action.upper()]
        if sel:
            parts.append(f"selector={sel!r}")
        if text:
            parts.append(f"text={text!r}")
        if value:
            parts.append(f"value={value!r}")
        if url:
            parts.append(f"url={url}")
        lines.append("  " + " | ".join(parts))
    return "\n".join(lines)


def _format_variant_elements(elements: list[dict[str, Any]]) -> str:
    if not elements:
        return "  (no recorded variant elements)"
    lines = []
    for el in elements:
        sel = el.get("selector") or ""
        typ = el.get("type") or ""
        txt = el.get("text") or ""
        lines.append(f"  - selector={sel!r} type={typ} text={txt!r}")
    return "\n".join(lines)


def _format_route_map(route_map: dict[str, str]) -> str:
    if not route_map:
        return "  (no route map captured)"
    return "\n".join(f"  {p} → {t!r}" for p, t in route_map.items())


# Static structural fallback when the project has no Phase-2 recordings yet
# (first-run case). Once recordings exist, A4 builds a per-project example
# from the actual ScenarioStep rows and passes it via context["few_shot_example"].
_FALLBACK_FEW_SHOT_SINGLE = """\
(STRUCTURAL fallback — your app has no recordings yet. Use selectors from the
RECORDED SELECTORS section verbatim once they exist.)

test("structural example", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.goto(env('BASE_URL') + '/');
  await page.getByRole('button', { name: /submit/i }).click();
  await expect(page.getByRole('heading')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});\
"""

_FALLBACK_FEW_SHOT_GROUPED = """\
(STRUCTURAL fallback — your app has no recordings yet. Use selectors from the
RECORDED SELECTORS section verbatim once they exist.)

test("structural example", async ({}, testInfo) => {
  const monitor = new NetworkMonitor(sharedPage);
  await sharedPage.getByRole('button', { name: /submit/i }).click();
  await expect(sharedPage.getByRole('heading')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});\
"""


def _test_id_attribute_directive(attr: str | None) -> str:
    """Render a per-spec `test.use({ testIdAttribute })` line, or empty.

    Playwright defaults to 'data-testid' — only emit when the recordings show
    a different attribute (e.g. 'data-test', 'data-cy', or another project
    migrated from Cypress). Empty otherwise to keep specs noise-free.
    """
    if not attr or attr == "data-testid":
        return ""
    safe = attr.replace("'", "")
    return f"test.use({{ testIdAttribute: '{safe}' }});\n\n"


def _resolve_few_shot(context: dict[str, Any], *, is_grouped: bool) -> str:
    """Prefer the per-project few-shot rendered from Phase-2 recordings.
    Falls back to the static structural example only when no recordings exist.

    Note: a synthesized example uses `page` (single-test idiom). For grouped
    output we still surface the synthesized text — the prompt's CRITICAL RULES
    section is explicit that grouped blocks must use `sharedPage` and `async ()`.
    The example is a guide, not a copy-paste source.
    """
    synth = context.get("few_shot_example")
    if synth:
        return str(synth)
    return _FALLBACK_FEW_SHOT_GROUPED if is_grouped else _FALLBACK_FEW_SHOT_SINGLE


def _strip_fences(raw: str) -> str:
    """Remove markdown code fences in all common LLM variants."""
    text = raw.strip()
    # Remove opening fence: ```typescript / ```ts / ```javascript / ```
    text = re.sub(r'^```[a-zA-Z]*\n?', '', text, count=1)
    # Remove closing fence at end
    text = re.sub(r'\n?```\s*$', '', text)
    # Normalise async ({page}) / async ({page,}) → async ({ page })
    text = re.sub(r'async\s*\(\{\s*page\s*,?\s*\}\)', 'async ({ page })', text)
    return text.strip()


_PAGE_FIXTURE_RE = re.compile(r'async\s*\(\s*\{[^}]*page')

# ── Post-generation fixer ─────────────────────────────────────────────────────
#
# These are deterministic patches applied to EVERY LLM output before validation.
# Each rule was derived from a real failure class observed in production runs.
# Rules MUST stay app-agnostic — anything app-specific belongs in the prompt
# (sourced from Phase-2 recordings via A4) so that this codebase scales across
# multiple tenants without regression risk.

_AUTH_TITLE_RE = re.compile(
    r"\b(log[ _-]?in|sign[ _-]?in|log[ _-]?out|sign[ _-]?out|register|reset password|change password)\b",
    re.IGNORECASE,
)

_TEST_USE_STORAGE_CLEAR = (
    "test.use({ storageState: { cookies: [], origins: [] } });"
)

# waitForURL('/foo.html')  or  waitForURL("/foo.html")   →   waitForURL('**/foo.html')
# Matches only bare absolute paths (leading '/', no protocol, no '**').
_BARE_WAIT_FOR_URL_RE = re.compile(
    r"""waitForURL\(\s*(['"])(/[a-zA-Z0-9_./-]+?)(\1)\s*\)"""
)

# Login-flow goto rewriting is now RECORDING-DRIVEN (multi-tenant).
#
# Previously this used a fixed list of common post-login paths, which silently
# broke apps whose actual login page or landing page did not match that list.
# Now A4 surfaces the actual login URL captured during Phase-2 recording
# (`auth_login_path` in context); the rewrite below uses that path verbatim.
#
# Two goto forms appear in LLM output and we cover both:
#   - template literal: goto(`${env('BASE_URL')}/SOMEPATH`)
#   - string concat:    goto(env('BASE_URL') + '/SOMEPATH')
#
# A path-extracting regex captures whatever path the LLM emitted; the rewriter
# compares against `auth_login_path` and rewrites only when they differ.
_GOTO_BASE_URL_TEMPLATE_RE = re.compile(
    r"""goto\(\s*`\$\{env\(\s*(['"])BASE_URL\1\s*\)\}(?P<path>/[^`]*)`\s*\)"""
)
_GOTO_BASE_URL_CONCAT_RE = re.compile(
    r"""goto\(\s*env\(\s*(['"])BASE_URL\1\s*\)\s*\+\s*(['"])(?P<path>/[^'"]*)\2\s*\)"""
)
_OLD_MONITOR_ASSERT_RE = re.compile(
    r"""(?P<indent>[ \t]*)expect\(\s*monitor\.hasFailures\(\)\s*\)\.toBe\(\s*false\s*\)\s*;"""
)
_SINGLE_TEST_SIGNATURE_RE = re.compile(
    r"""test\(\s*(?P<quote>['"])(?P<title>(?:\\.|(?!\1).)*)(?P=quote)\s*,\s*async\s*\(\s*\{\s*page\s*\}\s*\)\s*=>"""
)
_GROUPED_TEST_SIGNATURE_RE = re.compile(
    r"""test\(\s*(?P<quote>['"])(?P<title>(?:\\.|(?!\1).)*)(?P=quote)\s*,\s*async\s*\(\s*\)\s*=>"""
)


def _rewrite_login_goto(code: str, target_path: str) -> tuple[str, int]:
    """Rewrite any goto(env('BASE_URL') + '/X') / goto(`${env('BASE_URL')}/X`)
    where X != target_path into the canonical template-literal form pointing
    at target_path. Returns (new_code, num_replacements)."""
    n = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal n
        emitted_path = match.group("path") or "/"
        if emitted_path == target_path:
            return match.group(0)
        n += 1
        return f"goto(`${{env('BASE_URL')}}{target_path}`)"

    out = _GOTO_BASE_URL_TEMPLATE_RE.sub(repl, code)
    out = _GOTO_BASE_URL_CONCAT_RE.sub(repl, out)
    return out, n


def _ensure_test_info_signature(code: str, *, is_grouped: bool) -> tuple[str, int]:
    """Add Playwright's testInfo arg so generated tests can attach evidence."""
    if "testInfo" in code:
        return code, 0
    pattern = _GROUPED_TEST_SIGNATURE_RE if is_grouped else _SINGLE_TEST_SIGNATURE_RE

    def repl(match: re.Match[str]) -> str:
        title = match.group("title")
        quote = match.group("quote")
        signature = "async ({}, testInfo) =>" if is_grouped else "async ({ page }, testInfo) =>"
        return f"test({quote}{title}{quote}, {signature}"

    return pattern.subn(repl, code, count=1)


def _ensure_network_evidence_assertion(code: str) -> tuple[str, int]:
    """Replace the legacy boolean monitor assertion with evidence attachment."""
    if _has_network_logs_attachment(code) and _has_network_evidence_assertion(code):
        return code, 0

    def repl(match: re.Match[str]) -> str:
        indent = match.group("indent")
        return (
            f"{indent}await testInfo.attach('network_logs', "
            "{ body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });\n"
            f"{indent}expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);"
        )

    patched, n = _OLD_MONITOR_ASSERT_RE.subn(repl, code, count=1)
    if n:
        return patched, n

    # If the LLM omitted the monitor assertion entirely, append evidence lines
    # before the closing test block. Validation will still reject if this lands
    # outside an actual test() body.
    marker = "\n});"
    index = patched.rfind(marker)
    if index == -1:
        return patched, 0
    insertion = "\n" + "\n".join(_network_evidence_lines())
    return patched[:index] + insertion + patched[index:], 1

# Bare-tag selectors (locator('select'), locator("button"), etc.) are forbidden
# because Playwright strict-mode resolves them against ALL matching tags on the
# page — any page with multiple <select>, <button>, etc. elements throws on the
# first action and skips the rest of the serial group. We REJECT scripts
# containing any of these so the LLM retry loop tries again with the recorded
# selectors that A4 surfaces in the prompt.
_BARE_TAG_NAMES = ("select", "div", "span", "input", "button", "a", "p", "ul", "li", "img")
_BARE_TAG_LOCATOR_RE = re.compile(
    r"""\.locator\(\s*(['"])(?P<tag>""" + "|".join(_BARE_TAG_NAMES) + r""")\1\s*\)"""
)


# Non-Playwright APIs the LLM borrows from React Testing Library, Cypress, etc.
# These don't exist in @playwright/test and throw at runtime — the test then
# eats the per-test timeout. We reject so the LLM is re-sampled.
_INVALID_API_RE = re.compile(
    r"\b(?:"
    r"getAllByTestId|getAllByRole|getAllByText|getAllByLabelText|"
    r"findByRole|findByText|findByTestId|findByLabelText|"
    r"queryByRole|queryByText|queryByTestId|"
    r"screen\.(?:get|find|query)By\w+|"
    r"userEvent\.\w+|"
    r"fireEvent\.\w+|"
    r"cy\.\w+"
    r")\b"
)


def _invalid_api_violations(code: str) -> list[str]:
    """Return non-Playwright API names found in code (de-duped, sorted)."""
    return sorted({m.group(0) for m in _INVALID_API_RE.finditer(code)})


def _bare_tag_violations(code: str) -> list[str]:
    """Return list of forbidden bare-tag selectors found in code (e.g. ['select']).

    We do NOT auto-rewrite — there's no safe target to substitute. Instead we
    fail validation so the LLM is re-sampled with the recorded-selectors hint.
    """
    return sorted({m.group("tag") for m in _BARE_TAG_LOCATOR_RE.finditer(code)})


_HTML_TAG_RE = re.compile(
    r"<(?P<tag>" + "|".join(_BARE_TAG_NAMES) + r")\b(?P<attrs>[^>]*)>",
    re.IGNORECASE,
)
_HTML_ATTR_RE = re.compile(
    r"""(?P<name>[:A-Za-z_][\w:.-]*)(?:\s*=\s*(?P<quote>["'])(?P<quoted>.*?)(?P=quote)|\s*=\s*(?P<bare>[^\s"'=<>`]+))?"""
)
_STABLE_ATTR_PRIORITY = (
    "data-testid",
    "data-test",
    "data-cy",
    "data-qa",
    "data-pw",
    "data-test-id",
    "id",
    "name",
    "aria-label",
    "placeholder",
)


def _css_attr_value(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _selector_from_attrs(tag: str, attrs: dict[str, str]) -> str | None:
    lowered = {k.lower(): v for k, v in attrs.items() if v is not None and str(v).strip()}
    for attr in _STABLE_ATTR_PRIORITY:
        value = lowered.get(attr)
        if value:
            return f'[{attr}="{_css_attr_value(value)}"]'
    class_value = lowered.get("class", "").strip()
    if class_value and all(token and re.match(r"^[A-Za-z_][\w-]*$", token) for token in class_value.split()):
        return f"{tag.lower()}." + ".".join(class_value.split())
    return None


def _parse_html_elements(html: str) -> list[tuple[str, dict[str, str]]]:
    elements: list[tuple[str, dict[str, str]]] = []
    for match in _HTML_TAG_RE.finditer(html or ""):
        attrs: dict[str, str] = {}
        for attr_match in _HTML_ATTR_RE.finditer(match.group("attrs") or ""):
            name = (attr_match.group("name") or "").strip()
            if not name:
                continue
            value = attr_match.group("quoted")
            if value is None:
                value = attr_match.group("bare") or ""
            attrs[name] = value
        elements.append((match.group("tag").lower(), attrs))
    return elements


def _is_bare_tag_selector(selector: str) -> bool:
    return selector.strip().lower() in _BARE_TAG_NAMES


def _selector_from_interactive_elements(tag: str, elements: list[dict[str, Any]]) -> str | None:
    matches: list[str] = []
    for element in elements or []:
        element_type = str(element.get("type") or element.get("tag") or element.get("element_type") or "").lower()
        selector = str(element.get("selector") or "").strip()
        if element_type != tag or not selector or _is_bare_tag_selector(selector):
            continue
        matches.append(selector)
    unique = sorted(set(matches))
    return unique[0] if len(unique) == 1 else None


def _selector_from_html(tag: str, html: str) -> str | None:
    matches = [(t, attrs) for t, attrs in _parse_html_elements(html) if t == tag]
    if len(matches) != 1:
        return None
    return _selector_from_attrs(tag, matches[0][1])


def _context_selector_for_bare_tag(context: dict[str, Any], tag: str) -> str | None:
    selector_candidates: list[str] = []
    scan_units: list[dict[str, Any]] = []
    dom = context.get("dom") or {}
    if isinstance(dom, dict):
        scan_units.append(dom)
    snapshots = context.get("route_snapshots") or {}
    if isinstance(snapshots, dict):
        scan_units.extend(s for s in snapshots.values() if isinstance(s, dict))

    for unit in scan_units:
        selector = _selector_from_interactive_elements(tag, list(unit.get("interactive_elements") or []))
        if selector:
            selector_candidates.append(selector)
        selector = _selector_from_html(tag, str(unit.get("html") or ""))
        if selector:
            selector_candidates.append(selector)

    unique = sorted({candidate for candidate in selector_candidates if candidate and not _is_bare_tag_selector(candidate)})
    return unique[0] if len(unique) == 1 else None


def _ts_locator_selector(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _refine_bare_tag_locators(code: str, context: dict[str, Any]) -> tuple[str, list[str]]:
    """Replace unsafe `locator('tag')` only when route DOM proves one stable target."""
    replacements: dict[str, str] = {}
    for tag in _bare_tag_violations(code):
        selector = _context_selector_for_bare_tag(context, tag)
        if selector:
            replacements[tag] = selector
    if not replacements:
        return code, []

    fixes: list[str] = []

    def repl(match: re.Match[str]) -> str:
        tag = match.group("tag")
        selector = replacements.get(tag)
        if not selector:
            return match.group(0)
        fixes.append(f"{tag}->{selector}")
        return f".locator('{_ts_locator_selector(selector)}')"

    return _BARE_TAG_LOCATOR_RE.sub(repl, code), sorted(set(fixes))


# Invalid Playwright syntax — `locator('role=…', { name: '…' })` does NOT work.
# `role=` is a CSS selector engine that takes everything in the string; the
# second `{name}` argument is silently ignored or throws. The model conflates
# this with `getByRole('button', { name: '…' })`. Detect and reject so the
# LLM is re-sampled with explicit guidance to use getByRole instead.
_ROLE_LOCATOR_RE = re.compile(r"""\.locator\(\s*(['"])role\s*=""")
_LOCATOR_ACTION_RE = re.compile(
    r"""\.(?:locator|smartFind)\(\s*(['"])(?P<selector>.+?)\1\s*\)\s*\.\s*(?P<action>click|fill|selectOption|check|uncheck)\s*\(""",
)


def _has_role_selector_locator(code: str) -> bool:
    return bool(_ROLE_LOCATOR_RE.search(code))


def _path_from_url_value(url: str | None) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if not parsed.path:
            return "/"
        return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
    except Exception:
        return url or ""


def _route_snapshot_for_recorded_step(context: dict[str, Any], recorded: dict[str, Any]) -> dict[str, Any] | None:
    path = _path_from_url_value(str(recorded.get("url") or ""))
    snapshots = context.get("route_snapshots") or {}
    if isinstance(snapshots, dict):
        if path in snapshots and isinstance(snapshots[path], dict):
            return snapshots[path]
        base = path.split("?", 1)[0]
        if base in snapshots and isinstance(snapshots[base], dict):
            return snapshots[base]
    dom = context.get("dom") or {}
    return dom if isinstance(dom, dict) else None


def _stable_selector_from_element_text(snapshot: dict[str, Any], text: str) -> str | None:
    if not text or text.strip().isdigit():
        return None
    matches: list[str] = []
    for element in snapshot.get("interactive_elements") or []:
        if str(element.get("text") or "").strip() != text.strip():
            continue
        selector = str(element.get("selector") or "").strip()
        if selector and not _is_bare_tag_selector(selector):
            matches.append(selector)
    unique = sorted(set(matches))
    return unique[0] if len(unique) == 1 else None


def _stable_anchor_selector_containing_text(html: str, text: str) -> str | None:
    if not text:
        return None
    escaped = re.escape(text.strip())
    pattern = re.compile(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>(?:(?!</a>).)*?\b" + escaped + r"\b(?:(?!</a>).)*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )
    selectors: list[str] = []
    for match in pattern.finditer(html or ""):
        attrs: dict[str, str] = {}
        for attr_match in _HTML_ATTR_RE.finditer(match.group("attrs") or ""):
            name = (attr_match.group("name") or "").strip()
            if not name:
                continue
            value = attr_match.group("quoted")
            if value is None:
                value = attr_match.group("bare") or ""
            attrs[name] = value
        selector = _selector_from_attrs("a", attrs)
        if selector:
            selectors.append(selector)
    unique = sorted(set(selectors))
    return unique[0] if len(unique) == 1 else None


def _stable_selector_for_recorded_step(context: dict[str, Any], recorded: dict[str, Any]) -> str | None:
    selector = str(recorded.get("selector") or "").strip()
    if selector and not _is_bare_tag_selector(selector):
        return selector

    snapshot = _route_snapshot_for_recorded_step(context, recorded) or {}
    text = str(recorded.get("element_text") or "").strip()
    from_text = _stable_selector_from_element_text(snapshot, text)
    if from_text:
        return from_text
    if str(recorded.get("element_type") or "").lower() == "a" or selector.lower() == "a":
        return _stable_anchor_selector_containing_text(str(snapshot.get("html") or ""), text)
    return None


def _emitted_action_sequence(code: str) -> list[tuple[str, str]]:
    sequence: list[tuple[str, str]] = []
    for match in _LOCATOR_ACTION_RE.finditer(code):
        action = match.group("action")
        normalized = "select" if action == "selectOption" else action
        sequence.append((normalized, match.group("selector")))
    return sequence


def _expected_action_sequence(context: dict[str, Any]) -> list[tuple[str, str, str]]:
    recorded_steps = list(context.get("recorded_steps") or [])
    used_recorded: set[int] = set()
    expected: list[tuple[str, str, str]] = []
    steps = [str(step).strip() for step in (context.get("steps") or [])]

    for step_index, step in enumerate(steps):
        action = _desired_action_for_step(step)
        if action not in {"click", "fill", "select", "check"}:
            continue

        selector = _selector_from_step(step)
        if action == "click" and selector:
            later_fill_same_selector = any(
                (_desired_action_for_step(other) == "fill" and _selector_from_step(other) == selector)
                for other in steps
            )
            if later_fill_same_selector:
                continue
        recorded_index: int | None = None
        if not selector:
            raw_selector = _bare_selector_from_step(step)
            recorded_action = "fill" if action == "upload" else action
            if raw_selector:
                selector, recorded_index = _recorded_selector_matching_raw(
                    recorded_steps, recorded_action, raw_selector, used_recorded
                )
            if not selector:
                selector, recorded_index = _recorded_selector_for_action(
                    recorded_steps,
                    recorded_action,
                    used_recorded,
                    step,
                )
            if not selector:
                selector = _element_selector_for_step(context, recorded_action, step)
        elif _is_bare_tag_selector(selector):
            selector = None

        if recorded_index is not None:
            used_recorded.add(recorded_index)
            stable = _stable_selector_for_recorded_step(context, recorded_steps[recorded_index])
            selector = stable or selector

        if selector and not _is_bare_tag_selector(selector):
            expected.append(("select" if action == "select" else action, selector, step))

    return expected


def _sequence_coverage_errors(code: str, context: dict[str, Any] | None) -> list[str]:
    if not context:
        return []
    expected = _expected_action_sequence(context)
    if not expected:
        return []
    emitted = _emitted_action_sequence(code)
    cursor = 0
    missing: list[str] = []
    for action, selector, step in expected:
        found_at: int | None = None
        for index in range(cursor, len(emitted)):
            emitted_action, emitted_selector = emitted[index]
            if emitted_action == action and emitted_selector == selector:
                found_at = index
                break
        if found_at is None:
            missing.append(f"{action} {selector} from step {step!r}")
        else:
            cursor = found_at + 1
    if missing:
        return [f"missing approved step actions in order: {missing[:5]}"]
    return []


# Hardcoded http(s):// literals leak the dev BASE_URL into checked-in scripts
# and break any non-default environment. Always use env('BASE_URL') + path.
# We exclude empty bases and Playwright's wildcard glob form like '**/foo'.
_HARDCODED_URL_RE = re.compile(
    r"""(['"])https?://[A-Za-z0-9._\-:/?#\[\]@!$&'()*+,;=%]+\1"""
)


def _hardcoded_url_violations(code: str) -> list[str]:
    return [m.group(0) for m in _HARDCODED_URL_RE.finditer(code)]


_RAW_CREDENTIAL_RE = re.compile(
    r"""(['"])(?=[^'"]*(?:@|password|secret|token))[^'"]{6,}\1""",
    re.IGNORECASE,
)
_WAIT_FOR_TIMEOUT_RE = re.compile(r"\.waitForTimeout\s*\(")
_PLACEHOLDER_SCRIPT_RE = re.compile(
    r"SCRIPT GENERATION FAILED|manual review required|TODO|FIXME|SKIPPED:",
    re.IGNORECASE,
)
_NON_MONITOR_EXPECT_RE = re.compile(r"(?P<stmt>[^;\n]*\bexpect\s*\([^;\n]+;)", re.IGNORECASE)


def _raw_credential_violations(code: str) -> list[str]:
    violations: list[str] = []
    for match in _RAW_CREDENTIAL_RE.finditer(code):
        literal = match.group(0)
        lower = literal.lower()
        content = literal[1:-1].strip()
        if "base_url" in lower or "user_email" in lower or "user_password" in lower:
            continue
        # CSS selectors often contain words like "#password" or
        # "[data-testid='token-field']"; those are not raw secret values.
        if content.startswith(("#", ".", "[", "input", "button", "select")):
            continue
        violations.append(literal)
    return violations


def _has_network_monitor_first_action(code: str, page_var: str) -> bool:
    marker = f"const monitor = new NetworkMonitor({page_var});"
    lines = [
        line.strip()
        for line in code.splitlines()
        if line.strip() and not line.strip().startswith("//")
    ]
    for index, line in enumerate(lines):
        if "const monitor = new NetworkMonitor" in line:
            return line == marker and index > 0
    return False


def _has_business_expect(code: str) -> bool:
    for line in code.splitlines():
        if "expect(" not in line:
            continue
        if "monitor.hasFailures()" in line or "monitor.failures" in line:
            continue
        return True
    return False


def _context_contract_text(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    parts: list[str] = [
        str(context.get("title") or ""),
        str(context.get("target_page") or ""),
    ]
    parts.extend(str(step) for step in (context.get("steps") or []))
    parts.extend(str(item) for item in (context.get("acceptance_criteria") or []))
    text = " ".join(parts).lower()
    # "No application/network error" is a generic non-regression acceptance
    # criterion, not a negative-validation requirement.
    text = re.sub(r"\bno\s+[^.。;\n]*\b(?:application|network|system|console)\s+error(?:s)?\s+(?:is|are)\s+shown\b", "", text)
    text = re.sub(r"\bno\s+[^.。;\n]*\b(?:application|network|system|console)\s+error(?:s)?\b", "", text)
    # Routes and sort labels like /records, A-Z, low-high should not trigger
    # generic count/assertion contracts by looking like domain words/numbers.
    text = re.sub(r"/[a-z0-9_./?=&%-]+", " ", text)
    text = re.sub(r"\b[a-z]-[a-z]\b", " ", text)
    text = re.sub(r"\b(?:low|high)-(?:low|high)\b", " ", text)
    return text


def _non_monitor_expect_statements(code: str) -> list[str]:
    statements: list[str] = []
    for match in _NON_MONITOR_EXPECT_RE.finditer(code):
        stmt = match.group("stmt").strip()
        if "monitor.hasFailures()" not in stmt and "monitor.failures" not in stmt:
            statements.append(stmt)
    return statements


def _network_evidence_lines(page_var: str = "page") -> list[str]:
    return [
        "  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });",
        "  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);",
    ]


def _has_network_logs_attachment(code: str) -> bool:
    return bool(re.search(r"\btestInfo\.attach\(\s*['\"]network_logs['\"]", code))


def _has_network_evidence_assertion(code: str) -> bool:
    return "expect(monitor.failures" in code and ".toEqual([])" in code


def _expect_text(code: str) -> str:
    return "\n".join(_non_monitor_expect_statements(code)).lower()


def _contract_mentions(text: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        cleaned = term.strip()
        if not cleaned:
            continue
        if " " in cleaned:
            if cleaned in text:
                return True
            continue
        if re.search(rf"\b{re.escape(cleaned)}\b", text):
            return True
    return False


def _has_ui_assertion(expect_text: str, terms: tuple[str, ...]) -> bool:
    if not expect_text.strip():
        return False
    if not _contract_mentions(expect_text, terms):
        return False
    # URL checks alone prove navigation, not business-visible QA outcomes.
    non_url_lines = [
        line for line in expect_text.splitlines()
        if "page.url()" not in line and ".url()" not in line
    ]
    return bool(non_url_lines)


def _has_non_url_ui_assertion(expect_text: str) -> bool:
    if not expect_text.strip():
        return False
    return any(
        line
        for line in expect_text.splitlines()
        if "page.url()" not in line and ".url()" not in line
    )


def _has_count_or_quantity_assertion(expect_text: str) -> bool:
    if not _has_non_url_ui_assertion(expect_text):
        return False
    count_markers = (
        "tohavecount",
        ".count(",
        ".length",
        "length)",
        "quantity",
        "count",
        "number",
        "total",
        "tobe(",
        "toequal(",
    )
    compact = expect_text.replace(" ", "")
    return any(marker in expect_text or marker in compact for marker in count_markers)


def _assertion_keywords_from_context(contract_text: str) -> tuple[str, ...]:
    tokens = re.findall(r"[a-z][a-z0-9_-]{2,}", contract_text)
    generic = {
        "assert", "verify", "validate", "validation", "expected", "actual",
        "should", "must", "user", "tester", "page", "screen", "field", "button",
        "link", "click", "fill", "enter", "select", "open", "navigate", "go",
        "visible", "displayed", "shown", "appears", "successfully", "without",
        "with", "from", "into", "after", "before", "then", "and", "the", "that",
        "this", "all", "any", "same", "valid", "invalid", "value", "data",
    }
    keywords = [token for token in tokens if token not in generic]
    return tuple(dict.fromkeys(keywords[:12]))


def _grounding_text_from_context(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    parts: list[str] = [
        _context_contract_text(context),
        str((context.get("dom") or {}).get("html") or ""),
    ]
    for element in (context.get("dom") or {}).get("interactive_elements") or []:
        parts.append(str(element.get("selector") or ""))
        parts.append(str(element.get("text") or ""))
    for snapshot in (context.get("route_snapshots") or {}).values():
        if not isinstance(snapshot, dict):
            continue
        parts.append(str(snapshot.get("html") or ""))
        for element in snapshot.get("interactive_elements") or []:
            parts.append(str(element.get("selector") or ""))
            parts.append(str(element.get("text") or ""))
    for evidence in (context.get("assertion_evidence") or []):
        if not isinstance(evidence, dict):
            continue
        parts.extend(
            str(evidence.get(key) or "")
            for key in ("outcome", "source_text", "observable_hint", "kind")
        )
        hint = str(evidence.get("observable_hint") or "")
        if hint:
            parts.extend(re.findall(r"[a-z][a-z0-9_-]{2,}", hint.lower()))
    return "\n".join(parts).lower()


def _evidence_requires_visible_ui(evidence: dict[str, Any]) -> bool:
    kind = str(evidence.get("kind") or "").strip().lower()
    return kind in {
        "ui_text",
        "element_visible",
        "element_absent",
        "error_message",
        "attribute_check",
        "count_check",
    }


def _meaningful_assertion_texts(code: str) -> list[str]:
    return [
        stmt.lower()
        for stmt in _non_monitor_expect_statements(code)
        if "page.url()" not in stmt.lower() and ".url()" not in stmt.lower()
    ]


_ASSERTION_GROUNDING_STOP_WORDS = {
    "about",
    "after",
    "again",
    "against",
    "also",
    "and",
    "aria",
    "assert",
    "await",
    "before",
    "button",
    "click",
    "contain",
    "containtext",
    "data",
    "does",
    "expect",
    "false",
    "field",
    "fill",
    "form",
    "from",
    "getbyrole",
    "getbytext",
    "have",
    "hidden",
    "input",
    "label",
    "locator",
    "name",
    "not",
    "page",
    "role",
    "select",
    "should",
    "submit",
    "test",
    "text",
    "that",
    "this",
    "tobe",
    "tobehidden",
    "tobevisible",
    "tocontaintext",
    "tohaveattribute",
    "tohavecount",
    "tohavetext",
    "true",
    "value",
    "visible",
    "with",
}


def _assertion_mentions_grounded_text(assertions: list[str], grounding_text: str) -> bool:
    if not assertions:
        return False
    for assertion in assertions:
        for token in re.findall(r"[a-z][a-z0-9_-]{3,}", assertion.lower()):
            if token in _ASSERTION_GROUNDING_STOP_WORDS:
                continue
            if token in grounding_text:
                return True
        selectors = re.findall(r"""locator\(\s*['"]([^'"]+)['"]\s*\)""", assertion)
        if any(selector.lower() in grounding_text for selector in selectors):
            return True
    return False


def _evidence_driven_assertion_errors(code: str, context: dict[str, Any] | None) -> list[str]:
    evidence = [
        ev for ev in ((context or {}).get("assertion_evidence") or [])
        if isinstance(ev, dict)
    ]
    assertions = _meaningful_assertion_texts(code)
    if not evidence:
        return [] if assertions else ["missing business UI assertion (no evidence available)"]

    errors: list[str] = []
    high_confidence = [
        ev for ev in evidence
        if float(ev.get("confidence") or 0.0) >= 0.5
        and not (ev.get("grounding") == "inferred" and float(ev.get("confidence") or 0.0) < 0.7)
    ]
    if not high_confidence:
        errors.append("assertion evidence is low confidence; human review required")
        return errors

    visible_required = any(_evidence_requires_visible_ui(ev) for ev in high_confidence)
    if visible_required and not assertions:
        errors.append("evidence requires visible UI assertion but script only asserts navigation")

    if all(str(ev.get("kind") or "").lower() == "navigation" for ev in high_confidence):
        errors.append("navigation-only assertion evidence is insufficient")

    grounding_text = _grounding_text_from_context(context)
    if visible_required and assertions and not _assertion_mentions_grounded_text(assertions, grounding_text):
        errors.append("business assertion is not grounded in assertion evidence or DOM")

    return list(dict.fromkeys(errors))


def _common_script_quality_violations(code: str, *, page_var: str) -> list[str]:
    violations: list[str] = []
    if _WAIT_FOR_TIMEOUT_RE.search(code):
        violations.append("waitForTimeout")
    if not _has_business_expect(code):
        violations.append("missing business expect")
    if not _has_network_monitor_first_action(code, page_var):
        violations.append("missing first NetworkMonitor")
    if not _has_network_logs_attachment(code):
        violations.append("missing network_logs attachment")
    if not _has_network_evidence_assertion(code):
        violations.append("missing network evidence assertion")
    if _PLACEHOLDER_SCRIPT_RE.search(code):
        violations.append("placeholder/manual-review script")
    raw_credentials = _raw_credential_violations(code)
    if raw_credentials:
        violations.append(f"raw credentials {raw_credentials[:3]}")
    return violations


def _unexpected_duplicate_action_errors(code: str, context: dict[str, Any] | None) -> list[str]:
    if not context:
        return []
    expected = [(action, selector) for action, selector, _step in _expected_action_sequence(context)]
    expected_counts: dict[tuple[str, str], int] = {}
    for key in expected:
        expected_counts[key] = expected_counts.get(key, 0) + 1

    emitted = _emitted_action_sequence(code)
    emitted_counts: dict[tuple[str, str], int] = {}
    for key in emitted:
        emitted_counts[key] = emitted_counts.get(key, 0) + 1

    duplicates = [
        f"{action} {selector}"
        for (action, selector), count in emitted_counts.items()
        if count > expected_counts.get((action, selector), 0)
        and action in {"click", "fill"}
    ]
    return [f"unexpected duplicate generated actions: {duplicates[:5]}"] if duplicates else []


def _sorting_assertion_errors(code: str, context: dict[str, Any] | None) -> list[str]:
    contract = _context_contract_text(context)
    if not _contract_mentions(contract, ("sort", "sorted", "sorting", "ascending", "descending", "alphabetical", "price order")):
        return []
    code_lower = code.lower()
    if "arraycontaining" in code_lower:
        return ["sorting assertion only checks item presence; must compare ordered list values"]
    order_markers = (
        ".sort(",
        "localecompare",
        "toequal(sorted",
        "toequal(expect.arraycontaining",
        "every(",
        "ascending",
        "descending",
    )
    if not any(marker in code_lower for marker in order_markers):
        return ["missing ordered-list assertion for sorting behavior"]
    return []


def _script_validation_errors(code: str, context: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    if not code.strip():
        return ["empty response"]
    if "test(" not in code:
        errors.append("missing Playwright test() block")
    if not _PAGE_FIXTURE_RE.search(code) or "testInfo" not in code:
        errors.append("single-test block must use async ({ page }, testInfo)")
    errors.extend(_common_script_quality_violations(code, page_var="page"))
    bad = _bare_tag_violations(code)
    if bad:
        errors.append(f"bare-tag selectors {bad}")
    if _has_role_selector_locator(code):
        errors.append("invalid locator('role=...') syntax; use getByRole")
    urls = _hardcoded_url_violations(code)
    if urls:
        errors.append(f"hardcoded URLs {urls[:3]}; use env('BASE_URL')")
    apis = _invalid_api_violations(code)
    if apis:
        errors.append(f"non-Playwright APIs {apis}")
    errors.extend(_sequence_coverage_errors(code, context))
    errors.extend(_unexpected_duplicate_action_errors(code, context))
    errors.extend(_sorting_assertion_errors(code, context))
    errors.extend(_evidence_driven_assertion_errors(code, context))
    return errors


def _grouped_validation_errors(code: str, context: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    has_grouped_test_info_signature = (
        "async ({}, testInfo)" in code
        or "async({}, testInfo)" in code
        or "async ({ }, testInfo)" in code
    )
    if not code.strip():
        return ["empty response"]
    if "test(" not in code:
        errors.append("missing Playwright test() block")
    if "sharedPage" not in code:
        errors.append("grouped block must use sharedPage")
    if not has_grouped_test_info_signature:
        errors.append("grouped test() must use async ({}, testInfo) with no page fixture")
    errors.extend(_common_script_quality_violations(code, page_var="sharedPage"))
    bad = _bare_tag_violations(code)
    if bad:
        errors.append(f"bare-tag selectors {bad}")
    if _has_role_selector_locator(code):
        errors.append("invalid locator('role=...') syntax; use getByRole")
    urls = _hardcoded_url_violations(code)
    if urls:
        errors.append(f"hardcoded URLs {urls[:3]}; use env('BASE_URL')")
    apis = _invalid_api_violations(code)
    if apis:
        errors.append(f"non-Playwright APIs {apis}")
    errors.extend(_sequence_coverage_errors(code, context))
    errors.extend(_unexpected_duplicate_action_errors(code, context))
    errors.extend(_sorting_assertion_errors(code, context))
    errors.extend(_evidence_driven_assertion_errors(code, context))
    return errors


def _retry_feedback(errors: list[str]) -> str:
    if not errors:
        return ""
    lines = "\n".join(f"- {error}" for error in errors[:8])
    return f"""

IMPORTANT: Your previous Playwright block was rejected for these exact reasons:
{lines}

Regenerate the block and fix every issue. Return only the corrected test() block.
"""


def _is_auth_test(title: str) -> bool:
    """True if the title regex matches an auth flow (legacy, title-only check)."""
    return bool(_AUTH_TITLE_RE.search(title or ""))


# auth_mode values produced by A3 (_infer_auth_mode) that must START WITHOUT a
# stored session — login pages, signup, password reset, public/anonymous pages.
# `login_flow` was previously honored only by the title regex below, which let
# A3-classified login_flow tests slip through and load an authenticated state.
_ANON_START_AUTH_MODES = frozenset({"anonymous", "login_flow"})


def _requires_anonymous_start(auth_mode: str | None, title: str) -> bool:
    """Authoritative gate for "should this test start unauthenticated?".

    Combines two signals so we never silently mis-handle:
      1. A3's `auth_mode` (the source-of-truth classification stored in DB)
      2. Title regex (defense-in-depth, catches LLM titles A3 mis-tagged)
    """
    if (auth_mode or "").lower() in _ANON_START_AUTH_MODES:
        return True
    return _is_auth_test(title)


def _post_process_block(
    code: str,
    title: str,
    *,
    is_grouped: bool,
    auth_mode: str | None = None,
    auth_login_path: str | None = None,
) -> str:
    """Deterministic patches applied to every generated test block.

    Each rule targets a real failure class seen in production runs:

      1. Login tests must reset storageState (single-test scripts only —
         grouped scripts handle this at the beforeAll level, see
         _context_auth_state_path).
      2. `waitForURL('/path')` bare paths must use glob form to survive
         host/protocol variations and Playwright's strict URL matching.
      3. Login-flow tests goto the path captured in Phase-2 (auth_login_path)
         instead of whatever the LLM guessed (often a post-login URL).
    """
    patched = code
    changes: list[str] = []
    needs_anon = _requires_anonymous_start(auth_mode, title)

    # Rule 3 — recording-driven login goto rewrite (multi-tenant).
    # Fires only when:
    #   1. This is a login/anonymous-start test (needs_anon = True)
    #   2. Phase-2 captured a login URL we can compare against (auth_login_path)
    #   3. The LLM emitted a goto whose path differs from the recorded login URL
    # If any condition is missing, we leave the code untouched — better to let
    # the test fail visibly than to rewrite blindly to a wrong path. The LLM
    # prompt already instructs use of recorded selectors / URLs.
    if needs_anon and auth_login_path:
        new_patched, n = _rewrite_login_goto(patched, auth_login_path)
        if n:
            patched = new_patched
            changes.append(f"login-nav→{auth_login_path} x{n}")

    # Rule 1 — single-test login/signup/anonymous scripts need an explicit
    # storageState reset prepended. In grouped scripts the beforeAll context
    # governs this instead (see _context_auth_state_path).
    if (
        not is_grouped
        and needs_anon
        and _TEST_USE_STORAGE_CLEAR not in patched
    ):
        patched = _TEST_USE_STORAGE_CLEAR + "\n\n" + patched
        changes.append("storageState-clear")

    # Rule 2 — waitForURL bare paths → glob form
    new_patched, n = _BARE_WAIT_FOR_URL_RE.subn(r"waitForURL('**\2')", patched)
    if n:
        patched = new_patched
        changes.append(f"waitForURL-glob x{n}")

    new_patched, n = _ensure_test_info_signature(patched, is_grouped=is_grouped)
    if n:
        patched = new_patched
        changes.append("testInfo-signature")

    new_patched, n = _ensure_network_evidence_assertion(patched)
    if n:
        patched = new_patched
        changes.append("network-evidence")

    if changes:
        logger.info(
            "agent5: post-processed block title=%r fixes=%s",
            title, ", ".join(changes),
        )

    return patched


def _validate_script(code: str, context: dict[str, Any] | None = None) -> bool:
    errors = _script_validation_errors(code, context)
    if errors:
        logger.warning("agent5: rejecting script — validation errors: %s", errors)
        return False
    return True


def _validate_grouped_block(code: str, context: dict[str, Any] | None = None) -> bool:
    """Validate a grouped (serial describe) test block.

    NOTE: The `or` conditions for the async signature variants MUST be grouped
    inside parentheses. Without parens, Python evaluates `and` before `or`,
    causing the last two `or` branches to escape the `and` chain and always
    return truthy strings — a silent validation bypass bug.
    """
    errors = _grouped_validation_errors(code, context)
    if errors:
        logger.warning("agent5: rejecting grouped block — validation errors: %s", errors)
        return False
    return True


def _rate_limit_sleep() -> float:
    """Seconds to sleep between grouped subtask LLM calls."""
    return float(settings.llm_rate_limit_sleep)


def _context_auth_state_path(contexts: list[dict[str, Any]]) -> str | None:
    """Pick the storageState file to load in a grouped suite's beforeAll.

    Critical: if the FIRST subtask is anonymous (a login/signup test), we must
    return None so the describe's beforeAll creates a fresh, unauthenticated
    context. Loading storageState for an anonymous leading test lands the
    browser on the already-logged-in app, at which point the login form
    selectors (#user-name, #password, #login-button) time out and the entire
    serial group is skipped.

    A3 orders subtasks so unblocker tests run first, so contexts[0] is the
    canonical signal for the whole group.
    """
    if not contexts:
        return None
    # Both "anonymous" AND "login_flow" require a fresh, unauthenticated context.
    # Previously only "anonymous" was checked, so A3-classified login_flow tests
    # silently got storageState loaded → login form selectors timed out → group
    # cascaded as BLOCKED.
    first_auth_mode = (contexts[0].get("auth_mode") or "").lower()
    first_title = contexts[0].get("title") or ""
    if first_auth_mode in _ANON_START_AUTH_MODES or _is_auth_test(first_title):
        return None
    for context in contexts:
        if context.get("auth_mode") == "authenticated" and context.get("auth_state_path"):
            return str(context["auth_state_path"])
    return None


# ── Single test generation ────────────────────────────────────────────────────

def _ts_string(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def _env_or_literal(value: str) -> str:
    raw = str(value or "").strip().strip('"').strip("'")
    token = raw.strip("{}").strip()
    if token in {"USER_EMAIL", "USER_PASSWORD", "BASE_URL", "ADMIN_EMAIL", "ADMIN_PASSWORD"}:
        return f"env('{token}')"
    if "password" in raw.lower() or "secret" in raw.lower() or "@" in raw:
        return "env('USER_PASSWORD')" if "password" in raw.lower() else "env('USER_EMAIL')"
    return f"'{_ts_string(raw)}'"


def _path_from_step(step: str) -> str | None:
    match = re.search(r"\{BASE_URL\}\s*([^\s'\"]*)", step)
    if match:
        path = match.group(1) or "/"
        return path if path.startswith("/") else f"/{path}"
    match = re.search(r"env\(['\"]BASE_URL['\"]\)\s*\+\s*['\"]([^'\"]+)['\"]", step)
    if match:
        return match.group(1)
    match = re.search(r"(?<!\S)(/[A-Za-z0-9_./?=&%-]+)", step)
    if match:
        return match.group(1)
    return None


def _selector_from_step(step: str) -> str | None:
    masked = re.sub(r"\{BASE_URL\}\s*[^\s'\"]*", " ", step)
    masked = re.sub(r"env\(['\"]BASE_URL['\"]\)\s*\+\s*['\"][^'\"]+['\"]", " ", masked)
    masked = re.sub(r"(?<!\S)/[A-Za-z0-9_./?=&%-]+", " ", masked)
    for pattern in (
        r"(input\[[^\]]+\])",
        r"(button\[[^\]]+\])",
        r"(select\[[^\]]+\])",
        r"(\[[^\]]+\])",
        r"(#[A-Za-z0-9_-]+)",
        r"(\.[A-Za-z][A-Za-z0-9_-]+)",
    ):
        match = re.search(pattern, masked)
        if match:
            return match.group(1)
    return None


def _bare_selector_from_step(step: str) -> str | None:
    match = re.search(r"\b(" + "|".join(_BARE_TAG_NAMES) + r")\b", step.lower())
    return match.group(1) if match else None


def _recorded_selector_matching_raw(
    recorded_steps: list[dict[str, Any]],
    action: str,
    raw_selector: str,
    used: set[int],
) -> tuple[str | None, int | None]:
    for index, recorded in enumerate(recorded_steps):
        if index in used:
            continue
        if str(recorded.get("action") or "").lower() != action:
            continue
        if str(recorded.get("selector") or "").strip().lower() == raw_selector.lower():
            return str(recorded.get("selector") or "").strip(), index
    return None, None


def _recorded_selector_for_action(
    recorded_steps: list[dict[str, Any]],
    action: str,
    used: set[int],
    step_text: str = "",
) -> tuple[str | None, int | None]:
    step_tokens = {tok for tok in re.findall(r"[a-z0-9]+", step_text.lower()) if len(tok) > 2}
    best: tuple[int, int, str] | None = None
    for index, recorded in enumerate(recorded_steps):
        if index in used or str(recorded.get("action") or "").lower() != action:
            continue
        selector = str(recorded.get("selector") or "").strip()
        if not selector:
            continue
        haystack = " ".join(
            str(recorded.get(key) or "")
            for key in ("selector", "element_text", "element_type", "value")
        ).lower()
        # Avoid binding semantic bridge steps like "click the cart link" to
        # mutation controls such as "add-to-cart" just because they share a noun.
        # The actual bridge should come from a link/button DOM element or the
        # recorded bridge step, not from a prior add/remove action.
        if (
            action == "click"
            and _contract_mentions(step_text.lower(), ("link", "open", "go to", "navigate"))
            and _contract_mentions(haystack, ("add", "remove", "delete", "clear"))
        ):
            continue
        score = len(step_tokens & set(re.findall(r"[a-z0-9]+", haystack)))
        if best is None or score > best[0]:
            best = (score, index, selector)
    if best is None:
        return None, None
    # Do not let the fallback pick an arbitrary selector for a vague step like
    # "submit request". A positive token overlap means the approved step and
    # Phase-2 recording actually describe the same control.
    if step_tokens and best[0] <= 0:
        return None, None
    return best[2], best[1]


def _semantic_tokens(text: str) -> set[str]:
    raw = re.sub(r"\{[^}]*\}", " ", str(text or "").lower())
    raw = raw.replace("#", " ").replace("-", " ").replace("_", " ")
    tokens = {
        tok for tok in re.findall(r"[a-z0-9]+", raw)
        if len(tok) > 2 and tok not in {
            "click", "select", "choose", "from", "the", "link", "button",
            "dropdown", "option", "field", "input", "page", "with",
        }
    }
    expansions = {
        "checkout": {"checkout"},
        "cart": {"cart", "basket", "bag"},
        "basket": {"cart", "basket"},
        "review": {"review", "summary"},
        "summary": {"review", "summary"},
        "sort": {"sort", "sorting", "order"},
    }
    expanded = set(tokens)
    for token in tokens:
        expanded.update(expansions.get(token, set()))
    return expanded


def _element_selector_for_step(
    context: dict[str, Any],
    action: str,
    step_text: str,
) -> str | None:
    """Resolve semantic UI steps from DOM/route snapshot elements.

    This covers app-neutral bridge steps like "click the cart link" or
    "select sort option..." when the recorded selector was weak but the DOM
    snapshot contains a stable selector.
    """
    step_tokens = _semantic_tokens(step_text)
    if not step_tokens:
        return None

    elements: list[dict[str, Any]] = []
    dom = context.get("dom") or {}
    elements.extend([el for el in dom.get("interactive_elements") or [] if isinstance(el, dict)])
    for snapshot in (context.get("route_snapshots") or {}).values():
        if isinstance(snapshot, dict):
            elements.extend([el for el in snapshot.get("interactive_elements") or [] if isinstance(el, dict)])

    best: tuple[int, str] | None = None
    for element in elements:
        selector = str(element.get("selector") or "").strip()
        if not selector or _is_bare_tag_selector(selector):
            continue
        haystack = " ".join(
            str(element.get(key) or "")
            for key in ("selector", "text", "name", "aria_label", "role", "tag", "type", "placeholder", "label")
        )
        element_tokens = _semantic_tokens(haystack)
        overlap = step_tokens & element_tokens
        if not overlap:
            continue
        score = len(overlap)
        lower_selector = selector.lower()
        if action == "select" and ("select" in lower_selector or "sort" in lower_selector):
            score += 2
        if action == "click" and any(term in lower_selector for term in step_tokens):
            score += 1
        if best is None or score > best[0]:
            best = (score, selector)
    return best[1] if best else None


def _desired_action_for_step(step: str) -> str | None:
    lower = f" {step.lower()} "
    if "navigate to" in lower or " go to " in lower or " open " in lower:
        return "navigate"
    if lower.strip().startswith(("fill", "enter", "type")) or " fill " in lower:
        return "fill"
    if lower.strip().startswith(("select", "choose")) or " select " in lower:
        return "select"
    if lower.strip().startswith(("check", "uncheck")):
        return "check"
    if lower.strip().startswith(("upload", "attach")):
        return "upload"
    if lower.strip().startswith(("click", "tap", "press", "submit", "add", "remove")):
        return "click"
    if lower.strip().startswith("assert") and "visible" in lower:
        return "assert_visible"
    return None


def _grounding_report(context: dict[str, Any]) -> dict[str, Any]:
    recorded_steps = list(context.get("recorded_steps") or [])
    used_recorded: set[int] = set()
    actionable = 0
    grounded = 0
    ungrounded: list[str] = []

    for raw_step in context.get("steps") or []:
        step = str(raw_step).strip()
        if not step:
            continue
        action = _desired_action_for_step(step)
        if not action:
            continue
        actionable += 1
        if action == "navigate":
            if _path_from_step(step) or context.get("target_page"):
                grounded += 1
            else:
                ungrounded.append(step)
            continue
        if action == "assert_visible":
            if _selector_from_step(step):
                grounded += 1
            else:
                # Assertions often depend on page text from BRD/DOM rather than
                # a recorded selector, so do not fail the whole case on these.
                actionable -= 1
            continue
        if _selector_from_step(step):
            grounded += 1
            continue
        recorded_action = "fill" if action == "upload" else action
        raw_selector = _bare_selector_from_step(step)
        if raw_selector:
            selector, recorded_index = _recorded_selector_matching_raw(
                recorded_steps, recorded_action, raw_selector, used_recorded
            )
        else:
            selector, recorded_index = _recorded_selector_for_action(
                recorded_steps, recorded_action, used_recorded, step
            )
        if selector:
            grounded += 1
            if recorded_index is not None:
                used_recorded.add(recorded_index)
        else:
            ungrounded.append(step)

    ratio = (grounded / actionable) if actionable else 0.0
    return {
        "actionable": actionable,
        "grounded": grounded,
        "ratio": ratio,
        "ungrounded": ungrounded,
    }


def _can_use_deterministic_fallback(context: dict[str, Any]) -> tuple[bool, str]:
    report = _grounding_report(context)
    actionable = int(report["actionable"])
    grounded = int(report["grounded"])
    if actionable <= 0:
        return False, "no grounded actionable steps"
    if grounded <= 0:
        return False, f"0/{actionable} actionable steps grounded"
    # Allow a small amount of ungrounded assertion/narrative noise, but never a
    # mostly-guessed script. Real QA automation should surface missing Phase-2
    # context instead of inventing locators.
    if float(report["ratio"]) < 0.6:
        return False, f"{grounded}/{actionable} actionable steps grounded"
    return True, f"{grounded}/{actionable} actionable steps grounded"


def _stable_recorded_selector_or_raw(
    context: dict[str, Any],
    recorded_steps: list[dict[str, Any]],
    selector: str | None,
    recorded_index: int | None,
) -> str | None:
    if recorded_index is None:
        return None if selector and _is_bare_tag_selector(selector) else selector
    stable = _stable_selector_for_recorded_step(context, recorded_steps[recorded_index])
    if stable:
        return stable
    return None if selector and _is_bare_tag_selector(selector) else selector


def _deterministic_lines_from_steps(context: dict[str, Any], *, page_var: str) -> list[str]:
    lines: list[str] = []
    used_recorded: set[int] = set()
    recorded_steps = list(context.get("recorded_steps") or [])
    for raw_step in context.get("steps") or []:
        step = str(raw_step).strip()
        lower = step.lower()
        if not step:
            continue
        if lower.startswith("navigate") or "navigate to" in lower:
            path = _path_from_step(step) or context.get("target_page") or "/"
            lines.append(f"  await {page_var}.goto(env('BASE_URL') + '{_ts_string(path)}');")
            continue
        if lower.startswith("wait for url") or lower.startswith("assert url") or "url is" in lower or "url to contain" in lower:
            path = _path_from_step(step)
            if path:
                lines.append(f"  await {page_var}.waitForURL('**{_ts_string(path)}');")
            continue
        if lower.startswith("fill") or " fill " in f" {lower} ":
            selector = _selector_from_step(step)
            recorded_index = None
            if not selector:
                raw_selector = _bare_selector_from_step(step)
                if raw_selector:
                    selector, recorded_index = _recorded_selector_matching_raw(recorded_steps, "fill", raw_selector, used_recorded)
                if not selector:
                    selector, recorded_index = _recorded_selector_for_action(recorded_steps, "fill", used_recorded, step)
                if not selector:
                    selector = _element_selector_for_step(context, "fill", step)
            selector = _stable_recorded_selector_or_raw(context, recorded_steps, selector, recorded_index)
            if not selector:
                selector = _element_selector_for_step(context, "fill", step)
            if selector:
                if recorded_index is not None:
                    used_recorded.add(recorded_index)
                value_match = re.search(r"\bwith\s+(.+)$", step, re.IGNORECASE)
                lines.append(
                    f"  await {page_var}.locator('{_ts_string(selector)}').fill({_env_or_literal(value_match.group(1) if value_match else '')});"
                )
            continue
        if lower.startswith(("enter", "type")):
            selector = _selector_from_step(step)
            recorded_index = None
            if not selector:
                raw_selector = _bare_selector_from_step(step)
                if raw_selector:
                    selector, recorded_index = _recorded_selector_matching_raw(recorded_steps, "fill", raw_selector, used_recorded)
                if not selector:
                    selector, recorded_index = _recorded_selector_for_action(recorded_steps, "fill", used_recorded, step)
                if not selector:
                    selector = _element_selector_for_step(context, "fill", step)
            selector = _stable_recorded_selector_or_raw(context, recorded_steps, selector, recorded_index)
            if not selector:
                selector = _element_selector_for_step(context, "fill", step)
            if selector:
                if recorded_index is not None:
                    used_recorded.add(recorded_index)
                value_match = re.search(r"\b(?:with|value|text)\s+(.+)$", step, re.IGNORECASE)
                lines.append(
                    f"  await {page_var}.locator('{_ts_string(selector)}').fill({_env_or_literal(value_match.group(1) if value_match else '')});"
                )
            continue
        if lower.startswith("click") or " click " in f" {lower} ":
            selector = _selector_from_step(step)
            recorded_index = None
            if not selector:
                raw_selector = _bare_selector_from_step(step)
                if raw_selector:
                    selector, recorded_index = _recorded_selector_matching_raw(recorded_steps, "click", raw_selector, used_recorded)
                if not selector:
                    selector, recorded_index = _recorded_selector_for_action(recorded_steps, "click", used_recorded, step)
                if not selector:
                    selector = _element_selector_for_step(context, "click", step)
            selector = _stable_recorded_selector_or_raw(context, recorded_steps, selector, recorded_index)
            if not selector:
                selector = _element_selector_for_step(context, "click", step)
            if selector:
                if recorded_index is not None:
                    used_recorded.add(recorded_index)
                lines.append(f"  await {page_var}.locator('{_ts_string(selector)}').click();")
            continue
        if lower.startswith(("submit", "add", "remove", "tap", "press")):
            selector = _selector_from_step(step)
            recorded_index = None
            if not selector:
                raw_selector = _bare_selector_from_step(step)
                if raw_selector:
                    selector, recorded_index = _recorded_selector_matching_raw(recorded_steps, "click", raw_selector, used_recorded)
                if not selector:
                    selector, recorded_index = _recorded_selector_for_action(recorded_steps, "click", used_recorded, step)
                if not selector:
                    selector = _element_selector_for_step(context, "click", step)
            selector = _stable_recorded_selector_or_raw(context, recorded_steps, selector, recorded_index)
            if not selector:
                selector = _element_selector_for_step(context, "click", step)
            if selector:
                if recorded_index is not None:
                    used_recorded.add(recorded_index)
                lines.append(f"  await {page_var}.locator('{_ts_string(selector)}').click();")
            continue
        if lower.startswith("select") or " select " in f" {lower} ":
            selector = _selector_from_step(step)
            recorded_index = None
            if not selector:
                raw_selector = _bare_selector_from_step(step)
                if raw_selector:
                    selector, recorded_index = _recorded_selector_matching_raw(recorded_steps, "select", raw_selector, used_recorded)
                if not selector:
                    selector, recorded_index = _recorded_selector_for_action(recorded_steps, "select", used_recorded, step)
                if not selector:
                    selector = _element_selector_for_step(context, "select", step)
            selector = _stable_recorded_selector_or_raw(context, recorded_steps, selector, recorded_index)
            if not selector:
                selector = _element_selector_for_step(context, "select", step)
            option_match = re.search(r"['\"]([^'\"]+)['\"]", step)
            if selector and option_match:
                if recorded_index is not None:
                    used_recorded.add(recorded_index)
                lines.append(
                    f"  await {page_var}.locator('{_ts_string(selector)}').selectOption({{ label: '{_ts_string(option_match.group(1))}' }});"
                )
            continue
        if lower.startswith(("check", "uncheck")):
            selector = _selector_from_step(step)
            if selector:
                method = "uncheck" if lower.startswith("uncheck") else "check"
                lines.append(f"  await {page_var}.locator('{_ts_string(selector)}').{method}();")
            continue
        if lower.startswith(("upload", "attach")):
            selector = _selector_from_step(step)
            recorded_index = None
            if not selector:
                raw_selector = _bare_selector_from_step(step)
                if raw_selector:
                    selector, recorded_index = _recorded_selector_matching_raw(recorded_steps, "fill", raw_selector, used_recorded)
                if not selector:
                    selector, recorded_index = _recorded_selector_for_action(recorded_steps, "fill", used_recorded, step)
                if not selector:
                    selector = _element_selector_for_step(context, "fill", step)
            selector = _stable_recorded_selector_or_raw(context, recorded_steps, selector, recorded_index)
            if not selector:
                selector = _element_selector_for_step(context, "fill", step)
            file_match = re.search(r"['\"]([^'\"]+)['\"]", step)
            if selector and file_match:
                if recorded_index is not None:
                    used_recorded.add(recorded_index)
                lines.append(
                    f"  await {page_var}.locator('{_ts_string(selector)}').setInputFiles('{_ts_string(file_match.group(1))}');"
                )
            continue
        if lower.startswith("assert") and "visible" in lower:
            selector = _selector_from_step(step)
            if selector:
                lines.append(f"  await expect({page_var}.locator('{_ts_string(selector)}')).toBeVisible();")

    if not any("expect(" in line for line in lines):
        lines.append(f"  await expect({page_var}.locator('body')).toBeVisible();")
    lines.extend(_network_evidence_lines(page_var))
    return lines


def _deterministic_test_block(context: dict[str, Any], *, is_grouped: bool) -> str:
    page_var = "sharedPage" if is_grouped else "page"
    title = _ts_string(context.get("title") or context.get("test_id") or "Generated testcase")
    signature = "async ({}, testInfo)" if is_grouped else "async ({ page }, testInfo)"
    lines = [f'test("{title}", {signature} => {{', f"  const monitor = new NetworkMonitor({page_var});"]
    lines.extend(_deterministic_lines_from_steps(context, page_var=page_var))
    lines.append("});")
    return "\n".join(lines)


async def generate_script(context: dict[str, Any]) -> str | None:
    """Generate and save a single .spec.ts. Returns file path or None on failure."""
    test_id = context["test_id"]
    dom     = context.get("dom", {})

    steps_text       = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(context.get("steps", [])))
    acceptance_text  = "\n".join(
        f"  - {item}" for item in (context.get("acceptance_criteria") or [])
    )
    interactive_text = "\n".join(
        f"  - selector: {el.get('selector','')} type: {el.get('type','')} text: {el.get('text','')}"
        for el in dom.get("interactive_elements", [])[:30]
    )
    env_text = "\n".join(f"  {k} → env('{k}')" for k in context.get("env_placeholders", {}).keys())
    dom_html = (dom.get("html", "") or "")[:3000]
    recorded_text       = _format_recorded_steps(context.get("recorded_steps") or [])
    variant_text        = _format_variant_elements(context.get("recorded_variant_elements") or [])
    route_map_text      = _format_route_map(context.get("route_map") or {})

    prompt = _SCRIPT_PROMPT.format(
        few_shot_example=_resolve_few_shot(context, is_grouped=False),
        title               = context.get("title", ""),
        target_page         = context.get("target_page", "/"),
        steps               = steps_text or "  (no steps provided)",
        acceptance_criteria = acceptance_text or "  (none provided)",
        recorded_steps      = recorded_text,
        variant_elements    = variant_text,
        route_map           = route_map_text,
        interactive_elements= interactive_text or "  (none captured)",
        env_placeholders    = env_text,
        dom_html            = dom_html or "(no HTML snapshot available)",
    )

    test_block = ""
    last_errors: list[str] = []
    for attempt in range(_MAX_LLM_RETRIES):
        try:
            raw = _strip_fences(call_llm(prompt + _retry_feedback(last_errors), max_tokens=1500))
            # Deterministic patches run BEFORE validation so any fixes
            # (e.g. injected storageState reset) count toward acceptance.
            raw = _post_process_block(
                raw,
                context.get("title", ""),
                is_grouped=False,
                auth_mode=context.get("auth_mode"),
                auth_login_path=context.get("auth_login_path"),
            )
            raw, selector_fixes = _refine_bare_tag_locators(raw, context)
            if selector_fixes:
                logger.info(
                    "agent5: refined bare selectors test_id=%s fixes=%s",
                    test_id, selector_fixes,
                )
            if _validate_script(raw, context):
                test_block = raw
                break
            last_errors = _script_validation_errors(raw, context)
            logger.warning(
                "agent5 attempt %d: script validation failed for test_id=%s errors=%s",
                attempt + 1, test_id, last_errors,
            )
        except Exception as exc:
            logger.warning(
                "agent5 attempt %d/%d failed test_id=%s: %s",
                attempt + 1, _MAX_LLM_RETRIES, test_id, exc,
            )

    if not test_block:
        can_fallback, grounding_reason = _can_use_deterministic_fallback(context)
        if can_fallback:
            fallback = _post_process_block(
                _deterministic_test_block(context, is_grouped=False),
                context.get("title", ""),
                is_grouped=False,
                auth_mode=context.get("auth_mode"),
                auth_login_path=context.get("auth_login_path"),
            )
            fallback, selector_fixes = _refine_bare_tag_locators(fallback, context)
            if selector_fixes:
                logger.info(
                    "agent5: refined fallback bare selectors test_id=%s fixes=%s",
                    test_id, selector_fixes,
                )
            if _validate_script(fallback, context):
                logger.warning(
                    "agent5: using deterministic fallback script for test_id=%s grounding=%s after LLM validation errors=%s",
                    test_id, grounding_reason, last_errors,
                )
                test_block = fallback
        else:
            logger.error(
                "agent5: refusing deterministic fallback for test_id=%s - %s",
                test_id, grounding_reason,
            )

    if not test_block:
        logger.error("agent5 exhausted retries for test_id=%s - marking HUMAN_REVIEW", test_id)
        update_state(test_id, "HUMAN_REVIEW", run_id=context.get("run_id"))
        return None

    full_script = (
        _PREAMBLE
        + _test_id_attribute_directive(context.get("test_id_attribute"))
        + test_block
    )
    # Multi-tenant: scripts land under tests/generated/<project_id>/<run_id>/
    # when both ids are known. Falls back to the flat layout otherwise.
    project_id = context.get("project_id")
    run_id = context.get("run_id")
    script_path = mcp_server.write_script(
        test_id, full_script, project_id=project_id, run_id=run_id,
    )
    mcp_server.update_script_path(test_id, script_path)
    logger.info("agent5: script written test_id=%s path=%s", test_id, script_path)
    return script_path


# ── Grouped script generation ─────────────────────────────────────────────────

_GROUP_DESCRIBE_SHELL = '''\
test.describe.serial("{hls_title}", () => {{
  let sharedPage: Page;

  test.beforeAll(async ({{ browser }}) => {{
    const ctx = await browser.newContext({ctx_options});
    sharedPage = await ctx.newPage();
  }});

  test.afterAll(async () => {{
    await sharedPage.context().close();
  }});

{test_blocks}}});
'''


async def generate_grouped_script(
    contexts: list[dict[str, Any]],
    hls_id: str,
    hls_title: str,
    run_id: str | None = None,
) -> str | None:
    """Generate a test.describe.serial .spec.ts for all subtasks in one HLS.

    Each context → one LLM call → one test() block.
    Python assembles the blocks into the describe shell.
    Returns saved file path or None if any subtask fails generation.

    `run_id` is used (with the contexts' project_id) to scope the script under
    tests/generated/<project_id>/<run_id>/<hls_id>.spec.ts so concurrent runs
    of different projects can't trample each other's spec files.
    """
    test_blocks: list[str] = []
    sleep_secs = _rate_limit_sleep()

    for step_index, context in enumerate(contexts):
        test_id = context["test_id"]
        dom     = context.get("dom", {})

        if step_index > 0:
            logger.info(
                "agent5: rate-limit pause %.0fs before subtask %d/%d",
                sleep_secs, step_index + 1, len(contexts),
            )
            await asyncio.sleep(sleep_secs)

        steps_text       = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(context.get("steps", [])))
        acceptance_text  = "\n".join(
            f"  - {item}" for item in (context.get("acceptance_criteria") or [])
        )
        interactive_text = "\n".join(
            f"  - selector: {el.get('selector','')} type: {el.get('type','')} text: {el.get('text','')}"
            for el in dom.get("interactive_elements", [])[:25]
        )
        env_text = "\n".join(f"  {k} → env('{k}')" for k in context.get("env_placeholders", {}).keys())
        # Lifted from 800 → 3000 chars. The earlier cap left grouped scripts
        # without enough DOM to ground real selectors, which produced bare-tag
        # locators like locator('select'). Recorded selectors now also help.
        dom_html = (dom.get("html", "") or "")[:3000]
        recorded_text       = _format_recorded_steps(context.get("recorded_steps") or [])
        variant_text        = _format_variant_elements(context.get("recorded_variant_elements") or [])
        route_map_text      = _format_route_map(context.get("route_map") or {})

        prompt = _GROUPED_TEST_BLOCK_PROMPT.format(
            few_shot_example=_resolve_few_shot(context, is_grouped=True),
            step_index          = step_index,
            title               = context.get("title", ""),
            target_page         = context.get("target_page", "/"),
            steps               = steps_text or "  (no steps provided)",
            acceptance_criteria = acceptance_text or "  (none provided)",
            recorded_steps      = recorded_text,
            variant_elements    = variant_text,
            route_map           = route_map_text,
            interactive_elements= interactive_text or "  (none captured)",
            env_placeholders    = env_text,
            dom_html            = dom_html or "(no HTML snapshot available)",
        )

        block = ""
        last_errors: list[str] = []
        for attempt in range(_MAX_LLM_RETRIES):
            try:
                raw = _strip_fences(call_llm(prompt + _retry_feedback(last_errors), max_tokens=1200))
                # Patch before validation so injected fixes participate in acceptance.
                # is_grouped=True skips the storageState-clear injection because
                # the describe-shell's beforeAll governs auth state for the group.
                raw = _post_process_block(
                    raw,
                    context.get("title", ""),
                    is_grouped=True,
                    auth_mode=context.get("auth_mode"),
                    auth_login_path=context.get("auth_login_path"),
                )
                raw, selector_fixes = _refine_bare_tag_locators(raw, context)
                if selector_fixes:
                    logger.info(
                        "agent5: refined grouped bare selectors test_id=%s fixes=%s",
                        test_id, selector_fixes,
                    )
                if _validate_grouped_block(raw, context):
                    block = raw
                    break
                last_errors = _grouped_validation_errors(raw, context)
                logger.warning(
                    "agent5 grouped attempt %d: block validation failed test_id=%s errors=%s",
                    attempt + 1, test_id, last_errors,
                )
            except Exception as exc:
                logger.warning(
                    "agent5 grouped attempt %d/%d failed test_id=%s: %s",
                    attempt + 1, _MAX_LLM_RETRIES, test_id, exc,
                )

        if not block:
            can_fallback, grounding_reason = _can_use_deterministic_fallback(context)
            if can_fallback:
                fallback = _post_process_block(
                    _deterministic_test_block(context, is_grouped=True),
                    context.get("title", ""),
                    is_grouped=True,
                    auth_mode=context.get("auth_mode"),
                    auth_login_path=context.get("auth_login_path"),
                )
                fallback, selector_fixes = _refine_bare_tag_locators(fallback, context)
                if selector_fixes:
                    logger.info(
                        "agent5: refined grouped fallback bare selectors test_id=%s fixes=%s",
                        test_id, selector_fixes,
                    )
                if _validate_grouped_block(fallback, context):
                    logger.warning(
                        "agent5: using deterministic grouped fallback for test_id=%s grounding=%s after LLM validation errors=%s",
                        test_id, grounding_reason, last_errors,
                    )
                    block = fallback
            else:
                logger.error(
                    "agent5: refusing deterministic grouped fallback for test_id=%s - %s",
                    test_id, grounding_reason,
                )

        if not block:
            logger.error(
                "agent5: grouped block failed test_id=%s - aborting grouped script generation",
                test_id,
            )
            update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
            return None

        indented = "\n".join(f"  {line}" if line.strip() else "" for line in block.splitlines())
        test_blocks.append(indented)
        # script_path on each TestCase is set AFTER the actual write_script
        # call below — using the resolved (per-project/per-run) path. The
        # previous implementation pre-computed a flat-dir path here which
        # diverged from the actual write target after the multi-tenant move.

    if not test_blocks:
        logger.error("agent5: no test blocks generated for hls_id=%s", hls_id)
        return None

    context_auth_path = _context_auth_state_path(contexts)
    ctx_options = "{}"
    if context_auth_path and Path(context_auth_path).exists():
        auth_path = Path(context_auth_path).absolute().as_posix()
        ctx_options = f"{{ storageState: '{auth_path}' }}"

    # All subtasks in a group share the same project, so any context's
    # test_id_attribute is authoritative. Use the first non-None value.
    group_test_id_attr = next(
        (c.get("test_id_attribute") for c in contexts if c.get("test_id_attribute")),
        None,
    )
    full_script = (
        _PREAMBLE
        + _test_id_attribute_directive(group_test_id_attr)
        + _GROUP_DESCRIBE_SHELL.format(
            hls_title   = hls_title.replace('"', '\\"'),
            test_blocks = "\n\n".join(test_blocks) + "\n",
            ctx_options = ctx_options,
        )
    )

    project_id = next(
        (str(c.get("project_id")) for c in contexts if c.get("project_id")),
        None,
    )
    script_path = mcp_server.write_script(
        hls_id, full_script, project_id=project_id, run_id=run_id,
    )
    # Persist the actual resolved path on every TestCase in the group so the
    # worker / A7 can read script_path back correctly.
    for ctx in contexts:
        mcp_server.update_script_path(ctx["test_id"], script_path)
    logger.info(
        "agent5: grouped script written hls_id=%s subtasks=%d path=%s",
        hls_id, len(contexts), script_path,
    )
    return script_path
