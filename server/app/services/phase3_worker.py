"""Phase 3 â€” Chromium Worker.

Each worker thread:
  1. Consumes a run-scoped JSON job message from RabbitMQ.
  2. Runs the Playwright .spec.ts via subprocess with timeout.
  3. Parses per-test() results from the JSON reporter.
  4. For grouped runs: classifies each subtask individually.
     - PASS: normal
     - FAIL: routed through A6 Classifier (APP_ERROR / SCRIPT_ERROR â†’ A7)
     - skipped: marked BLOCKED with blocked_by pointing to the first failed subtask
  5. ACKs the message when done.

Workers run as threads (ThreadPoolExecutor) inside the same FastAPI process.
"""
from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import sys
import time
import uuid
import argparse
from pathlib import Path
from typing import Any

import pika

from app.core.config import settings
from app.services import mcp_server, state_store
from app.services.job_claim_service import mark_job_completed, try_claim_job
from app.services.phase3_jobs import job_script_path, parse_job, serialize_job
from app.services.artifact_paths import generated_base, upsert_manifest_entry, traces_dir_from_script, traces_dir_for_test
from app.services.artifact_registry import register_artifact
from app.services.queue_topology import declare_topology, republish_with_attempt

logger = logging.getLogger(__name__)

_SENTINEL = "__STOP__"
_IDLE_TIMEOUT_S = 15
_POLL_INTERVAL_S = 1
_DEFAULT_PLAYWRIGHT_TIMEOUT_MS = 30_000
_HEADED_DEMO_MIN_TIMEOUT_MS = 120_000
_SLOW_MO_ACTION_BUDGET = 40
# Backoff cap for consecutive foreign-run jobs. Without backoff, a worker
# bound to run A repeatedly NACK+requeues jobs for run B at the poll interval,
# burning RabbitMQ I/O. We grow the sleep exponentially up to this cap (with
# jitter) so concurrent runs converge to fair sharing instead of churn.
_FOREIGN_JOB_BACKOFF_MAX_S = 5.0


def _server_cwd() -> str:
    """Return the server project directory regardless of script nesting depth."""
    return str(Path(__file__).resolve().parents[2])


def _rabbitmq_channel() -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    """Private per-worker connection â€” separate from the producer pool."""
    # Append heartbeat=0 to prevent connection drops during long 60s Playwright timeouts

    url = settings.rabbitmq_url
    if "?" not in url:
        url += "?heartbeat=0"
    elif "heartbeat=" not in url:
        url += "&heartbeat=0"

    conn = pika.BlockingConnection(pika.URLParameters(url))
    ch = conn.channel()
    declare_topology(ch)
    ch.basic_qos(prefetch_count=1)
    return conn, ch


def _run_is_active(run_id: str) -> bool | None:
    """Return True for running, False for inactive/missing, None when DB status is unknown."""
    try:
        run_uuid = uuid.UUID(run_id)
    except (TypeError, ValueError):
        logger.warning("worker: invalid run_id in job: %s", run_id)
        return False
    try:
        from sqlalchemy import select
        from app.db.session import SessionLocal
        from app.models.phase3 import TestRun

        with SessionLocal() as db:
            status_value = db.execute(
                select(TestRun.status).where(TestRun.run_id == run_uuid)
            ).scalar_one_or_none()
        return status_value == "running"
    except Exception as exc:
        logger.warning("worker: failed to check run status for run_id=%s: %s", run_id, exc)
        return None


