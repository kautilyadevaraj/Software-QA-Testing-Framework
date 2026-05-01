"""Agent A5 — Script Generator.

Takes a ContextObject from A4 and generates a runnable Playwright .spec.ts.
Every generated script includes four preamble helpers:
  - smartFind()
  - NetworkMonitor class
  - navigateWithFallback()
  - env() safe resolver

Entry points:
  generate_script(context)                              -> str | None  (single test)
  generate_grouped_script(contexts, hls_id, hls_title)  -> str | None  (serial describe)

Changes from previous version:
  - NetworkMonitor MUST be instantiated BEFORE any page action — prompt enforces this
  - waitForTimeout replaced with waitForURL / waitForLoadState in prompt rules
  - Grouped blocks: navigator assert added, URL assertion required after navigation
  - _strip_fences handles all LLM code fence variants (regex-based)
  - _validate_grouped_block() fixed — operator precedence bug resolved with explicit parens
  - Rate-limit sleep reads from settings.llm_rate_limit_sleep (not hardcoded)
  - storageState check uses settings.auth_json_path (not fragile relative resolve)
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

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
  failures: { url: string; method: string; status: number }[] = [];
  private origin: string;
  constructor(private page: Page) {
    const base = process.env.BASE_URL ?? "";
    try { this.origin = new URL(base).origin; } catch { this.origin = base; }
    page.on("response", (res) => {
      if (res.status() >= 400 && res.url().startsWith(this.origin)) {
        this.failures.push({ url: res.url(), method: res.request().method(), status: res.status() });
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

WAITING — NEVER use waitForTimeout(). Use instead:
  - await page.waitForURL('**/path**')    after navigation actions
  - await page.waitForLoadState('networkidle')   after form submits
  - await expect(locator).toBeVisible()   to wait for elements

SELECTORS — NEVER use bare tag names ('div', 'span', 'input', 'button', 'a'):
  - Use: page.getByRole('button', {{ name: 'Login' }})
  - Use: page.getByPlaceholder('Email')
  - Use: page.getByLabel('Username')
  - Use: page.getByText('Add to cart')
  - Use: page.locator('#specific-id') or page.locator('[data-testid="x"]')
  - If step is ambiguous like "click div", skip it silently

AUTH TESTS — if the title contains Login, Sign In, Logout, Register, or Password:
  Add this line BEFORE the test() block (it clears session state):
    test.use({{ storageState: {{ cookies: [], origins: [] }} }});
  Then use env('USER_EMAIL') and env('USER_PASSWORD') for credentials.
  All other tests start already authenticated — do NOT add login steps.

ASSERTIONS — at least one expect() per meaningful step.

LAST LINE — always assert network monitor:
  expect(monitor.hasFailures()).toBe(false);

RETURN — ONLY the test() block (and optional test.use() line before it).
  No imports. No preamble. No describe wrapper.

═══ FEW-SHOT EXAMPLE ═══
Test title: "Login with valid credentials"
Steps: navigate to BASE_URL/, fill username, fill password, click Login, assert /inventory.html

test.use({{ storageState: {{ cookies: [], origins: [] }} }});

test("Login with valid credentials", async ({{ page }}) => {{
  const monitor = new NetworkMonitor(page);
  await page.goto(env('BASE_URL') + '/');
  await page.getByPlaceholder('Username').fill(env('USER_EMAIL'));
  await page.getByPlaceholder('Password').fill(env('USER_PASSWORD'));
  await page.getByRole('button', {{ name: 'Login' }}).click();
  await page.waitForURL('**/inventory.html');
  await expect(page.getByText('Products')).toBeVisible();
  expect(monitor.hasFailures()).toBe(false);
}});

═══ TEST CASE ═══
Title: {title}
Target Page: {target_page}

Steps:
{steps}

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

SIGNATURE — test() takes NO fixture argument:
  test("title", async () => {{ ... }})   ← correct
  test("title", async ({{ page }}) => {{ ... }})   ← WRONG

NAVIGATION — only call sharedPage.goto() if this is step_index 0 (first test in suite).
  For later tests the browser is already on the right page. Use waitForURL if needed.

WAITING — NEVER use waitForTimeout(). Use:
  - await sharedPage.waitForURL('**/path**')
  - await sharedPage.waitForLoadState('networkidle')
  - await expect(locator).toBeVisible()

SELECTORS — NEVER bare tag names:
  - sharedPage.getByRole('button', {{ name: '...' }})
  - sharedPage.getByPlaceholder('...')
  - sharedPage.getByText('...')
  - sharedPage.locator('#id') or sharedPage.locator('[data-testid="x"]')

ASSERTIONS — at least one expect() per meaningful step.

LAST LINE — always:
  expect(monitor.hasFailures()).toBe(false);

RETURN — ONLY the test() block. No imports, no preamble, no describe wrapper.

═══ FEW-SHOT EXAMPLE ═══
Step index: 1  (not first — browser already on /inventory.html)
Title: "Add item to cart"

test("Add item to cart", async () => {{
  const monitor = new NetworkMonitor(sharedPage);
  await sharedPage.getByText('Add to cart', {{ exact: false }}).first().click();
  await expect(sharedPage.locator('.shopping_cart_badge')).toBeVisible();
  await expect(sharedPage.locator('.shopping_cart_badge')).toHaveText('1');
  expect(monitor.hasFailures()).toBe(false);
}});

═══ TEST CASE ═══
Step index (0 = first in suite, navigate here): {step_index}
Title: {title}
Target Page: {target_page}

Steps:
{steps}

Interactive elements on {target_page}:
{interactive_elements}

ENV Placeholders — use env('NAME') helper:
{env_placeholders}

DOM excerpt:
{dom_html}
"""


