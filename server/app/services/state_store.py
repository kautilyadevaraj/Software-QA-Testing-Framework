"""Phase 3 live execution state — Postgres-backed (formerly JSON-backed).

Public API preserved for backward compatibility with existing call sites;
all writes now go directly to `phase3_execution_state` / `phase3_hls_groups`
via the execution_state_service and hls_group_service modules.

No more `state.json`.  Justifications:
  - JSON file is unreachable by workers in separate containers.
  - FastAPI restarts wiped in-flight state.
  - Dual-write to JSON + DB caused divergence bugs.

All functions remain idempotent and fail-open: a DB outage must not crash a
worker mid-test (the job will be retried via the DLX + max-attempts path).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.phase3 import Phase3ExecutionState, TestCase, TestRun
from app.services import hls_group_service
from app.services.execution_state_service import (
    append_execution_network_log,
    increment_execution_retries,
    upsert_execution_state,
)

logger = logging.getLogger(__name__)


def _infer_run_id(test_id: str | uuid.UUID) -> uuid.UUID | None:
    """Locate the active execute run for a test_id (used by read helpers)."""
    try:
        tid = test_id if isinstance(test_id, uuid.UUID) else uuid.UUID(str(test_id))
    except (TypeError, ValueError):
        return None
    try:
        with SessionLocal() as db:
            tc = db.get(TestCase, tid)
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
    except Exception:
        return None


# ── Public API (unchanged signatures) ────────────────────────────────────


def init_test(test_id: str | uuid.UUID, run_id: str | uuid.UUID | None = None) -> None:
    """Register a new test entry with status PENDING."""
    try:
        upsert_execution_state(test_id, "PENDING", run_id=run_id)
    except Exception as exc:  # pragma: no cover
        logger.warning("state_store.init_test: %s", exc)


def update_state(test_id: str | uuid.UUID, status: str, **extras: Any) -> None:
    """Atomically update a test's status and any additional fields.

    `run_id` should be forwarded by callers that know it (workers do). Without
    an explicit run_id, upsert_execution_state has to infer the active execute
    run, which can race with run-status transitions and silently drop the
    update — leaving Phase3ExecutionState empty for the whole run.
    """
    try:
        upsert_execution_state(
            test_id,
            status,
            run_id=extras.get("run_id"),
            retries=extras.get("retries"),
            blocked_by=extras.get("blocked_by"),
            jira_ticket=extras.get("jira_ticket"),
            trace_path=extras.get("trace_path"),
            network_logs_count=(
                len(extras["network_logs"]) if isinstance(extras.get("network_logs"), list) else None
            ),
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("state_store.update_state: %s", exc)


def increment_retries(test_id: str | uuid.UUID) -> int:
    """Increment retry count atomically. Returns the new count (best effort)."""
    try:
        new = increment_execution_retries(test_id)
        return int(new or 0)
    except Exception as exc:  # pragma: no cover
        logger.warning("state_store.increment_retries: %s", exc)
        return 0


def get_status(test_id: str | uuid.UUID) -> dict[str, Any] | None:
    """Return the state entry for a test, or None if not found."""
    try:
        tid = test_id if isinstance(test_id, uuid.UUID) else uuid.UUID(str(test_id))
    except (TypeError, ValueError):
        return None
    try:
        with SessionLocal() as db:
            row = db.execute(
                select(Phase3ExecutionState)
                .where(Phase3ExecutionState.test_id == tid)
                .order_by(Phase3ExecutionState.updated_at.desc())
                .limit(1)
            ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "status": row.status,
            "retries": row.retries,
            "jira_ticket": row.jira_ticket,
            "trace_path": row.trace_path,
            "blocked_by": str(row.blocked_by) if row.blocked_by else None,
        }
    except Exception:
        return None


def get_retry_count(test_id: str | uuid.UUID) -> int:
    return hls_group_service.get_execution_retries(str(test_id))


def append_network_log(test_id: str | uuid.UUID, log: dict[str, Any]) -> None:
    """Increment network_logs_count for a test (the full log row is written
    separately to the NetworkLog table via save_test_result)."""
    # `log` param preserved for call-site compatibility but is no longer used
    # for storage — the authoritative copy lives in `network_logs` table.
    _ = log
    try:
        append_execution_network_log(test_id)
    except Exception as exc:  # pragma: no cover
        logger.warning("state_store.append_network_log: %s", exc)


def init_hls_group(hls_id: str, ordered_test_ids: list[str]) -> None:
    """Store ordered list of test_ids for a grouped HLS spec in the DB."""
    run_id = _lookup_run_id_for_ordered_tests(ordered_test_ids)
    if run_id is None:
        logger.warning(
            "init_hls_group: could not infer run_id for hls_id=%s — skipping persist",
            hls_id,
        )
        return
    hls_group_service.save_hls_group(hls_id, str(run_id), ordered_test_ids)


def get_hls_group(hls_id: str) -> list[str] | None:
    return hls_group_service.get_hls_group(hls_id)


# ── Deprecated / no-op helpers kept for call-site compatibility ───────────────


def get_all() -> dict[str, Any]:
    """No-op: state.json is gone. Callers should query Phase3ExecutionState
    directly via execution_state_service.list_execution_state()."""
    return {}


def get_many(test_ids: set[str]) -> dict[str, Any]:
    """Deprecated — returns empty dict. Kept so existing call sites compile."""
    _ = test_ids
    return {}


def clear_tests(test_ids: set[str]) -> None:
    """Remove specific execution-state rows (called on reset/cancel)."""
    if not test_ids:
        return
    valid_uuids: list[uuid.UUID] = []
    for tid in test_ids:
        try:
            valid_uuids.append(uuid.UUID(str(tid)))
        except (TypeError, ValueError):
            continue
    if not valid_uuids:
        return
    try:
        from sqlalchemy import delete as sa_delete
        with SessionLocal() as db:
            db.execute(
                sa_delete(Phase3ExecutionState).where(
                    Phase3ExecutionState.test_id.in_(valid_uuids)
                )
            )
            db.commit()
    except Exception as exc:  # pragma: no cover
        logger.warning("state_store.clear_tests: %s", exc)


def clear() -> None:
    """No-op: state.json is gone. Use clear_tests(ids) for scoped cleanup."""
    return None


clear_all = clear


# ── Internal helpers ─────────────────────────────────────────────────────


def _lookup_run_id_for_ordered_tests(ordered_test_ids: list[str]) -> uuid.UUID | None:
    """Find the active execute run that owns the first test_id in the group."""
    if not ordered_test_ids:
        return None
    return _infer_run_id(ordered_test_ids[0])
