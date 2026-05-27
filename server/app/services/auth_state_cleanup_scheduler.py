"""Background scheduler for auth_state cleanup.

Uses a daemon `threading.Timer` chain so the cleanup runs every
`auth_state_cleanup_interval_minutes` minutes without adding a dependency
on APScheduler / Celery-beat.

In production (K8s / multi-pod) the scheduler is enabled on ONE pod only
(or replaced with an external cron running the CLI); otherwise every pod
would sweep in parallel. Control via the `auth_state_cleanup_enabled` flag.
"""
from __future__ import annotations

import logging
import threading
import time

from app.core.config import settings
from app.services.auth_state_cleanup_service import sweep_expired_auth_states

logger = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _loop() -> None:
    interval = max(60, settings.auth_state_cleanup_interval_minutes * 60)
    logger.info(
        "auth_cleanup_scheduler: started (interval=%ds, retention=%dh)",
        interval, settings.auth_state_retention_hours,
    )
    # First sweep: wait a short delay so server can finish boot
    first_delay = 30 if interval > 30 else interval
    if _stop_event.wait(first_delay):
        return

    while not _stop_event.is_set():
        try:
            result = sweep_expired_auth_states()
            logger.info(
                "auth_cleanup_scheduler: swept rows=%d files=%d skipped_active=%d",
                result["rows_deleted"], result["files_deleted"], result["rows_skipped_active_run"],
            )
        except Exception as exc:
            logger.exception("auth_cleanup_scheduler: sweep failed: %s", exc)
        # Sleep until next interval or stop signal
        if _stop_event.wait(interval):
            break

    logger.info("auth_cleanup_scheduler: stopped")


def start_scheduler() -> bool:
    """Start the background cleanup loop. Idempotent — a second call is a no-op.

    Returns True if a new thread was spawned, False if disabled or already running.
    """
    global _thread
    if not settings.auth_state_cleanup_enabled:
        logger.info("auth_cleanup_scheduler: disabled via config")
        return False
    if _thread and _thread.is_alive():
        return False
    _stop_event.clear()
    _thread = threading.Thread(
        target=_loop,
        name="auth-cleanup-scheduler",
        daemon=True,
    )
    _thread.start()
    return True


def stop_scheduler(timeout_s: float = 5.0) -> None:
    """Signal the loop to exit and wait briefly for the thread to finish."""
    global _thread
    _stop_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=timeout_s)
    _thread = None