# â”€â”€ Subprocess execution â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _build_env(
    run_id: str | None = None,
    *,
    storage_state_path: str | None = None,
    test_id: str | None = None,
    credential_id: str | None = None,
    plan_run_id: str | None = None,
) -> dict[str, str]:
    """Build subprocess environment with DB credential-profile values.

    App credentials intentionally come from uploaded CSV -> CredentialProfile
    rows, not server/.env and not prebuilt Playwright storageState files.
    """
    env = os.environ.copy()
    env.pop("AUTH_STATE_PATH", None)
    env["PLAYWRIGHT_HEADED"] = "true" if settings.playwright_headed else "false"
    env["PLAYWRIGHT_SLOW_MO_MS"] = str(settings.playwright_slow_mo_ms)
    logger.info(
        "worker: PLAYWRIGHT_HEADED=%s slowMo=%sms",
        env["PLAYWRIGHT_HEADED"], env["PLAYWRIGHT_SLOW_MO_MS"],
    )
    # Per-test timeout — read by playwright.config.ts. Decoupled from the
    # subprocess kill timer below so a hung selector bails fast (default 30s)
    # without consuming the whole suite's budget.
    test_timeout_ms = settings.resolved_playwright_test_timeout_ms
    if settings.playwright_headed and test_timeout_ms <= _DEFAULT_PLAYWRIGHT_TIMEOUT_MS:
        # Headed/demo mode slows every action so humans can watch execution.
        # A valid multi-step business flow can exceed 30s purely from slowMo,
        # so lift only the default timeout. Explicit timeout config still wins.
        slow_mo_budget = settings.playwright_slow_mo_ms * _SLOW_MO_ACTION_BUDGET
        test_timeout_ms = max(test_timeout_ms, _HEADED_DEMO_MIN_TIMEOUT_MS, slow_mo_budget)
    env["PLAYWRIGHT_TEST_TIMEOUT_MS"] = str(test_timeout_ms)
    env["BASE_URL"] = settings.base_url
    # Legacy app-user .env values are intentionally not forwarded. Phase 3 app
    # credentials must be project-scoped CredentialProfile rows.
    for key in (
        "USER_EMAIL",
        "USER_PASSWORD",
        "ADMIN_EMAIL",
        "ADMIN_PASSWORD",
        "AUTH_STATE_PATH",
    ):
        env.pop(key, None)
    if run_id:
        try:
            from sqlalchemy import select
            from app.db.session import SessionLocal
            from app.models.phase3 import TestCase, TestRun
            from app.models.project import CredentialProfile, Project
            from app.services.credential_service import get_profile_password

            with SessionLocal() as db:
                project_row = db.execute(
                    select(Project)
                    .join(TestRun, TestRun.project_id == Project.id)
                    .where(TestRun.run_id == uuid.UUID(run_id))
                    .limit(1)
                ).scalar_one_or_none()
                if project_row and (project_row.url or "").strip():
                    env["BASE_URL"] = project_row.url.rstrip("/")

                tc_row: TestCase | None = None
                if test_id:
                    tc_query = select(TestCase).where(TestCase.test_id == uuid.UUID(test_id))
                    if plan_run_id:
                        tc_query = tc_query.where(TestCase.run_id == uuid.UUID(plan_run_id))
                    tc_row = db.execute(tc_query.limit(1)).scalar_one_or_none()

                    if plan_run_id and tc_row is None:
                        raise RuntimeError(
                            "No Phase 3 test case found for this planning run "
                            f"(test_id={test_id}, plan_run_id={plan_run_id}, execute_run_id={run_id})"
                        )

                resolved_credential_id = credential_id
                if not resolved_credential_id and tc_row is not None:
                    resolved_credential_id = tc_row.credential_id
                    resolved_credential_id = str(resolved_credential_id) if resolved_credential_id else None

                if not resolved_credential_id:
                    if tc_row is not None and (tc_row.auth_mode or "authenticated").lower() in {"authenticated", "login_flow"}:
                        raise RuntimeError(
                            "Credentialed Phase 3 test has no bound project credential "
                            f"(test_id={test_id}, plan_run_id={plan_run_id}, auth_mode={tc_row.auth_mode})"
                        )
                    return env

                row = db.execute(
                    select(CredentialProfile, Project)
                    .join(Project, CredentialProfile.project_id == Project.id)
                    .where(CredentialProfile.id == uuid.UUID(str(resolved_credential_id)))
                    .limit(1)
                ).first()
                if row is None:
                    raise RuntimeError(
                        "No credential profile found for the required Phase 3 test "
                        f"(run_id={run_id}, test_id={test_id}, credential_id={resolved_credential_id})"
                    )
                profile, project = row
                env["BASE_URL"] = (profile.endpoint or project.url or settings.base_url).rstrip("/")
                env["TEST_USERNAME"] = profile.username
                env["TEST_PASSWORD"] = get_profile_password(profile)
                env["TEST_ROLE"] = profile.role or ""
                env["TEST_LOGIN_URL"] = (profile.endpoint or project.url or settings.base_url).rstrip("/")
        except Exception as exc:
            if test_id or credential_id:
                raise
            logger.warning("worker: failed to load run-scoped env for run_id=%s: %s", run_id, exc)
    return env


