"""Phase 3 FastAPI router.

All endpoints are mounted under /api/v1/projects/{project_id}/phase3/
and require JWT authentication.

Endpoints (Generate → Approve → Execute flow):
  POST   /plan                          — A3 planning only, returns TC document
  GET    /tc-document                   — download X-Ray CSV for a run
  GET    /tc-document/json              — TC list as JSON for UI accordion
  PATCH  /approve-all                   — bulk-approve all TCs for a run
  PATCH  /test-cases/{test_id}/approval — per-TC approval status update
  POST   /execute                       — A4+A5+workers (gated: all TCs must be APPROVED)

Legacy / unchanged:
  POST   /trigger                       — DEPRECATED alias for /plan+/execute (kept for compat)
  GET    /run-status                    — latest run counters + run_type
  GET    /execution-state               — live per-test status from state_store
  GET    /execution-report.csv          — final pass/fail execution report
  GET    /review-queue                  — list review items
  PATCH  /review-queue/{id}             — mark reviewed / store jira_ref
  GET    /review-queue/stream           — SSE stream of new review items
  GET    /script/{test_id}              — fetch generated .spec.ts content
  GET    /trace/{test_id}               — download Playwright trace .zip (FAIL/HUMAN_REVIEW)
  GET    /screenshot/{test_id}          — download assertion screenshot .png (PASS)
  POST   /review-queue/{id}/rerun       — save edited script and re-enqueue
  POST   /raise-jira                    — raise a Jira issue prefixed with [TC-XXX]
"""
import asyncio
import csv
import importlib
import io
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.core.config import get_settings
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.phase3 import AuthState, Phase3Artifact, Phase3ExecutionState, Phase3HlsGroup, ReviewQueueItem, TestCase, TestResult, TestRun
from app.models.project import CredentialProfile, HighLevelScenario, Project, ProjectJiraConfig
from app.models.scenario import RecordingSession
from app.models.user import User
from app.schemas.phase3 import (
    ApprovalPatch,
    ApproveAllRequest,
    ExecuteRequest,
    PlanRunResponse,
    Phase3ArtifactResponse,
    RaiseJiraRequest,
    RerunRequest,
    ReviewQueueItem as ReviewQueueItemSchema,
    ReviewQueuePatch,
    RunStatusResponse,
    ScriptResponse,
    TestCaseApprovalResponse,
    TriggerRunResponse,
    UpdateTestCaseRequest,
)
from app.services.artifact_paths import legacy_tc_document_path, tc_document_path
from app.services.artifact_registry import register_artifact
from app.utils.rate_limiter import limiter

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/projects/{project_id}/phase3", tags=["phase3"])


class _LazyModule:
    def __init__(self, module_name: str) -> None:
        self._module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, name: str):
        return getattr(self._load(), name)