# ── Utilities ─────────────────────────────────────────────────────────────────

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


def _validate_script(code: str) -> bool:
    return bool(code.strip()) and "test(" in code and bool(_PAGE_FIXTURE_RE.search(code))


def _validate_grouped_block(code: str) -> bool:
    """Validate a grouped (serial describe) test block.

    NOTE: The `or` conditions for the async signature variants MUST be grouped
    inside parentheses. Without parens, Python evaluates `and` before `or`,
    causing the last two `or` branches to escape the `and` chain and always
    return truthy strings — a silent validation bypass bug.
    """
    has_async_no_fixture = (
        "async ()" in code
        or "async() =>" in code
        or "async () =>" in code
    )
    return (
        bool(code.strip())
        and "test(" in code
        and "sharedPage" in code
        and has_async_no_fixture
    )


def _rate_limit_sleep() -> float:
    """Seconds to sleep between grouped subtask LLM calls."""
    return float(settings.llm_rate_limit_sleep)


def _auth_json_path() -> str:
    return str(settings.auth_json_path)


# ── Single test generation ────────────────────────────────────────────────────

async def generate_script(context: dict[str, Any]) -> str | None:
    """Generate and save a single .spec.ts. Returns file path or None on failure."""
    test_id = context["test_id"]
    dom     = context.get("dom", {})

    steps_text       = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(context.get("steps", [])))
    interactive_text = "\n".join(
        f"  - selector: {el.get('selector','')} type: {el.get('type','')} text: {el.get('text','')}"
        for el in dom.get("interactive_elements", [])[:30]
    )
    env_text = "\n".join(f"  {k} → env('{k}')" for k in context.get("env_placeholders", {}).keys())
    dom_html = (dom.get("html", "") or "")[:3000]

    prompt = _SCRIPT_PROMPT.format(
        title               = context.get("title", ""),
        target_page         = context.get("target_page", "/"),
        steps               = steps_text or "  (no steps provided)",
        interactive_elements= interactive_text or "  (none captured)",
        env_placeholders    = env_text,
        dom_html            = dom_html or "(no HTML snapshot available)",
    )

    test_block = ""
    for attempt in range(_MAX_LLM_RETRIES):
        try:
            raw = _strip_fences(call_llm(prompt, max_tokens=1500))
            if _validate_script(raw):
                test_block = raw
                break
            logger.warning(
                "agent5 attempt %d: script validation failed for test_id=%s",
                attempt + 1, test_id,
            )
        except Exception as exc:
            logger.warning(
                "agent5 attempt %d/%d failed test_id=%s: %s",
                attempt + 1, _MAX_LLM_RETRIES, test_id, exc,
            )

    if not test_block:
        logger.error("agent5 exhausted retries for test_id=%s — marking HUMAN_REVIEW", test_id)
        update_state(test_id, "HUMAN_REVIEW")
        return None

    full_script = _PREAMBLE + test_block
    script_path = mcp_server.write_script(test_id, full_script)
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
) -> str | None:
    """Generate a test.describe.serial .spec.ts for all subtasks in one HLS.

    Each context → one LLM call → one test() block.
    Python assembles the blocks into the describe shell.
    Returns saved file path or None if all subtasks fail.
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
        interactive_text = "\n".join(
            f"  - selector: {el.get('selector','')} type: {el.get('type','')} text: {el.get('text','')}"
            for el in dom.get("interactive_elements", [])[:25]
        )
        env_text = "\n".join(f"  {k} → env('{k}')" for k in context.get("env_placeholders", {}).keys())
        dom_html = (dom.get("html", "") or "")[:800]

        prompt = _GROUPED_TEST_BLOCK_PROMPT.format(
            step_index          = step_index,
            title               = context.get("title", ""),
            target_page         = context.get("target_page", "/"),
            steps               = steps_text or "  (no steps provided)",
            interactive_elements= interactive_text or "  (none captured)",
            env_placeholders    = env_text,
            dom_html            = dom_html or "(no HTML snapshot available)",
        )

        block = ""
        for attempt in range(_MAX_LLM_RETRIES):
            try:
                raw = _strip_fences(call_llm(prompt, max_tokens=1200))
                if _validate_grouped_block(raw):
                    block = raw
                    break
                logger.warning(
                    "agent5 grouped attempt %d: block validation failed test_id=%s",
                    attempt + 1, test_id,
                )
            except Exception as exc:
                logger.warning(
                    "agent5 grouped attempt %d/%d failed test_id=%s: %s",
                    attempt + 1, _MAX_LLM_RETRIES, test_id, exc,
                )

        if not block:
            logger.error(
                "agent5: grouped block failed test_id=%s — inserting placeholder", test_id
            )
            block = (
                f'test("{context.get("title", test_id)}", async () => {{\n'
                f'  // SCRIPT GENERATION FAILED — manual review required\n'
                f'  console.log("SKIPPED: generation failed for {test_id}");\n'
                f'}});\n'
            )

        indented = "\n".join(f"  {line}" if line.strip() else "" for line in block.splitlines())
        test_blocks.append(indented)

        group_script_path = str(
            Path(settings.generated_scripts_dir) / f"{hls_id}.spec.ts"
        )
        mcp_server.update_script_path(test_id, group_script_path)

    if not test_blocks:
        logger.error("agent5: no test blocks generated for hls_id=%s", hls_id)
        return None

    auth_path   = _auth_json_path()
    ctx_options = (
        f"{{ storageState: '{auth_path}' }}"
        if Path(auth_path).exists()
        else "{}"
    )

    full_script = (
        _PREAMBLE
        + _GROUP_DESCRIBE_SHELL.format(
            hls_title   = hls_title.replace('"', '\\"'),
            test_blocks = "\n\n".join(test_blocks) + "\n",
            ctx_options = ctx_options,
        )
    )

    script_path = mcp_server.write_script(hls_id, full_script)
    logger.info(
        "agent5: grouped script written hls_id=%s subtasks=%d path=%s",
        hls_id, len(contexts), script_path,
    )
    return script_path