def _run_spec(
    script_path: Path,
    run_id: str | None = None,
    *,
    storage_state_path: str | None = None,
    test_id: str | None = None,
    credential_id: str | None = None,
    plan_run_id: str | None = None,
) -> dict[str, Any]:
    """Execute a .spec.ts file via npx playwright test --reporter=json.

    Returns:
        {
          "exit_code": int,
          "stdout": str,
          "stderr": str,
          "report": dict,   # parsed JSON reporter output (may be {})
        }

    Output directory is scoped to the individual test_id when available so that
    concurrent workers in the same run never overwrite each other's trace.zip or
    assertion_screenshot.png files (the overwrite bug fix).
    """
    # Subprocess wallclock — generous (default 600s) so a 6-test serial suite
    # with one or two hung tests can still complete and emit JSON results.
    # Decoupled from the per-test timeout (PLAYWRIGHT_TEST_TIMEOUT_MS, ~30s)
    # passed via env to playwright.config.ts.
    timeout_s = settings.resolved_worker_subprocess_timeout_ms / 1000

    # ── Per-test scoped output dir (overwrite bug fix) ─────────────────────────
    # When test_id is available (single_test jobs), each test gets its own
    # subdirectory:  <run_dir>/traces/<test_id_short>/
    # Grouped jobs fall back to the shared run-level traces dir because they
    # run multiple test() blocks in one Playwright process and A6 handles
    # individual trace paths via reporter attachment.
    if test_id:
        output_dir = traces_dir_for_test(script_path, test_id)
        # Tell the generated .spec.ts where to save the assertion screenshot.
        screenshot_env_path = str(output_dir / "assertion_screenshot.png")
    else:
        output_dir = traces_dir_from_script(script_path)
        screenshot_env_path = ""

    try:
        env = _build_env(
            run_id,
            storage_state_path=storage_state_path,
            test_id=test_id,
            credential_id=credential_id,
            plan_run_id=plan_run_id,
        )
        # Inject the screenshot path so page.screenshot({ path: process.env.SQAT_SCREENSHOT_PATH })
        # inside the generated .spec.ts resolves to a unique, non-conflicting path.
        if screenshot_env_path:
            env["SQAT_SCREENSHOT_PATH"] = screenshot_env_path

        npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
        proc = subprocess.run(
            [
                npx_cmd, "playwright", "test",
                script_path.as_posix(),
                "--reporter=json",
                # retain-on-failure: keep trace for failed tests without
                # requiring PW retries (we set retries=0; A7 retries at agent level).
                "--trace=retain-on-failure",
                f"--output={output_dir}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
            env=env,
            cwd=_server_cwd(),   # server/ (where playwright.config.ts lives)
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
        # Surface the expected screenshot path so the caller can check existence
        # after determining the test outcome (PASS → register, FAIL → skip).
        "screenshot_env_path": screenshot_env_path,
    }


# â”€â”€ JSON reporter parsing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _walk_specs(report: dict[str, Any]):
    """Yield (title, playwright_status, error_message, network_logs, trace_path) per test()."""
    for top_suite in report.get("suites", []):
        suites = [top_suite] + top_suite.get("suites", [])
        for suite in suites:
            for spec in suite.get("specs", []):
                title = spec.get("title", "")
                tests = spec.get("tests", [{}])
                test = tests[0] if tests else {}
                # Playwright's JSON reporter exposes TWO status fields:
                #   test.status             — outcome vs expectation:
                #                             "expected" | "unexpected" | "flaky" | "skipped"
                #   test.results[0].status  — actual run status:
                #                             "passed" | "failed" | "timedOut" | "skipped" | "interrupted"
                # The downstream branches below key off "passed"/"failed"/"skipped",
                # so we must read the run-level status. Reading test.status caused
                # every grouped subtask to look "skipped" → beforeAll-FAIL mis-classification.
                results_list = test.get("results") or [{}]
                run_status = (results_list[0] or {}).get("status", "skipped")
                # Normalize timeouts and interruptions to "failed" so A6 can triage them.
                pw_status = "failed" if run_status in ("timedOut", "interrupted") else run_status

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
                                raw_body = attachment.get("body", "")
                                if raw_body:
                                    import base64
                                    try:
                                        decoded = base64.b64decode(raw_body).decode("utf-8")
                                        network_logs = json.loads(decoded)
                                    except Exception:
                                        network_logs = json.loads(raw_body)
                            except Exception:
                                pass
                        if name == "trace" and attachment.get("path"):
                            trace_path = attachment["path"]

                yield title, pw_status, error_message, network_logs, trace_path



# â”€â”€ Grouped HLS path â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _register_pass_screenshot(
    test_id: str,
    run_id: str,
    screenshot_path: str,
    *,
    project_id: str,
) -> None:
    """Persist the assertion screenshot produced by a passing single_test job.

    The screenshot is captured inside the .spec.ts via:
        await page.screenshot({ path: process.env.SQAT_SCREENSHOT_PATH });
    injected right after the final expect() call by Agent A5.
    """
    p = Path(screenshot_path)
    if not p.exists():
        logger.debug(
            "worker: assertion screenshot not found at %s for test_id=%s — skipping",
            screenshot_path, test_id,
        )
        return
    try:
        state_store.update_state(
            test_id,
            "PASS",
            run_id=run_id,
            screenshot_path=screenshot_path,
        )
        if project_id:
            from app.services.artifact_registry import register_artifact
            register_artifact(
                project_id=project_id,
                run_id=run_id,
                test_id=test_id,
                artifact_type="SCREENSHOT",
                path=p,
            )
        logger.info(
            "worker: assertion screenshot registered test_id=%s path=%s",
            test_id, screenshot_path,
        )
    except Exception as exc:
        logger.warning(
            "worker: failed to register assertion screenshot for test_id=%s: %s",
            test_id, exc,
        )


