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
import re
import uuid
from typing import Literal

from app.services import mcp_server, state_store

logger = logging.getLogger(__name__)

ClassificationResult = Literal["PASS", "SCRIPT_ERROR", "APP_ERROR", "HUMAN_REVIEW"]


_NEGATIVE_INTENT_RE = re.compile(
    r"\b(invalid|negative|required|missing|empty|blank|locked|disabled|expired|"
    r"unauthori[sz]ed|forbidden|validation|reject|error message|cannot|should not)\b",
    re.IGNORECASE,
)
_INFRA_ERROR_RE = re.compile(
    r"(Missing env var|ECONNREFUSED|ERR_CONNECTION_REFUSED|net::ERR_(?!ABORTED\b)|"
    r"browser executable|Cannot find module|No such file|ENOTFOUND|EAI_AGAIN|"
    r"Timed out waiting for external|Test timed out$)",
    re.IGNORECASE,
)
_AUTH_ERROR_RE = re.compile(
    r"(storageState|AUTH_STATE_PATH|login|sign in|authenticated|unauthorized|forbidden|"
    r"401|403|Missing env var: USER_|Missing env var: ADMIN_)",
    re.IGNORECASE,
)
_REPAIRABLE_SCRIPT_RE = re.compile(
    r"(locator|strict mode|Timeout .*locator|waiting for selector|not visible|"
    r"waitForURL|navigation|Target page|Element is not|has no method|"
    r"ReferenceError|TypeError|SyntaxError|"
    r"Element is detached from the DOM|frame was detached|"
    r"Execution context was destroyed|page\.waitForSelector: Timeout|"
    r"Test timeout of \d+ms exceeded|net::ERR_ABORTED)",
    re.IGNORECASE | re.DOTALL,
)
_STATIC_ASSET_RE = re.compile(
    r"\.(?:jpg|jpeg|png|gif|svg|ico|webp|woff|woff2|ttf|eot|css|js|map)(?:\?.*)?$",
    re.IGNORECASE,
)
_STATIC_RESOURCE_TYPES = {"image", "font", "stylesheet", "script"}


def _status_code(log: dict) -> int:
    try:
        return int(log.get("status_code", log.get("status", 0)) or 0)
    except (TypeError, ValueError):
        return 0


def _is_static_asset_log(log: dict) -> bool:
    url = str(log.get("url") or "")
    resource_type = str(log.get("resourceType") or log.get("resource_type") or "").lower()
    if resource_type in _STATIC_RESOURCE_TYPES:
        return True
    path = url.split("?", 1)[0]
    return bool(_STATIC_ASSET_RE.search(path))


def _failing_network_logs(network_logs: list[dict]) -> list[dict]:
    return [
        log
        for log in network_logs
        if _status_code(log) >= 400 and not _is_static_asset_log(log)
    ]


def _has_app_error(network_logs: list[dict]) -> bool:
    return bool(_failing_network_logs(network_logs))


def _test_intent_text(test_id: str) -> str:
    try:
        from app.db.session import SessionLocal
        from app.models.phase3 import TestCase

        with SessionLocal() as db:
            tc = db.get(TestCase, uuid.UUID(test_id))
            if not tc:
                return ""
            return " ".join(
                [
                    tc.title or "",
                    " ".join(str(a) for a in (tc.acceptance_criteria or [])),
                ]
            )
    except Exception as exc:
        logger.warning("classifier: failed to load test intent for test_id=%s: %s", test_id, exc)
        return ""


def _is_negative_intent(test_id: str) -> bool:
    return bool(_NEGATIVE_INTENT_RE.search(_test_intent_text(test_id)))


def _is_expected_negative_network(test_id: str, failing_logs: list[dict]) -> bool:
    if not failing_logs or not _is_negative_intent(test_id):
        return False
    # 4xx can be expected for negative validation/auth tests. 5xx is never
    # expected QA behavior and remains APP_ERROR.
    return all(400 <= _status_code(log) < 500 for log in failing_logs)


def _looks_like_infra_error(error_log: str) -> bool:
    return bool(_INFRA_ERROR_RE.search(error_log or ""))


def _looks_like_auth_error(error_log: str, failing_logs: list[dict]) -> bool:
    if _AUTH_ERROR_RE.search(error_log or ""):
        return True
    return any(_status_code(log) in {401, 403} for log in failing_logs)


def _looks_repairable(error_log: str) -> bool:
    return bool(_REPAIRABLE_SCRIPT_RE.search(error_log or ""))


