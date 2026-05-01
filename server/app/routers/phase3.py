"""Phase 3 FastAPI router.

All endpoints are mounted under /api/v1/projects/{project_id}/phase3/
and require JWT authentication.

Endpoints (Generate → Approve → Execute flow):
  POST   /plan                          — A3 planning only, returns TC document
  GET    /tc-document                   — download TC markdown for a run
  GET    /tc-document/json              — TC list as JSON for UI accordion
  PATCH  /approve-all                   — bulk-approve all TCs for a run
  PATCH  /test-cases/{test_id}/approval — per-TC approval status update
  POST   /execute                       — A4+A5+workers (gated: all TCs must be APPROVED)

Legacy / unchanged:
  POST   /trigger                       — DEPRECATED alias for /plan+/execute (kept for compat)
  GET    /run-status                    — latest run counters + run_type
  GET    /execution-state               — live per-test status from state_store
  GET    /review-queue                  — list review items
  PATCH  /review-queue/{id}             — mark reviewed / store jira_ref
  GET    /review-queue/stream           — SSE stream of new review items
  GET    /script/{test_id}              — fetch generated .spec.ts content
  GET    /trace/{test_id}               — download Playwright trace .zip
  POST   /review-queue/{id}/rerun       — save edited script and re-enqueue
  POST   /raise-jira                    — raise a Jira issue prefixed with [TC-XXX]
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.core.config import get_settings
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.phase3 import ReviewQueueItem, TestCase, TestResult, TestRun
from app.models.project import HighLevelScenario, Project, ProjectJiraConfig
from app.models.user import User
from app.schemas.phase3 import (
    ApprovalPatch,
    ApproveAllRequest,
    ExecuteRequest,
    PlanRunResponse,
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
from app.services import mcp_server, state_store
from app.services.jira_service import create_jira_issue, is_jira_configured
from app.utils.rate_limiter import limiter

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/projects/{project_id}/phase3", tags=["phase3"])


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
    """Start a Phase 3 test run. 409 if a run is already in progress."""
    _get_project_or_404(db, current_user.id, project_id)

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
    The TC document (markdown + JSON) is generated and written to disk;
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
            detail="All Phase 2 scenarios must be completed before planning Phase 3",
        )

    # Delete stale test cases from a previous planning run for this project
    old_tcs = db.execute(
        select(TestCase).where(TestCase.project_id == project_id)
    ).scalars().all()
    for tc in old_tcs:
        db.delete(tc)
    db.commit()

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
    """Step 3 of 3: Start Playwright execution. All TCs must be APPROVED.

    Guards:
      400 — unapproved test cases exist (approval gate)
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

    # Guard: approval gate — ALL test cases must be APPROVED
    unapproved = db.execute(
        select(TestCase)
        .where(
            TestCase.project_id == project_id,
            TestCase.approval_status != "APPROVED",
        )
        .limit(1)
    ).scalars().first()
    if unapproved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Test case '{unapproved.title}' (status: {unapproved.approval_status}) "
                "is not approved. All test cases must be APPROVED before execution."
            ),
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
            await run_phase3(project_id_str, run_id_str)
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
    """Step 2a of 3: Bulk-approve all PENDING test cases for the given planning run.

    Sets approval_status = 'APPROVED' on all test cases in the project.
    Returns {approved_count: N}.
    """
    _get_project_or_404(db, current_user.id, project_id)

    tcs = db.execute(
        select(TestCase).where(
            TestCase.project_id == project_id,
            TestCase.approval_status != "APPROVED",
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

    status must be 'APPROVED' or 'NEEDS_EDIT'.
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

    tc.approval_status = payload.status
    db.commit()
    db.refresh(tc)

    # Resolve depends_on UUIDs to titles
    all_tcs = db.execute(
        select(TestCase).where(TestCase.project_id == project_id)
    ).scalars().all()
    title_map = {str(t.test_id): t.title for t in all_tcs}
    depends_on_titles = [title_map.get(str(d), str(d)) for d in (tc.depends_on or [])]

    return TestCaseApprovalResponse(
        test_id=tc.test_id,
        tc_number=tc.tc_number,
        title=tc.title,
        steps=tc.steps,
        acceptance_criteria=tc.acceptance_criteria or [],
        target_page=tc.target_page,
        hls_id=tc.hls_id,
        scenario_title=None,
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

    # ── Regenerate the tc_document_{run_id}.md so the downloaded markdown
    #    always reflects human edits, not just the original AI-generated version.
    if changed:
        try:
            from app.agents.agent3_planner import generate_tc_document
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
                tid_map = {r["test_id"]: r["title"] for r in tc_rows}
                for row in tc_rows:
                    row["depends_on_titles"] = [
                        tid_map.get(dep, dep) for dep in row.get("depends_on", [])
                    ]
                project_obj = db.get(Project, project_id)
                project_name = project_obj.name if project_obj else "Project"
                markdown = generate_tc_document(tc_rows, project_name=project_name)
                doc_path = Path(settings.generated_scripts_dir) / f"tc_document_{latest_plan_run.run_id}.md"
                doc_path.write_text(markdown, encoding="utf-8")
                logger.info(
                    "update_test_case_content: regenerated tc_document at %s", doc_path
                )
        except Exception as regen_exc:
            # Non-fatal — DB is already updated; just log the failure
            logger.warning(
                "update_test_case_content: failed to regenerate tc_document: %s", regen_exc
            )

    return TestCaseApprovalResponse(
        test_id=tc.test_id,
        tc_number=tc.tc_number,
        title=tc.title,
        steps=tc.steps,
        acceptance_criteria=tc.acceptance_criteria or [],
        target_page=tc.target_page,
        hls_id=tc.hls_id,
        scenario_title=None,
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
    """Download the TC markdown document for a planning run."""
    _get_project_or_404(db, current_user.id, project_id)

    doc_path = Path(settings.generated_scripts_dir) / f"tc_document_{run_id}.md"
    if not doc_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TC document not generated yet. Wait for /plan to complete.",
        )
    return FileResponse(
        str(doc_path),
        media_type="text/markdown",
        filename=f"test_cases_{run_id}.md",
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RunStatusResponse:
    _get_project_or_404(db, current_user.id, project_id)

    run = db.execute(
        select(TestRun)
        .where(TestRun.project_id == project_id)
        .order_by(TestRun.created_at.desc())
        .limit(1)
    ).scalars().first()

    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Phase 3 run found for this project")

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
    )


# ── GET /execution-state ──────────────────────────────────────────────────────


@router.get("/execution-state")
@limiter.limit(settings.rate_limit_api)
def get_execution_state(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Return live per-test execution state from the in-process state_store.

    Joins live status (PENDING/PASS/FAIL/BLOCKED/SCRIPT_ERROR/APP_ERROR/HUMAN_REVIEW)
    with TestCase title from the DB so the frontend can display a real-time
    execution log without waiting for the run to complete.

    Returns [] when no run is in progress (state_store is empty).
    """
    _get_project_or_404(db, current_user.id, project_id)

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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReviewQueueItemSchema]:
    _get_project_or_404(db, current_user.id, project_id)

    query = (
        select(ReviewQueueItem)
        .join(TestCase, ReviewQueueItem.test_id == TestCase.test_id)
        .where(TestCase.project_id == project_id)
        .order_by(ReviewQueueItem.created_at.desc())
    )
    if item_status:
        query = query.where(ReviewQueueItem.status == item_status)

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

    item = db.get(ReviewQueueItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review queue item not found")

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
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EventSourceResponse:
    """Server-Sent Events stream of new review queue items for this project."""
    _get_project_or_404(db, current_user.id, project_id)

    seen_ids: set[str] = set()

    async def event_generator():
        heartbeat_counter = 0
        while True:
            await asyncio.sleep(1)
            heartbeat_counter += 1

            new_items = db.execute(
                select(ReviewQueueItem)
                .join(TestCase, ReviewQueueItem.test_id == TestCase.test_id)
                .where(TestCase.project_id == project_id)
                .order_by(ReviewQueueItem.created_at.asc())
            ).scalars().all()

            for item in new_items:
                key = str(item.id)
                if key not in seen_ids:
                    seen_ids.add(key)
                    yield {
                        "event": "review_item",
                        "data": ReviewQueueItemSchema.model_validate(item).model_dump_json(),
                    }

            if heartbeat_counter >= 30:
                yield {"event": "heartbeat", "data": "ping"}
                heartbeat_counter = 0

    return EventSourceResponse(event_generator())


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

    item = db.get(ReviewQueueItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review queue item not found")

    test_id_str = str(item.test_id)

    mcp_server.write_script(test_id_str, payload.script_content)

    item.status = "rerunning"
    db.commit()
    db.refresh(item)

    background_tasks.add_task(_execute_rerun, test_id_str, str(item_id))

    return ReviewQueueItemSchema.model_validate(item)


# ── Rerun background task ────────────────────────────────────────────────────


def _execute_rerun(test_id_str: str, item_id_str: str) -> None:
    """Run the edited Playwright spec, update TestResult and ReviewQueueItem."""
    from app.db.session import SessionLocal
    from app.services.phase3_worker import run_playwright_spec
    from app.services import state_store as ss

    ss.init_test(test_id_str)
    try:
        result = run_playwright_spec(test_id_str)
        final_status = "PASS" if result.get("status") == "PASS" else "SCRIPT_ERROR"
        if final_status == "SCRIPT_ERROR":
            err = result.get("error_message") or result.get("stderr") or ""
            logger.warning("rerun: SCRIPT_ERROR for test_id=%s: %s", test_id_str, err[:400])
    except Exception as exc:
        logger.exception("rerun: playwright failed for test_id=%s: %s", test_id_str, exc)
        final_status = "SCRIPT_ERROR"

    ss.update_state(test_id_str, final_status)

    with SessionLocal() as db:
        from sqlalchemy import update as sa_update
        db.execute(
            sa_update(TestResult)
            .where(TestResult.test_id == uuid.UUID(test_id_str))
            .values(status=final_status)
        )
        rqi = db.get(ReviewQueueItem, uuid.UUID(item_id_str))
        if rqi:
            rqi.status = "resolved" if final_status == "PASS" else "pending"
        db.commit()
        logger.info("rerun: test_id=%s → %s", test_id_str, final_status)


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

    item = db.get(ReviewQueueItem, payload.review_queue_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review queue item not found")

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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Download the Playwright trace .zip for a test result.

    The trace is stored by the worker under tests/generated/traces/.
    Open it at https://trace.playwright.dev for visual inspection.
    """
    _get_project_or_404(db, current_user.id, project_id)

    result = db.execute(
        select(TestResult)
        .join(TestCase, TestResult.test_id == TestCase.test_id)
        .where(
            TestResult.test_id == test_id,
            TestCase.project_id == project_id,
        )
    ).scalar_one_or_none()

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


# ── DELETE /reset ─────────────────────────────────────────────────────────────


@router.delete("/reset", status_code=status.HTTP_200_OK)
@limiter.limit(settings.rate_limit_api)
def reset_phase3(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Hard-reset Phase 3 for a project.

    Deletes ALL test_cases, test_results, review_queue_items, and test_runs
    belonging to this project, and clears the in-memory state_store and
    RabbitMQ queue. Use this to start fresh before a new planning run.
    """
    _get_project_or_404(db, current_user.id, project_id)

    # Cascade order: dependents first, then parents
    # test_results and review_queue_items FK → test_cases
    tc_ids = db.execute(
        select(TestCase.test_id).where(TestCase.project_id == project_id)
    ).scalars().all()

    deleted_results  = 0
    deleted_reviews  = 0
    deleted_tcs      = 0
    deleted_runs     = 0

    if tc_ids:
        deleted_results = db.query(TestResult).filter(
            TestResult.test_id.in_(tc_ids)
        ).delete(synchronize_session=False)

        deleted_reviews = db.query(ReviewQueueItem).filter(
            ReviewQueueItem.test_id.in_(tc_ids)
        ).delete(synchronize_session=False)

    deleted_tcs = db.query(TestCase).filter(
        TestCase.project_id == project_id
    ).delete(synchronize_session=False)

    deleted_runs = db.query(TestRun).filter(
        TestRun.project_id == project_id
    ).delete(synchronize_session=False)

    db.commit()

    # Clear in-memory state and RabbitMQ queue
    state_store.clear()
    try:
        mcp_server.purge_queue()
    except Exception as exc:
        logger.warning("reset_phase3: purge_queue failed: %s", exc)

    logger.info(
        "reset_phase3: project_id=%s deleted tcs=%d results=%d reviews=%d runs=%d",
        project_id, deleted_tcs, deleted_results, deleted_reviews, deleted_runs,
    )
    return {
        "deleted_test_cases":    deleted_tcs,
        "deleted_test_results":  deleted_results,
        "deleted_review_items":  deleted_reviews,
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

    - Purges the RabbitMQ queue so workers stop receiving new jobs.
    - Marks the latest running/planning run as 'cancelled' in the DB.
    - Clears the in-memory state store.

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

    # Stop workers by purging the queue
    try:
        mcp_server.purge_queue()
    except Exception as exc:
        logger.warning("cancel_phase3_run: purge_queue failed (workers may still be running): %s", exc)

    # Clear in-memory state
    state_store.clear()

    return {
        "cancelled": cancelled_run_id is not None,
        "run_id": cancelled_run_id,
        "message": "Run cancelled" if cancelled_run_id else "No active run found",
    }