def _classify_single_run_result(
    test_id: str,
    run_id: str,
    run: dict[str, Any],
    *,
    project_id: str = "",
) -> str:
    """Parse a JSON reporter result for one spec and send it through A6."""
    from app.agents.agent6_classifier import classify

    error_message = ""
    network_logs: list[dict] = []
    trace_path: str | None = None

    for _title, _pw_status, err, logs, trace in _walk_specs(run.get("report", {})):
        if err and not error_message:
            error_message = err
        network_logs.extend(logs)
        if trace:
            trace_path = trace

    for log in network_logs:
        state_store.append_network_log(test_id, log)

    is_pass = run.get("exit_code") == 0

    if is_pass:
        # On PASS: store the assertion screenshot (no trace needed).
        screenshot_path = run.get("screenshot_env_path") or ""
        if screenshot_path:
            _register_pass_screenshot(
                test_id, run_id, screenshot_path, project_id=project_id
            )
        else:
            state_store.update_state(test_id, "PASS", run_id=run_id)
    elif trace_path:
        # On FAIL: persist the trace path so A6/A7 and the UI can access it.
        state_store.update_state(
            test_id,
            "PENDING",
            run_id=run_id,
            trace_path=trace_path,
        )

    return classify(
        test_id=test_id,
        run_id=run_id,
        playwright_result="PASS" if is_pass else "FAIL",
        network_logs=network_logs,
        error_log=error_message or run.get("stderr", ""),
    )


def _update_review_rerun_status(review_item_id: str | None, classification: str) -> None:
    if not review_item_id:
        return
    try:
        from app.db.session import SessionLocal
        from app.models.phase3 import ReviewQueueItem

        with SessionLocal() as db:
            item = db.get(ReviewQueueItem, uuid.UUID(str(review_item_id)))
            if item:
                item.status = "resolved" if classification == "PASS" else "pending"
                db.commit()
    except Exception as exc:
        logger.warning(
            "worker: failed to update rerun review item %s after classification=%s: %s",
            review_item_id,
            classification,
            exc,
        )