def _assertion_review_reason(error_log: str) -> str:
    text = (error_log or "").strip()
    if not text:
        return "Business assertion failed, but Playwright did not provide a detailed assertion message."

    expected = re.search(r"Expected:\s*([^\n\r]+)", text, re.IGNORECASE)
    received = re.search(r"Received:\s*([^\n\r]+)", text, re.IGNORECASE)
    if expected and received:
        return f"Business assertion mismatch: expected {expected.group(1).strip()}, received {received.group(1).strip()}."

    first_error_line = next(
        (
            line.strip()
            for line in text.splitlines()
            if line.strip()
            and not line.strip().startswith(("Call log:", "at "))
            and "node_modules" not in line
        ),
        "",
    )
    if first_error_line:
        return first_error_line[:240]
    return "Business assertion failed and needs tester review."


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
    failing_logs = _failing_network_logs(network_logs)

    if playwright_result != "FAIL" and _is_expected_negative_network(test_id, failing_logs):
        mcp_server.save_test_result(test_id=test_id, status="PASS", run_id=run_id, network_logs=network_logs)
        state_store.update_state(test_id, "PASS", run_id=run_id, network_logs=network_logs)
        logger.info("classifier: PASS expected negative network behavior test_id=%s", test_id)
        return "PASS"

    if playwright_result == "FAIL" and _looks_like_infra_error(error_log):
        mcp_server.save_test_result(test_id=test_id, status="HUMAN_REVIEW", run_id=run_id)
        _write_review_queue(
            test_id=test_id,
            run_id=run_id,
            review_type="TASK",
            evidence={"category": "INFRA_ERROR", "error_log": error_log[:1000]},
        )
        state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
        logger.info("classifier: INFRA_ERROR -> HUMAN_REVIEW for test_id=%s", test_id)
        return "HUMAN_REVIEW"

    if playwright_result == "FAIL" and _looks_like_auth_error(error_log, failing_logs) and not _is_negative_intent(test_id):
        mcp_server.save_test_result(
            test_id=test_id,
            status="HUMAN_REVIEW",
            run_id=run_id,
            network_logs=network_logs,
        )
        _write_review_queue(
            test_id=test_id,
            run_id=run_id,
            review_type="TASK",
            evidence={"category": "AUTH_ERROR", "failing_requests": failing_logs, "error_log": error_log[:1000]},
        )
        state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
        logger.info("classifier: AUTH_ERROR -> HUMAN_REVIEW for test_id=%s", test_id)
        return "HUMAN_REVIEW"

    # ── APP_ERROR: server-side failures detected in network traffic ──────────
    if failing_logs and not _is_expected_negative_network(test_id, failing_logs):
        mcp_server.save_test_result(
            test_id=test_id,
            status="APP_ERROR",
            run_id=run_id,
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
        state_store.update_state(test_id, "APP_ERROR", run_id=run_id)
        logger.info("classifier: APP_ERROR for test_id=%s failing_requests=%d", test_id, len(failing_logs))
        return "APP_ERROR"

    # ── SCRIPT_ERROR: Playwright itself failed, but no server errors ─────────
    if playwright_result == "FAIL":
        if not _looks_repairable(error_log):
            reason = _assertion_review_reason(error_log)
            mcp_server.save_test_result(test_id=test_id, status="HUMAN_REVIEW", run_id=run_id)
            _write_review_queue(
                test_id=test_id,
                run_id=run_id,
                review_type="TASK",
                evidence={
                    "category": "ASSERTION_REVIEW",
                    "reason": reason,
                    "classification_note": "Failure is not clearly repairable as selector/navigation/script issue.",
                    "error_log": error_log[:1000],
                },
            )
            state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)
            logger.info("classifier: non-repairable failure -> HUMAN_REVIEW for test_id=%s", test_id)
            return "HUMAN_REVIEW"

        # Both single AND grouped failures route to A7. Grouped tests now use
        # block-replace splicing (see agent7_retry._repair_grouped); A7 detects
        # grouped membership via Phase3HlsGroup so we don't need to thread a
        # flag through. The is_grouped argument is kept for future routing /
        # observability hooks but no longer controls the early HUMAN_REVIEW.
        state_store.update_state(test_id, "SCRIPT_ERROR", run_id=run_id)
        logger.info(
            "classifier: SCRIPT_ERROR for test_id=%s (grouped=%s) — routing to A7",
            test_id, is_grouped,
        )

        try:
            asyncio.run(_signal_retry(test_id, run_id, error_log))
        except Exception as exc:
            logger.error("classifier: failed to signal retry agent for test_id=%s: %s", test_id, exc)
            state_store.update_state(test_id, "HUMAN_REVIEW", run_id=run_id)

        return "SCRIPT_ERROR"

    # ── PASS ──────────────────────────────────────────────────────────────────
    mcp_server.save_test_result(
        test_id=test_id,
        status="PASS",
        run_id=run_id,
        network_logs=network_logs,
    )
    state_store.update_state(test_id, "PASS", run_id=run_id, network_logs=network_logs)
    mcp_server.mark_complete(test_id)
    logger.info("classifier: PASS for test_id=%s", test_id)
    return "PASS"


async def _signal_retry(test_id: str, run_id: str, error_log: str) -> None:
    from app.graph.phase3_graph import on_script_error_signal
    await on_script_error_signal(test_id, run_id, error_log)
