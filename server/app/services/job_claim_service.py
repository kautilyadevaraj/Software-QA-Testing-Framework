"""Phase 3 worker idempotency — Postgres-backed job claim lock.

Why:
    RabbitMQ delivers at-least-once. If a worker crashes between running
    Playwright and ACKing the message, RabbitMQ will redeliver. Without a
    claim lock, a second worker would run the same test again, possibly
    raising duplicate Jira bugs / writing duplicate rows.

Usage:
    from app.services.job_claim_service import try_claim_job, mark_job_completed

    if not try_claim_job(job):
        # another worker owns it — ACK and skip
        ch.basic_ack(...)
        continue

    # ... run the job ...

    mark_job_completed(job["job_id"], status="completed")

Retry semantics:
    When _handle_job_failure republishes a job after a transient error, it
    MUST generate a new job_id (see phase3_worker._republish_with_new_id).
    Otherwise the retry would hit the claim lock and be silently dropped.
"""
from __future__ import annotations

import logging
import socket
import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import SessionLocal
from app.models.phase3 import Phase3JobClaim

logger = logging.getLogger(__name__)

_WORKER_HOST = socket.gethostname()


def try_claim_job(job: dict[str, Any]) -> bool:
    """Attempt to acquire an idempotency lock for this job_id.

    Returns True if this worker claimed it (proceed to execute).
    Returns False if another worker already has/had it (ACK and skip).
    """
    job_id_str = str(job.get("job_id") or "")
    run_id_str = str(job.get("run_id") or "")
    if not job_id_str or not run_id_str:
        # Malformed — let caller handle. We don't want to block unknown jobs.
        logger.warning("try_claim_job: missing job_id/run_id — cannot claim")
        return True

    try:
        job_uuid = uuid.UUID(job_id_str)
        run_uuid = uuid.UUID(run_id_str)
    except (TypeError, ValueError):
        logger.warning("try_claim_job: invalid UUID format: %s / %s", job_id_str, run_id_str)
        return True

    stmt = (
        pg_insert(Phase3JobClaim)
        .values(
            job_id=job_uuid,
            run_id=run_uuid,
            job_type=str(job.get("job_type") or "unknown"),
            status="claimed",
            worker_host=_WORKER_HOST[:128],
            attempt=int(job.get("attempt") or 1),
        )
        .on_conflict_do_nothing(index_elements=["job_id"])
    )
    try:
        with SessionLocal() as db:
            result = db.execute(stmt)
            db.commit()
            claimed = bool(result.rowcount)
        if not claimed:
            logger.warning(
                "try_claim_job: job_id=%s already claimed — skipping duplicate delivery",
                job_id_str,
            )
        return claimed
    except Exception as exc:
        # Fail-open: if the claim table is unreachable, allow execution to
        # proceed rather than stalling the entire pipeline. Idempotency is
        # best-effort, not a correctness invariant.
        logger.error("try_claim_job: DB error for job_id=%s: %s — proceeding", job_id_str, exc)
        return True


def mark_job_completed(job_id: str, *, status: str = "completed", error: str | None = None) -> None:
    """Update a claim row to its terminal state."""
    try:
        job_uuid = uuid.UUID(str(job_id))
    except (TypeError, ValueError):
        return
    try:
        from sqlalchemy import func
        stmt = (
            update(Phase3JobClaim)
            .where(Phase3JobClaim.job_id == job_uuid)
            .values(status=status, completed_at=func.now(), error=(error or None))
        )
        with SessionLocal() as db:
            db.execute(stmt)
            db.commit()
    except Exception as exc:  # pragma: no cover
        logger.warning("mark_job_completed: DB error for job_id=%s: %s", job_id, exc)
