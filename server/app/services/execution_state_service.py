from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.phase3 import Phase3ExecutionState, ReviewQueueItem, TestCase, TestResult, TestRun
from app.models.project import HighLevelScenario


def _as_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def infer_active_execute_run_id(db: Session, test_id: uuid.UUID) -> uuid.UUID | None:
    tc = db.get(TestCase, test_id)
    if not tc:
        return None
    return db.execute(
        select(TestRun.run_id)
        .where(
            TestRun.project_id == tc.project_id,
            TestRun.run_type == "execute",
            TestRun.status == "running",
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def upsert_execution_state(
    test_id: str | uuid.UUID,
    status: str,
    *,
    run_id: str | uuid.UUID | None = None,
    retries: int | None = None,
    blocked_by: str | uuid.UUID | None = None,
    jira_ticket: str | None = None,
    trace_path: str | None = None,
    screenshot_path: str | None = None,
    network_logs_count: int | None = None,
) -> None:
    tid = _as_uuid(test_id)
    if not tid:
        return

    with SessionLocal() as db:
        rid = _as_uuid(run_id) or infer_active_execute_run_id(db, tid)
        if not rid:
            return

        row = db.execute(
            select(Phase3ExecutionState).where(
                Phase3ExecutionState.run_id == rid,
                Phase3ExecutionState.test_id == tid,
            )
        ).scalar_one_or_none()
        if row is None:
            row = Phase3ExecutionState(
                id=uuid.uuid4(),
                run_id=rid,
                test_id=tid,
                status=status,
            )
            db.add(row)
        else:
            row.status = status

        if retries is not None:
            row.retries = retries
        if blocked_by is not None:
            row.blocked_by = _as_uuid(blocked_by)
        if jira_ticket is not None:
            row.jira_ticket = jira_ticket
        if trace_path is not None:
            row.trace_path = trace_path
        if screenshot_path is not None:
            row.screenshot_path = screenshot_path
        if network_logs_count is not None:
            row.network_logs_count = network_logs_count

        db.commit()


def increment_execution_retries(test_id: str | uuid.UUID, *, run_id: str | uuid.UUID | None = None) -> int | None:
    tid = _as_uuid(test_id)
    if not tid:
        return None

    with SessionLocal() as db:
        rid = _as_uuid(run_id) or infer_active_execute_run_id(db, tid)
        if not rid:
            return None
        row = db.execute(
            select(Phase3ExecutionState).where(
                Phase3ExecutionState.run_id == rid,
                Phase3ExecutionState.test_id == tid,
            )
        ).scalar_one_or_none()
        if row is None:
            row = Phase3ExecutionState(
                id=uuid.uuid4(),
                run_id=rid,
                test_id=tid,
                status="PENDING",
                retries=1,
            )
            db.add(row)
        else:
            row.retries += 1
        db.commit()
        return row.retries


def append_execution_network_log(test_id: str | uuid.UUID, *, run_id: str | uuid.UUID | None = None) -> None:
    tid = _as_uuid(test_id)
    if not tid:
        return

    with SessionLocal() as db:
        rid = _as_uuid(run_id) or infer_active_execute_run_id(db, tid)
        if not rid:
            return
        row = db.execute(
            select(Phase3ExecutionState).where(
                Phase3ExecutionState.run_id == rid,
                Phase3ExecutionState.test_id == tid,
            )
        ).scalar_one_or_none()
        if row is None:
            row = Phase3ExecutionState(
                id=uuid.uuid4(),
                run_id=rid,
                test_id=tid,
                status="PENDING",
                network_logs_count=1,
            )
            db.add(row)
        else:
            row.network_logs_count += 1
        db.commit()


def list_execution_state(db: Session, project_id: uuid.UUID, run_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    if run_id is None:
        run_id = db.execute(
            select(TestRun.run_id)
            .where(
                TestRun.project_id == project_id,
                TestRun.run_type == "execute",
            )
            .order_by(TestRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    if run_id is None:
        return []

    rows = db.execute(
        select(Phase3ExecutionState, TestCase)
        .join(TestCase, Phase3ExecutionState.test_id == TestCase.test_id)
        .where(
            Phase3ExecutionState.run_id == run_id,
            TestCase.project_id == project_id,
        )
    ).all()
    hls_ids = {tc.hls_id for _state, tc in rows if tc.hls_id}
    scenario_titles: dict[uuid.UUID, str] = {}
    if hls_ids:
        scenario_titles = {
            row.id: row.title
            for row in db.execute(
                select(HighLevelScenario.id, HighLevelScenario.title).where(
                    HighLevelScenario.id.in_(hls_ids)
                )
            ).all()
        }

    def _reason_from_evidence(evidence: dict[str, Any] | None) -> str | None:
        if not isinstance(evidence, dict):
            return None
        for key in ("reason", "validation_reason", "action"):
            value = evidence.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        error_log = evidence.get("error_log")
        if isinstance(error_log, str) and error_log.strip():
            first_line = next((line.strip() for line in error_log.splitlines() if line.strip()), "")
            return first_line[:240] if first_line else None
        failing = evidence.get("failing_requests")
        if isinstance(failing, list) and failing:
            first = failing[0] if isinstance(failing[0], dict) else {}
            status = first.get("status") or first.get("status_code")
            method = first.get("method") or "HTTP"
            url = first.get("url") or "unknown URL"
            return f"{method} {url} returned {status}"
        return None

    result = []
    for state, tc in rows:
        review = db.execute(
            select(ReviewQueueItem)
            .where(
                ReviewQueueItem.run_id == state.run_id,
                ReviewQueueItem.test_id == state.test_id,
            )
            .order_by(ReviewQueueItem.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        latest_result = db.execute(
            select(TestResult)
            .where(
                TestResult.run_id == state.run_id,
                TestResult.test_id == state.test_id,
            )
            .order_by(TestResult.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        evidence = review.evidence if review else {}
        review_category = None
        if isinstance(evidence, dict):
            review_category = evidence.get("category") or review.review_type if review else None
        failure_reason = _reason_from_evidence(evidence)
        trace_path = state.trace_path or (latest_result.trace_path if latest_result else None)
        screenshot_path = state.screenshot_path or (latest_result.screenshot_path if latest_result else None)
        result.append(
            {
                "test_id": str(state.test_id),
                "tc_number": tc.tc_number,
                "title": tc.title,
                "scenario_title": scenario_titles.get(tc.hls_id) if tc.hls_id else None,
                "target_page": tc.target_page,
                "status": state.status,
                "retries": state.retries,
                "blocked_by": str(state.blocked_by) if state.blocked_by else None,
                "network_logs_count": state.network_logs_count,
                "failure_reason": failure_reason,
                "review_category": str(review_category) if review_category else None,
                "review_type": review.review_type if review else None,
                "review_status": review.status if review else None,
                "jira_ref": review.jira_ref if review else state.jira_ticket,
                "trace_path": trace_path,
                "screenshot_path": screenshot_path,
            }
        )

    order = {"PENDING": 0, "PASS": 1, "FAIL": 2, "SCRIPT_ERROR": 3,
             "APP_ERROR": 4, "BLOCKED": 5, "HUMAN_REVIEW": 6}
    result.sort(key=lambda x: order.get(x["status"], 99))
    return result
