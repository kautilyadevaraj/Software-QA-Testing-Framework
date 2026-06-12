"""Durable HLS group ordering — replaces JSON `hls:{id}` entries in state.json.

Purpose
-------
When A5 generates a single grouped spec (one .spec.ts per HLS containing
multiple test() blocks), the worker matches JSON-reporter results to test_ids
by POSITIONAL INDEX, not title. That ordered list must survive:

  - FastAPI / worker restarts
  - Multi-container worker pools (no shared local disk)
  - RabbitMQ redelivery (worker B picks up work started by worker A)

Prior to this table the ordered list lived in state_store.json, which broke
all three guarantees above.

Contract
--------
    save_hls_group(hls_id, run_id, ordered_test_ids)  # idempotent upsert
    get_hls_group(hls_id)                             # -> list[str] | None
    delete_hls_group(hls_id)                          # on run cancel
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import SessionLocal
from app.models.phase3 import Phase3HlsGroup

logger = logging.getLogger(__name__)


def save_hls_group(hls_id: str, run_id: str, ordered_test_ids: list[str]) -> None:
    """Upsert the ordered test_ids for an HLS group. Idempotent on hls_id."""
    try:
        hls_uuid = uuid.UUID(str(hls_id))
        run_uuid = uuid.UUID(str(run_id))
    except (TypeError, ValueError):
        logger.warning(
            "save_hls_group: invalid hls_id=%s or run_id=%s", hls_id, run_id
        )
        return

    stmt = (
        pg_insert(Phase3HlsGroup)
        .values(
            hls_id=hls_uuid,
            run_id=run_uuid,
            ordered_test_ids=[str(t) for t in ordered_test_ids],
        )
        .on_conflict_do_update(
            index_elements=["hls_id"],
            set_={
                "run_id": run_uuid,
                "ordered_test_ids": [str(t) for t in ordered_test_ids],
            },
        )
    )
    try:
        with SessionLocal() as db:
            db.execute(stmt)
            db.commit()
    except Exception as exc:  # pragma: no cover
        logger.warning("save_hls_group: DB error for hls_id=%s: %s", hls_id, exc)


def get_hls_group(hls_id: str) -> list[str] | None:
    """Return the ordered test_ids for an HLS group, or None if not found."""
    try:
        hls_uuid = uuid.UUID(str(hls_id))
    except (TypeError, ValueError):
        return None
    try:
        with SessionLocal() as db:
            row = db.execute(
                select(Phase3HlsGroup.ordered_test_ids).where(
                    Phase3HlsGroup.hls_id == hls_uuid
                )
            ).scalar_one_or_none()
    except Exception as exc:  # pragma: no cover
        logger.warning("get_hls_group: DB error for hls_id=%s: %s", hls_id, exc)
        return None
    if not row:
        return None
    return [str(t) for t in row]


def delete_hls_group(hls_id: str) -> None:
    """Remove an HLS group — used on run cancel / reset."""
    try:
        hls_uuid = uuid.UUID(str(hls_id))
    except (TypeError, ValueError):
        return
    try:
        with SessionLocal() as db:
            db.execute(delete(Phase3HlsGroup).where(Phase3HlsGroup.hls_id == hls_uuid))
            db.commit()
    except Exception as exc:  # pragma: no cover
        logger.warning("delete_hls_group: DB error for hls_id=%s: %s", hls_id, exc)


def get_execution_retries(test_id: str) -> int:
    """Return the DB-backed retry count for a test, 0 if no row yet.

    Replaces state_store.get_retry_count(). Reads from Phase3ExecutionState.
    """
    from app.models.phase3 import Phase3ExecutionState

    try:
        tid = uuid.UUID(str(test_id))
    except (TypeError, ValueError):
        return 0
    try:
        with SessionLocal() as db:
            row = db.execute(
                select(Phase3ExecutionState.retries)
                .where(Phase3ExecutionState.test_id == tid)
                .order_by(Phase3ExecutionState.updated_at.desc())
                .limit(1)
            ).scalar_one_or_none()
        return int(row or 0)
    except Exception:  # pragma: no cover
        return 0
