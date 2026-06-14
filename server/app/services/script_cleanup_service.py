"""Generated-script directory sweeper — multi-tenant disk hygiene.

Scripts written by A5/A7 land under
    tests/generated/<project_id>/<run_id>/<test_id|hls_id>.spec.ts

Without a sweeper, every test run accumulates new .spec.ts files (and
Playwright traces / videos under each subdir) on the worker host's disk.
With one tenant that's annoying; with many it's a real availability risk.

Policy
------
A run-scoped subdir is eligible for deletion when ALL of:
  1. It looks like `<project_id>/<run_id>/` (UUID-shaped both segments)
  2. The directory's mtime is older than `script_retention_hours`
  3. The matching `test_runs` row is NOT `running` (skip live runs)
     If the run is not in the DB at all, fall through to delete (orphan).

Entry points
------------
  - `sweep_expired_scripts(...)` — library call, returns counts
  - `python -m app.services.script_cleanup_service` — CLI for cron

Safety
------
- Only deletes inside `settings.generated_scripts_dir`. Never traverses
  outside via symlinks (`Path.is_symlink()` skip).
- Legacy flat-layout files (no project_id parent) are never touched —
  this sweeper only knows the multi-tenant layout.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.phase3 import TestRun

logger = logging.getLogger(__name__)


def _looks_like_uuid(name: str) -> bool:
    try:
        uuid.UUID(name)
    except (TypeError, ValueError):
        return False
    return True


def _run_is_active(db: Session, run_id_str: str) -> bool:
    try:
        rid = uuid.UUID(run_id_str)
    except (TypeError, ValueError):
        return False
    row = db.execute(
        select(TestRun.status).where(TestRun.run_id == rid)
    ).scalar_one_or_none()
    return row == "running"


def sweep_expired_scripts(
    db: Session | None = None,
    *,
    retention_hours: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Delete expired per-run script subdirectories.

    Returns: {'dirs_deleted', 'dirs_skipped_active', 'dirs_skipped_recent',
              'retention_hours', 'dry_run'}
    """
    close_db = db is None
    db = db or SessionLocal()

    retention = retention_hours if retention_hours is not None else settings.script_retention_hours
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=max(1, retention))).timestamp()

    base = Path(settings.generated_scripts_dir)
    if not base.exists():
        if close_db:
            db.close()
        return {
            "dirs_deleted": 0,
            "dirs_skipped_active": 0,
            "dirs_skipped_recent": 0,
            "retention_hours": retention,
            "dry_run": dry_run,
        }

    deleted = skipped_active = skipped_recent = 0

    try:
        for project_dir in base.iterdir():
            if project_dir.is_symlink() or not project_dir.is_dir():
                continue
            if not _looks_like_uuid(project_dir.name):
                continue
            for run_dir in project_dir.iterdir():
                if run_dir.is_symlink() or not run_dir.is_dir():
                    continue
                if not _looks_like_uuid(run_dir.name):
                    continue
                try:
                    mtime = run_dir.stat().st_mtime
                except OSError:
                    continue
                if mtime > cutoff_ts:
                    skipped_recent += 1
                    continue
                if _run_is_active(db, run_dir.name):
                    skipped_active += 1
                    continue
                if dry_run:
                    deleted += 1
                    continue
                try:
                    shutil.rmtree(run_dir, ignore_errors=False)
                    deleted += 1
                    logger.info("script_cleanup: removed %s", run_dir)
                except OSError as exc:
                    logger.warning("script_cleanup: failed to remove %s: %s", run_dir, exc)
    finally:
        if close_db:
            db.close()

    result = {
        "dirs_deleted": deleted,
        "dirs_skipped_active": skipped_active,
        "dirs_skipped_recent": skipped_recent,
        "retention_hours": retention,
        "dry_run": dry_run,
    }
    logger.info("script_cleanup: %s", result)
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--retention-hours", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sweep_expired_scripts(retention_hours=args.retention_hours, dry_run=args.dry_run)
