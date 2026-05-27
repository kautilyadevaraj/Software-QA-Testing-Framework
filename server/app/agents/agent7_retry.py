"""Agent A7 — Retry Agent.

On SCRIPT_ERROR, reads the broken script + fresh DOM snapshot and asks the LLM
to repair it. Max 3 attempts per test_id. On exhaustion, marks HUMAN_REVIEW
and creates a review_queue entry (type=TASK).

Two repair modes:
  - SINGLE: replaces the entire {test_id}.spec.ts file (one test() block).
  - GROUPED: locates the failing test() block inside a describe.serial spec by
    title, repairs ONLY that block, splices it back into the file preserving
    all other tests + the describe shell, then re-enqueues the whole HLS group.

Entry point: repair(test_id, run_id, error_log)
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from app.services import mcp_server, state_store
from app.utils.llm import call_llm

logger = logging.getLogger(__name__)

_MAX_RETRY_ATTEMPTS = 3
_MAX_LLM_RETRIES = 2

_REPAIR_PROMPT = """\
You are Agent A7, a Playwright TypeScript script repair specialist.

The test() block below failed. Analyse the error log + fresh DOM snapshot,
and return a SINGLE corrected test() block. The preamble (imports, smartFind,
NetworkMonitor, navigateWithFallback, env) is written elsewhere — do NOT
repeat it. Return only the test() block.

═══ CRITICAL RULES (same as A5 — the repair must not regress script quality) ═══

SCOPE — Fix ONLY the failing parts. Keep working sections byte-identical.
  Do NOT rename variables, restructure assertions, or add new expect()s
  unless directly needed to satisfy the fix.

NETWORK MONITOR — MUST remain the first line inside the test body:
  const monitor = new NetworkMonitor(page);
  Never remove it. Never move actions above it.
  Single-test signature must preserve testInfo:
    test("title", async ({{ page }}, testInfo) => {{ ... }})

ENV VARIABLES — Use the env() helper, NEVER process.env directly:
  CORRECT:  env('BASE_URL')
  WRONG:    process.env.BASE_URL   ← will bypass the missing-var guard

WAITING — NEVER use waitForTimeout(). Use instead:
  - await page.waitForURL('**/path**')    after navigation
  - await page.waitForLoadState('networkidle')   after form submits
  - await expect(locator).toBeVisible()   to wait for elements

SELECTORS — NEVER emit bare tag names ('select', 'div', 'span', 'input', 'button', 'a').
  Prefer RECORDED SELECTORS below verbatim. Otherwise:
  - page.getByRole('button', {{ name: 'Login' }})
  - page.getByPlaceholder('Email')
  - page.getByLabel('Username')
  - page.getByText('Add to cart')
  - page.locator('#specific-id') or page.locator('[data-testid="x"]')

INVALID SYNTAX — NEVER use these:
  - page.locator('role=button', {{ name: '...' }})   ← ignored / throws
  - Use:  page.getByRole('button', {{ name: '...' }})

URLS — NEVER hardcode http://… / https://… literals. Use env('BASE_URL') + path.

