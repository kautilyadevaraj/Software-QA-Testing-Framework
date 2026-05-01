"""Agent A7 — Retry Agent.

On SCRIPT_ERROR, reads the broken script + fresh DOM snapshot and asks Groq
to repair it. Max 3 attempts per test_id. On exhaustion, marks HUMAN_REVIEW
and creates a review_queue entry (type=TASK).

Entry point: repair(test_id, error_log)
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from app.services import mcp_server, state_store
from app.utils.llm import call_llm

logger = logging.getLogger(__name__)

_MAX_RETRY_ATTEMPTS = 3
_MAX_LLM_RETRIES = 2

_REPAIR_PROMPT = """\
You are Agent A7, a Playwright script repair specialist.

The following Playwright TypeScript test script failed. Analyze the error and fix the script.

Rules:
- Fix ONLY the failing parts. Do not rewrite working sections.
- Keep smartFind(), NetworkMonitor, and navigateWithFallback() usage intact.
- Return ONLY the fixed test() block (no imports, no preamble).
- If the error is clearly an application bug (e.g. 404, 500), explain why in a comment and return the original test block unchanged.

Error Log:
{error_log}

Current Script:
{script}

Fresh DOM snapshot for {target_page}:
{dom_html}
"""


def _write_retry_history(test_id: str, attempt: int, error_log: str, fix: str | None) -> None:
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.models.phase3 import RetryHistory, TestResult

    with SessionLocal() as db:
        # Ensure TestResult exists — RetryHistory has a FK to test_results
        existing = db.execute(
            select(TestResult).where(TestResult.test_id == uuid.UUID(test_id))
        ).scalar_one_or_none()
        if existing is None:
            db.add(TestResult(
                test_id=uuid.UUID(test_id),
                status="RETRYING",
                retries=attempt,
            ))
            db.flush()

        db.add(RetryHistory(
            id=uuid.uuid4(),
            test_id=uuid.UUID(test_id),
            attempt_number=attempt,
            error_snapshot=error_log[:2000],
            llm_fix_applied=fix[:4000] if fix else None,
        ))
        db.commit()


def _write_review_queue(test_id: str, run_id: str | None, error_log: str) -> None:
    from app.db.session import SessionLocal
    from app.models.phase3 import ReviewQueueItem

    if not run_id:
        return

    with SessionLocal() as db:
        db.add(ReviewQueueItem(
            id=uuid.uuid4(),
            test_id=uuid.UUID(test_id),
            run_id=uuid.UUID(run_id),
            review_type="TASK",
            evidence={"error_log": error_log[:500], "retries_exhausted": _MAX_RETRY_ATTEMPTS},
            status="pending",
        ))
        db.commit()


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
        state_store.update_state(test_id, "HUMAN_REVIEW")
        _write_review_queue(test_id, run_id, error_log)
        return

    attempt = current_retries + 1
    logger.info("agent7: repair attempt %d/%d for test_id=%s", attempt, _MAX_RETRY_ATTEMPTS, test_id)

    # Read current script — use script_path from DB (grouped specs are {hls_id}.spec.ts)
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
    script_path_str = tc.script_path if tc and tc.script_path else None
    if script_path_str and Path(script_path_str).exists():
        actual_script_path = Path(script_path_str)
    else:
        # Legacy single-test path
        actual_script_path = Path(settings.generated_scripts_dir) / f"{test_id}.spec.ts"

    try:
        script = actual_script_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("agent7: script not found at %s for test_id=%s — marking HUMAN_REVIEW", actual_script_path, test_id)
        state_store.update_state(test_id, "HUMAN_REVIEW")
        _write_review_queue(test_id, run_id, error_log)
        return

    try:
        dom = mcp_server.get_snapshot(project_id, target_page)
        dom_html = (dom.get("html", "") or "")[:2000]
    except Exception:
        dom_html = "(snapshot unavailable)"

    prompt = _REPAIR_PROMPT.format(
        error_log=error_log[:1500],
        script=script[:3000],
        target_page=target_page,
        dom_html=dom_html,
    )

    from app.agents.agent5_script_generator import _strip_fences, _PAGE_FIXTURE_RE

    fixed_block: str | None = None
    for llm_attempt in range(_MAX_LLM_RETRIES):
        try:
            raw = _strip_fences(call_llm(prompt, max_tokens=4096))
            if raw.strip() and "test(" in raw and _PAGE_FIXTURE_RE.search(raw):
                fixed_block = raw
                break
            logger.warning("agent7 LLM attempt %d: invalid output", llm_attempt + 1)
        except Exception as exc:
            logger.warning("agent7 LLM attempt %d failed: %s", llm_attempt + 1, exc)

    _write_retry_history(test_id, attempt, error_log, fixed_block)

    if not fixed_block:
        logger.error("agent7: LLM repair failed for test_id=%s attempt=%d", test_id, attempt)
        state_store.increment_retries(test_id)
        mcp_server.enqueue(test_id)
        return

    # Write repaired script (keep preamble, replace test block)
    from app.agents.agent5_script_generator import _PREAMBLE
    full_script = _PREAMBLE + fixed_block
    mcp_server.write_script(test_id, full_script)

    new_retries = state_store.increment_retries(test_id)
    state_store.update_state(test_id, "SCRIPT_ERROR", retries=new_retries)

    logger.info("agent7: script repaired for test_id=%s — re-enqueuing (attempt %d)", test_id, attempt)
    mcp_server.enqueue(test_id)
