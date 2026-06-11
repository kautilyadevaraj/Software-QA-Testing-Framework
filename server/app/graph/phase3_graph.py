"""Phase 3 Orchestrator — asyncio state machine.

Sequences: A3 Planner → A4 Context Builder → A5 Script Generator → enqueue.
After all tests are enqueued it waits for embedded or external workers,
then persists final run counters from phase3_execution_state.

Entry point: run_phase3(project_id, run_id)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.phase3 import Phase3ExecutionState, ReviewQueueItem, TestCase, TestResult, TestRun
from app.models.project import HighLevelScenario, ProjectJiraConfig
from app.models.scenario import RecordingFlow, ScenarioStep
from app.services import mcp_server, state_store
from app.services.artifact_paths import tc_document_path
from app.services.artifact_registry import register_artifact

logger = logging.getLogger(__name__)

_MAX_AGENT_RETRIES = 2
_TERMINAL_STATUSES = {"PASS", "APP_ERROR", "HUMAN_REVIEW", "BLOCKED"}


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


def _enqueue_phase3_job(job: dict[str, Any]) -> None:
    """Publish a run-scoped Phase 3 job or fail the run orchestration."""
    if not mcp_server.enqueue(job):
        job_type = job.get("job_type", "unknown")
        job_run_id = job.get("run_id", "unknown")
        raise RuntimeError(f"Failed to enqueue Phase 3 {job_type} job for run_id={job_run_id}")


def _write_generation_review_item(
    test_id: str,
    run_id: str,
    *,
    category: str,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    """Create a review item for A4/A5 preparation failures.

    These failures happen before Playwright execution, so A6/A7 never get a
    chance to create review_queue rows. The execution grid still shows
    HUMAN_REVIEW, and this keeps the human review queue consistent with it.
    """
    try:
        tid = uuid.UUID(str(test_id))
        rid = uuid.UUID(str(run_id))
    except (TypeError, ValueError):
        logger.warning(
            "Skipping generation review item for invalid ids: test_id=%s run_id=%s",
            test_id, run_id,
        )
        return

    payload = {
        "category": category,
        "reason": reason,
        **(evidence or {}),
    }
    try:
        with SessionLocal() as db:
            existing = db.execute(
                select(ReviewQueueItem.id).where(
                    ReviewQueueItem.test_id == tid,
                    ReviewQueueItem.run_id == rid,
                    ReviewQueueItem.status != "reviewed",
                ).limit(1)
            ).scalar_one_or_none()
            if existing:
                return
            db.add(ReviewQueueItem(
                test_id=tid,
                run_id=rid,
                review_type="TASK",
                evidence=payload,
                status="pending",
            ))
            db.commit()
    except Exception as exc:  # pragma: no cover - review visibility must not crash a run
        logger.warning(
            "Failed to create generation review item for test_id=%s run_id=%s: %s",
            test_id, run_id, exc,
        )


def recompute_run_counters(run_id: str) -> dict[str, int]:
    """Recompute TestRun.{passed,failed,skipped,human_review} from durable state.

    Source of truth (in order):
      1. Phase3ExecutionState (one row per test_id, kept in sync by the worker).
      2. TestResult fallback when ExecutionState is empty for this run.

    Returns the new counts. Used by both the end-of-run finalizer in
    `run_phase3` and the rerun BackgroundTask so review-resolved tests
    are reflected in the dashboard summary, not just the per-test grid.
    """
    snapshot = _execution_snapshot(run_id)
    if not snapshot:
        with SessionLocal() as db:
            result_rows = db.execute(
                select(TestResult).where(TestResult.run_id == uuid.UUID(run_id))
            ).scalars().all()
        snapshot = {str(r.test_id): {"status": r.status} for r in result_rows}

    with SessionLocal() as db:
        run = db.get(TestRun, uuid.UUID(run_id))
        total = run.total if run else len(snapshot)

    passed       = sum(1 for e in snapshot.values() if e.get("status") == "PASS")
    human_review = sum(1 for e in snapshot.values() if e.get("status") == "HUMAN_REVIEW")
    blocked      = sum(1 for e in snapshot.values() if e.get("status") == "BLOCKED")
    failed       = max(total - passed - human_review - blocked, 0)

    _update_run(run_id, passed=passed, failed=failed, human_review=human_review, skipped=blocked)
    return {"passed": passed, "failed": failed, "human_review": human_review, "skipped": blocked}


def _execution_snapshot(run_id: str) -> dict[str, dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.execute(
            select(Phase3ExecutionState).where(
                Phase3ExecutionState.run_id == uuid.UUID(run_id)
            )
        ).scalars().all()
    return {
        str(row.test_id): {
            "status": row.status,
            "retries": row.retries,
            "jira_ticket": row.jira_ticket,
            "trace_path": row.trace_path,
            "network_logs_count": row.network_logs_count,
        }
        for row in rows
    }


async def _wait_for_external_workers(run_id: str, total: int) -> None:
    """Wait until standalone workers have written terminal DB state for all tests."""
    deadline = time.monotonic() + settings.phase3_external_run_timeout_s
    while time.monotonic() < deadline:
        snapshot = _execution_snapshot(run_id)
        terminal_count = sum(
            1 for entry in snapshot.values()
            if entry.get("status") in _TERMINAL_STATUSES
        )
        if terminal_count >= total:
            return
        await asyncio.sleep(2)
    raise TimeoutError(
        f"Timed out waiting for external Phase 3 workers for run_id={run_id}"
    )


# ── Per-HLS pipeline (A3 + A4 + grouped A5) ─────────────────────────────────




# Minimum recorded-step count for an HLS to be plannable. A genuine flow
# (login, browse, submit) is at least navigate + 2 actions; below this the
# LLM has nothing to ground on and falls back to hallucinating selectors.
_MIN_RECORDED_STEPS_FOR_PLANNING = 3


def _recording_is_plannable(hls_id: str) -> tuple[bool, str]:
    """Gate A3 planning on recording quality.

    Returns (ok, reason). When ok is False, the caller should SKIP this HLS
    rather than feed A3 thin / missing recordings — those produce poor test
    plans that cascade into selector hallucination later in the pipeline.
    """
    from app.models.scenario import RecordingSession

    with SessionLocal() as db:
        session = db.execute(
            select(RecordingSession)
            .where(RecordingSession.scenario_id == uuid.UUID(hls_id))
            .order_by(RecordingSession.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if session is None:
            return False, "no recording session"
        if session.status != "completed":
            return False, f"recording status={session.status!r}"
        flow = db.execute(
            select(RecordingFlow)
            .where(RecordingFlow.recording_id == session.id)
            .order_by(RecordingFlow.flow_index.desc())
            .limit(1)
        ).scalar_one_or_none()
        if flow is None:
            return False, "no recording flow"
        if not flow.phase3_ready:
            metadata = flow.metadata_json or {}
            reasons = metadata.get("quality_failure_reasons") or []
            reason_text = ", ".join(str(reason) for reason in reasons) if reasons else "quality gate failed"
            return False, f"recording not phase3_ready: {reason_text}"
        step_count = db.execute(
            select(func.count(ScenarioStep.id))
            .where(ScenarioStep.scenario_id == uuid.UUID(hls_id))
        ).scalar_one()
    if step_count < _MIN_RECORDED_STEPS_FOR_PLANNING:
        return False, f"only {step_count} recorded steps (min {_MIN_RECORDED_STEPS_FOR_PLANNING})"
    return True, "ok"


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
        "", recorded_steps, tc_sequence_start,
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
        generate_grouped_script, contexts, hls_id, htc_title, None,
        label=f"A5-group[{hls_id[:8]}]",
    )
    if script_path is None:
        logger.error(
            "A5 grouped failed for hls_id=%s — marking all subtasks HUMAN_REVIEW", hls_id
        )
        for tid in test_ids:
            state_store.update_state(tid, "HUMAN_REVIEW")
        return False, next_sequence

    raise RuntimeError(
        "_process_hls_group is disabled for production; use /phase3/plan then /phase3/execute."
    )


async def _execute_hls_group(
    hls_id: str,
    htc_title: str,
    project_id: str,
    run_id: str,
    plan_run_id: str | None = None,
    auth_state_paths: dict[str, str] | None = None,
    *,
    hls_index: int | None = None,
    total_hls: int | None = None,
) -> tuple[bool, int]:
    """Execution-only: load existing test_ids from DB → A4 → A5 grouped → enqueue.

    Called by run_phase3() after test_cases have been approved.
    A3 is NOT called here — test_cases must already exist from the planning phase.

    Returns (success: bool, subtask_count: int).
    """
    from app.agents.agent4_context_builder import build_context
    from app.agents.agent5_script_generator import generate_grouped_script
    from app.services.phase3_jobs import build_hls_group_job
    from app.services import phase3_progress

    # UI progress: entering A4 (context build) for this HLS
    phase3_progress.set_stage(
        run_id, phase3_progress.STAGE_EXEC_A4,
        current_hls_index=hls_index, total_hls=total_hls,
        current_hls_title=htc_title,
    )

    # Load approved test_ids for this HLS in creation order
    with SessionLocal() as db:
        query = select(TestCase).where(
            TestCase.project_id == uuid.UUID(project_id),
            TestCase.hls_id == uuid.UUID(hls_id),
            TestCase.approval_status == "APPROVED",
        )
        if plan_run_id:
            query = query.where(TestCase.run_id == uuid.UUID(plan_run_id))
        tc_rows = db.execute(query.order_by(TestCase.created_at)).scalars().all()
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
            state_store.update_state(tid, "HUMAN_REVIEW", run_id=run_id)
            return False, len(test_ids)
        credential_id = ctx.get("credential_id")
        if credential_id and auth_state_paths and credential_id in auth_state_paths:
            ctx["auth_state_path"] = auth_state_paths[credential_id]
        contexts.append(ctx)

    ordered_test_ids = [ctx["test_id"] for ctx in contexts]
    state_store.init_hls_group(hls_id, ordered_test_ids)

    # UI progress: transitioning from A4 to A5 for this HLS
    phase3_progress.set_stage(
        run_id, phase3_progress.STAGE_EXEC_A5,
        current_hls_index=hls_index, total_hls=total_hls,
        current_hls_title=htc_title,
    )

    # A5 grouped: one .spec.ts for the whole HLS
    logger.info(
        "A5 generating grouped spec for hls_id=%s (%d subtasks)",
        hls_id[:8], len(test_ids),
    )
    script_path = await _retry_async(
        generate_grouped_script, contexts, hls_id, htc_title, run_id,
        label=f"A5-group[{hls_id[:8]}]",
    )
    if script_path is None:
        logger.error(
            "A5 grouped failed for hls_id=%s — marking all subtasks HUMAN_REVIEW", hls_id
        )
        for tid in test_ids:
            state_store.update_state(tid, "HUMAN_REVIEW", run_id=run_id)
        return False, len(test_ids)

    # All subtasks in a serial HLS group share one browser context, so they
    # MUST share one credential. Mixed credentials → silently running
    # everything as the first subtask's role (real risk on enterprise apps
    # with admin/user splits in one HLS). Refuse rather than mis-execute:
    # mark every subtask HUMAN_REVIEW so a human can split the group.
    distinct_creds = {
        str(c["credential_id"]) for c in contexts if c.get("credential_id")
    }
    if len(distinct_creds) > 1:
        logger.error(
            "credential mismatch in hls_id=%s: subtasks reference %d distinct "
            "credentials %s — refusing to run (split into per-credential HLS)",
            hls_id, len(distinct_creds), sorted(distinct_creds),
        )
        for tid in test_ids:
            state_store.update_state(tid, "HUMAN_REVIEW", run_id=run_id)
        return False, len(test_ids)

    credential_id = next(iter(distinct_creds), None)
    # Pair the storage_state_path with the chosen credential so they can't
    # disagree (a bug-shaped invariant — the path must belong to that cred).
    storage_state_path = next(
        (
            str(ctx.get("auth_state_path"))
            for ctx in contexts
            if ctx.get("auth_state_path") and str(ctx.get("credential_id") or "") == (credential_id or "")
        ),
        None,
    )
    job = build_hls_group_job(
        project_id=project_id,
        run_id=run_id,
        plan_run_id=plan_run_id,
        hls_id=hls_id,
        script_path=script_path,
        ordered_test_ids=ordered_test_ids,
        credential_id=credential_id,
        storage_state_path=storage_state_path,
    )
    _enqueue_phase3_job(job)
    logger.info("Enqueued grouped hls_id=%s (%d subtasks)", hls_id, len(test_ids))
    return True, len(test_ids)


# ── Planning-only phase (called by POST /phase3/plan) ────────────────────────────


async def _execute_hls_independent(
    hls_id: str,
    htc_title: str,
    project_id: str,
    run_id: str,
    plan_run_id: str | None = None,
    auth_state_paths: dict[str, str] | None = None,
    *,
    hls_index: int | None = None,
    total_hls: int | None = None,
) -> tuple[bool, int]:
    """Execute approved test cases independently.

    HLS remains a planning/progress grouping only. Runtime isolation is per
    TestCase: one A4 context, one A5 spec, one worker job.
    """
    from app.agents.agent4_context_builder import build_context
    from app.agents.agent5_script_generator import generate_script
    from app.services.phase3_jobs import build_single_test_job
    from app.services import phase3_progress
    from app.services.script_cache_service import (
        mark_generation_failed,
        materialize_cached_script,
        store_generated_script,
    )

    phase3_progress.set_stage(
        run_id, phase3_progress.STAGE_EXEC_A4,
        current_hls_index=hls_index, total_hls=total_hls,
        current_hls_title=htc_title,
    )

    with SessionLocal() as db:
        query = select(TestCase).where(
            TestCase.project_id == uuid.UUID(project_id),
            TestCase.hls_id == uuid.UUID(hls_id),
            TestCase.approval_status == "APPROVED",
        )
        if plan_run_id:
            query = query.where(TestCase.run_id == uuid.UUID(plan_run_id))
        tc_rows = db.execute(query.order_by(TestCase.created_at)).scalars().all()
        test_ids = [str(tc.test_id) for tc in tc_rows]

    if not test_ids:
        logger.warning(
            "_execute_hls_independent: no approved test_cases found for hls_id=%s",
            hls_id,
        )
        return False, 0

    enqueued = 0
    for i, tid in enumerate(test_ids, 1):
        state_store.init_test(tid, run_id=run_id)
        logger.info(
            "A4 building context %d/%d for independent test_id=%s hls_id=%s",
            i, len(test_ids), tid[:8], hls_id[:8],
        )
        ctx = await _retry_async(build_context, tid, project_id, label=f"A4[{tid[:8]}]")
        if ctx is None:
            logger.error(
                "A4 failed for test_id=%s in hls_id=%s - marking HUMAN_REVIEW",
                tid, hls_id,
            )
            state_store.update_state(tid, "HUMAN_REVIEW", run_id=run_id)
            _write_generation_review_item(
                tid,
                run_id,
                category="CONTEXT_BUILD_FAILED",
                reason="A4 could not build grounded automation context for this approved test case.",
                evidence={
                    "hls_id": hls_id,
                    "stage": "A4",
                    "action": "Review recorded context, selectors, and uploaded app documentation before rerun.",
                },
            )
            continue

        credential_id = ctx.get("credential_id")
        if credential_id and auth_state_paths and credential_id in auth_state_paths:
            ctx["auth_state_path"] = auth_state_paths[credential_id]
        ctx["run_id"] = run_id

        phase3_progress.set_stage(
            run_id, phase3_progress.STAGE_EXEC_A5,
            current_hls_index=hls_index, total_hls=total_hls,
            current_hls_title=htc_title,
        )

        script_path = materialize_cached_script(ctx)
        if script_path:
            logger.info(
                "A5 cache hit for independent test_id=%s hls_id=%s",
                tid[:8], hls_id[:8],
            )
        else:
            logger.info(
                "A5 generating independent spec for test_id=%s hls_id=%s",
                tid[:8], hls_id[:8],
            )
            script_path = await _retry_async(
                generate_script, ctx, label=f"A5-single[{tid[:8]}]",
            )
        if script_path is None:
            validation_errors = list(ctx.get("last_validation_errors") or [])
            validation_reason = "; ".join(str(error) for error in validation_errors[:6]) or "A5 returned no grounded Playwright script"
            logger.error(
                "A5 failed for test_id=%s in hls_id=%s - marking HUMAN_REVIEW errors=%s",
                tid, hls_id, validation_errors,
            )
            mark_generation_failed(ctx, validation_reason)
            state_store.update_state(tid, "HUMAN_REVIEW", run_id=run_id)
            _write_generation_review_item(
                tid,
                run_id,
                category="SCRIPT_GENERATION_FAILED",
                reason="A5 could not generate a grounded Playwright script for this approved test case.",
                evidence={
                    "hls_id": hls_id,
                    "stage": "A5",
                    "validation_reason": validation_reason,
                    "validation_errors": validation_errors,
                    "script_status": "HUMAN_REVIEW",
                    "action": "Review testcase steps/assertions and recorded selectors, then edit or rerun script generation.",
                },
            )
            continue
        store_generated_script(ctx, script_path)

        _enqueue_phase3_job(
            build_single_test_job(
                project_id=project_id,
                run_id=run_id,
                plan_run_id=plan_run_id,
                test_id=tid,
                script_path=script_path,
                storage_state_path=ctx.get("auth_state_path"),
                credential_id=str(ctx.get("credential_id")) if ctx.get("credential_id") else None,
            )
        )
        enqueued += 1
        logger.info("Enqueued independent test_id=%s hls_id=%s", tid[:8], hls_id[:8])

    logger.info(
        "Independent execution prepared hls_id=%s approved=%d enqueued=%d human_review=%d",
        hls_id[:8], len(test_ids), enqueued, len(test_ids) - enqueued,
    )
    return True, len(test_ids)


async def run_phase3_planning(
    project_id: str,
    run_id: str,
    project_name: str = "Project",
) -> list[str]:
    """Run A3 only for all HLS in a project. Write X-Ray CSV document. Update run status.

    Called by POST /phase3/plan.
    Does NOT run A4/A5 or spawn workers — that is handled by run_phase3_execution().

    Returns flat list of all created test_ids.
    """
    from app.agents.agent3_planner import plan, plan_xray_metadata_for_cases
    from app.agents.xray_csv_generator import fallback_xray_rows_from_a3, render_xray_csv
    from app.services import phase3_progress

    logger.info(
        "Phase3 planning started: project_id=%s run_id=%s", project_id, run_id
    )
    phase3_progress.start_run(run_id, stage=phase3_progress.STAGE_PLANNING_A3)

    with SessionLocal() as db:
        htcs = db.execute(
            select(HighLevelScenario).where(
                HighLevelScenario.project_id == uuid.UUID(project_id),
                HighLevelScenario.status == "completed",
            )
        ).scalars().all()
        htc_list = [(str(h.id), h.title, h.description) for h in htcs]
        jira_config = db.execute(
            select(ProjectJiraConfig).where(ProjectJiraConfig.project_id == uuid.UUID(project_id))
        ).scalars().first()
        csv_project_key = (
            jira_config.jira_project_key
            if jira_config
            else "".join(ch for ch in project_name.upper() if ch.isalnum())[:10] or "TBD"
        )

    if not htc_list:
        logger.warning("No completed HTCs for project_id=%s", project_id)
        _update_run(run_id, status="planned", total=0)
        return []

    all_test_ids: list[str] = []
    tc_sequence = 1  # global counter — never resets across HLS

    skipped: list[tuple[str, str]] = []
    for idx, (hls_id, title, description) in enumerate(htc_list, 1):
        logger.info("A3 planning HLS %d/%d: '%s'", idx, len(htc_list), title[:50])
        phase3_progress.set_stage(
            run_id, phase3_progress.STAGE_PLANNING_A3,
            current_hls_index=idx - 1, total_hls=len(htc_list),
            current_hls_title=title,
        )

        # Gate: skip HLS whose recordings are missing/incomplete. Better than
        # feeding A3 thin data and producing test cases the LLM had to invent
        # selectors for. Skipped HLS are logged + reported in the run summary.
        ok, reason = _recording_is_plannable(hls_id)
        if not ok:
            logger.warning(
                "Skipping HLS '%s' (id=%s) — %s. Re-run the recorder for this scenario.",
                title[:50], hls_id, reason,
            )
            skipped.append((title, reason))
            continue

        if idx > 1:
            logger.info("Rate-limit pause (%ss) before next HLS...", settings.llm_rate_limit_sleep)
            await asyncio.sleep(settings.llm_rate_limit_sleep)

        recorded_steps = await _fetch_recorded_steps(hls_id)
        pages = mcp_server.list_pages(project_id)
        test_ids = await _retry_async(
            plan,
            title, description, pages, project_id, hls_id,
            run_id, recorded_steps, tc_sequence,
            label=f"A3-plan[{title[:30]}]",
        )
        if test_ids:
            all_test_ids.extend(test_ids)
            tc_sequence += len(test_ids)

    # Generate X-Ray CSV document for download. UI review still uses JSON from DB.
    tc_rows   = mcp_server.get_test_cases_for_run(project_id=project_id, run_id=run_id)
    title_map = {row["test_id"]: row["title"] for row in tc_rows}
    for row in tc_rows:
        row["depends_on_titles"] = [
            title_map.get(dep_id, dep_id)
            for dep_id in row.get("depends_on", [])
        ]

    xray_export_rows = [
        row for row in tc_rows
        if str(row.get("approval_status") or "PENDING").upper() != "EXCLUDED"
    ]
    xray_metadata, xray_diag = plan_xray_metadata_for_cases(
        project_id=project_id,
        hls_items=[(title, description) for _, title, description in htc_list],
        tc_rows=xray_export_rows,
    )
    xray_rows = fallback_xray_rows_from_a3(
        xray_export_rows,
        project_key=csv_project_key,
        requirement="TBD",
        metadata_by_title=xray_metadata,
    )
    xray_diag["rows_generated"] = len(xray_rows)
    logger.info(
        "Phase3 X-Ray CSV source=%s chunks_found=%s rows_generated=%s automation_cases=%s fallback_reason=%s",
        xray_diag.get("source"),
        xray_diag.get("chunks_found"),
        xray_diag.get("rows_generated"),
        len(xray_export_rows),
        xray_diag.get("fallback_reason") or "",
    )
    csv_text = render_xray_csv(xray_rows)
    doc_path = tc_document_path(project_id, run_id)
    doc_path.write_text(csv_text, encoding="utf-8", newline="")
    register_artifact(
        project_id=project_id,
        run_id=run_id,
        artifact_type="XRAY_CSV",
        path=doc_path,
    )

    _update_run(run_id, status="planned", total=len(all_test_ids))
    phase3_progress.set_stage(run_id, phase3_progress.STAGE_EXEC_DONE)
    if skipped:
        logger.warning(
            "Phase3 planning skipped %d HLS due to incomplete recordings: %s",
            len(skipped), [f"{t!r} ({r})" for t, r in skipped[:5]],
        )
    logger.info(
        "Phase3 planning complete: run_id=%s - %d test cases created, %d HLS skipped, X-Ray CSV -> %s",
        run_id, len(all_test_ids), len(skipped), doc_path,
    )
    return all_test_ids


# ── Main entry point ──────────────────────────────────────────────────────────────────


async def run_phase3(project_id: str, run_id: str, plan_run_id: str | None = None) -> None:
    """Execution-only phase (called by POST /phase3/execute).

    Assumes test_cases already exist in the DB (created by run_phase3_planning).
    Runs A4 + A5 for each HLS, spawns Chromium workers, waits for drain.

    NOTE: The caller (router) must verify all test_cases have
    approval_status='APPROVED' before invoking this function.
    """
    from app.services import phase3_progress

    logger.info("Phase3 execution started: project_id=%s run_id=%s", project_id, run_id)
    run_start = time.monotonic()
    phase3_progress.start_run(run_id, stage=phase3_progress.STAGE_EXEC_PREFLIGHT)

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

    # Inline-login mode: credentials come from CredentialProfile rows at worker
    # runtime. Do not create/pass Playwright storageState files.
    auth_state_paths: dict[str, str] = {}

    # In local/demo embedded-worker mode, each execution run owns the worker
    # lifecycle. Purge stale RabbitMQ messages so an old run cannot starve the
    # new run by being endlessly requeued at the head of the shared queue.
    #
    # Do not purge in external-worker deployments: production workers receive
    # run-scoped JSON jobs, and purging would delete other projects' work.
    if settings.phase3_embedded_workers:
        mcp_server.purge_queue()

    # Resume check: only re-enqueue scripts from a *previous* run if the DB
    # execution-state table still has entries for this run. If empty (fresh
    # start), run the full A4→A5 pipeline.
    with SessionLocal() as db:
        live_state_count = db.execute(
            select(func.count()).select_from(Phase3ExecutionState)
            .where(Phase3ExecutionState.run_id == uuid.UUID(run_id))
        ).scalar_one() or 0
    resumable_jobs:   list[dict[str, Any]] = []

    if live_state_count > 0:
        with SessionLocal() as db:
            from app.services.phase3_jobs import build_hls_group_job, build_single_test_job

            already_run_ids = select(TestResult.test_id).where(
                TestResult.run_id == uuid.UUID(run_id)
            )
            resumable_query = select(TestCase).where(
                TestCase.project_id == uuid.UUID(project_id),
                TestCase.script_path.isnot(None),
                ~TestCase.test_id.in_(already_run_ids),
            )
            if plan_run_id:
                resumable_query = resumable_query.where(TestCase.run_id == uuid.UUID(plan_run_id))
            resumable_rows = db.execute(resumable_query).scalars().all()
            seen_scripts: set[str] = set()
            rows_by_script: dict[str, list[TestCase]] = {}
            for tc in resumable_rows:
                if tc.script_path:
                    rows_by_script.setdefault(tc.script_path, []).append(tc)
            for tc in resumable_rows:
                sp = tc.script_path
                if sp and Path(sp).exists() and sp not in seen_scripts:
                    seen_scripts.add(sp)
                    name        = Path(sp).name
                    script_stem = name.split(".")[0]
                    grouped_ids = state_store.get_hls_group(script_stem)
                    script_rows = rows_by_script.get(sp, [tc])
                    ordered_ids = grouped_ids or [str(row.test_id) for row in script_rows]
                    if grouped_ids:
                        resumable_jobs.append(
                            build_hls_group_job(
                                project_id=project_id,
                                run_id=run_id,
                                plan_run_id=plan_run_id,
                                hls_id=script_stem,
                                script_path=sp,
                                ordered_test_ids=ordered_ids,
                                credential_id=(
                                    str(script_rows[0].credential_id)
                                    if script_rows and script_rows[0].credential_id
                                    else None
                                ),
                            )
                        )
                    else:
                        resumable_jobs.append(
                            build_single_test_job(
                                project_id=project_id,
                                run_id=run_id,
                                plan_run_id=plan_run_id,
                                test_id=str(tc.test_id),
                                script_path=sp,
                                storage_state_path=(
                                    auth_state_paths.get(str(tc.credential_id))
                                    if tc.credential_id
                                    else None
                                ),
                                credential_id=str(tc.credential_id) if tc.credential_id else None,
                            )
                        )
                    for test_id in ordered_ids:
                        state_store.init_test(test_id, run_id=run_id)

    if resumable_jobs:
        logger.info(
            "Resuming: re-enqueueing %d job(s) (skipping A4/A5) for project_id=%s",
            len(resumable_jobs), project_id,
        )
        for job_id in resumable_jobs:
            _enqueue_phase3_job(job_id)
        total = len(resumable_jobs)
        all_hls_ids: list[str] = []

        _update_run(run_id, total=total)
        phase3_progress.set_stage(run_id, phase3_progress.STAGE_EXEC_QUEUING, total_hls=len(htc_list))
        logger.info(
            "Enqueued %d tests across %d HLS groups for run_id=%s",
            total, len(all_hls_ids), run_id,
        )

        if total == 0:
            _update_run(
                run_id, status="completed",
                duration_seconds=int(time.monotonic() - run_start),
            )
            phase3_progress.set_stage(run_id, phase3_progress.STAGE_EXEC_DONE)
            return

        phase3_progress.set_stage(run_id, phase3_progress.STAGE_EXEC_RUNNING)
        loop = asyncio.get_event_loop()

        if settings.phase3_embedded_workers:
            from app.services.phase3_worker import worker_loop
            executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=settings.chromium_workers,
                thread_name_prefix="chromium-worker",
            )
            worker_futures = [
                loop.run_in_executor(executor, worker_loop, run_id)
                for _ in range(settings.chromium_workers)
            ]
            await asyncio.gather(*worker_futures, return_exceptions=True)
            executor.shutdown(wait=False)
        else:
            logger.info(
                "Phase3 embedded workers disabled; external workers will process run_id=%s",
                run_id,
            )
            await _wait_for_external_workers(run_id, total)

    else:
        # Fresh execute: A4 → A5 per HLS using existing approved test_cases from DB.
        # A3 is NOT re-run here — test_cases already exist from the planning phase.
        #
        # STREAMING EXECUTION: workers start BEFORE the A4→A5 loop so each test
        # case begins executing the moment its script is enqueued — without
        # waiting for all scripts to finish generating first.
        #
        # Idle timeout is extended to cover worst-case generation time:
        #   llm_rate_limit_sleep × HLS_count (generation time)
        #   + 60s safety buffer for the last test to complete
        # This prevents a worker from exiting early while Groq is still producing
        # scripts for later HLS groups.
        #
        # The per-worker run_id gate in worker_loop means workers naturally drain
        # to empty and then exit when generation is done and all scripts are run.
        all_hls_ids = []
        total = 0

        loop = asyncio.get_event_loop()
        executor: concurrent.futures.ThreadPoolExecutor | None = None
        worker_futures: list = []
        worker_stop_event: threading.Event | None = None

        if settings.phase3_embedded_workers:
            from app.services.phase3_worker import worker_loop

            generation_budget_s = int(
                settings.llm_rate_limit_sleep * len(htc_list)
            )
            streaming_idle_timeout_s = max(300, generation_budget_s + 60)
            logger.info(
                "Streaming execution: starting %d worker(s) before A4/A5 loop "
                "(idle_timeout=%ds, HLS=%d, rate_limit_sleep=%ss)",
                settings.chromium_workers,
                streaming_idle_timeout_s,
                len(htc_list),
                settings.llm_rate_limit_sleep,
            )
            executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=settings.chromium_workers,
                thread_name_prefix="chromium-worker",
            )
            worker_stop_event = threading.Event()
            worker_futures = [
                loop.run_in_executor(
                    executor,
                    lambda: worker_loop(
                        run_id,
                        idle_timeout_s=streaming_idle_timeout_s,
                        stop_event=worker_stop_event,
                    ),
                )
                for _ in range(settings.chromium_workers)
            ]

        logger.info("Starting A4→A5 pipeline for %d HLS groups", len(htc_list))
        try:
            for idx, (hls_id, title, _description) in enumerate(htc_list, 1):
                logger.info("Processing HLS %d/%d: '%s'", idx, len(htc_list), title[:50])
                if idx > 1:
                    logger.info("Rate-limit pause (%ss) before next HLS group...", settings.llm_rate_limit_sleep)
                    await asyncio.sleep(settings.llm_rate_limit_sleep)
                success, subtask_count = await _execute_hls_independent(
                    hls_id, title, project_id, run_id, plan_run_id, auth_state_paths,
                    hls_index=idx - 1, total_hls=len(htc_list),
                )
                if success:
                    total += subtask_count
                    all_hls_ids.append(hls_id)
        except Exception:
            if worker_stop_event:
                worker_stop_event.set()
            if worker_futures:
                await asyncio.gather(*worker_futures, return_exceptions=True)
            if executor:
                executor.shutdown(wait=False)
            raise

        _update_run(run_id, total=total)
        phase3_progress.set_stage(run_id, phase3_progress.STAGE_EXEC_QUEUING, total_hls=len(htc_list))
        logger.info(
            "Enqueued %d tests across %d HLS groups for run_id=%s",
            total, len(all_hls_ids), run_id,
        )

        if total == 0:
            _update_run(
                run_id, status="completed",
                duration_seconds=int(time.monotonic() - run_start),
            )
            phase3_progress.set_stage(run_id, phase3_progress.STAGE_EXEC_DONE)
            if worker_stop_event:
                worker_stop_event.set()
            if worker_futures:
                await asyncio.gather(*worker_futures, return_exceptions=True)
            if executor:
                executor.shutdown(wait=False)
            return

        phase3_progress.set_stage(run_id, phase3_progress.STAGE_EXEC_RUNNING)
        if worker_stop_event:
            worker_stop_event.set()

        # Await pre-started workers (streaming path).
        # Workers were launched BEFORE the A4/A5 loop and have been processing
        # jobs concurrently with generation. Now we wait for them to drain.
        # External-worker deployments fall back to the DB-drain poller.
        if settings.phase3_embedded_workers and worker_futures:
            await asyncio.gather(*worker_futures, return_exceptions=True)
            if executor:
                executor.shutdown(wait=False)
        elif not settings.phase3_embedded_workers:
            logger.info(
                "Phase3 embedded workers disabled; external workers will process run_id=%s",
                run_id,
            )
            await _wait_for_external_workers(run_id, total)

    duration_s = int(time.monotonic() - run_start)

    # 6. Flush legacy local state, then use DB execution state as the source of truth.
    flush_result = mcp_server.flush_state_to_db(run_id)
    logger.info("Flushed %d results to DB for run_id=%s", flush_result.get("flushed", 0), run_id)
    snapshot = _execution_snapshot(run_id) or state_store.get_all()


    # Defensive fallback: if Phase3ExecutionState was never populated for this
    # run (we've observed this when the embedded-worker session lost the
    # active TestRun cache), fall back to TestResult — the worker's authoritative
    # write — so the run summary doesn't undercount real PASSes.
    if not snapshot:
        with SessionLocal() as db:
            result_rows = db.execute(
                select(TestResult).where(TestResult.run_id == uuid.UUID(run_id))
            ).scalars().all()
        if result_rows:
            snapshot = {str(r.test_id): {"status": r.status} for r in result_rows}
            logger.warning(
                "Phase3ExecutionState empty for run_id=%s — using TestResult fallback (%d rows)",
                run_id, len(result_rows),
            )

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
    phase3_progress.set_stage(run_id, phase3_progress.STAGE_EXEC_DONE)
    logger.info(
        "Phase3 run completed: run_id=%s passed=%d failed=%d blocked=%d human_review=%d duration=%ds",
        run_id, passed, failed, blocked, human_review, duration_s,
    )

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
        state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