FINAL NETWORK EVIDENCE — must remain at the end:
  await testInfo.attach('network_logs', {{ body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' }});
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);

APPLICATION BUG — if the failure is clearly a server 4xx/5xx (visible in the
  error log or network log), return the ORIGINAL test block byte-for-byte
  unchanged with ONLY a single-line `// A7: <one-line reason>` inserted on
  line 2. Do NOT modify any other line. If you change the body, do NOT
  include a `// A7:` comment — the comment is reserved for "no script bug".

RETURN — ONLY the test() block (and optional test.use() line before it).
  No imports. No preamble. No describe wrapper. No markdown fences.

═══ FEW-SHOT EXAMPLE ═══

Error log: `Error: locator.click: Target closed — strict mode violation:
  getByRole('button').filter({{ hasText: 'Submit' }}) resolved to 2 elements`

Original (broken) block:
test("Submit feedback form", async ({{ page }}, testInfo) => {{
  const monitor = new NetworkMonitor(page);
  await page.goto(env('BASE_URL') + '/feedback');
  await page.getByRole('button').filter({{ hasText: 'Submit' }}).click();
  await testInfo.attach('network_logs', {{ body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' }});
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
}});

Repaired block (tightens the ambiguous selector using DOM evidence):
test("Submit feedback form", async ({{ page }}, testInfo) => {{
  const monitor = new NetworkMonitor(page);
  await page.goto(env('BASE_URL') + '/feedback');
  await page.getByRole('button', {{ name: 'Submit feedback', exact: true }}).click();
  await page.waitForLoadState('networkidle');
  await testInfo.attach('network_logs', {{ body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' }});
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
}});

═══ YOUR REPAIR TASK ═══

Error Log:
{error_log}

Current Script (full file — locate and fix just the broken test() block):
{script}

RECORDED SELECTORS (Phase-2 ground truth — prefer these verbatim):
{recorded_steps}

Recorded variant elements (real DOM captured during recording):
{variant_elements}

Known route map (path → link/button text that navigates there):
{route_map}

Fresh DOM snapshot for {target_page} (use selectors from this):
{dom_html}
"""


# ── Grouped repair prompt ─────────────────────────────────────────────────────

_GROUPED_REPAIR_PROMPT = """\
You are Agent A7 repairing ONE failing test() block inside a Playwright
test.describe.serial() suite. The suite uses a shared `sharedPage` across all
tests. The describe shell, beforeAll, afterAll, and OTHER passing tests are
NOT shown — repair only the block below and return a single corrected block.

═══ CRITICAL RULES (must hold for the repaired block) ═══

NETWORK MONITOR — first line inside the test body:
  const monitor = new NetworkMonitor(sharedPage);

PAGE VARIABLE — use `sharedPage` everywhere. NEVER use `page` or new pages.

SIGNATURE — grouped tests do not use page fixture, but do receive testInfo:
  test("title", async ({{}}, testInfo) => {{ ... }})

WAITING — NEVER waitForTimeout(). Use waitForURL / waitForLoadState / expect().

SELECTORS — NEVER bare tag names ('select', 'div', 'span', 'input', 'button',
'a'). Prefer the RECORDED SELECTORS below verbatim — they were captured during
a real Phase-2 recording run and are ground truth.

INVALID SYNTAX — NEVER use these:
  - sharedPage.locator('role=button', {{ name: '...' }})  ← ignores 2nd arg / throws
  - Use:  sharedPage.getByRole('button', {{ name: '...' }})

URLS — NEVER hardcode http://… / https://… literals. Use env('BASE_URL') + path.

selectOption — NEVER bare strings. Use {{ value: '…' }} or {{ label: '…' }}.

FINAL NETWORK EVIDENCE — must remain at the end:
  await testInfo.attach('network_logs', {{ body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' }});
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);

APPLICATION BUG — if the failure is clearly server-side (4xx/5xx in network
log), return the ORIGINAL block byte-for-byte unchanged with ONLY a single
`// A7: <one-line reason>` inserted on line 2. Do NOT modify any other line.
If you change the body, do NOT include a `// A7:` comment.

RETURN — ONLY the repaired test() block. No fences, no describe wrapper.

═══ REPAIR TASK ═══

Error Log:
{error_log}

Original (failing) block:
{block}

RECORDED SELECTORS (Phase-2 ground truth — prefer these verbatim):
{recorded_steps}

Recorded variant elements (real DOM captured during recording):
{variant_elements}

Known route map (path → link/button text that navigates there):
{route_map}

Fresh DOM snapshot for {target_page}:
{dom_html}
"""


# ── Grouped script parser (paren-balanced walker) ─────────────────────────────
#
# Why a hand-rolled walker instead of regex: a Playwright test() body contains
# arbitrary JS — nested braces, template literals with `${...}` interpolation,
# and inline strings — none of which are safely matched by a single regex.
# We walk character-by-character, tracking string and comment state, and count
# parens until the test() invocation is balanced. Output offsets are inclusive
# of the trailing semicolon so splice/replace is byte-precise.

_TEST_INVOCATION_RE = re.compile(
    r"""(?P<start>(?<!\.)\btest\s*\(\s*(?P<q>['"])(?P<title>(?:\\.|(?!(?P=q)).)*)(?P=q)\s*,)""",
)


def _find_balanced_invocation_end(script: str, open_paren_idx: int) -> int | None:
    """Return the index of the ';' that closes a test(...) call starting at
    `open_paren_idx` (the index of the '(' after `test`). Returns None if the
    file ends before a balanced match.

    Handles: '...' "..." `...` (with ${} nesting), // line comments,
    /* block comments */, and backslash escapes inside strings.
    """
    n = len(script)
    i = open_paren_idx
    if i >= n or script[i] != "(":
        return None

    depth = 0
    # template_stack tracks nested template-literal contexts so ${expr} braces
    # don't leak into the paren count when expr has its own parens or templates.
    template_stack: list[bool] = []  # True == currently inside template literal

    while i < n:
        c = script[i]

        # --- comments ---
        if c == "/" and i + 1 < n and script[i + 1] == "/":
            nl = script.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if c == "/" and i + 1 < n and script[i + 1] == "*":
            end = script.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue

        # --- string literals ---
        if c == "'" or c == '"':
            quote = c
            i += 1
            while i < n:
                if script[i] == "\\":
                    i += 2
                    continue
                if script[i] == quote:
                    i += 1
                    break
                i += 1
            continue

        # --- template literal (backtick) ---
        if c == "`":
            i += 1
            while i < n:
                if script[i] == "\\":
                    i += 2
                    continue
                if script[i] == "`":
                    i += 1
                    break
                if script[i] == "$" and i + 1 < n and script[i + 1] == "{":
                    template_stack.append(True)
                    depth += 1
                    i += 2
                    # Continue walking outer loop so we re-enter regular handling
                    # for the ${expr}.
                    break
                i += 1
            continue

        # --- close of ${...} inside template literal ---
        if c == "}" and template_stack and depth > 0:
            depth -= 1
            template_stack.pop()
            i += 1
            # After the closing }, we're back in the template literal — keep
            # consuming until the closing backtick.
            while i < n:
                if script[i] == "\\":
                    i += 2
                    continue
                if script[i] == "`":
                    i += 1
                    break
                if script[i] == "$" and i + 1 < n and script[i + 1] == "{":
                    template_stack.append(True)
                    depth += 1
                    i += 2
                    break
                i += 1
            continue

        # --- parentheses ---
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                # Look for the closing ';' (skip whitespace).
                j = i + 1
                while j < n and script[j].isspace():
                    j += 1
                if j < n and script[j] == ";":
                    return j
                return i  # no semicolon — return at the closing paren

        i += 1

    return None


def _find_grouped_test_blocks(script: str) -> list[dict[str, Any]]:
    """Locate every top-level test() invocation in a describe.serial spec.

    Returns a list of {'title', 'start', 'end', 'text'} dicts where
    [start, end] are inclusive offsets covering the entire `test(...);` call.

    Lone `test.describe.serial(...)`, `test.beforeAll(...)`, `test.afterAll(...)`,
    and `test.use(...)` are NOT matched because the regex's negative lookbehind
    `(?<!\\.)` rejects `.test(` and we explicitly only match `test(` (no dot).
    """
    blocks: list[dict[str, Any]] = []
    for m in _TEST_INVOCATION_RE.finditer(script):
        # Ensure this is `test(` and not `xtest(` / `mytest(`.
        if m.start() > 0 and (script[m.start() - 1].isalnum() or script[m.start() - 1] == "_"):
            continue
        # Find the actual '(' character.
        paren_idx = script.find("(", m.start())
        if paren_idx == -1:
            continue
        end = _find_balanced_invocation_end(script, paren_idx)
        if end is None:
            continue
        blocks.append({
            "title": m.group("title"),
            "start": m.start(),
            "end": end,
            "text": script[m.start(): end + 1],
        })
    return blocks


_A7_COMMENT_RE = re.compile(r"^\s*//\s*A7:", re.MULTILINE)
_LOCATOR_LITERAL_RE = re.compile(r"""\.locator\(\s*(['"])(?P<selector>[^'"]+)\1""")
_USER_FACING_LOCATOR_RE = re.compile(
    r"""\.(?:getByText|getByPlaceholder|getByLabel)\(\s*(['"])(?P<text>[^'"]+)\1"""
)
_ROLE_NAME_RE = re.compile(
    r"""getByRole\(\s*(['"])[^'"]+\1\s*,\s*\{[^}]*name\s*:\s*(['"])(?P<name>[^'"]+)\2""",
    re.DOTALL,
)


def _strip_a7_comments(text: str) -> str:
    """Remove every line that begins with `// A7:` (any indent)."""
    return "\n".join(
        ln for ln in text.splitlines() if not re.match(r"^\s*//\s*A7:", ln)
    )


def _a7_comment_discipline_ok(original: str, repaired: str) -> bool:
    """Enforce the "// A7:" usage rule from the prompt.

    Rule: a `// A7:` comment is reserved for "this is an application bug, not a
    script bug — leaving the test untouched". The model is observed to add
    that comment AND modify the body anyway, leaving a half-fix that masks
    real failures. We require that whenever the comment is present, the rest
    of the block (with all `// A7:` lines stripped) is byte-identical to the
    original.

    Returns True if compliant (or if no comment was added — body changes are
    free in that case), False if the model violated the rule.
    """
    if not _A7_COMMENT_RE.search(repaired):
        return True
    return _strip_a7_comments(original).strip() == _strip_a7_comments(repaired).strip()


def _splice_block(script: str, block: dict[str, Any], replacement: str) -> str:
    """Return a new script with the given block replaced by `replacement`.

    `replacement` should be the new test() invocation including trailing `;`.
    Indentation around the original block is preserved.
    """
    return script[: block["start"]] + replacement + script[block["end"] + 1:]


def _context_grounding_text(context: dict[str, Any], original: str) -> str:
    recorded = context.get("recorded_steps") or []
    variants = context.get("recorded_variant_elements") or []
    route_map = context.get("route_map") or {}
    dom_html = ((context.get("dom") or {}).get("html") or "")
    parts = [original, dom_html]
    for row in recorded:
        parts.extend(str(row.get(key) or "") for key in ("selector", "element_text", "element_type", "value"))
    for row in variants:
        parts.extend(str(row.get(key) or "") for key in ("selector", "text", "type"))
    parts.extend(str(k) for k in route_map.keys())
    parts.extend(str(v) for v in route_map.values())
    return "\n".join(parts).lower()


def _repair_grounding_violations(original: str, repaired: str, context: dict[str, Any]) -> list[str]:
    """Reject repairs that introduce selectors/text absent from script, DOM, or recording.

    This is intentionally conservative. A7 is a repair agent, not a planner; if
    it cannot ground a new locator in evidence, route to human review instead
    of inventing a plausible-looking selector.
    """
    grounding_text = _context_grounding_text(context, original)
    violations: list[str] = []
    for match in _LOCATOR_LITERAL_RE.finditer(repaired):
        selector = match.group("selector")
        if selector in {"body"}:
            continue
        if selector.lower() not in grounding_text:
            violations.append(f"ungrounded locator({selector!r})")
    for pattern, label in (
        (_USER_FACING_LOCATOR_RE, "user-facing locator"),
        (_ROLE_NAME_RE, "role name"),
    ):
        for match in pattern.finditer(repaired):
            text = (match.groupdict().get("text") or match.groupdict().get("name") or "").strip()
            if text and text.lower() not in grounding_text:
                violations.append(f"ungrounded {label} {text!r}")
    return violations


def _mark_human_review(
    test_id: str,
    run_id: str,
    error_log: str,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    logger.error("agent7: %s for test_id=%s - marking HUMAN_REVIEW", reason, test_id)
    state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
    payload = {"category": "A7_REPAIR_REJECTED", "reason": reason, "error_log": error_log[:1000]}
    if evidence:
        payload.update(evidence)
    _write_review_queue(test_id, run_id, payload)


def _write_retry_history(test_id: str, run_id: str, attempt: int, error_log: str, fix: str | None) -> None:
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.models.phase3 import RetryHistory, TestResult

    with SessionLocal() as db:
        # Ensure TestResult exists — RetryHistory has a FK to test_results
        tid = uuid.UUID(test_id)
        rid = uuid.UUID(run_id)
        existing = db.execute(
            select(TestResult).where(
                TestResult.test_id == tid,
                TestResult.run_id == rid,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = TestResult(
                test_id=tid,
                run_id=rid,
                status="RETRYING",
                retries=attempt,
            )
            db.add(existing)
        else:
            existing.status = "RETRYING"
            existing.retries = attempt
        db.flush()

        db.add(RetryHistory(
            id=uuid.uuid4(),
            test_id=tid,
            test_result_id=existing.id,
            attempt_number=attempt,
            error_snapshot=error_log[:2000],
            llm_fix_applied=fix[:4000] if fix else None,
        ))
        db.commit()


def _write_review_queue(test_id: str, run_id: str | None, evidence: str | dict[str, Any]) -> None:
    from app.db.session import SessionLocal
    from app.models.phase3 import ReviewQueueItem

    if not run_id:
        return

    if isinstance(evidence, str):
        payload = {"category": "A7_REPAIR_REJECTED", "error_log": evidence[:1000], "retries_exhausted": _MAX_RETRY_ATTEMPTS}
    else:
        payload = evidence

    with SessionLocal() as db:
        db.add(ReviewQueueItem(
            id=uuid.uuid4(),
            test_id=uuid.UUID(test_id),
            run_id=uuid.UUID(run_id),
            review_type="TASK",
            evidence=payload,
            status="pending",
        ))
        db.commit()


def _build_retry_job(test_id: str, run_id: str, script_path: str | None = None) -> dict[str, Any]:
    from pathlib import Path
    from sqlalchemy import select
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.models.phase3 import TestCase
    from app.services.artifact_paths import generated_base
    from app.services.phase3_jobs import build_single_test_job

    project_id: str | None = None
    resolved_script_path = script_path
    with SessionLocal() as db:
        tc = db.execute(
            select(TestCase).where(TestCase.test_id == uuid.UUID(test_id))
        ).scalar_one_or_none()
        if tc:
            project_id = str(tc.project_id)
            resolved_script_path = resolved_script_path or tc.script_path

    if not resolved_script_path:
        resolved_script_path = str(generated_base() / f"{test_id}.spec.ts")

    return build_single_test_job(
        project_id=project_id,
        run_id=run_id,
        test_id=test_id,
        script_path=resolved_script_path,
    )


def _lookup_grouped_context(test_id: str) -> dict[str, Any] | None:
    """If `test_id` belongs to a grouped HLS spec, return routing info for retry.

    Returns None for a single-test spec. The presence of a Phase3HlsGroup row
    naming this test_id is the authoritative signal for "this is grouped".
    """
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.models.phase3 import Phase3HlsGroup, TestCase

    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
        if tc is None or tc.hls_id is None:
            return None
        group = db.get(Phase3HlsGroup, tc.hls_id)
        if group is None:
            return None
        # Only treat as grouped if this test_id is actually in the ordered list
        # AND there is more than one test in the group (a 1-test group is
        # functionally a single-test spec with a describe wrapper).
        ordered = [str(t) for t in (group.ordered_test_ids or [])]
        if test_id not in ordered or len(ordered) < 2:
            return None
        # Resolve titles in execution order so the splicer can match by title.
        ordered_titles: list[str] = []
        for tid in ordered:
            sub = db.get(TestCase, uuid.UUID(tid))
            ordered_titles.append(sub.title if sub else "")
        return {
            "hls_id": str(tc.hls_id),
            "ordered_test_ids": ordered,
            "ordered_titles": ordered_titles,
            "title": tc.title,
            "project_id": str(tc.project_id),
            "run_id": str(group.run_id),
            "script_path": tc.script_path,
        }


def _build_grouped_retry_job(
    *,
    project_id: str,
    run_id: str,
    hls_id: str,
    script_path: str,
    ordered_test_ids: list[str],
    attempt: int,
) -> dict[str, Any]:
    """Build an HLS group retry job. Re-runs ALL tests in the serial group so
    previously-BLOCKED siblings get a fresh chance once the broken block is
    repaired. credential_id / storage_state_path are looked up from any test
    in the group (they're identical across the group)."""
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.models.phase3 import TestCase, AuthState
    from app.services.phase3_jobs import build_hls_group_job

    credential_id: str | None = None
    storage_state_path: str | None = None
    if ordered_test_ids:
        with SessionLocal() as db:
            sample = db.get(TestCase, uuid.UUID(ordered_test_ids[0]))
            if sample and sample.credential_id:
                credential_id = str(sample.credential_id)
                auth = db.execute(
                    select(AuthState).where(
                        AuthState.run_id == uuid.UUID(run_id),
                        AuthState.credential_id == sample.credential_id,
                        AuthState.status == "ready",
                    )
                ).scalar_one_or_none()
                if auth and auth.storage_state_path:
                    storage_state_path = auth.storage_state_path

    return build_hls_group_job(
        project_id=project_id,
        run_id=run_id,
        plan_run_id=None,
        hls_id=hls_id,
        script_path=script_path,
        ordered_test_ids=ordered_test_ids,
        credential_id=credential_id,
        storage_state_path=storage_state_path,
        attempt=attempt + 1,
    )


async def _repair_grouped(
    *,
    test_id: str,
    run_id: str,
    error_log: str,
    attempt: int,
    grouped: dict[str, Any],
) -> None:
    """Grouped repair path: locate the failing block by title, ask the LLM to
    repair it, splice it back into the same {hls_id}.spec.ts, then re-enqueue
    the whole HLS group job."""
    from pathlib import Path
    from app.agents.agent5_script_generator import (
        _strip_fences,
        _post_process_block,
        _validate_grouped_block,
    )

    script_path_str = grouped.get("script_path")
    if not script_path_str:
        logger.error("agent7-grouped: missing script_path for test_id=%s", test_id)
        state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
        _write_review_queue(test_id, run_id, error_log)
        return

    actual_path = Path(script_path_str)
    try:
        script = actual_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("agent7-grouped: script not found at %s", actual_path)
        state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
        _write_review_queue(test_id, run_id, error_log)
        return

    # Find the failing block by title.
    blocks = _find_grouped_test_blocks(script)
    title = grouped["title"]
    target_block = next((b for b in blocks if b["title"] == title), None)
    if target_block is None:
        logger.error(
            "agent7-grouped: failing block titled %r not found in %s (found %d blocks)",
            title, actual_path, len(blocks),
        )
        state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
        _write_review_queue(test_id, run_id, error_log)
        return

    # Pull the SAME enrichment A5 receives (recorded_steps, variants, route_map)
    # via A4. Repairing with a strictly weaker prompt than the original generator
    # is the core reason A7 used to hallucinate the same selectors that broke
    # the script in the first place.
    project_id = grouped["project_id"]
    from app.agents.agent4_context_builder import build_context
    from app.agents.agent5_script_generator import (
        _format_recorded_steps,
        _format_variant_elements,
        _format_route_map,
    )
    try:
        a4_context = await build_context(test_id, project_id)
    except Exception as exc:
        logger.warning("agent7-grouped: A4 enrichment unavailable for test_id=%s: %s", test_id, exc)
        a4_context = {}

    target_page = a4_context.get("target_page", "/")
    dom_html = ((a4_context.get("dom") or {}).get("html") or "")[:2000] or "(snapshot unavailable)"
    recorded_text = _format_recorded_steps(a4_context.get("recorded_steps") or [])
    variant_text = _format_variant_elements(a4_context.get("recorded_variant_elements") or [])
    route_map_text = _format_route_map(a4_context.get("route_map") or {})

    # Need tc for auth_mode passed to post_process below.
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.models.phase3 import TestCase
    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))

    prompt = _GROUPED_REPAIR_PROMPT.format(
        error_log=error_log[:1500],
        block=target_block["text"],
        target_page=target_page,
        recorded_steps=recorded_text,
        variant_elements=variant_text,
        route_map=route_map_text,
        dom_html=dom_html,
    )

    fixed_block: str | None = None
    last_validation_reason = ""
    last_grounding_errors: list[str] = []
    for llm_attempt in range(_MAX_LLM_RETRIES):
        try:
            raw = _strip_fences(call_llm(prompt, max_tokens=2000))
            raw = _post_process_block(
                raw, title, is_grouped=True,
                auth_mode=(tc.auth_mode if tc else None),
                    auth_login_path=a4_context.get("auth_login_path"),
            )
            if not _validate_grouped_block(raw):
                last_validation_reason = "A5 grouped validation rejected repaired block"
                logger.warning("agent7-grouped LLM attempt %d: invalid output", llm_attempt + 1)
                continue
            if not _a7_comment_discipline_ok(target_block["text"], raw):
                last_validation_reason = "A7 comment used while changing test body"
                logger.warning(
                    "agent7-grouped LLM attempt %d: '// A7:' comment present "
                    "but body diverged from original — rejecting half-fix",
                    llm_attempt + 1,
                )
                continue
            grounding_errors = _repair_grounding_violations(target_block["text"], raw, a4_context)
            if grounding_errors:
                last_grounding_errors = grounding_errors
                last_validation_reason = "Repair introduced ungrounded selectors/text"
                logger.warning(
                    "agent7-grouped LLM attempt %d: ungrounded repair rejected: %s",
                    llm_attempt + 1, grounding_errors,
                )
                continue
            fixed_block = raw
            break
        except Exception as exc:
            logger.warning("agent7-grouped LLM attempt %d failed: %s", llm_attempt + 1, exc)

    _write_retry_history(test_id, run_id, attempt, error_log, fixed_block)

    if not fixed_block:
        logger.error(
            "agent7-grouped: LLM repair failed test_id=%s attempt=%d — re-enqueuing unchanged group",
            test_id, attempt,
        )
        state_store.increment_retries(test_id)
        _mark_human_review(
            test_id,
            run_id,
            error_log,
            f"agent7 grouped repair failed validation on attempt {attempt}",
            {
                "repair_attempt": attempt,
                "validation_reason": last_validation_reason or "LLM did not return a valid grouped Playwright block",
                "grounding_errors": last_grounding_errors,
                "original_block": target_block["text"][:3000],
                "repaired_block": fixed_block[:3000] if fixed_block else "",
                "script_path": str(actual_path),
            },
        )
        return

    # Preserve indentation: the LLM returns a flush-left block but the original
    # was indented inside the describe shell. Re-indent to match the original
    # block's leading whitespace on its first line.
    leading_ws = ""
    line_start = script.rfind("\n", 0, target_block["start"]) + 1
    leading_ws = script[line_start: target_block["start"]]
    indented = "\n".join(
        (leading_ws + ln) if ln.strip() and i > 0 else (ln if i > 0 else ln)
        for i, ln in enumerate(fixed_block.splitlines())
    )
    new_script = _splice_block(script, target_block, indented)

    actual_path.write_text(new_script, encoding="utf-8")

    new_retries = state_store.increment_retries(test_id)
    state_store.update_state(test_id, "SCRIPT_ERROR", run_id=run_id, retries=new_retries)
    logger.info(
        "agent7-grouped: spliced repaired block for test_id=%s title=%r — re-enqueuing group hls_id=%s (attempt %d)",
        test_id, title, grouped["hls_id"], attempt,
    )
    mcp_server.enqueue(_build_grouped_retry_job(
        project_id=project_id,
        run_id=run_id,
        hls_id=grouped["hls_id"],
        script_path=str(actual_path),
        ordered_test_ids=grouped["ordered_test_ids"],
        attempt=attempt,
    ))


def _get_run_id_for_test(test_id: str) -> str | None:
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.models.phase3 import ReviewQueueItem

    with SessionLocal() as db:
        item = db.execute(
            select(ReviewQueueItem.run_id).where(
                ReviewQueueItem.test_id == uuid.UUID(test_id)
            )
        ).scalar_one_or_none()
    return str(item) if item else None


async def repair(test_id: str, run_id: str, error_log: str) -> None:
    """Attempt to repair a failing script. Marks HUMAN_REVIEW after max attempts."""
    current_retries = state_store.get_retry_count(test_id)

    if current_retries >= _MAX_RETRY_ATTEMPTS:
        logger.info("agent7: max retries reached for test_id=%s — marking HUMAN_REVIEW", test_id)
        state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
        _write_review_queue(test_id, run_id, error_log)
        return

    attempt = current_retries + 1
    logger.info("agent7: repair attempt %d/%d for test_id=%s", attempt, _MAX_RETRY_ATTEMPTS, test_id)

    # Grouped vs single dispatch — grouped repair is block-replace + group re-enqueue.
    grouped = _lookup_grouped_context(test_id)
    if grouped is not None:
        await _repair_grouped(
            test_id=test_id, run_id=run_id, error_log=error_log,
            attempt=attempt, grouped=grouped,
        )
        return

    # Read current script — single-test path.
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.models.phase3 import TestCase

    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
    target_page = tc.target_page if tc else "/"
    project_id = str(tc.project_id) if tc else ""

    # Determine script path: prefer DB-stored path, fall back to test_id-named file
    from pathlib import Path
    from app.core.config import settings
    from app.services.artifact_paths import generated_base
    script_path_str = tc.script_path if tc and tc.script_path else None
    if script_path_str and Path(script_path_str).exists():
        actual_script_path = Path(script_path_str)
    else:
        # Legacy single-test path
        actual_script_path = generated_base() / f"{test_id}.spec.ts"

    try:
        script = actual_script_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("agent7: script not found at %s for test_id=%s — marking HUMAN_REVIEW", actual_script_path, test_id)
        state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
        _write_review_queue(test_id, run_id, error_log)
        return

    # Pull A4 enrichment (recorded_steps + variants + route_map) so A7 has the
    # same grounding A5 used when generating the script. Without this, A7
    # tends to hallucinate the same selectors that broke the original.
    from app.agents.agent4_context_builder import build_context
    from app.agents.agent5_script_generator import (
        _format_recorded_steps,
        _format_variant_elements,
        _format_route_map,
    )
    try:
        a4_context = await build_context(test_id, project_id)
    except Exception as exc:
        logger.warning("agent7: A4 enrichment unavailable for test_id=%s: %s", test_id, exc)
        a4_context = {}
    target_page = a4_context.get("target_page", target_page)
    dom_html = ((a4_context.get("dom") or {}).get("html") or "")[:2000] or "(snapshot unavailable)"
    recorded_text = _format_recorded_steps(a4_context.get("recorded_steps") or [])
    variant_text = _format_variant_elements(a4_context.get("recorded_variant_elements") or [])
    route_map_text = _format_route_map(a4_context.get("route_map") or {})

    prompt = _REPAIR_PROMPT.format(
        error_log=error_log[:1500],
        script=script[:3000],
        target_page=target_page,
        recorded_steps=recorded_text,
        variant_elements=variant_text,
        route_map=route_map_text,
        dom_html=dom_html,
    )

    from app.agents.agent5_script_generator import (
        _strip_fences,
        _post_process_block,
        _validate_script,
        _PREAMBLE,
    )

    # Extract the original test() block from the script for comment-discipline
    # comparison. Falls back to the whole script if the parse misses.
    original_blocks = _find_grouped_test_blocks(script)
    original_block_text = original_blocks[0]["text"] if original_blocks else script

    fixed_block: str | None = None
    last_validation_reason = ""
    last_grounding_errors: list[str] = []
    for llm_attempt in range(_MAX_LLM_RETRIES):
        try:
            raw = _strip_fences(call_llm(prompt, max_tokens=4096))
            raw = _post_process_block(
                raw,
                tc.title if tc else "",
                is_grouped=False,
                auth_mode=(tc.auth_mode if tc else None),
                auth_login_path=a4_context.get("auth_login_path"),
            )
            if not _validate_script(raw):
                last_validation_reason = "A5 validation rejected repaired block"
                logger.warning("agent7 LLM attempt %d: A5 validation rejected output", llm_attempt + 1)
                continue
            if not _a7_comment_discipline_ok(original_block_text, raw):
                last_validation_reason = "A7 comment used while changing test body"
                logger.warning(
                    "agent7 LLM attempt %d: '// A7:' comment present but body "
                    "diverged from original — rejecting half-fix",
                    llm_attempt + 1,
                )
                continue
            grounding_errors = _repair_grounding_violations(original_block_text, raw, a4_context)
            if grounding_errors:
                last_grounding_errors = grounding_errors
                last_validation_reason = "Repair introduced ungrounded selectors/text"
                logger.warning(
                    "agent7 LLM attempt %d: ungrounded repair rejected: %s",
                    llm_attempt + 1, grounding_errors,
                )
                continue
            fixed_block = raw
            break
        except Exception as exc:
            logger.warning("agent7 LLM attempt %d failed: %s", llm_attempt + 1, exc)

    _write_retry_history(test_id, run_id, attempt, error_log, fixed_block)

    if not fixed_block:
        state_store.increment_retries(test_id)
        _mark_human_review(
            test_id,
            run_id,
            error_log,
            f"agent7 repair failed validation on attempt {attempt}",
            {
                "repair_attempt": attempt,
                "validation_reason": last_validation_reason or "LLM did not return a valid Playwright block",
                "grounding_errors": last_grounding_errors,
                "original_block": original_block_text[:3000],
                "repaired_block": fixed_block[:3000] if fixed_block else "",
                "script_path": str(actual_script_path),
            },
        )
        return

    # Write repaired script (keep preamble, replace test block).
    # Multi-tenant: same per-project/per-run layout A5 uses.
    full_script = _PREAMBLE + fixed_block
    retry_script_path = mcp_server.write_script(
        test_id, full_script, project_id=project_id, run_id=run_id,
    )
    mcp_server.update_script_path(test_id, retry_script_path)

    new_retries = state_store.increment_retries(test_id)
    state_store.update_state(test_id, "SCRIPT_ERROR", run_id=run_id, retries=new_retries)

    logger.info("agent7: script repaired for test_id=%s — re-enqueuing (attempt %d)", test_id, attempt)
    mcp_server.enqueue(_build_retry_job(test_id, run_id, retry_script_path))