mcp_server = _LazyModule("app.services.mcp_server")
state_store = _LazyModule("app.services.state_store")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _get_project_or_404(db: Session, user_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _get_active_run(db: Session, project_id: uuid.UUID) -> TestRun | None:
    """Return any in-progress run (planning or execution) for a project.

    Checks both 'running' (execute) and 'planning' (plan) statuses so that
    concurrent /plan or /execute calls on the same project are correctly blocked.
    """
    return db.execute(
        select(TestRun)
        .where(
            TestRun.project_id == project_id,
            TestRun.status.in_(["running", "planning"]),
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    ).scalars().first()


def _resolve_scenario_title(db: Session, hls_id: uuid.UUID | None) -> str | None:
    """Look up the parent HLS title for a test case.

    Used by PATCH /test-cases/{id}/approval and /content so the response carries
    the same scenario_title the GET endpoint produces. Without this, the UI's
    groupBy(scenario_title) collapses the bucket header to a generic placeholder
    after every approval/edit.
    """
    if not hls_id:
        return None
    hls = db.get(HighLevelScenario, hls_id)
    return hls.title if hls else None


def _latest_execute_run(db: Session, project_id: uuid.UUID, run_id: uuid.UUID | None) -> TestRun | None:
    if run_id is not None:
        run = db.get(TestRun, run_id)
        if run and run.project_id == project_id and run.run_type == "execute":
            return run
        return None
    return db.execute(
        select(TestRun)
        .where(TestRun.project_id == project_id, TestRun.run_type == "execute")
        .order_by(TestRun.created_at.desc())
        .limit(1)
    ).scalars().first()


def _plan_run_for_execution_report(db: Session, project_id: uuid.UUID, execute_run: TestRun) -> TestRun | None:
    """Find the planning run whose test cases should appear in the report."""
    plan_run = db.execute(
        select(TestRun)
        .where(
            TestRun.project_id == project_id,
            TestRun.run_type == "plan",
            TestRun.created_at <= execute_run.created_at,
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    ).scalars().first()
    if plan_run:
        return plan_run
    return db.execute(
        select(TestRun)
        .where(TestRun.project_id == project_id, TestRun.run_type == "plan")
        .order_by(TestRun.created_at.desc())
        .limit(1)
    ).scalars().first()


def _csv_multiline(value: list | tuple | None) -> str:
    if not value:
        return ""
    return "\n".join(str(item).strip() for item in value if str(item).strip())


def _regenerate_xray_csv_for_latest_plan(db: Session, project_id: uuid.UUID) -> None:
    """Best-effort CSV refresh after testcase review actions.

    EXCLUDED cases stay visible in the UI but are omitted from the default
    X-Ray CSV export and execution scope.
    """
    from app.agents.agent3_planner import plan_xray_metadata_for_cases
    from app.agents.xray_csv_generator import fallback_xray_rows_from_a3, render_xray_csv

    latest_plan_run = db.execute(
        select(TestRun)
        .where(TestRun.project_id == project_id, TestRun.run_type == "plan")
        .order_by(TestRun.created_at.desc())
        .limit(1)
    ).scalars().first()
    if not latest_plan_run:
        return

    tc_rows = mcp_server.get_test_cases_for_run(
        project_id=str(project_id), run_id=str(latest_plan_run.run_id)
    )
    tc_rows = [
        row for row in tc_rows
        if str(row.get("approval_status") or "PENDING").upper() != "EXCLUDED"
    ]
    project_obj = db.get(Project, project_id)
    project_name = project_obj.name if project_obj else "Project"
    jira_config = db.execute(
        select(ProjectJiraConfig).where(ProjectJiraConfig.project_id == project_id)
    ).scalars().first()
    csv_project_key = (
        jira_config.jira_project_key
        if jira_config
        else "".join(ch for ch in project_name.upper() if ch.isalnum())[:10] or "TBD"
    )
    hls_rows = db.execute(
        select(HighLevelScenario.title, HighLevelScenario.description).where(
            HighLevelScenario.project_id == project_id,
            HighLevelScenario.status == "completed",
        )
    ).all()
    xray_metadata, xray_diag = plan_xray_metadata_for_cases(
        project_id=str(project_id),
        hls_items=[(title, description) for title, description in hls_rows],
        tc_rows=tc_rows,
    )
    xray_rows = fallback_xray_rows_from_a3(
        tc_rows,
        project_key=csv_project_key,
        requirement="TBD",
        metadata_by_title=xray_metadata,
    )
    csv_text = render_xray_csv(xray_rows)
    doc_path = tc_document_path(str(project_id), str(latest_plan_run.run_id))
    doc_path.write_text(csv_text, encoding="utf-8", newline="")
    register_artifact(
        project_id=str(project_id),
        run_id=str(latest_plan_run.run_id),
        artifact_type="XRAY_CSV",
        path=doc_path,
    )
    logger.info(
        "regenerated X-Ray CSV at %s source=%s chunks_found=%s rows_generated=%s",
        doc_path,
        xray_diag.get("source"),
        xray_diag.get("chunks_found"),
        len(xray_rows),
    )


def _get_review_item_for_project_or_404(
    db: Session,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
) -> ReviewQueueItem:
    item = db.execute(
        select(ReviewQueueItem)
        .join(TestCase, ReviewQueueItem.test_id == TestCase.test_id)
        .where(
            ReviewQueueItem.id == item_id,
            TestCase.project_id == project_id,
        )
    ).scalars().first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review queue item not found")
    return item


def _backfill_review_queue_for_human_review(
    db: Session,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None,
) -> None:
    """Ensure every HUMAN_REVIEW execution-state row has a review_queue item."""
    state_query = (
        select(Phase3ExecutionState)
        .join(TestCase, Phase3ExecutionState.test_id == TestCase.test_id)
        .where(
            TestCase.project_id == project_id,
            Phase3ExecutionState.status == "HUMAN_REVIEW",
        )
    )
    if run_id:
        state_query = state_query.where(Phase3ExecutionState.run_id == run_id)

    states = db.execute(state_query).scalars().all()
    created = 0
    for state in states:
        existing = db.execute(
            select(ReviewQueueItem.id).where(
                ReviewQueueItem.test_id == state.test_id,
                ReviewQueueItem.run_id == state.run_id,
            ).limit(1)
        ).scalar_one_or_none()
        if existing:
            continue
        db.add(ReviewQueueItem(
            test_id=state.test_id,
            run_id=state.run_id,
            review_type="TASK",
            status="pending",
            evidence={
                "category": "AUTOMATION_REVIEW",
                "reason": "Execution state is HUMAN_REVIEW but no review item was recorded by the failing stage.",
                "action": "Review the testcase/script generation reason, edit if needed, and rerun.",
            },
        ))
        created += 1

    if created:
        db.commit()
        logger.info(
            "Backfilled %d review_queue item(s) for project_id=%s run_id=%s",
            created, project_id, run_id,
        )


# ── POST /trigger ────────────────────────────────────────────────────────────


@router.post("/trigger", response_model=TriggerRunResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.rate_limit_api)
def trigger_phase3(
    request: Request,
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TriggerRunResponse:
    """Deprecated one-shot Phase 3 entrypoint."""
    _get_project_or_404(db, current_user.id, project_id)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Phase 3 one-shot trigger is disabled for production. "
            "Use POST /phase3/plan, approve test cases, then POST /phase3/execute."
        ),
    )

    # Guard: no concurrent runs
    if _get_active_run(db, project_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A Phase 3 run is already in progress")

    # Guard: all Phase 2 scenarios must be completed
    incomplete = db.execute(
        select(HighLevelScenario).where(
            HighLevelScenario.project_id == project_id,
            HighLevelScenario.status != "completed",
        ).limit(1)
    ).scalars().first()
    if incomplete:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="All Phase 2 scenarios must be completed before running Phase 3",
        )

    run = TestRun(
        run_id=uuid.uuid4(),
        project_id=project_id,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    run_id_str = str(run.run_id)
    project_id_str = str(project_id)

    async def _start_phase3() -> None:
        """Async wrapper for the Phase 3 orchestration graph.

        FastAPI BackgroundTasks natively awaits async callables — no asyncio.run() needed.
        Uses SessionLocal() for the error-path DB write since the request-scoped
        `db` session is closed by the time this runs.
        """
        from app.db.session import SessionLocal as _SL
        from app.graph.phase3_graph import run_phase3
        from sqlalchemy import update
        try:
            await run_phase3(project_id_str, run_id_str)
        except Exception as exc:
            logger.exception(
                "Phase 3 run failed: project_id=%s run_id=%s: %s",
                project_id_str, run_id_str, exc,
            )
            with _SL() as fail_db:
                fail_db.execute(
                    update(TestRun)
                    .where(TestRun.run_id == uuid.UUID(run_id_str))
                    .values(status="failed")
                )
                fail_db.commit()

    background_tasks.add_task(_start_phase3)

    return TriggerRunResponse(run_id=run.run_id, status="running")


# ── POST /plan ────────────────────────────────────────────────────────────────────


@router.post("/plan", response_model=PlanRunResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.rate_limit_api)
def plan_phase3(
    request: Request,
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlanRunResponse:
    """Step 1 of 3: Run A3 for all completed HLS. Creates test cases in PENDING state.

    Returns a run_id that the client stores and passes to /execute when ready.
    The TC document (X-Ray CSV + JSON) is generated and written to disk;
    poll GET /tc-document/json to populate the approval accordion.

    Errors:
      409 — a planning run is already in progress
      422 — Phase 2 scenarios not completed yet
    """
    project = _get_project_or_404(db, current_user.id, project_id)

    # Guard: no concurrent planning runs
    active_plan = db.execute(
        select(TestRun)
        .where(
            TestRun.project_id == project_id,
            TestRun.run_type == "plan",
            TestRun.status.in_(["running", "planning"]),
        )
        .limit(1)
    ).scalars().first()
    if active_plan:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A planning run is already in progress",
        )

    recorded_hls = db.execute(
        select(RecordingSession)
        .join(HighLevelScenario, RecordingSession.scenario_id == HighLevelScenario.id)
        .where(
            RecordingSession.project_id == project_id,
            RecordingSession.status == "completed",
            HighLevelScenario.status == "completed",
        )
        .limit(1)
    ).scalars().first()
    if not recorded_hls:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Record at least one HLS scenario before planning Phase 3",
        )

    run = TestRun(
        run_id=uuid.uuid4(),
        project_id=project_id,
        run_type="plan",
        status="planning",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    run_id_str     = str(run.run_id)
    project_id_str = str(project_id)
    project_name   = project.name

    async def _run_planning() -> None:
        from app.db.session import SessionLocal as _SL
        from app.graph.phase3_graph import run_phase3_planning
        from sqlalchemy import update
        try:
            await run_phase3_planning(project_id_str, run_id_str, project_name)
        except Exception as exc:
            logger.exception(
                "Phase 3 planning failed: project_id=%s run_id=%s: %s",
                project_id_str, run_id_str, exc,
            )
            with _SL() as fail_db:
                fail_db.execute(
                    update(TestRun)
                    .where(TestRun.run_id == uuid.UUID(run_id_str))
                    .values(status="failed")
                )
                fail_db.commit()

    background_tasks.add_task(_run_planning)
    return PlanRunResponse(run_id=run.run_id, status="planning", total_test_cases=0)


# ── POST /execute ───────────────────────────────────────────────────────────────────


@router.post("/execute", response_model=TriggerRunResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.rate_limit_api)
def execute_phase3(
    request: Request,
    project_id: uuid.UUID,
    payload: ExecuteRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TriggerRunResponse:
    """Step 3 of 3: Start Playwright execution. Active TCs must be APPROVED.

    Guards:
      400 — unapproved active test cases exist (approval gate)
      404 — project not found
      409 — an execution run is already in progress
    """
    _get_project_or_404(db, current_user.id, project_id)

    # Guard: no concurrent execution runs
    if _get_active_run(db, project_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An execution run is already in progress",
        )

    # Guard: approval gate — EXCLUDED cases are intentionally skipped.
    planned_case = db.execute(
        select(TestCase)
        .where(
            TestCase.project_id == project_id,
            TestCase.run_id == payload.run_id,
        )
        .limit(1)
    ).scalars().first()
    if not planned_case:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No test cases found for the selected planning run.",
        )

    approved_case = db.execute(
        select(TestCase)
        .where(
            TestCase.project_id == project_id,
            TestCase.run_id == payload.run_id,
            TestCase.approval_status == "APPROVED",
        )
        .limit(1)
    ).scalars().first()
    if not approved_case:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No approved test cases available for execution. Approve or restore at least one test case.",
        )

    unapproved = db.execute(
        select(TestCase)
        .where(
            TestCase.project_id == project_id,
            TestCase.run_id == payload.run_id,
            TestCase.approval_status.notin_(["APPROVED", "EXCLUDED"]),
        )
        .limit(1)
    ).scalars().first()
    if unapproved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Test case '{unapproved.title}' (status: {unapproved.approval_status}) "
                "is not approved. Approve it or exclude it before execution."
            ),
        )

    # Unified preflight: env vars (BASE_URL/USER_EMAIL/USER_PASSWORD) + credentials.
    # Prevents the demo-failure case where all tests die on env('USER_EMAIL').
    from app.services.phase3_preflight import check_execution_preflight, format_issues
    preflight_issues = check_execution_preflight(db, project_id, payload.run_id)
    if preflight_issues:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=format_issues(preflight_issues),
        )

    run = TestRun(
        run_id=uuid.uuid4(),
        project_id=project_id,
        run_type="execute",
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    run_id_str     = str(run.run_id)
    project_id_str = str(project_id)

    async def _start_execution() -> None:
        from app.db.session import SessionLocal as _SL
        from app.graph.phase3_graph import run_phase3
        from sqlalchemy import update
        try:
            await run_phase3(project_id_str, run_id_str, str(payload.run_id))
        except Exception as exc:
            logger.exception(
                "Phase 3 execution failed: project_id=%s run_id=%s: %s",
                project_id_str, run_id_str, exc,
            )
            with _SL() as fail_db:
                fail_db.execute(
                    update(TestRun)
                    .where(TestRun.run_id == uuid.UUID(run_id_str))
                    .values(status="failed")
                )
                fail_db.commit()

    background_tasks.add_task(_start_execution)
    return TriggerRunResponse(run_id=run.run_id, status="running")


# ── PATCH /approve-all ───────────────────────────────────────────────────────────


@router.patch("/approve-all")
@limiter.limit(settings.rate_limit_api)
def approve_all_test_cases(
    request: Request,
    project_id: uuid.UUID,
    payload: ApproveAllRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Step 2a of 3: Bulk-approve active pending/needs-edit test cases for the run.

    EXCLUDED test cases are intentionally left untouched.
    Returns {approved_count: N}.
    """
    _get_project_or_404(db, current_user.id, project_id)

    tcs = db.execute(
        select(TestCase).where(
            TestCase.project_id == project_id,
            TestCase.run_id == payload.run_id,
            TestCase.approval_status != "APPROVED",
            TestCase.approval_status != "EXCLUDED",
        )
    ).scalars().all()

    for tc in tcs:
        tc.approval_status = "APPROVED"
    db.commit()

    logger.info(
        "approve-all: approved %d test cases for project_id=%s",
        len(tcs), project_id,
    )
    return {"approved_count": len(tcs)}


# ── PATCH /test-cases/{test_id}/approval ──────────────────────────────────────────


@router.patch("/test-cases/{test_id}/approval", response_model=TestCaseApprovalResponse)
@limiter.limit(settings.rate_limit_api)
def set_test_case_approval(
    request: Request,
    project_id: uuid.UUID,
    test_id: uuid.UUID,
    payload: ApprovalPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TestCaseApprovalResponse:
    """Step 2b of 3: Set approval status on a single test case.

    status must be 'APPROVED', 'NEEDS_EDIT', or 'EXCLUDED'.
    """
    _get_project_or_404(db, current_user.id, project_id)

    tc = db.execute(
        select(TestCase).where(
            TestCase.test_id == test_id,
            TestCase.project_id == project_id,
        )
    ).scalars().first()
    if not tc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test case not found")

    # Run-scope guard: when client supplies run_id, it must match this
    # test_case's owning run. Defends against stale-UI patches after re-plan.
    if payload.run_id is not None and tc.run_id != payload.run_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Test case belongs to run {tc.run_id}, not {payload.run_id}. "
                "Refresh the page — this run was likely re-planned."
            ),
        )

    tc.approval_status = payload.status
    db.commit()
    db.refresh(tc)
    if payload.status == "EXCLUDED":
        try:
            _regenerate_xray_csv_for_latest_plan(db, project_id)
        except Exception as regen_exc:
            logger.warning(
                "set_test_case_approval: failed to regenerate X-Ray CSV after exclude: %s",
                regen_exc,
            )

    # Resolve depends_on UUIDs to titles
    all_tcs = db.execute(
        select(TestCase).where(TestCase.project_id == project_id)
    ).scalars().all()
    title_map = {str(t.test_id): t.title for t in all_tcs}
    depends_on_titles = [title_map.get(str(d), str(d)) for d in (tc.depends_on or [])]

    # Preserve scenario_title so the UI's groupBy(scenario_title) doesn't collapse
    # the bucket header to a generic placeholder after this PATCH.
    scenario_title = _resolve_scenario_title(db, tc.hls_id)

    return TestCaseApprovalResponse(
        test_id=tc.test_id,
        tc_number=tc.tc_number,
        title=tc.title,
        steps=tc.steps,
        acceptance_criteria=tc.acceptance_criteria or [],
        target_page=tc.target_page,
        hls_id=tc.hls_id,
        scenario_title=scenario_title,
        approval_status=tc.approval_status,
        depends_on_titles=depends_on_titles,
    )


# ── PATCH /test-cases/{test_id}/content ──────────────────────────────────────────


@router.patch("/test-cases/{test_id}/content", response_model=TestCaseApprovalResponse)
@limiter.limit(settings.rate_limit_api)
def update_test_case_content(
    request: Request,
    project_id: uuid.UUID,
    test_id: uuid.UUID,
    payload: UpdateTestCaseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TestCaseApprovalResponse:
    """Inline human edit: update title, steps, and/or acceptance_criteria.

    Any content change automatically resets approval_status → NEEDS_EDIT so the
    reviewer must explicitly re-approve before execution is allowed.
    Only fields present in the payload are updated (partial update).
    """
    _get_project_or_404(db, current_user.id, project_id)

    tc = db.execute(
        select(TestCase).where(
            TestCase.test_id == test_id,
            TestCase.project_id == project_id,
        )
    ).scalars().first()
    if not tc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test case not found")

    changed = False
    if payload.title is not None:
        tc.title = payload.title.strip()
        changed = True
    if payload.steps is not None:
        tc.steps = [s.strip() for s in payload.steps if s.strip()]
        changed = True
    if payload.acceptance_criteria is not None:
        tc.acceptance_criteria = [c.strip() for c in payload.acceptance_criteria if c.strip()]
        changed = True

    if changed:
        # Any content edit requires re-approval before execution
        tc.approval_status = "NEEDS_EDIT"

    db.commit()
    db.refresh(tc)

    all_tcs = db.execute(
        select(TestCase).where(TestCase.project_id == project_id)
    ).scalars().all()
    title_map = {str(t.test_id): t.title for t in all_tcs}
    depends_on_titles = [title_map.get(str(d), str(d)) for d in (tc.depends_on or [])]

    logger.info(
        "update_test_case_content: test_id=%s changed=%s approval_status=%s",
        test_id, changed, tc.approval_status,
    )

    # Regenerate the downloadable X-Ray CSV so fallback A3 exports reflect
    # human edits. BRD/Qdrant generation remains the preferred source when
    # document chunks are available.
    if changed:
        try:
            from app.agents.agent3_planner import plan_xray_metadata_for_cases
            from app.agents.xray_csv_generator import fallback_xray_rows_from_a3, render_xray_csv
            latest_plan_run = db.execute(
                select(TestRun)
                .where(TestRun.project_id == project_id, TestRun.run_type == "plan")
                .order_by(TestRun.created_at.desc())
                .limit(1)
            ).scalars().first()
            if latest_plan_run:
                tc_rows = mcp_server.get_test_cases_for_run(
                    project_id=str(project_id), run_id=str(latest_plan_run.run_id)
                )
                tc_rows = [
                    row for row in tc_rows
                    if str(row.get("approval_status") or "PENDING").upper() != "EXCLUDED"
                ]
                tid_map = {r["test_id"]: r["title"] for r in tc_rows}
                for row in tc_rows:
                    row["depends_on_titles"] = [
                        tid_map.get(dep, dep) for dep in row.get("depends_on", [])
                    ]
                project_obj = db.get(Project, project_id)
                project_name = project_obj.name if project_obj else "Project"
                jira_config = db.execute(
                    select(ProjectJiraConfig).where(ProjectJiraConfig.project_id == project_id)
                ).scalars().first()
                csv_project_key = (
                    jira_config.jira_project_key
                    if jira_config
                    else "".join(ch for ch in project_name.upper() if ch.isalnum())[:10] or "TBD"
                )
                hls_rows = db.execute(
                    select(HighLevelScenario.title, HighLevelScenario.description).where(
                        HighLevelScenario.project_id == project_id,
                        HighLevelScenario.status == "completed",
                    )
                ).all()
                xray_metadata, xray_diag = plan_xray_metadata_for_cases(
                    project_id=str(project_id),
                    hls_items=[(title, description) for title, description in hls_rows],
                    tc_rows=tc_rows,
                )
                xray_rows = fallback_xray_rows_from_a3(
                    tc_rows,
                    project_key=csv_project_key,
                    requirement="TBD",
                    metadata_by_title=xray_metadata,
                )
                xray_diag["rows_generated"] = len(xray_rows)
                logger.info(
                    "update_test_case_content: X-Ray CSV source=%s chunks_found=%s rows_generated=%s automation_cases=%s fallback_reason=%s",
                    xray_diag.get("source"),
                    xray_diag.get("chunks_found"),
                    xray_diag.get("rows_generated"),
                    len(tc_rows),
                    xray_diag.get("fallback_reason") or "",
                )
                csv_text = render_xray_csv(xray_rows)
                doc_path = tc_document_path(str(project_id), str(latest_plan_run.run_id))
                doc_path.write_text(csv_text, encoding="utf-8", newline="")
                register_artifact(
                    project_id=str(project_id),
                    run_id=str(latest_plan_run.run_id),
                    artifact_type="XRAY_CSV",
                    path=doc_path,
                )
                logger.info(
                    "update_test_case_content: regenerated tc_document at %s", doc_path
                )
        except Exception as regen_exc:
            # Non-fatal — DB is already updated; just log the failure
            logger.warning(
                "update_test_case_content: failed to regenerate tc_document: %s", regen_exc
            )

    # Preserve scenario_title so the UI's groupBy(scenario_title) doesn't collapse
    # the bucket header to a generic placeholder after this PATCH.
    scenario_title = _resolve_scenario_title(db, tc.hls_id)

    return TestCaseApprovalResponse(
        test_id=tc.test_id,
        tc_number=tc.tc_number,
        title=tc.title,
        steps=tc.steps,
        acceptance_criteria=tc.acceptance_criteria or [],
        target_page=tc.target_page,
        hls_id=tc.hls_id,
        scenario_title=scenario_title,
        approval_status=tc.approval_status,
        depends_on_titles=depends_on_titles,
    )



# ── GET /tc-document ─────────────────────────────────────────────────────────────────


@router.get("/tc-document")
@limiter.limit(settings.rate_limit_api)
def download_tc_document(
    request: Request,
    project_id: uuid.UUID,
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Download the X-Ray CSV document for a planning run."""
    _get_project_or_404(db, current_user.id, project_id)

    doc_path = tc_document_path(str(project_id), run_id)
    if not doc_path.exists():
        doc_path = legacy_tc_document_path(run_id)
    if not doc_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="X-Ray CSV document not generated yet. Wait for /plan to complete.",
        )
    return FileResponse(
        str(doc_path),
        media_type="text/csv",
        filename=f"test_cases_{run_id}.csv",
    )


# ── GET /execution-report.csv ──────────────────────────────────────────────


@router.get("/execution-report.csv")
@limiter.limit(settings.rate_limit_api)
def download_execution_report_csv(
    request: Request,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Download a QA execution report CSV for the latest or requested execute run."""
    _get_project_or_404(db, current_user.id, project_id)

    execute_run = _latest_execute_run(db, project_id, run_id)
    if not execute_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No execution run found. Execute approved test cases first.",
        )

    plan_run = _plan_run_for_execution_report(db, project_id, execute_run)
    tc_query = select(TestCase).where(TestCase.project_id == project_id)
    if plan_run:
        tc_query = tc_query.where(TestCase.run_id == plan_run.run_id)
    tc_query = tc_query.where(TestCase.approval_status != "EXCLUDED")
    test_cases = db.execute(
        tc_query.order_by(TestCase.tc_number.asc().nullslast(), TestCase.created_at.asc())
    ).scalars().all()

    states = {
        row.test_id: row
        for row in db.execute(
            select(Phase3ExecutionState).where(Phase3ExecutionState.run_id == execute_run.run_id)
        ).scalars().all()
    }
    results = {
        row.test_id: row
        for row in db.execute(
            select(TestResult).where(TestResult.run_id == execute_run.run_id)
        ).scalars().all()
    }
    reviews = {}
    for row in db.execute(
        select(ReviewQueueItem)
        .where(ReviewQueueItem.run_id == execute_run.run_id)
        .order_by(ReviewQueueItem.created_at.desc())
    ).scalars().all():
        reviews.setdefault(row.test_id, row)

    hls_ids = {tc.hls_id for tc in test_cases if tc.hls_id}
    scenario_titles = {}
    if hls_ids:
        scenario_titles = {
            row.id: row.title
            for row in db.execute(
                select(HighLevelScenario.id, HighLevelScenario.title).where(
                    HighLevelScenario.id.in_(hls_ids)
                )
            ).all()
        }

    headers = [
        "Run_ID",
        "Plan_Run_ID",
        "TCID",
        "Scenario",
        "Title",
        "Approval_Status",
        "Execution_Status",
        "Result_Status",
        "Final_Status",
        "Retries",
        "Network_Logs_Count",
        "Jira_Ref",
        "Trace_Path",
        "Screenshot_Path",
        "Script_Path",
        "Steps",
        "Acceptance_Criteria",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()

    for tc in test_cases:
        state = states.get(tc.test_id)
        result = results.get(tc.test_id)
        review = reviews.get(tc.test_id)
        approval = str(tc.approval_status or "PENDING").upper()
        execution_status = state.status if state else ""
        result_status = result.status if result else ""
        final_status = execution_status or result_status or "NOT_RUN"
        writer.writerow({
            "Run_ID": str(execute_run.run_id),
            "Plan_Run_ID": str(plan_run.run_id) if plan_run else "",
            "TCID": tc.tc_number or str(tc.test_id),
            "Scenario": scenario_titles.get(tc.hls_id, "") if tc.hls_id else "",
            "Title": tc.title,
            "Approval_Status": approval,
            "Execution_Status": execution_status,
            "Result_Status": result_status,
            "Final_Status": final_status,
            "Retries": state.retries if state else (result.retries if result else 0),
            "Network_Logs_Count": state.network_logs_count if state else 0,
            "Jira_Ref": review.jira_ref if review else (state.jira_ticket if state else result.jira_ticket if result else ""),
            "Trace_Path": state.trace_path if state and state.trace_path else (result.trace_path if result else ""),
            "Screenshot_Path": state.screenshot_path if state and state.screenshot_path else (result.screenshot_path if result else ""),
            "Script_Path": tc.script_path or "",
            "Steps": _csv_multiline(tc.steps),
            "Acceptance_Criteria": _csv_multiline(tc.acceptance_criteria),
        })

    filename = f"execution_report_{execute_run.run_id}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── GET /tc-document/json ─────────────────────────────────────────────────────────────


@router.get("/tc-document/json")
@limiter.limit(settings.rate_limit_api)
def get_tc_document_json(
    request: Request,
    project_id: uuid.UUID,
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Return test cases as structured JSON for the UI approval accordion.

    Each item includes tc_number, title, steps, acceptance_criteria,
    hls_id, scenario_title, approval_status, and depends_on_titles.
    """
    _get_project_or_404(db, current_user.id, project_id)

    tc_rows = mcp_server.get_test_cases_for_run(
        project_id=str(project_id), run_id=run_id
    )
    if not tc_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No test cases found. Run POST /phase3/plan first.",
        )

    # Resolve depends_on UUIDs to title strings
    title_map = {row["test_id"]: row["title"] for row in tc_rows}
    for row in tc_rows:
        row["depends_on_titles"] = [
            title_map.get(dep, dep) for dep in row.get("depends_on", [])
        ]
    return tc_rows



# ── GET /run-status ──────────────────────────────────────────────────────────


@router.get("/run-status", response_model=RunStatusResponse)
@limiter.limit(settings.rate_limit_api)
def get_run_status(
    request: Request,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RunStatusResponse:
    _get_project_or_404(db, current_user.id, project_id)

    run = db.execute(
        select(TestRun)
        .where(
            TestRun.project_id == project_id,
            *((TestRun.run_id == run_id,) if run_id else ()),
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    ).scalars().first()

    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Phase 3 run found for this project")

    # Attach in-memory progress (populated by phase3_graph via phase3_progress).
    # Returns None once the run finishes or if nothing has been published yet.
    from app.services import phase3_progress
    progress = phase3_progress.get_progress(str(run.run_id))

    return RunStatusResponse(
        run_id=run.run_id,
        project_id=run.project_id,
        total=run.total,
        passed=run.passed,
        failed=run.failed,
        skipped=run.skipped,
        human_review=run.human_review,
        duration_seconds=run.duration_seconds,
        status=run.status,
        run_type=run.run_type if run.run_type else "execute",
        created_at=run.created_at,
        progress=progress,
    )


# ── GET /runs ────────────────────────────────────────────────────────────────


@router.get("/runs")
@limiter.limit(settings.rate_limit_api)
def list_phase3_runs(
    request: Request,
    project_id: uuid.UUID,
    run_type: Literal["plan", "execute", "all"] = Query(default="all"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """List recent Phase 3 runs for a project, newest first.

    Backs the UI history dropdown so a user can switch between prior
    plan runs without losing review/approval context. Filter by run_type to
    show only planning runs or only execution runs.
    """
    _get_project_or_404(db, current_user.id, project_id)

    query = select(TestRun).where(TestRun.project_id == project_id)
    if run_type != "all":
        query = query.where(TestRun.run_type == run_type)
    query = query.order_by(TestRun.created_at.desc()).limit(limit)

    runs = db.execute(query).scalars().all()
    return [
        {
            "run_id":           str(r.run_id),
            "run_type":         r.run_type or "execute",
            "status":           r.status,
            "total":            r.total,
            "passed":           r.passed,
            "failed":           r.failed,
            "human_review":     r.human_review,
            "duration_seconds": r.duration_seconds,
            "created_at":       r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]


# ── GET /execution-state ──────────────────────────────────────────────────────


@router.get("/execution-state")
@limiter.limit(settings.rate_limit_api)
def get_execution_state(
    request: Request,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Return live per-test execution state.

    Production path reads durable DB execution state written by workers.
    Falls back to legacy state_store JSON only when no DB state exists yet.
    """
    _get_project_or_404(db, current_user.id, project_id)

    from app.services.execution_state_service import list_execution_state

    db_state = list_execution_state(db, project_id, run_id=run_id)
    if db_state:
        return db_state

    live_state = state_store.get_all()
    if not live_state:
        return []

    test_ids_uuid = []
    for tid in live_state:
        try:
            test_ids_uuid.append(uuid.UUID(tid))
        except ValueError:
            pass

    if not test_ids_uuid:
        return []

    test_cases = db.execute(
        select(TestCase.test_id, TestCase.title, TestCase.target_page)
        .where(
            TestCase.project_id == project_id,
            TestCase.test_id.in_(test_ids_uuid),
        )
    ).all()

    title_by_id = {str(row.test_id): row.title for row in test_cases}
    page_by_id  = {str(row.test_id): row.target_page for row in test_cases}

    result = []
    for test_id, state in live_state.items():
        if test_id not in title_by_id:
            continue
        result.append({
            "test_id": test_id,
            "title": title_by_id.get(test_id, f"Test {test_id[:8]}"),
            "target_page": page_by_id.get(test_id, ""),
            "status": state.get("status", "PENDING"),
            "retries": state.get("retries", 0),
            "blocked_by": state.get("blocked_by"),
            "network_logs_count": len(state.get("network_logs", [])),
        })

    order = {"PENDING": 0, "PASS": 1, "FAIL": 2, "SCRIPT_ERROR": 3,
             "APP_ERROR": 4, "BLOCKED": 5, "HUMAN_REVIEW": 6}
    result.sort(key=lambda x: order.get(x["status"], 99))
    return result


# ── GET /review-queue ────────────────────────────────────────────────────────


@router.get("/review-queue", response_model=list[ReviewQueueItemSchema])
@limiter.limit(settings.rate_limit_api)
def list_review_queue(
    request: Request,
    project_id: uuid.UUID,
    item_status: str | None = None,
    run_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReviewQueueItemSchema]:
    _get_project_or_404(db, current_user.id, project_id)
    _backfill_review_queue_for_human_review(db, project_id, run_id)

    query = (
        select(ReviewQueueItem)
        .join(TestCase, ReviewQueueItem.test_id == TestCase.test_id)
        .where(TestCase.project_id == project_id)
        .order_by(ReviewQueueItem.created_at.desc())
    )
    if item_status:
        query = query.where(ReviewQueueItem.status == item_status)
    if run_id:
        query = query.where(ReviewQueueItem.run_id == run_id)

    items = db.execute(query).scalars().all()
    return [ReviewQueueItemSchema.model_validate(i) for i in items]


# ── PATCH /review-queue/{id} ─────────────────────────────────────────────────


@router.patch("/review-queue/{item_id}", response_model=ReviewQueueItemSchema)
@limiter.limit(settings.rate_limit_api)
def patch_review_queue_item(
    request: Request,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ReviewQueuePatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReviewQueueItemSchema:
    _get_project_or_404(db, current_user.id, project_id)

    item = _get_review_item_for_project_or_404(db, project_id, item_id)

    if payload.jira_ref is not None:
        item.jira_ref = payload.jira_ref
    if payload.status is not None:
        item.status = payload.status
    else:
        item.status = "reviewed"

    db.commit()
    db.refresh(item)
    return ReviewQueueItemSchema.model_validate(item)


# ── GET /review-queue/stream (SSE) ───────────────────────────────────────────


@router.get("/review-queue/stream")
async def stream_review_queue(
    request: Request,
    response: Response,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EventSourceResponse:
    """Server-Sent Events stream of new review queue items for this project."""
    _get_project_or_404(db, current_user.id, project_id)

    # Import SessionLocal for fresh sessions inside the long-lived generator.
    # The request-scoped `db` session goes stale after the HTTP response is sent,
    # so worker-committed review_queue rows would never appear.
    from app.db.session import SessionLocal as _SL

    seen_ids: set[str] = set()

    async def event_generator():
        heartbeat_counter = 0
        while True:
            await asyncio.sleep(1)
            heartbeat_counter += 1

            try:
                with _SL() as fresh_db:
                    query = (
                        select(ReviewQueueItem)
                        .join(TestCase, ReviewQueueItem.test_id == TestCase.test_id)
                        .where(TestCase.project_id == project_id)
                        .order_by(ReviewQueueItem.created_at.asc())
                    )
                    if run_id:
                        query = query.where(ReviewQueueItem.run_id == run_id)
                    new_items = fresh_db.execute(query).scalars().all()

                    for item in new_items:
                        key = str(item.id)
                        if key not in seen_ids:
                            seen_ids.add(key)
                            yield {
                                "event": "review_item",
                                "data": ReviewQueueItemSchema.model_validate(item).model_dump_json(),
                            }
            except Exception:
                pass  # transient DB errors — retry on next iteration

            if heartbeat_counter >= 30:
                yield {"event": "heartbeat", "data": "ping"}
                heartbeat_counter = 0

    es_response = EventSourceResponse(event_generator())
    es_response.raw_headers.extend(response.raw_headers)
    return es_response


# ── GET /script/{test_id} ────────────────────────────────────────────────────


@router.get("/script/{test_id}", response_model=ScriptResponse)
@limiter.limit(settings.rate_limit_api)
def get_script(
    request: Request,
    project_id: uuid.UUID,
    test_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScriptResponse:
    _get_project_or_404(db, current_user.id, project_id)

    tc = db.get(TestCase, uuid.UUID(test_id))
    if not tc or tc.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test case not found")

    content = ""
    try:
        from pathlib import Path
        if tc.script_path and Path(tc.script_path).exists():
            content = Path(tc.script_path).read_text(encoding="utf-8")
        else:
            content = mcp_server.read_script(test_id)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script file not found")

    return ScriptResponse(test_id=test_id, script_content=content)


@router.get("/artifacts", response_model=list[Phase3ArtifactResponse])
@limiter.limit(settings.rate_limit_api)
def list_phase3_artifacts(
    request: Request,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None = Query(default=None),
    test_id: uuid.UUID | None = Query(default=None),
    artifact_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Phase3ArtifactResponse]:
    _get_project_or_404(db, current_user.id, project_id)
    query = select(Phase3Artifact).where(
        Phase3Artifact.project_id == project_id,
        Phase3Artifact.status == "ACTIVE",
    )
    if run_id:
        query = query.where(Phase3Artifact.run_id == run_id)
    if test_id:
        query = query.where(Phase3Artifact.test_id == test_id)
    if artifact_type:
        query = query.where(Phase3Artifact.artifact_type == artifact_type.upper())
    rows = db.execute(query.order_by(Phase3Artifact.created_at.desc())).scalars().all()
    return [Phase3ArtifactResponse.model_validate(row) for row in rows]


@router.get("/artifacts/{artifact_id}/download")
@limiter.limit(settings.rate_limit_api)
def download_phase3_artifact(
    request: Request,
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    _get_project_or_404(db, current_user.id, project_id)
    artifact = db.get(Phase3Artifact, artifact_id)
    if not artifact or artifact.project_id != project_id or artifact.status != "ACTIVE":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    path = Path(artifact.path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file missing")
    return FileResponse(
        path=str(path),
        media_type=artifact.mime_type or "application/octet-stream",
        filename=artifact.filename,
    )


# ── POST /review-queue/{id}/rerun ────────────────────────────────────────────


@router.post("/review-queue/{item_id}/rerun", response_model=ReviewQueueItemSchema)
@limiter.limit(settings.rate_limit_api)
def rerun_review_item(
    request: Request,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: RerunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReviewQueueItemSchema:
    _get_project_or_404(db, current_user.id, project_id)

    item = _get_review_item_for_project_or_404(db, project_id, item_id)
    tc = db.get(TestCase, item.test_id)

    test_id_str = str(item.test_id)
    run_id_str = str(item.run_id)

    # Write under the multi-tenant layout so the rerun script lives next to
    # the original (instead of orphaning a copy in the legacy flat dir).
    script_path = mcp_server.write_script(
        test_id_str,
        payload.script_content,
        project_id=str(project_id),
        run_id=run_id_str,
    )
    mcp_server.update_script_path(test_id_str, script_path)

    item.status = "rerunning"
    # The original run is `completed` by now — worker would discard any job
    # whose run isn't `running`. Flip it back so the rerun can be consumed.
    # The spawned worker_loop below drains the job, then we restore the
    # completed status (see _run_rerun_worker).
    run = db.get(TestRun, item.run_id)
    if run is not None and run.status != "running":
        run.status = "running"
    db.commit()
    db.refresh(item)

    from app.services.state_store import increment_retries
    increment_retries(item.test_id)

    from app.services.phase3_jobs import build_single_test_job

    queued = mcp_server.enqueue(
        build_single_test_job(
            project_id=str(project_id),
            run_id=run_id_str,
            plan_run_id=str(tc.run_id) if tc and tc.run_id else None,
            test_id=test_id_str,
            script_path=script_path,
            review_item_id=str(item.id),
            credential_id=str(tc.credential_id) if tc and tc.credential_id else None,
        )
    )
    if not queued:
        item.status = "pending"
        if run is not None:
            run.status = "completed"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to queue rerun job. Please try again.",
        )

    # In embedded-worker mode (dev/local default) the worker_loop spawned by
    # the original run has already exited. Spawn a one-off worker just for
    # this rerun so the job gets consumed instead of sitting in the queue.
    # External-worker deployments don't need this — the standalone worker
    # consumes any active run; the run-status flip above is sufficient.
    if get_settings().phase3_embedded_workers:
        background_tasks.add_task(_run_rerun_worker, run_id_str)

    return ReviewQueueItemSchema.model_validate(item)


def _run_rerun_worker(run_id: str) -> None:
    """Spawn a one-off worker_loop for a single rerun job, then refresh the
    dashboard counters and restore `completed` status. Runs in a FastAPI
    BackgroundTasks thread so the HTTP response returns immediately."""
    from app.services.phase3_worker import worker_loop
    from app.graph.phase3_graph import recompute_run_counters
    try:
        # Short idle timeout — the rerun is already in the queue when this
        # task fires. 30s is enough for the worker to claim, run Playwright,
        # classify, and then idle out.
        worker_loop(run_id, idle_timeout_s=30)
    except Exception:
        logger.exception("rerun worker_loop failed for run_id=%s", run_id)
    finally:
        # Reflect the rerun's PASS/FAIL in TestRun.passed/failed/human_review
        # so the dashboard summary stays consistent with the per-test grid.
        try:
            counts = recompute_run_counters(run_id)
            logger.info("rerun: recomputed counters for run_id=%s → %s", run_id, counts)
        except Exception:
            logger.exception("rerun: failed to recompute counters for run_id=%s", run_id)
        # Restore completed status regardless of outcome — the original run
        # is logically done; the rerun is a side-channel job.
        try:
            from app.db.session import SessionLocal
            with SessionLocal() as db:
                run = db.execute(
                    select(TestRun).where(TestRun.run_id == uuid.UUID(run_id))
                ).scalar_one_or_none()
                if run is not None and run.status == "running":
                    run.status = "completed"
                    db.commit()
        except Exception:
            logger.exception("rerun: failed to restore run status for run_id=%s", run_id)

# ── POST /raise-jira ─────────────────────────────────────────────────────────


@router.post("/raise-jira", response_model=ReviewQueueItemSchema)
@limiter.limit(settings.rate_limit_api)
def raise_jira(
    request: Request,
    project_id: uuid.UUID,
    payload: RaiseJiraRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReviewQueueItemSchema:
    """Raise a Jira issue for a review queue item.

    The issue summary is prefixed with the test case's TC number, e.g.
    '[TC-003] Cart total wrong after coupon', maintaining the RTM chain:
    Jira Bug → Test Case → Scenario → Epic.
    """
    _get_project_or_404(db, current_user.id, project_id)

    from app.services.jira_service import create_jira_issue, is_jira_configured

    if not is_jira_configured():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Jira integration is not configured for this server",
        )

    jira_config = db.execute(
        select(ProjectJiraConfig).where(ProjectJiraConfig.project_id == project_id)
    ).scalar_one_or_none()
    if not jira_config or not jira_config.jira_project_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No Jira project linked to this project",
        )

    item = _get_review_item_for_project_or_404(db, project_id, payload.review_queue_id)

    # ── RTM: prefix summary with [TC-XXX] ────────────────────────────────────
    tc = db.get(TestCase, item.test_id)
    tc_number    = tc.tc_number if tc and tc.tc_number else ""
    jira_summary = f"[{tc_number}] {payload.summary}" if tc_number else payload.summary

    description_body = payload.description or ""
    if tc_number:
        description_body = (
            f"{description_body}\n\n"
            f"**Test Case Reference:** {tc_number}\n"
            f"**Traceability:** {tc_number} \u2192 {tc.title if tc else ''}"
        ).strip()

    try:
        result = create_jira_issue(
            project_key=jira_config.jira_project_key,
            title=jira_summary,
            description=description_body,
            issue_type=payload.issue_type,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    item.jira_ref = result["jira_issue_key"]
    item.status   = "reviewed"
    db.commit()
    db.refresh(item)

    logger.info(
        "raise-jira: created %s for test_id=%s tc_number=%s",
        result["jira_issue_key"], item.test_id, tc_number,
    )
    return ReviewQueueItemSchema.model_validate(item)


# ── GET /trace/{test_id} ─────────────────────────────────────────────────────


@router.get("/trace/{test_id}")
@limiter.limit(settings.rate_limit_api)
def get_trace(
    request: Request,
    project_id: uuid.UUID,
    test_id: uuid.UUID,
    run_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Download the Playwright trace .zip for a test result.

    The trace is stored by the worker under tests/generated/traces/.
    Open it at https://trace.playwright.dev for visual inspection.
    """
    _get_project_or_404(db, current_user.id, project_id)

    result_query = (
        select(TestResult)
        .join(TestCase, TestResult.test_id == TestCase.test_id)
        .where(
            TestResult.test_id == test_id,
            TestCase.project_id == project_id,
        )
        .order_by(TestResult.created_at.desc())
        .limit(1)
    )
    if run_id:
        result_query = result_query.where(TestResult.run_id == run_id)
    result = db.execute(result_query).scalar_one_or_none()

    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test result not found")

    trace_path = result.trace_path
    if not trace_path or not Path(trace_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No trace file available for this test. Traces are captured on first retry only.",
        )

    return FileResponse(
        path=trace_path,
        media_type="application/zip",
        filename=f"trace_{test_id}.zip",
    )


# ── GET /screenshot/{test_id} ────────────────────────────────────────────────


@router.get("/screenshot/{test_id}")
@limiter.limit(settings.rate_limit_api)
def get_assertion_screenshot(
    request: Request,
    project_id: uuid.UUID,
    test_id: uuid.UUID,
    run_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the assertion screenshot PNG captured after the last expect() for a PASS test.

    Screenshots are produced by the SQAT assertion-screenshot injection in Agent A5:
        if (process.env.SQAT_SCREENSHOT_PATH) {
            await page.screenshot({ path: process.env.SQAT_SCREENSHOT_PATH });
        }
    and stored in tests/generated/<project_id>/<run_id>/traces/<test_id_short>/assertion_screenshot.png.

    Returns 404 if no screenshot exists (FAIL tests have a trace instead; use GET /trace/{test_id}).
    """
    _get_project_or_404(db, current_user.id, project_id)

    result_query = (
        select(TestResult)
        .join(TestCase, TestResult.test_id == TestCase.test_id)
        .where(
            TestResult.test_id == test_id,
            TestCase.project_id == project_id,
        )
        .order_by(TestResult.created_at.desc())
        .limit(1)
    )
    if run_id:
        result_query = result_query.where(TestResult.run_id == run_id)
    result = db.execute(result_query).scalar_one_or_none()

    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test result not found")

    screenshot_path = result.screenshot_path
    if not screenshot_path or not Path(screenshot_path).exists():
        # Also check Phase3ExecutionState as a fallback (state may be ahead of test_results flush)
        exec_state = db.execute(
            select(Phase3ExecutionState)
            .where(
                Phase3ExecutionState.test_id == test_id,
            )
            .order_by(Phase3ExecutionState.updated_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if exec_state and exec_state.screenshot_path and Path(exec_state.screenshot_path).exists():
            screenshot_path = exec_state.screenshot_path
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No assertion screenshot available for this test. Screenshots are captured on PASS outcomes only.",
            )

    return FileResponse(
        path=screenshot_path,
        media_type="image/png",
        filename=f"assertion_screenshot_{test_id}.png",
    )


# ── GET /network-logs/{test_id} ──────────────────────────────────────────────


@router.get("/network-logs/{test_id}")
@limiter.limit(settings.rate_limit_api)
def get_network_logs(
    request: Request,
    project_id: uuid.UUID,
    test_id: uuid.UUID,
    run_id: uuid.UUID | None = Query(default=None),
    failures_only: bool = Query(default=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """List captured network requests for a test result.

    Used by the live-log "X 4xx/5xx" badge — clicking it expands an inline
    panel showing the failing requests so demo viewers see *why* a test was
    flagged without leaving the page.

    Defaults to ``failures_only=True`` because the badge counts failures; pass
    ``failures_only=false`` to include all captured requests.
    """
    from app.models.phase3 import NetworkLog

    _get_project_or_404(db, current_user.id, project_id)

    # Verify the test_id belongs to this project before exposing logs.
    tc = db.execute(
        select(TestCase).where(
            TestCase.test_id == test_id,
            TestCase.project_id == project_id,
        )
    ).scalars().first()
    if not tc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test case not found")

    # Pin to the most recent test_result for this (test_id, run_id) pair so the
    # caller gets logs from the run currently displayed in the UI.
    result_query = (
        select(TestResult)
        .where(TestResult.test_id == test_id)
        .order_by(TestResult.created_at.desc())
        .limit(1)
    )
    if run_id:
        result_query = result_query.where(TestResult.run_id == run_id)
    result = db.execute(result_query).scalar_one_or_none()

    if not result:
        return []

    log_query = (
        select(NetworkLog)
        .where(NetworkLog.test_result_id == result.id)
        .order_by(NetworkLog.created_at.asc())
    )
    if failures_only:
        log_query = log_query.where(NetworkLog.is_failure.is_(True))

    logs = db.execute(log_query).scalars().all()
    return [
        {
            "id":          str(log.id),
            "url":         log.url,
            "method":      log.method,
            "status_code": log.status_code,
            "is_failure":  log.is_failure,
            "created_at":  log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


# ── DELETE /reset ─────────────────────────────────────────────────────────────


@router.delete("/reset", status_code=status.HTTP_200_OK)
@limiter.limit(settings.rate_limit_api)
def reset_phase3(
    request: Request,
    project_id: uuid.UUID,
    scope: Literal["current_run", "all"] = Query(
        default="all",
        description="'current_run' wipes only the latest run; 'all' wipes every Phase 3 record for the project.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Reset Phase 3 data for a project.

    scope='all' (default, backward compatible):
        Deletes ALL test_cases, test_results, review_queue_items, and test_runs
        for the project. Use this to fully reset the project before a fresh demo.

    scope='current_run':
        Deletes only the most-recent test_run plus its test_cases, results, and
        review items. Earlier runs (e.g. an approved plan-run a user wants to
        keep) are preserved. Returns {deleted_runs: 0, ...} if no runs exist.

    Queued jobs are never purged from RabbitMQ — workers discard deleted/inactive
    run jobs by run_id when they pick them up.
    """
    _get_project_or_404(db, current_user.id, project_id)

    deleted_results  = 0
    deleted_reviews  = 0
    deleted_tcs      = 0
    deleted_runs     = 0
    deleted_exec     = 0
    deleted_auth     = 0
    deleted_hls      = 0
    archived_artifacts = 0

    if scope == "current_run":
        # Find the most recent run; if none, nothing to do.
        latest_run = db.execute(
            select(TestRun)
            .where(TestRun.project_id == project_id)
            .order_by(TestRun.created_at.desc())
            .limit(1)
        ).scalars().first()

        if not latest_run:
            logger.info("reset_phase3(current_run): no run found for project_id=%s", project_id)
            return {
                "scope": scope,
                "deleted_test_cases":   0,
                "deleted_test_results": 0,
                "deleted_review_items": 0,
                "deleted_execution_state": 0,
                "deleted_auth_states": 0,
                "deleted_hls_groups": 0,
                "archived_artifacts": 0,
                "deleted_runs":         0,
            }

        run_id = latest_run.run_id
        tc_ids = db.execute(
            select(TestCase.test_id).where(
                TestCase.project_id == project_id,
                TestCase.run_id == run_id,
            )
        ).scalars().all()

        if tc_ids:
            deleted_exec = db.query(Phase3ExecutionState).filter(
                Phase3ExecutionState.test_id.in_(tc_ids)
            ).delete(synchronize_session=False)
            deleted_results = db.query(TestResult).filter(
                TestResult.test_id.in_(tc_ids)
            ).delete(synchronize_session=False)
            deleted_reviews = db.query(ReviewQueueItem).filter(
                ReviewQueueItem.test_id.in_(tc_ids)
            ).delete(synchronize_session=False)
            deleted_tcs = db.query(TestCase).filter(
                TestCase.test_id.in_(tc_ids)
            ).delete(synchronize_session=False)

        # Also drop review items attached to this run that may not share a tc_id
        # (defensive; e.g. orphaned reviews from a prior crash).
        deleted_reviews += db.query(ReviewQueueItem).filter(
            ReviewQueueItem.run_id == run_id
        ).delete(synchronize_session=False)
        deleted_exec += db.query(Phase3ExecutionState).filter(
            Phase3ExecutionState.run_id == run_id
        ).delete(synchronize_session=False)
        deleted_auth = db.query(AuthState).filter(
            AuthState.run_id == run_id
        ).delete(synchronize_session=False)
        deleted_hls = db.query(Phase3HlsGroup).filter(
            Phase3HlsGroup.run_id == run_id
        ).delete(synchronize_session=False)
        archived_artifacts = db.query(Phase3Artifact).filter(
            Phase3Artifact.run_id == run_id,
            Phase3Artifact.status != "DELETED",
        ).update({"status": "DELETED"}, synchronize_session=False)

        deleted_runs = db.query(TestRun).filter(
            TestRun.run_id == run_id
        ).delete(synchronize_session=False)

        db.commit()
        state_store.clear_tests({str(tid) for tid in tc_ids})

        logger.info(
            "reset_phase3(current_run): project_id=%s run_id=%s deleted tcs=%d results=%d reviews=%d exec=%d auth=%d hls=%d artifacts=%d runs=%d",
            project_id, run_id, deleted_tcs, deleted_results, deleted_reviews,
            deleted_exec, deleted_auth, deleted_hls, archived_artifacts, deleted_runs,
        )
        return {
            "scope":                 scope,
            "run_id":                str(run_id),
            "deleted_test_cases":    deleted_tcs,
            "deleted_test_results":  deleted_results,
            "deleted_review_items":  deleted_reviews,
            "deleted_execution_state": deleted_exec,
            "deleted_auth_states":   deleted_auth,
            "deleted_hls_groups":    deleted_hls,
            "archived_artifacts":    archived_artifacts,
            "deleted_runs":          deleted_runs,
        }

    # scope == "all" — full nuke (legacy behaviour).
    # Cascade order: dependents first, then parents.
    tc_ids = db.execute(
        select(TestCase.test_id).where(TestCase.project_id == project_id)
    ).scalars().all()

    if tc_ids:
        deleted_exec = db.query(Phase3ExecutionState).filter(
            Phase3ExecutionState.test_id.in_(tc_ids)
        ).delete(synchronize_session=False)
        deleted_results = db.query(TestResult).filter(
            TestResult.test_id.in_(tc_ids)
        ).delete(synchronize_session=False)

        deleted_reviews = db.query(ReviewQueueItem).filter(
            ReviewQueueItem.test_id.in_(tc_ids)
        ).delete(synchronize_session=False)

    run_ids = db.execute(
        select(TestRun.run_id).where(TestRun.project_id == project_id)
    ).scalars().all()
    if run_ids:
        deleted_exec += db.query(Phase3ExecutionState).filter(
            Phase3ExecutionState.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        deleted_auth = db.query(AuthState).filter(
            AuthState.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        deleted_hls = db.query(Phase3HlsGroup).filter(
            Phase3HlsGroup.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        archived_artifacts = db.query(Phase3Artifact).filter(
            Phase3Artifact.run_id.in_(run_ids),
            Phase3Artifact.status != "DELETED",
        ).update({"status": "DELETED"}, synchronize_session=False)

    deleted_tcs = db.query(TestCase).filter(
        TestCase.project_id == project_id
    ).delete(synchronize_session=False)

    deleted_runs = db.query(TestRun).filter(
        TestRun.project_id == project_id
    ).delete(synchronize_session=False)

    db.commit()
    state_store.clear_tests({str(tid) for tid in tc_ids})

    logger.info(
        "reset_phase3(all): project_id=%s deleted tcs=%d results=%d reviews=%d exec=%d auth=%d hls=%d artifacts=%d runs=%d",
        project_id, deleted_tcs, deleted_results, deleted_reviews,
        deleted_exec, deleted_auth, deleted_hls, archived_artifacts, deleted_runs,
    )
    return {
        "scope":                 scope,
        "deleted_test_cases":    deleted_tcs,
        "deleted_test_results":  deleted_results,
        "deleted_review_items":  deleted_reviews,
        "deleted_execution_state": deleted_exec,
        "deleted_auth_states":   deleted_auth,
        "deleted_hls_groups":    deleted_hls,
        "archived_artifacts":    archived_artifacts,
        "deleted_runs":          deleted_runs,
    }


# ── POST /cancel ──────────────────────────────────────────────────────────────


@router.post("/cancel", status_code=status.HTTP_200_OK)
@limiter.limit(settings.rate_limit_api)
def cancel_phase3_run(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Cancel an active Phase 3 run.

    - Marks the latest running/planning run as 'cancelled' in the DB.
    - Clears only the cancelled run's local state entries.
    - Leaves the shared RabbitMQ queue intact; workers discard cancelled run jobs.

    Safe to call even if no run is active (returns 'no_active_run').
    """
    _get_project_or_404(db, current_user.id, project_id)

    # Find the active run
    active_run = db.execute(
        select(TestRun)
        .where(
            TestRun.project_id == project_id,
            TestRun.status.in_(["running", "planning"]),
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    ).scalars().first()

    cancelled_run_id: str | None = None
    if active_run:
        active_run.status = "cancelled"
        db.commit()
        cancelled_run_id = str(active_run.run_id)
        logger.info("cancel_phase3_run: marked run %s as cancelled", cancelled_run_id)

    # Clear only this run's local state entries.
    if cancelled_run_id:
        cancelled_test_ids = db.execute(
            select(Phase3ExecutionState.test_id).where(
                Phase3ExecutionState.run_id == uuid.UUID(cancelled_run_id)
            )
        ).scalars().all()
        state_store.clear_tests({str(tid) for tid in cancelled_test_ids})

    return {
        "cancelled": cancelled_run_id is not None,
        "run_id": cancelled_run_id,
        "message": "Run cancelled" if cancelled_run_id else "No active run found",
    }