def _write_worker_review_queue(
    test_id: str,
    run_id: str,
    *,
    category: str,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    """Create a durable review item for worker-level failures."""
    try:
        from app.db.session import SessionLocal
        from app.models.phase3 import ReviewQueueItem

        payload = {
            "category": category,
            "reason": reason,
            **(evidence or {}),
        }
        with SessionLocal() as db:
            db.add(ReviewQueueItem(
                test_id=uuid.UUID(str(test_id)),
                run_id=uuid.UUID(str(run_id)),
                review_type="TASK",
                evidence=payload,
                status="pending",
            ))
            db.commit()
    except Exception as exc:
        logger.warning(
            "worker: failed to create review item for test_id=%s run_id=%s category=%s: %s",
            test_id, run_id, category, exc,
        )


def _update_artifact_manifest(
    *,
    project_id: str,
    run_id: str,
    test_id: str,
    script_path: Path,
    classification: str,
) -> None:
    try:
        with SessionLocal() as db:
            tc = db.get(TestCase, uuid.UUID(test_id))
            upsert_manifest_entry(project_id, run_id, {
                "test_id": test_id,
                "tc_number": tc.tc_number if tc else "",
                "title": tc.title if tc else "",
                "status": classification,
                "script_path": str(script_path),
                "trace_path": (state_store.get_status(test_id) or {}).get("trace_path"),
            })
    except Exception:
        logger.debug("worker: failed to update artifact manifest for test_id=%s", test_id, exc_info=True)


def _register_playwright_artifacts(
    *,
    project_id: str,
    run_id: str,
    test_id: str,
    script_path: Path,
) -> None:
    """Register any Playwright artifacts (traces, videos) found in the test-scoped dir.

    For single_test jobs this is called AFTER _classify_single_run_result() which
    already handles assertion screenshots on PASS. Here we pick up traces (FAIL) and
    any video files (headed mode). PNG screenshots that are assertion screenshots
    were already registered by _register_pass_screenshot(); we skip them here to
    avoid double-registration.
    """
    if not project_id:
        return
    # Prefer the test-scoped directory; fall back to the run-level traces dir.
    scoped_dir = traces_dir_for_test(script_path, test_id)
    search_dir = scoped_dir if scoped_dir.exists() else traces_dir_from_script(script_path)
    if not search_dir.exists():
        return
    type_by_suffix = {
        ".zip": "TRACE",
        ".webm": "VIDEO",
        ".json": "REPORT",
        # NOTE: .png excluded here — assertion screenshots are registered by
        # _register_pass_screenshot() to avoid double-registration.
    }
    for file in search_dir.rglob("*"):
        if not file.is_file():
            continue
        # Skip assertion screenshots already handled by _register_pass_screenshot()
        if file.name == "assertion_screenshot.png":
            continue
        artifact_type = type_by_suffix.get(file.suffix.lower())
        if not artifact_type:
            continue
        register_artifact(
            project_id=project_id,
            run_id=run_id,
            test_id=test_id,
            artifact_type=artifact_type,
            path=file,
        )


def _handle_job_failure(
    ch: pika.adapters.blocking_connection.BlockingChannel,
    delivery_tag: int,
    job: dict[str, Any],
    exc: Exception,
) -> None:
    """Retry-with-backoff or dead-letter a failed job.

    Behaviour:
      attempt < MAX  →  republish with attempt+1, ACK original
      attempt >= MAX →  basic_reject(requeue=False) → RabbitMQ routes to DLX
                         and we also mark the job HUMAN_REVIEW in state_store.
    """
    attempt = int(job.get("attempt") or 1)
    max_attempts = int(settings.phase3_max_attempts or 3)

    if attempt < max_attempts:
        next_job = dict(job)
        next_job["attempt"] = attempt + 1
        # Generate a fresh job_id for the retry so it does NOT collide with
        # the claim lock of the failed attempt. The original job_id is kept
        # as `retry_of` for traceability.
        next_job["retry_of"] = job.get("job_id")
        next_job["job_id"] = str(uuid.uuid4())
        try:
            republish_with_attempt(ch, serialize_job(next_job).encode())
            ch.basic_ack(delivery_tag=delivery_tag)
            mark_job_completed(str(job.get("job_id")), status="retried", error=str(exc)[:500])
            logger.warning(
                "worker: job_id=%s retried as %s (attempt=%d/%d) after error: %s",
                job.get("job_id"), next_job["job_id"], attempt + 1, max_attempts, exc,
            )
            return
        except Exception as republish_exc:  # pragma: no cover
            logger.error(
                "worker: republish failed for job_id=%s: %s — falling through to DLX",
                job.get("job_id"), republish_exc,
            )

    # Terminal: mark HUMAN_REVIEW and dead-letter
    _mark_job_human_review(job)
    ch.basic_reject(delivery_tag=delivery_tag, requeue=False)
    mark_job_completed(str(job.get("job_id")), status="dead_lettered", error=str(exc)[:500])
    logger.error(
        "worker: job_id=%s dead-lettered after %d attempts — last error: %s",
        job.get("job_id"), attempt, exc,
    )


def _mark_job_human_review(job: dict[str, Any]) -> None:
    """Surface an unhandled worker failure without leaving reruns stuck."""
    job_run_id = job.get("run_id")
    if job.get("job_type") == "hls_group":
        for test_id in job.get("ordered_test_ids", []):
            state_store.update_state(str(test_id), "HUMAN_REVIEW", run_id=job_run_id)
    elif job.get("job_type") == "single_test" and job.get("test_id"):
        state_store.update_state(str(job["test_id"]), "HUMAN_REVIEW", run_id=job_run_id)
        _update_review_rerun_status(job.get("review_item_id"), "SCRIPT_ERROR")


def _run_grouped_spec(
    hls_id: str,
    run_id: str,
    script_path: Path | None = None,
    ordered_test_ids: list[str] | None = None,
    storage_state_path: str | None = None,
    credential_id: str | None = None,
    plan_run_id: str | None = None,
) -> None:
    """Run {hls_id}.spec.ts and write one TestResult per subtask.

    Matches Playwright test() results to test_ids by position (index), NOT by title.
    The LLM may rephrase test titles; positional matching is always correct because
    A5 generates test() blocks in the same order as the contexts list.

    Classification per subtask:
      Playwright passed  â†’ PASS
      Playwright failed  â†’ A6 Classifier (APP_ERROR | SCRIPT_ERROR â†’ A7)
      Playwright skipped â†’ BLOCKED (blocked_by = first_failed_test_id)

    BLOCKED tests never trigger A7 or create review queue entries.
    """
    from app.agents.agent6_classifier import classify

    script_path = script_path or generated_base() / f"{hls_id}.spec.ts"
    if not script_path.exists():
        logger.error("worker: grouped script not found for hls_id=%s", hls_id)
        for tid in ordered_test_ids or []:
            mcp_server.save_test_result(test_id=str(tid), status="HUMAN_REVIEW", run_id=run_id)
            state_store.update_state(str(tid), "HUMAN_REVIEW", run_id=run_id)
        return

    # Retrieve ordered test_ids stored at group creation time
    ordered_test_ids = ordered_test_ids or state_store.get_hls_group(hls_id) or []
    if not ordered_test_ids:
        logger.error(
            "worker: no HLS group found in state_store for hls_id=%s â€” "
            "state may have been cleared after a server restart. Re-trigger the run.",
            hls_id,
        )
        return

    run = _run_spec(
        script_path,
        run_id,
        storage_state_path=storage_state_path,
        credential_id=credential_id,
        plan_run_id=plan_run_id,
    )
    results = list(_walk_specs(run["report"]))

    if not results:
        logger.warning(
            "worker: Playwright returned 0 test results for hls_id=%s "
            "(exit_code=%d). stderr: %s",
            hls_id, run["exit_code"], run["stderr"][:500],
        )
        # Mark all subtasks HUMAN_REVIEW so they surface in the review queue
        for tid in ordered_test_ids:
            mcp_server.save_test_result(test_id=tid, status="HUMAN_REVIEW", run_id=run_id)
            state_store.update_state(tid, "HUMAN_REVIEW", run_id=run_id)
        return

    first_failed_id: str | None = None

    for idx, (title, pw_status, error_message, network_logs, trace) in enumerate(results):
        if idx >= len(ordered_test_ids):
            logger.warning(
                "worker: more test() results (%d) than registered subtasks (%d) for hls_id=%s â€” ignoring extras",
                len(results), len(ordered_test_ids), hls_id,
            )
            break

        test_id = ordered_test_ids[idx]
        logger.debug("worker: hls_id=%s idx=%d title=%r pw_status=%s test_id=%s", hls_id, idx, title, pw_status, test_id)

        for log in network_logs:
            state_store.append_network_log(test_id, log)

        if pw_status == "passed":
            mcp_server.save_test_result(
                test_id=test_id,
                status="PASS",
                run_id=run_id,
                trace_path=trace,
                network_logs=network_logs,
            )
            state_store.update_state(test_id, "PASS", run_id=run_id, trace_path=trace)
            mcp_server.mark_complete(test_id)
            logger.info("worker: PASS test_id=%s title=%r", test_id, title)

        elif pw_status == "failed":
            # Let classify() be the single authority for status assignment.
            # Only forward trace_path metadata so it gets stored.
            if trace:
                state_store.update_state(test_id, "PENDING", run_id=run_id, trace_path=trace)
            # A6's contract uses uppercase "FAIL"/"PASS" — passing the lowercase
            # Playwright status would silently fall through to the PASS branch
            # and hide every real script failure.
            verdict = classify(
                test_id=test_id,
                run_id=run_id,
                playwright_result="FAIL",
                network_logs=network_logs,
                error_log=error_message,
                is_grouped=True,
            )
            # Only block downstream tests when A6 confirms a real failure.
            # If A6 returned PASS (e.g. soft assertion deemed benign), siblings
            # in the serial group should continue running.
            if verdict != "PASS" and not first_failed_id:
                first_failed_id = test_id
            logger.info(
                "worker: FAIL test_id=%s title=%r verdict=%s",
                test_id, title, verdict,
            )

        else:  # skipped â€” blocked by upstream OR beforeAll crashed
            if first_failed_id is None:
                # No prior failure: beforeAll failed (TS compile error, context crash, etc.)
                # Mark first skipped as SCRIPT_ERROR so it surfaces in review queue.
                first_failed_id = test_id
                classify(
                    test_id=test_id,
                    run_id=run_id,
                    playwright_result="FAIL",
                    network_logs=network_logs,
                    error_log=error_message or run["stderr"][:300] or "beforeAll hook failed â€” all tests skipped",
                    is_grouped=True,
                )
                logger.warning(
                    "worker: beforeAll FAIL -> first skipped = HUMAN_REVIEW: test_id=%s title=%r",
                    test_id, title,
                )
            else:
                mcp_server.save_test_result(test_id=test_id, status="BLOCKED", run_id=run_id)
                state_store.update_state(test_id, "BLOCKED", run_id=run_id, blocked_by=first_failed_id)
                logger.info("worker: BLOCKED test_id=%s title=%r (blocked_by=%s)", test_id, title, first_failed_id)

    if len(results) < len(ordered_test_ids):
        missing_ids = ordered_test_ids[len(results):]
        reason = (
            "Playwright returned fewer results than expected for this grouped spec. "
            "This usually means a compile error, beforeAll crash, or reporter truncation "
            "prevented later tests from producing result records."
        )
        logger.warning(
            "worker: grouped hls_id=%s returned %d/%d result(s); marking leftovers HUMAN_REVIEW: %s",
            hls_id, len(results), len(ordered_test_ids), missing_ids,
        )
        for tid in missing_ids:
            mcp_server.save_test_result(test_id=tid, status="HUMAN_REVIEW", run_id=run_id)
            state_store.update_state(tid, "HUMAN_REVIEW", run_id=run_id)
            _write_worker_review_queue(
                tid,
                run_id,
                category="GROUPED_RESULT_MISSING",
                reason=reason,
                evidence={
                    "hls_id": hls_id,
                    "expected_results": len(ordered_test_ids),
                    "actual_results": len(results),
                    "stage": "worker",
                },
            )


# â”€â”€ Worker loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def worker_loop(
    run_id: str,
    *,
    idle_timeout_s: int = _IDLE_TIMEOUT_S,
    stop_event: Any | None = None,
) -> None:
    """Blocking worker loop â€” runs in a ThreadPoolExecutor thread.

    Polls RabbitMQ with basic_get. Exits after IDLE_TIMEOUT_S consecutive
    seconds of empty queue (long enough for A7-repaired scripts to be re-enqueued).
    """
    logger.info("worker_loop started for run_id=%s", run_id)

    try:
        conn, ch = _rabbitmq_channel()
    except Exception as exc:
        logger.error("worker_loop: cannot connect to RabbitMQ: %s", exc)
        return

    idle_s = 0
    foreign_streak = 0
    try:
        while idle_timeout_s <= 0 or idle_s < idle_timeout_s:
            method_frame, _props, body = ch.basic_get(
                queue=settings.rabbitmq_queue, auto_ack=False
            )

            if body is None:
                if stop_event is not None and stop_event.is_set():
                    logger.info("worker_loop stop requested for run_id=%s", run_id)
                    break
                idle_s += _POLL_INTERVAL_S
                time.sleep(_POLL_INTERVAL_S)
                continue

            idle_s = 0
            message = body.decode().strip()

            if message == _SENTINEL:
                ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                break

            try:
                job = parse_job(message)
                if not job:
                    logger.warning("worker: rejecting non-JSON job: %s", message[:80])
                    ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                    continue
                logger.info(
                    "worker: dequeued job_type=%s job_id=%s run_id=%s",
                    job.get("job_type"), job.get("job_id"), job.get("run_id"),
                )

                job_run_id = str(job.get("run_id") or "")
                if job_run_id != run_id:
                    active_status = _run_is_active(job_run_id)
                    if active_status is False:
                        logger.info(
                            "worker: discarding stale foreign job for inactive run_id=%s "
                            "while worker handles run_id=%s",
                            job_run_id,
                            run_id,
                        )
                        ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                        continue
                    if active_status is None:
                        logger.warning(
                            "worker: foreign run status unknown for run_id=%s; requeueing job",
                            job_run_id,
                        )
                        ch.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)
                        time.sleep(_POLL_INTERVAL_S)
                        continue

                    foreign_streak += 1
                    backoff = min(
                        _POLL_INTERVAL_S * (2 ** min(foreign_streak, 5)),
                        _FOREIGN_JOB_BACKOFF_MAX_S,
                    ) + random.uniform(0, 0.25)
                    logger.info(
                        "worker: requeueing job for run_id=%s while worker handles run_id=%s "
                        "(foreign_streak=%d, sleep=%.1fs)",
                        job_run_id, run_id, foreign_streak, backoff,
                    )
                    ch.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)
                    time.sleep(backoff)
                    continue
                foreign_streak = 0
                active_status = _run_is_active(job_run_id)
                if active_status is None:
                    logger.warning("worker: run status unknown for run_id=%s; requeueing job", job_run_id)
                    ch.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)
                    continue
                if not active_status:
                    logger.info("worker: discarding job for inactive run_id=%s", job_run_id)
                    ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                    continue

                # Idempotency: skip duplicate deliveries from RabbitMQ redelivery
                if not try_claim_job(job):
                    logger.warning(
                        "worker: claim skipped job_type=%s job_id=%s run_id=%s",
                        job.get("job_type"), job.get("job_id"), job.get("run_id"),
                    )
                    ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                    continue

                if job["job_type"] == "hls_group":
                    hls_id = str(job["hls_id"])
                    ordered_ids = [str(tid) for tid in job.get("ordered_test_ids", [])]
                    logger.info("worker: processing JSON grouped hls_id=%s", hls_id)
                    _run_grouped_spec(
                        hls_id=hls_id,
                        run_id=job_run_id,
                        script_path=job_script_path(job),
                        ordered_test_ids=ordered_ids,
                        storage_state_path=job.get("storage_state_path"),
                        credential_id=job.get("credential_id"),
                        plan_run_id=job.get("plan_run_id"),
                    )
                elif job["job_type"] == "single_test":
                    test_id = str(job["test_id"])
                    logger.info(
                        "worker: processing JSON single test_id=%s script=%s",
                        test_id, job.get("script_path"),
                    )
                    result = _run_spec(
                        job_script_path(job),
                        job_run_id,
                        storage_state_path=job.get("storage_state_path"),
                        test_id=test_id,
                        credential_id=job.get("credential_id"),
                        plan_run_id=job.get("plan_run_id"),
                    )
                    classification = _classify_single_run_result(
                        test_id,
                        job_run_id,
                        result,
                        project_id=str(job.get("project_id") or ""),
                    )
                    _update_artifact_manifest(
                        project_id=str(job.get("project_id") or ""),
                        run_id=job_run_id,
                        test_id=test_id,
                        script_path=job_script_path(job),
                        classification=classification,
                    )
                    _register_playwright_artifacts(
                        project_id=str(job.get("project_id") or ""),
                        run_id=job_run_id,
                        test_id=test_id,
                        script_path=job_script_path(job),
                    )
                    _update_review_rerun_status(job.get("review_item_id"), classification)
                ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                mark_job_completed(str(job.get("job_id")))

            except Exception as exc:
                logger.exception("worker: unhandled error for message '%s': %s", message, exc)
                job = parse_job(message)
                if job:
                    _handle_job_failure(ch, method_frame.delivery_tag, job, exc)
                else:
                    ch.basic_ack(delivery_tag=method_frame.delivery_tag)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    logger.info("worker_loop finished for run_id=%s", run_id)


