"""Phase 3 — Chromium Worker.

Each worker thread:
  1. Consumes a job message from RabbitMQ.
     - Plain UUID string  → single-test legacy path (unchanged)
     - 'hls:{hls_id}'    → grouped HLS path (test.describe.serial spec)
  2. Runs the Playwright .spec.ts via subprocess with timeout.
  3. Parses per-test() results from the JSON reporter.
  4. For grouped runs: classifies each subtask individually.
     - PASS: normal
     - FAIL: routed through A6 Classifier (APP_ERROR / SCRIPT_ERROR → A7)
     - skipped: marked BLOCKED with blocked_by pointing to the first failed subtask
  5. ACKs the message when done.

Workers run as threads (ThreadPoolExecutor) inside the same FastAPI process.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pika

from app.core.config import settings
from app.services import mcp_server, state_store

logger = logging.getLogger(__name__)

_SENTINEL = "__STOP__"
_IDLE_TIMEOUT_S = 15
_POLL_INTERVAL_S = 1

# Prefix used to distinguish grouped HLS messages from single-test messages
_HLS_PREFIX = "hls:"


def _rabbitmq_channel() -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    """Private per-worker connection — separate from the producer pool."""
    # Append heartbeat=0 to prevent connection drops during long 60s Playwright timeouts
    url = settings.rabbitmq_url
    if "?" not in url:
        url += "?heartbeat=0"
    elif "heartbeat=" not in url:
        url += "&heartbeat=0"
        
    conn = pika.BlockingConnection(pika.URLParameters(url))
    ch = conn.channel()
    ch.queue_declare(queue=settings.rabbitmq_queue, durable=True)
    ch.basic_qos(prefetch_count=1)
    return conn, ch


# ── Subprocess execution ──────────────────────────────────────────────────────


def _build_env() -> dict[str, str]:
    """Build subprocess environment with credentials forwarded."""
    env = os.environ.copy()
    env["PLAYWRIGHT_HEADED"] = "true" if settings.playwright_headed else "false"
    env["BASE_URL"] = settings.base_url
    env["USER_EMAIL"] = settings.user_email
    env["USER_PASSWORD"] = settings.user_password
    env["ADMIN_EMAIL"] = settings.admin_email
    env["ADMIN_PASSWORD"] = settings.admin_password
    return env


def _run_spec(script_path: Path) -> dict[str, Any]:
    """Execute a .spec.ts file via npx playwright test --reporter=json.

    Returns:
        {
          "exit_code": int,
          "stdout": str,
          "stderr": str,
          "report": dict,   # parsed JSON reporter output (may be {})
        }
    """
    timeout_s = settings.test_timeout_ms / 1000
    traces_dir = script_path.parent / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    try:
        npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
        proc = subprocess.run(
            [
                npx_cmd, "playwright", "test",
                script_path.as_posix(),
                "--reporter=json",
                "--trace=on-first-retry",
                f"--output={traces_dir}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
            env=_build_env(),
            cwd=str(script_path.parent.parent.parent),   # server/ (where playwright.config.ts lives)
        )
    except subprocess.TimeoutExpired:
        logger.warning("worker: timeout for %s after %.0fs", script_path.name, timeout_s)
        return {"exit_code": -1, "stdout": "", "stderr": "Test timed out", "report": {}}
    except Exception as exc:
        logger.exception("worker: subprocess error for %s: %s", script_path.name, exc)
        return {"exit_code": -1, "stdout": "", "stderr": str(exc), "report": {}}

    report: dict[str, Any] = {}
    try:
        report = json.loads(proc.stdout)
    except Exception:
        pass

    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:4000],
        "stderr": proc.stderr[:2000],
        "report": report,
    }


# ── JSON reporter parsing ─────────────────────────────────────────────────────


def _walk_specs(report: dict[str, Any]):
    """Yield (title, playwright_status, error_message, network_logs, trace_path) per test()."""
    for top_suite in report.get("suites", []):
        suites = [top_suite] + top_suite.get("suites", [])
        for suite in suites:
            for spec in suite.get("specs", []):
                title = spec.get("title", "")
                tests = spec.get("tests", [{}])
                test = tests[0] if tests else {}
                pw_status = test.get("status", "skipped")  # passed | failed | skipped

                error_message = ""
                network_logs: list[dict] = []
                trace_path: str | None = None

                for result in test.get("results", []):
                    err = result.get("error") or {}
                    if err and not error_message:
                        error_message = err.get("message", "")[:500]
                    for attachment in result.get("attachments", []):
                        name = attachment.get("name", "")
                        if name == "network_logs":
                            try:
                                network_logs = json.loads(attachment.get("body", "[]"))
                            except Exception:
                                pass
                        if name == "trace" and attachment.get("path"):
                            trace_path = attachment["path"]

                yield title, pw_status, error_message, network_logs, trace_path


# ── Single-test path (legacy) ─────────────────────────────────────────────────


def run_playwright_spec(test_id: str) -> dict[str, Any]:
    """Run a single {test_id}.spec.ts. Returns legacy result dict for A6."""
    script_path = Path(settings.generated_scripts_dir) / f"{test_id}.spec.ts"
    if not script_path.exists():
        logger.error("worker: script not found for test_id=%s", test_id)
        # Return APP_ERROR instead of FAIL to prevent infinite Agent 7 repair loops
        return {"status": "APP_ERROR", "exit_code": -1, "stdout": "", "stderr": "Script file not found", "network_logs": []}

    run = _run_spec(script_path)
    status = "PASS" if run["exit_code"] == 0 else "FAIL"

    # Extract first error + network logs from single-test report
    error_message = ""
    network_logs: list[dict] = []
    for _title, _pw, err, logs, trace in _walk_specs(run["report"]):
        if err and not error_message:
            error_message = err
        network_logs.extend(logs)
        if trace:
            state_store.update_state(test_id, status, trace_path=trace)

    return {
        "status": status,
        "exit_code": run["exit_code"],
        "stdout": run["stdout"],
        "stderr": run["stderr"],
        "error_message": error_message,
        "network_logs": network_logs,
    }


# ── Grouped HLS path ──────────────────────────────────────────────────────────


def _run_grouped_spec(hls_id: str, run_id: str) -> None:
    """Run {hls_id}.spec.ts and write one TestResult per subtask.

    Matches Playwright test() results to test_ids by position (index), NOT by title.
    The LLM may rephrase test titles; positional matching is always correct because
    A5 generates test() blocks in the same order as the contexts list.

    Classification per subtask:
      Playwright passed  → PASS
      Playwright failed  → A6 Classifier (APP_ERROR | SCRIPT_ERROR → A7)
      Playwright skipped → BLOCKED (blocked_by = first_failed_test_id)

    BLOCKED tests never trigger A7 or create review queue entries.
    """
    from app.agents.agent6_classifier import classify

    script_path = Path(settings.generated_scripts_dir) / f"{hls_id}.spec.ts"
    if not script_path.exists():
        logger.error("worker: grouped script not found for hls_id=%s", hls_id)
        return

    # Retrieve ordered test_ids stored at group creation time
    ordered_test_ids: list[str] = state_store.get_hls_group(hls_id) or []
    if not ordered_test_ids:
        logger.error(
            "worker: no HLS group found in state_store for hls_id=%s — "
            "state may have been cleared after a server restart. Re-trigger the run.",
            hls_id,
        )
        return

    run = _run_spec(script_path)
    results = list(_walk_specs(run["report"]))

    if not results:
        logger.warning(
            "worker: Playwright returned 0 test results for hls_id=%s "
            "(exit_code=%d). stderr: %s",
            hls_id, run["exit_code"], run["stderr"][:500],
        )
        # Mark all subtasks HUMAN_REVIEW so they surface in the review queue
        for tid in ordered_test_ids:
            state_store.update_state(tid, "HUMAN_REVIEW")
        return

    first_failed_id: str | None = None

    for idx, (title, pw_status, error_message, network_logs, trace) in enumerate(results):
        if idx >= len(ordered_test_ids):
            logger.warning(
                "worker: more test() results (%d) than registered subtasks (%d) for hls_id=%s — ignoring extras",
                len(results), len(ordered_test_ids), hls_id,
            )
            break

        test_id = ordered_test_ids[idx]
        logger.debug("worker: hls_id=%s idx=%d title=%r pw_status=%s test_id=%s", hls_id, idx, title, pw_status, test_id)

        for log in network_logs:
            state_store.append_network_log(test_id, log)

        if pw_status == "passed":
            state_store.update_state(test_id, "PASS", trace_path=trace)
            mcp_server.mark_complete(test_id)
            logger.info("worker: PASS test_id=%s title=%r", test_id, title)

        elif pw_status == "failed":
            if not first_failed_id:
                first_failed_id = test_id
            if trace:
                state_store.update_state(test_id, "SCRIPT_ERROR", trace_path=trace)
            classify(
                test_id=test_id,
                run_id=run_id,
                playwright_result=pw_status,
                network_logs=network_logs,
                error_log=error_message,
                is_grouped=True,
            )
            logger.info("worker: FAIL test_id=%s title=%r", test_id, title)

        else:  # skipped — blocked by upstream OR beforeAll crashed
            if first_failed_id is None:
                # No prior failure: beforeAll failed (TS compile error, context crash, etc.)
                # Mark first skipped as SCRIPT_ERROR so it surfaces in review queue.
                first_failed_id = test_id
                classify(
                    test_id=test_id,
                    run_id=run_id,
                    playwright_result="FAIL",
                    network_logs=network_logs,
                    error_log=error_message or run["stderr"][:300] or "beforeAll hook failed — all tests skipped",
                )
                logger.warning(
                    "worker: beforeAll FAIL -> first skipped = SCRIPT_ERROR: test_id=%s title=%r",
                    test_id, title,
                )
            else:
                state_store.update_state(test_id, "BLOCKED", blocked_by=first_failed_id)
                logger.info("worker: BLOCKED test_id=%s title=%r (blocked_by=%s)", test_id, title, first_failed_id)


# ── Worker loop ───────────────────────────────────────────────────────────────


def worker_loop(run_id: str) -> None:
    """Blocking worker loop — runs in a ThreadPoolExecutor thread.

    Polls RabbitMQ with basic_get. Exits after IDLE_TIMEOUT_S consecutive
    seconds of empty queue (long enough for A7-repaired scripts to be re-enqueued).
    """
    from app.agents.agent6_classifier import classify

    logger.info("worker_loop started for run_id=%s", run_id)

    try:
        conn, ch = _rabbitmq_channel()
    except Exception as exc:
        logger.error("worker_loop: cannot connect to RabbitMQ: %s", exc)
        return

    idle_s = 0
    try:
        while idle_s < _IDLE_TIMEOUT_S:
            method_frame, _props, body = ch.basic_get(
                queue=settings.rabbitmq_queue, auto_ack=False
            )

            if body is None:
                idle_s += _POLL_INTERVAL_S
                time.sleep(_POLL_INTERVAL_S)
                continue

            idle_s = 0
            message = body.decode().strip()

            if message == _SENTINEL:
                ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                break

            try:
                if message.startswith(_HLS_PREFIX):
                    # ── Grouped HLS path ──────────────────────────────────
                    hls_id = message[len(_HLS_PREFIX):]
                    logger.debug("worker: processing grouped hls_id=%s", hls_id)
                    _run_grouped_spec(hls_id, run_id)

                else:
                    # ── Single-test legacy path ───────────────────────────
                    test_id = message
                    logger.debug("worker: processing test_id=%s", test_id)

                    if not mcp_server.check_dependencies(test_id):
                        logger.debug("worker: dependencies not met for test_id=%s — requeuing", test_id)
                        ch.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=False)
                        mcp_server.requeue(test_id, delay_s=settings.requeue_delay_ms // 1000)
                        continue

                    result = run_playwright_spec(test_id)

                    for log in result.get("network_logs", []):
                        state_store.append_network_log(test_id, log)

                    classify(
                        test_id=test_id,
                        run_id=run_id,
                        playwright_result=result["status"],
                        network_logs=result.get("network_logs", []),
                        error_log=result.get("error_message") or result.get("stderr", ""),
                    )

                ch.basic_ack(delivery_tag=method_frame.delivery_tag)

            except Exception as exc:
                logger.exception("worker: unhandled error for message '%s': %s", message, exc)
                # For single tests, mark HUMAN_REVIEW; for groups, individual subtasks
                # are already classified inside _run_grouped_spec
                if not message.startswith(_HLS_PREFIX):
                    state_store.update_state(message, "HUMAN_REVIEW")
                ch.basic_ack(delivery_tag=method_frame.delivery_tag)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    logger.info("worker_loop finished for run_id=%s", run_id)
