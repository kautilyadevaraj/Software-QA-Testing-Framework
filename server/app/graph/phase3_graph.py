"""Phase 3 Orchestrator — asyncio state machine.

Sequences: A3 Planner → A4 Context Builder → A5 Script Generator → enqueue.
After all tests are enqueued it spawns Chromium worker threads and waits
for the RabbitMQ queue to drain before flushing state.json to PostgreSQL.

Entry point: run_phase3(project_id, run_id)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.phase3 import TestCase, TestResult, TestRun
from app.models.project import HighLevelScenario
from app.models.scenario import ScenarioStep
from app.services import mcp_server, state_store

logger = logging.getLogger(__name__)

_MAX_AGENT_RETRIES = 2


# ── Internal helpers ─────────────────────────────────────────────────────────


async def _retry_async(coro_fn, *args, retries: int = _MAX_AGENT_RETRIES, label: str = "") -> Any:
    """Call an async function up to *retries* times, returning None on total failure."""
    for attempt in range(retries + 1):
        try:
            return await coro_fn(*args)
        except Exception as exc:
            logger.warning("%s attempt %d/%d failed: %s", label, attempt + 1, retries + 1, exc)
            if attempt < retries:
                await asyncio.sleep(2 ** attempt)
    return None


def _update_run(run_id: str, **fields: Any) -> None:
    with SessionLocal() as db:
        run = db.get(TestRun, uuid.UUID(run_id))
        if run:
            for k, v in fields.items():
                setattr(run, k, v)
            db.commit()


# ── Per-HLS pipeline (A3 + A4 + grouped A5) ─────────────────────────────────




async def _fetch_recorded_steps(hls_id: str) -> list[dict]:
    """Return recorder-captured ScenarioSteps for an HLS, ordered by step_index."""
    with SessionLocal() as db:
        rows = db.execute(
            select(ScenarioStep)
            .where(ScenarioStep.scenario_id == uuid.UUID(hls_id))
            .order_by(ScenarioStep.step_index)
        ).scalars().all()
        return [
            {
                "action_type": r.action_type,
                "url": r.url,
                "selector": r.selector,
                "value": r.value,
            }
            for r in rows
        ]


async def _process_hls_group(
    hls_id: str,
    htc_title: str,
    htc_description: str,
    project_id: str,
    tc_sequence_start: int = 1,
) -> tuple[bool, int]:
    """Run A3 → A4 (all subtasks) → A5 grouped → enqueue hls:hls_id.

    Returns (success: bool, next_tc_sequence: int).
    """
    from app.agents.agent3_planner import plan
    from app.agents.agent4_context_builder import build_context
    from app.agents.agent5_script_generator import generate_grouped_script

    # A3: decompose HTC into test_cases, grounded with recorder steps
    recorded_steps = await _fetch_recorded_steps(hls_id)
    pages = mcp_server.list_pages(project_id)
    test_ids = await _retry_async(
        plan,
        htc_title, htc_description, pages, project_id, hls_id,
        recorded_steps, tc_sequence_start,
        label=f"A3[{htc_title[:30]}]",
    )
    if not test_ids:
        logger.warning("A3 returned no test_ids for HTC '%s'", htc_title)
        return False, tc_sequence_start

    next_sequence = tc_sequence_start + len(test_ids)

    # A4: build context for every subtask
    contexts: list[dict] = []
    for i, tid in enumerate(test_ids, 1):
        state_store.init_test(tid)
        logger.info(
            "A4 building context %d/%d for test_id=%s in hls_id=%s",
            i, len(test_ids), tid[:8], hls_id[:8],
        )
        ctx = await _retry_async(build_context, tid, project_id, label=f"A4[{tid[:8]}]")
        if ctx is None:
            logger.error(
                "A4 failed for test_id=%s in hls_id=%s — skipping group", tid, hls_id
            )
            state_store.update_state(tid, "HUMAN_REVIEW")
            return False, next_sequence
        logger.info(
            "A4 context ready for test_id=%s title=%r", tid[:8], ctx.get("title", "?")[:40]
        )
        contexts.append(ctx)

    # Store test_ids as an ordered list — positional index matches test() block
    # order in the spec. Avoids fragile title-string matching.
    ordered_test_ids = [ctx["test_id"] for ctx in contexts]
    state_store.init_hls_group(hls_id, ordered_test_ids)

    # A5 grouped: generate one .spec.ts for the whole HLS
    logger.info(
        "A5 generating grouped spec for hls_id=%s (%d subtasks) — LLM call in progress...",
        hls_id[:8], len(test_ids),
    )
    script_path = await _retry_async(
        generate_grouped_script, contexts, hls_id, htc_title,
        label=f"A5-group[{hls_id[:8]}]",
    )
    if script_path is None:
        logger.error(
            "A5 grouped failed for hls_id=%s — marking all subtasks HUMAN_REVIEW", hls_id
        )
        for tid in test_ids:
            state_store.update_state(tid, "HUMAN_REVIEW")
        return False, next_sequence

    mcp_server.enqueue(f"hls:{hls_id}")
    logger.info("Enqueued grouped hls_id=%s (%d subtasks)", hls_id, len(test_ids))
    return True, next_sequence


async def _execute_hls_group(
    hls_id: str,
    htc_title: str,
    project_id: str,
) -> tuple[bool, int]:
    """Execution-only: load existing test_ids from DB → A4 → A5 grouped → enqueue.

    Called by run_phase3() after test_cases have been approved.
    A3 is NOT called here — test_cases must already exist from the planning phase.

    Returns (success: bool, subtask_count: int).
    """
    from app.agents.agent4_context_builder import build_context
    from app.agents.agent5_script_generator import generate_grouped_script

    # Load approved test_ids for this HLS in creation order
    with SessionLocal() as db:
        tc_rows = db.execute(
            select(TestCase)
            .where(
                TestCase.project_id == uuid.UUID(project_id),
                TestCase.hls_id == uuid.UUID(hls_id),
            )
            .order_by(TestCase.created_at)
        ).scalars().all()
        test_ids = [str(tc.test_id) for tc in tc_rows]

    if not test_ids:
        logger.warning(
            "_execute_hls_group: no test_cases found for hls_id=%s — was planning run?",
            hls_id,
        )
        return False, 0

    # A4: build context for every subtask
    contexts: list[dict] = []
    for i, tid in enumerate(test_ids, 1):
        state_store.init_test(tid)
        logger.info(
            "A4 building context %d/%d for test_id=%s in hls_id=%s",
            i, len(test_ids), tid[:8], hls_id[:8],
        )
        ctx = await _retry_async(build_context, tid, project_id, label=f"A4[{tid[:8]}]")
        if ctx is None:
            logger.error(
                "A4 failed for test_id=%s in hls_id=%s — skipping group", tid, hls_id
            )
            state_store.update_state(tid, "HUMAN_REVIEW")
            return False, len(test_ids)
        contexts.append(ctx)

    ordered_test_ids = [ctx["test_id"] for ctx in contexts]
    state_store.init_hls_group(hls_id, ordered_test_ids)

    # A5 grouped: one .spec.ts for the whole HLS
    logger.info(
        "A5 generating grouped spec for hls_id=%s (%d subtasks)",
        hls_id[:8], len(test_ids),
    )
    script_path = await _retry_async(
        generate_grouped_script, contexts, hls_id, htc_title,
        label=f"A5-group[{hls_id[:8]}]",
    )
    if script_path is None:
        logger.error(
            "A5 grouped failed for hls_id=%s — marking all subtasks HUMAN_REVIEW", hls_id
        )
        for tid in test_ids:
            state_store.update_state(tid, "HUMAN_REVIEW")
        return False, len(test_ids)

    mcp_server.enqueue(f"hls:{hls_id}")
    logger.info("Enqueued grouped hls_id=%s (%d subtasks)", hls_id, len(test_ids))
    return True, len(test_ids)


# ── Planning-only phase (called by POST /phase3/plan) ────────────────────────────


async def run_phase3_planning(
    project_id: str,
    run_id: str,
    project_name: str = "Project",
) -> list[str]:
    """Run A3 only for all HLS in a project. Write TC document. Update run status.

    Called by POST /phase3/plan.
    Does NOT run A4/A5 or spawn workers — that is handled by run_phase3_execution().

    Returns flat list of all created test_ids.
    """
    from app.agents.agent3_planner import plan, generate_tc_document

    logger.info(
        "Phase3 planning started: project_id=%s run_id=%s", project_id, run_id
    )

    with SessionLocal() as db:
        htcs = db.execute(
            select(HighLevelScenario).where(
                HighLevelScenario.project_id == uuid.UUID(project_id),
                HighLevelScenario.status == "completed",
            )
        ).scalars().all()
        htc_list = [(str(h.id), h.title, h.description) for h in htcs]

    if not htc_list:
        logger.warning("No completed HTCs for project_id=%s", project_id)
        _update_run(run_id, status="planned", total=0)
        return []

    all_test_ids: list[str] = []
    tc_sequence = 1  # global counter — never resets across HLS

    for idx, (hls_id, title, description) in enumerate(htc_list, 1):
        logger.info("A3 planning HLS %d/%d: '%s'", idx, len(htc_list), title[:50])
        if idx > 1:
            logger.info("Rate-limit pause (8s) before next HLS...")
            await asyncio.sleep(8)

        recorded_steps = await _fetch_recorded_steps(hls_id)
        pages = mcp_server.list_pages(project_id)
        test_ids = await _retry_async(
            plan,
            title, description, pages, project_id, hls_id,
            recorded_steps, tc_sequence,
            label=f"A3-plan[{title[:30]}]",
        )
        if test_ids:
            all_test_ids.extend(test_ids)
            tc_sequence += len(test_ids)

    # Generate TC document for UI display
    tc_rows   = mcp_server.get_test_cases_for_run(project_id=project_id, run_id=run_id)
    title_map = {row["test_id"]: row["title"] for row in tc_rows}
    for row in tc_rows:
        row["depends_on_titles"] = [
            title_map.get(dep_id, dep_id)
            for dep_id in row.get("depends_on", [])
        ]

    markdown = generate_tc_document(tc_rows, project_name=project_name)
    doc_dir  = Path(settings.generated_scripts_dir)
    doc_dir.mkdir(parents=True, exist_ok=True)
    doc_path = doc_dir / f"tc_document_{run_id}.md"
    doc_path.write_text(markdown, encoding="utf-8")

    _update_run(run_id, status="planned", total=len(all_test_ids))
    logger.info(
        "Phase3 planning complete: run_id=%s — %d test cases created, TC doc → %s",
        run_id, len(all_test_ids), doc_path,
    )
    return all_test_ids


# ── Main entry point ──────────────────────────────────────────────────────────────────


async def run_phase3(project_id: str, run_id: str) -> None:
    """Execution-only phase (called by POST /phase3/execute).

    Assumes test_cases already exist in the DB (created by run_phase3_planning).
    Runs A4 + A5 for each HLS, spawns Chromium workers, waits for drain.

    NOTE: The caller (router) must verify all test_cases have
    approval_status='APPROVED' before invoking this function.
    """
    logger.info("Phase3 execution started: project_id=%s run_id=%s", project_id, run_id)
    run_start = time.monotonic()

    with SessionLocal() as db:
        htcs = db.execute(
            select(HighLevelScenario).where(
                HighLevelScenario.project_id == uuid.UUID(project_id),
                HighLevelScenario.status == "completed",
            )
        ).scalars().all()
        htc_list = [(str(h.id), h.title, h.description) for h in htcs]

    if not htc_list:
        logger.warning("No completed HTCs for project_id=%s", project_id)
        _update_run(run_id, status="completed", total=0, duration_seconds=0)
        return

    # Purge stale queue messages from previous runs
    mcp_server.purge_queue()

    # Resume check: only re-enqueue scripts from a *previous* run if state_store
    # still has entries. If cleared (fresh start), run the full A4→A5 pipeline.
    live_state_count  = len(state_store.get_all())
    resumable_jobs:   list[str] = []

    if live_state_count > 0:
        with SessionLocal() as db:
            already_run_ids = select(TestResult.test_id)
            resumable_rows = db.execute(
                select(TestCase).where(
                    TestCase.project_id == uuid.UUID(project_id),
                    TestCase.script_path.isnot(None),
                    ~TestCase.test_id.in_(already_run_ids),
                )
            ).scalars().all()
            seen_scripts: set[str] = set()
            for tc in resumable_rows:
                sp = tc.script_path
                if sp and Path(sp).exists() and sp not in seen_scripts:
                    seen_scripts.add(sp)
                    name        = Path(sp).name
                    script_stem = name.split(".")[0]
                    job_id = (
                        f"hls:{script_stem}"
                        if state_store.get_hls_group(script_stem)
                        else script_stem
                    )
                    resumable_jobs.append(job_id)
                    state_store.init_test(str(tc.test_id))

    if resumable_jobs:
        logger.info(
            "Resuming: re-enqueueing %d job(s) (skipping A4/A5) for project_id=%s",
            len(resumable_jobs), project_id,
        )
        for job_id in resumable_jobs:
            mcp_server.enqueue(job_id)
        total = len(resumable_jobs)
        all_hls_ids: list[str] = []
    else:
        # Fresh execute: A4 → A5 per HLS using existing approved test_cases from DB.
        # A3 is NOT re-run here — test_cases already exist from the planning phase.
        all_hls_ids = []
        total = 0
        logger.info("Starting A4→A5 pipeline for %d HLS groups", len(htc_list))
        for idx, (hls_id, title, _description) in enumerate(htc_list, 1):
            logger.info("Processing HLS %d/%d: '%s'", idx, len(htc_list), title[:50])
            if idx > 1:
                logger.info("Rate-limit pause (8s) before next HLS group...")
                await asyncio.sleep(8)
            success, subtask_count = await _execute_hls_group(
                hls_id, title, project_id
            )
            if success:
                total += subtask_count
                all_hls_ids.append(hls_id)

    _update_run(run_id, total=total)
    logger.info(
        "Enqueued %d tests across %d HLS groups for run_id=%s",
        total, len(all_hls_ids), run_id,
    )

    if total == 0:
        _update_run(
            run_id, status="completed",
            duration_seconds=int(time.monotonic() - run_start),
        )
        return

    loop = asyncio.get_event_loop()

    # 4a. Run auth setup once — creates tests/auth.json
    await loop.run_in_executor(None, _run_auth_setup)

    # 4b. Spawn Chromium workers
    from app.services.phase3_worker import worker_loop
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=settings.chromium_workers,
        thread_name_prefix="chromium-worker",
    )
    worker_futures = [
        loop.run_in_executor(executor, worker_loop, run_id)
        for _ in range(settings.chromium_workers)
    ]

    # 5. Wait for all worker threads to finish
    await asyncio.gather(*worker_futures, return_exceptions=True)
    executor.shutdown(wait=False)

    duration_s = int(time.monotonic() - run_start)

    # 6. Flush + update run counters (BLOCKED is counted in skipped)
    snapshot = state_store.get_all()
    flush_result = mcp_server.flush_state_to_db(run_id)
    logger.info("Flushed %d results to DB for run_id=%s", flush_result.get("flushed", 0), run_id)

    passed      = sum(1 for e in snapshot.values() if e.get("status") == "PASS")
    human_review = sum(1 for e in snapshot.values() if e.get("status") == "HUMAN_REVIEW")
    blocked     = sum(1 for e in snapshot.values() if e.get("status") == "BLOCKED")
    failed      = max(total - passed - human_review - blocked, 0)

    _update_run(
        run_id,
        passed=passed,
        failed=failed,
        human_review=human_review,
        skipped=blocked,
        status="completed",
        duration_seconds=duration_s,
    )
    logger.info(
        "Phase3 run completed: run_id=%s passed=%d failed=%d blocked=%d human_review=%d duration=%ds",
        run_id, passed, failed, blocked, human_review, duration_s,
    )


# ── Auth setup helper ───────────────────────────────────────────────────────


def _run_auth_setup() -> None:
    """Run auth.setup.ts once (blocking) to create tests/auth.json."""
    server_dir = Path(settings.generated_scripts_dir).parent.parent
    setup_file = server_dir / "tests" / "auth.setup.ts"

    if not setup_file.exists():
        logger.warning("auth.setup.ts not found at %s — skipping auth setup", setup_file)
        return

    env = os.environ.copy()
    env["PLAYWRIGHT_HEADED"] = "true"
    env["BASE_URL"] = settings.base_url
    env["USER_EMAIL"] = settings.user_email
    env["USER_PASSWORD"] = settings.user_password

    auth_config = server_dir / "playwright.auth.config.ts"

    try:
        npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
        proc = subprocess.run(
            [npx_cmd, "playwright", "test", "--config", str(auth_config), "--reporter=dot"],
            capture_output=True,
            text=True,
            timeout=60,
            shell=False,
            env=env,
            cwd=str(server_dir),
        )
        if proc.returncode == 0:
            logger.info("auth.setup: authenticated session saved to tests/auth.json")
        else:
            logger.warning(
                "auth.setup: login failed (rc=%d) — tests will run without storageState.\n%s",
                proc.returncode,
                proc.stderr[:500],
            )
    except Exception as exc:
        logger.warning("auth.setup: failed to run: %s", exc)


# ── Signal from Classifier (SCRIPT_ERROR path) ───────────────────────────────


async def on_script_error_signal(test_id: str, run_id: str, error_log: str) -> None:
    """
    Routes to the Retry Agent (A7). Runs in the worker thread's event loop.
    """
    from app.agents.agent7_retry import repair
    try:
        await repair(test_id, run_id, error_log)
    except Exception as exc:
        logger.exception("on_script_error_signal failed for test_id=%s: %s", test_id, exc)
        state_store.update_state(test_id, "HUMAN_REVIEW")