def standalone_worker_loop(*, idle_timeout_s: int = 0) -> None:
    """Continuously consume run-scoped JSON jobs from RabbitMQ.

    This is the production worker entrypoint. Non-JSON jobs are rejected because
    they do not carry a run_id and cannot be safely claimed by a shared worker pool.
    """
    logger.info("standalone Phase 3 worker started")

    while True:
        try:
            conn, ch = _rabbitmq_channel()
        except Exception as exc:
            logger.error("standalone worker: cannot connect to RabbitMQ: %s", exc)
            time.sleep(5)
            continue

        idle_s = 0
        try:
            while idle_timeout_s <= 0 or idle_s < idle_timeout_s:
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
                    return

                job = parse_job(message)
                if not job:
                    logger.warning("standalone worker: rejecting legacy/non-JSON job: %s", message[:80])
                    ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                    continue

                job_run_id = str(job.get("run_id") or "")
                if not job_run_id:
                    logger.info("standalone worker: discarding job without run_id")
                    ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                    continue
                active_status = _run_is_active(job_run_id)
                if active_status is None:
                    logger.warning(
                        "standalone worker: run status unknown for run_id=%s; requeueing job",
                        job_run_id,
                    )
                    ch.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)
                    continue
                if not active_status:
                    logger.info("standalone worker: discarding inactive job run_id=%s", job_run_id)
                    ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                    continue

                # Idempotency: skip duplicate deliveries from RabbitMQ redelivery
                if not try_claim_job(job):
                    ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                    continue

                try:
                    if job["job_type"] == "hls_group":
                        _run_grouped_spec(
                            hls_id=str(job["hls_id"]),
                            run_id=job_run_id,
                            script_path=job_script_path(job),
                            ordered_test_ids=[str(tid) for tid in job.get("ordered_test_ids", [])],
                            storage_state_path=job.get("storage_state_path"),
                            credential_id=job.get("credential_id"),
                            plan_run_id=job.get("plan_run_id"),
                        )
                    elif job["job_type"] == "single_test":
                        test_id = str(job["test_id"])
                        result = _run_spec(
                            job_script_path(job),
                            job_run_id,
                            storage_state_path=job.get("storage_state_path"),
                            test_id=test_id,
                            credential_id=job.get("credential_id"),
                            plan_run_id=job.get("plan_run_id"),
                        )
                        classification = _classify_single_run_result(
                            test_id,
                            job_run_id,
                            result,
                            project_id=str(job.get("project_id") or ""),
                        )
                        _update_artifact_manifest(
                            project_id=str(job.get("project_id") or ""),
                            run_id=job_run_id,
                            test_id=test_id,
                            script_path=job_script_path(job),
                            classification=classification,
                        )
                        _register_playwright_artifacts(
                            project_id=str(job.get("project_id") or ""),
                            run_id=job_run_id,
                            test_id=test_id,
                            script_path=job_script_path(job),
                        )
                        _update_review_rerun_status(job.get("review_item_id"), classification)
                    ch.basic_ack(delivery_tag=method_frame.delivery_tag)
                    mark_job_completed(str(job.get("job_id")))
                except Exception as exc:
                    logger.exception("standalone worker: job failed: %s", exc)
                    _handle_job_failure(ch, method_frame.delivery_tag, job, exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if idle_timeout_s > 0:
            break


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Phase 3 RabbitMQ worker")
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=0,
        help="Exit after N idle seconds; 0 means run continuously.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    standalone_worker_loop(idle_timeout_s=args.idle_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
