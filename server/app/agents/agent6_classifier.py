"""Agent A6 — Classifier (rule-based, no LLM).

Classification logic:
  - Any network log with status_code >= 400  → APP_ERROR → review_queue (BUG)
  - Playwright result == FAIL, no 4xx/5xx   → SCRIPT_ERROR → signal Retry Agent (A7)
  - All pass                                 → PASS → update state + mark_complete

Entry point: classify(test_id, run_id, playwright_result, network_logs, error_log)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Literal

from app.services import mcp_server, state_store

logger = logging.getLogger(__name__)

ClassificationResult = Literal["PASS", "SCRIPT_ERROR", "APP_ERROR"]


def _has_app_error(network_logs: list[dict]) -> bool:
    return any(log.get("status_code", 0) >= 400 for log in network_logs)


def _write_review_queue(test_id: str, run_id: str, review_type: str, evidence: dict) -> None:
    from app.db.session import SessionLocal
    from app.models.phase3 import ReviewQueueItem

    with SessionLocal() as db:
        item = ReviewQueueItem(
            id=uuid.uuid4(),
            test_id=uuid.UUID(test_id),
            run_id=uuid.UUID(run_id),
            review_type=review_type,
            evidence=evidence,
            status="pending",
        )
        db.add(item)
        db.commit()
    logger.info("classifier: review_queue entry created test_id=%s type=%s", test_id, review_type)


def classify(
    test_id: str,
    run_id: str,
    playwright_result: str,
    network_logs: list[dict],
    error_log: str = "",
    is_grouped: bool = False,
) -> ClassificationResult:
    """Classify a test result and take appropriate action."""

    # ── APP_ERROR: server-side failures detected in network traffic ──────────
    if _has_app_error(network_logs):
        failing_logs = [log for log in network_logs if log.get("status_code", 0) >= 400]
        mcp_server.save_test_result(
            test_id=test_id,
            status="APP_ERROR",
            network_logs=network_logs,
        )
        _write_review_queue(
            test_id=test_id,
            run_id=run_id,
            review_type="BUG",
            evidence={
                "failing_requests": failing_logs,
                "error_log": error_log[:500],
            },
        )
        state_store.update_state(test_id, "APP_ERROR")
        logger.info("classifier: APP_ERROR for test_id=%s failing_requests=%d", test_id, len(failing_logs))
        return "APP_ERROR"

    # ── SCRIPT_ERROR: Playwright itself failed, but no server errors ─────────
    if playwright_result == "FAIL":
        if is_grouped:
            # A7 auto-repair does not currently support rewriting individual blocks inside a grouped HLS script.
            # Mark it for HUMAN_REVIEW directly to avoid breaking the script structure.
            state_store.update_state(test_id, "HUMAN_REVIEW")
            logger.info("classifier: SCRIPT_ERROR for grouped test_id=%s — marking HUMAN_REVIEW (skipping A7)", test_id)
            _write_review_queue(
                test_id=test_id,
                run_id=run_id,
                review_type="TASK",
                evidence={"error_log": error_log[:500]},
            )
            return "HUMAN_REVIEW"

        state_store.update_state(test_id, "SCRIPT_ERROR")
        logger.info("classifier: SCRIPT_ERROR for test_id=%s — routing to A7", test_id)

        try:
            asyncio.run(_signal_retry(test_id, run_id, error_log))
        except Exception as exc:
            logger.error("classifier: failed to signal retry agent for test_id=%s: %s", test_id, exc)
            state_store.update_state(test_id, "HUMAN_REVIEW")

        return "SCRIPT_ERROR"

    # ── PASS ──────────────────────────────────────────────────────────────────
    state_store.update_state(test_id, "PASS", network_logs=network_logs)
    mcp_server.mark_complete(test_id)
    logger.info("classifier: PASS for test_id=%s", test_id)
    return "PASS"


async def _signal_retry(test_id: str, run_id: str, error_log: str) -> None:
    from app.graph.phase3_graph import on_script_error_signal
    await on_script_error_signal(test_id, run_id, error_log)
