"""Expired auth-state sweeper — deletes stale storageState files + DB rows.

Rationale
---------
`auth_state_service` writes one file per (project_id, run_id, credential_id)
into `server/tests/.auth/{project_id}/{run_id}/{credential_id}.json`.
Each corresponding `auth_states` row carries an `expires_at` column.

Without a sweeper:
  - disk fills up (every run creates ~N credential files)
  - DB grows unbounded
  - stale / failed auth states are never reclaimed

Policy
------
A row is eligible for cleanup when EITHER:
  1. `expires_at` is set and < now()                (explicit TTL)
  2. `created_at` is older than retention_hours     (fallback TTL)

And the associated test_run is NOT still active (`status == 'running'`).
This avoids deleting files under an actively-executing run.

Entry points
------------
  - `sweep_expired_auth_states(...)`  — library call, returns counts
  - `python -m app.services.auth_state_cleanup_service`  — CLI for cron
  - (wired into FastAPI startup later via APScheduler)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.phase3 import AuthState, TestRun

logger = logging.getLogger(__name__)


def _delete_file_if_exists(path: str) -> bool:
    if not path:
        return False
    p = Path(path)
    if not p.exists():
        return False
    try:
        p.unlink()
        return True
    except OSError as exc:  # pragma: no cover
        logger.warning("auth_cleanup: failed to delete %s: %s", p, exc)
        return False


def sweep_expired_auth_states(
    db: Session | None = None,
    *,
    retention_hours: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Delete expired auth_states rows and their storageState files.

    Returns counts: {'rows_deleted', 'files_deleted', 'rows_skipped_active_run'}
    """
    close_db = db is None
    db = db or SessionLocal()

    retention = retention_hours if retention_hours is not None else settings.auth_state_retention_hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, retention))
    now = datetime.now(timezone.utc)

    rows_deleted = 0
    files_deleted = 0
    skipped_active = 0

    try:
        # Candidate rows: expired OR older than retention window
        candidates = db.execute(
            select(AuthState, TestRun.status)
            .outerjoin(TestRun, AuthState.run_id == TestRun.run_id)
            .where(
                or_(
                    AuthState.expires_at.is_not(None) & (AuthState.expires_at < now),
                    AuthState.created_at < cutoff,
                )
            )
        ).all()

        for auth_state, run_status in candidates:
            # Skip rows belonging to an active run — deleting mid-execution
            # would crash the worker's Playwright subprocess
            if run_status == "running":
                skipped_active += 1
                continue

            if not dry_run:
                if _delete_file_if_exists(auth_state.storage_state_path):
                    files_deleted += 1
                db.delete(auth_state)
                rows_deleted += 1
            else:
                # Count what WOULD be deleted without touching disk or DB
                if Path(auth_state.storage_state_path or "").exists():
                    files_deleted += 1

        if not dry_run:
            db.commit()
    finally:
        if close_db:
            db.close()

    result = {
        "rows_deleted": rows_deleted,
        "files_deleted": files_deleted,
        "rows_skipped_active_run": skipped_active,
        "retention_hours": retention,
        "dry_run": dry_run,
    }
    logger.info("auth_cleanup: %s", result)
    return result


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sweep expired Phase 3 auth states")
    parser.add_argument(
        "--retention-hours",
        type=int,
        default=None,
        help=f"Override retention window (default: {settings.auth_state_retention_hours}h)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List what would be deleted without touching DB/files",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    result = sweep_expired_auth_states(
        retention_hours=args.retention_hours, dry_run=args.dry_run
    )
    print(
        f"auth_cleanup: rows_deleted={result['rows_deleted']} "
        f"files_deleted={result['files_deleted']} "
        f"skipped_active={result['rows_skipped_active_run']} "
        f"retention={result['retention_hours']}h "
        f"dry_run={result['dry_run']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
