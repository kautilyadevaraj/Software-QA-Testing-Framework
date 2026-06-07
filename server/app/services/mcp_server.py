"""Phase 3 MCP Server — exposes 6 tool groups to the agent pipeline.

Tools:
  Database : generate_id, save_test_case, update_script_path,
             save_test_result, flush_to_postgres
  DOM      : list_pages, get_snapshot
  Script   : write_script, read_script
  State    : update_state_local, flush_state_to_db
  Queue    : enqueue, mark_complete
  Credentials: get_placeholders
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

import pika
from fastmcp import FastMCP
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.phase3 import NetworkLog, Phase3ExecutionState, ReviewQueueItem, TestCase, TestResult, TestRun
from app.models.scenario import DiscoveredRoute
from app.services.artifact_paths import (
    generated_base,
    scripts_dir,
    testcase_script_name,
    upsert_manifest_entry,
)
from app.services.artifact_registry import register_artifact
from app.services import state_store
from app.utils.dom_preprocessor import minify_html

logger = logging.getLogger(__name__)

mcp = FastMCP("phase3-mcp")


# ── RabbitMQ producer pool ────────────────────────────────────────────────────
# Workers keep their own private connections (see phase3_worker.py).
# The producer side (enqueue / purge) reuses one persistent connection to
# avoid 47+ TCP open/close cycles per Phase 3 run.

_rmq_lock = threading.Lock()
_rmq_producer_conn: pika.BlockingConnection | None = None


def _get_producer_channel() -> tuple[pika.BlockingConnection, Any]:
    """Return (conn, channel) reusing a cached producer connection."""
    from app.services.queue_topology import declare_topology

    global _rmq_producer_conn
    with _rmq_lock:
        try:
            if _rmq_producer_conn is None or _rmq_producer_conn.is_closed:
                params = pika.URLParameters(settings.rabbitmq_url)
                params.heartbeat = 600
                params.blocked_connection_timeout = 300
                _rmq_producer_conn = pika.BlockingConnection(params)
            ch = _rmq_producer_conn.channel()
            declare_topology(ch)
            return _rmq_producer_conn, ch
        except Exception:
            _rmq_producer_conn = None   # force reconnect next call
            raise

# ── Helpers ──────────────────────────────────────────────────────────────────


def _scripts_dir() -> Path:
    p = generated_base()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _resolve_script_dir(project_id: str | None = None, run_id: str | None = None) -> Path:
    """Return the directory where a script for (project_id, run_id) should live.

    Multi-tenant layout: tests/generated/<project_id>/<run_id>/...

    Falls back to the flat legacy directory when either id is missing — keeps
    older single-tenant deployments working and lets non-execution code paths
    (TC document generation, ad-hoc tooling) keep using the simple form.

    NOTE: Playwright's `testDir` in playwright.config.ts is set to
    `./tests/generated` and recursively discovers .spec.ts files. Per-project
    subdirectories are subtrees of that root, so test discovery still works
    when we pass the explicit script path to `playwright test <path>`.
    """
    base = generated_base()
    if project_id and run_id:
        out = scripts_dir(project_id, run_id)
    elif project_id:
        out = base / project_id
    else:
        out = base
    out.mkdir(parents=True, exist_ok=True)
    return out


@mcp.tool()
def generate_id() -> str:
    """Return a new UUID4 string. Agents must always call this instead of generating IDs."""
    return str(uuid.uuid4())


@mcp.tool()
def save_test_case(
    test_id: str,
    project_id: str,
    title: str,
    steps: list[dict[str, Any]],
    depends_on: list[str],
    target_page: str,
    hls_id: str = "",
    run_id: str = "",
    tc_number: str = "",
    acceptance_criteria: list[str] | None = None,
    assertion_evidence: list[dict[str, Any]] | None = None,
    auth_mode: str = "authenticated",
    credential_id: str | None = None,
    credential_role: str | None = None,
) -> str:
    """Persist a test case to the database. Idempotent — skips if test_id already exists.

    Also guards against (project_id, tc_number) duplicates that arise when a
    planning run is retried after a crash (new UUID, same TC-001 number).

    Args:
        hls_id:              UUID string of the parent High-Level Scenario (Phase 2).
        tc_number:           Human-readable number e.g. 'TC-001' for RTM traceability.
        acceptance_criteria: List of verifiable pass conditions shown in TC document.
        assertion_evidence:  Automation-facing evidence for grounded assertions.
    """
    with SessionLocal() as db:
        # Guard 1: identical test_id (normal idempotency)
        existing = db.get(TestCase, uuid.UUID(test_id))
        if existing:
            return test_id

        # Guard 2: same (project_id, tc_number) from a crashed/retried plan run
        if tc_number:
            dup = db.execute(
                select(TestCase).where(
                    TestCase.project_id == uuid.UUID(project_id),
                    TestCase.run_id == (uuid.UUID(run_id) if run_id else None),
                    TestCase.tc_number == tc_number,
                )
            ).scalar_one_or_none()
            if dup:
                logger.warning(
                    "save_test_case: duplicate (project_id=%s, tc_number=%s) — skipping new test_id=%s, keeping %s",
                    project_id, tc_number, test_id, dup.test_id,
                )
                return str(dup.test_id)

        valid_deps: list[uuid.UUID] = []
        for d in depends_on:
            try:
                valid_deps.append(uuid.UUID(str(d)))
            except (ValueError, AttributeError):
                logger.warning("save_test_case: skipping invalid depends_on value: %s", d)
        tc = TestCase(
            test_id=uuid.UUID(test_id),
            project_id=uuid.UUID(project_id),
            title=title,
            steps=steps,
            depends_on=valid_deps,
            target_page=target_page,
            hls_id=uuid.UUID(hls_id) if hls_id else None,
            run_id=uuid.UUID(run_id) if run_id else None,
            tc_number=tc_number or None,
            acceptance_criteria=acceptance_criteria or [],
            assertion_evidence=assertion_evidence or [],
            auth_mode=auth_mode,
            credential_id=uuid.UUID(credential_id) if credential_id else None,
            credential_role=credential_role,
        )
        db.add(tc)
        db.commit()
    state_store.init_test(test_id)
    return test_id


def update_assertion_evidence(test_id: str, assertion_evidence: list[dict[str, Any]]) -> bool:
    """Persist A3b assertion evidence for an existing test case."""
    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
        if not tc:
            return False
        tc.assertion_evidence = assertion_evidence or []
        db.commit()
    return True


def update_script_path(test_id: str, script_path: str) -> bool:
    """Update the script_path column on an existing test_case row."""
    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
        if not tc:
            return False
        tc.script_path = script_path
        db.commit()
    return True


@mcp.tool()
def save_test_result(
    test_id: str,
    status: str,
    run_id: str | None = None,
    retries: int = 0,
    jira_ticket: str | None = None,
    trace_path: str | None = None,
    network_logs: list[dict[str, Any]] | None = None,
) -> bool:
    """Immediately write a test result to the DB (used for APP_ERROR classification)."""
    with SessionLocal() as db:
        tid = uuid.UUID(test_id)
        rid = uuid.UUID(run_id) if run_id else None
        query = select(TestResult).where(TestResult.test_id == tid)
        if rid:
            query = query.where(TestResult.run_id == rid)
        else:
            query = query.order_by(TestResult.created_at.desc()).limit(1)
        existing = db.execute(query).scalar_one_or_none()
        if existing:
            existing.status = status
            if rid:
                existing.run_id = rid
            existing.retries = retries
            existing.jira_ticket = jira_ticket
            existing.trace_path = trace_path
            result = existing
        else:
            result = TestResult(
                id=uuid.uuid4(),
                test_id=tid,
                run_id=rid,
                status=status,
                retries=retries,
                jira_ticket=jira_ticket,
                trace_path=trace_path,
            )
            db.add(result)
            db.flush()
        if network_logs is not None:
            db.execute(delete(NetworkLog).where(NetworkLog.test_result_id == result.id))
            for log in (network_logs or []):
                db.add(NetworkLog(
                    id=uuid.uuid4(),
                    test_id=tid,
                    test_result_id=result.id,
                    url=log.get("url", ""),
                    method=log.get("method", "GET"),
                    status_code=log.get("status_code", 0),
                    is_failure=log.get("is_failure", False),
                ))
        db.commit()
    state_store.update_state(test_id, status, run_id=run_id, retries=retries)
    return True


@mcp.tool()
def flush_to_postgres(run_id: str) -> dict[str, int]:
    """Bulk-flush state.json entries to test_results / network_logs / retry_history.

    Already-persisted test_ids (UNIQUE constraint) are skipped gracefully.
    Returns counts of flushed and skipped rows.
    """
    rid = uuid.UUID(run_id)
    flushed = 0
    skipped = 0
    with SessionLocal() as db:
        execution_rows = list(db.execute(
            select(Phase3ExecutionState).where(Phase3ExecutionState.run_id == rid)
        ).scalars())
        for entry in execution_rows:
            try:
                existing = db.execute(
                    select(TestResult).where(
                        TestResult.test_id == entry.test_id,
                        TestResult.run_id == rid,
                    )
                ).scalar_one_or_none()
                if existing:
                    existing.status = entry.status
                    existing.retries = entry.retries
                    existing.jira_ticket = entry.jira_ticket or existing.jira_ticket
                    existing.trace_path = entry.trace_path or existing.trace_path
                else:
                    db.add(TestResult(
                        id=uuid.uuid4(),
                        test_id=entry.test_id,
                        run_id=rid,
                        status=entry.status,
                        retries=entry.retries,
                        jira_ticket=entry.jira_ticket,
                        trace_path=entry.trace_path,
                    ))
                flushed += 1
            except Exception as exc:
                logger.warning("flush_to_postgres: skipping %s - %s", entry.test_id, exc)
                skipped += 1
        db.commit()
    return {"flushed": flushed, "skipped": skipped}

    run_test_ids: set[str] = set()
    execution_status_by_test_id: dict[str, str] = {}
    with SessionLocal() as db:
        execution_rows = db.execute(
            select(Phase3ExecutionState.test_id, Phase3ExecutionState.status)
            .where(Phase3ExecutionState.run_id == rid)
        ).all()
        execution_status_by_test_id = {str(test_id): status for test_id, status in execution_rows}
        run_test_ids = set(execution_status_by_test_id)

    snapshot = (
        state_store.get_many(run_test_ids)
        if run_test_ids
        else state_store.get_all()
    )  # already excludes hls: metadata keys
    flushed = 0
    skipped = 0
    with SessionLocal() as db:
        for test_id_str, entry in snapshot.items():
            try:
                tid = uuid.UUID(test_id_str)
                existing = db.execute(
                    select(TestResult).where(
                        TestResult.test_id == tid,
                        TestResult.run_id == rid,
                    )
                ).scalar_one_or_none()
                if existing:
                    entry_status = entry.get("status", existing.status)
                    execution_status = execution_status_by_test_id.get(test_id_str)
                    if execution_status and entry_status == "PENDING":
                        entry_status = execution_status
                    existing.status = entry_status
                    existing.retries = entry.get("retries", existing.retries)
                    existing.jira_ticket = entry.get("jira_ticket") or existing.jira_ticket
                    existing.trace_path = entry.get("trace_path") or existing.trace_path
                    result = existing
                    flushed += 1
                else:
                    entry_status = entry.get("status", "UNKNOWN")
                    execution_status = execution_status_by_test_id.get(test_id_str)
                    if execution_status and entry_status == "PENDING":
                        entry_status = execution_status
                    result = TestResult(
                        id=uuid.uuid4(),
                        test_id=tid,
                        run_id=rid,
                        status=entry_status,
                        retries=entry.get("retries", 0),
                        jira_ticket=entry.get("jira_ticket"),
                        trace_path=entry.get("trace_path"),
                    )
                    db.add(result)
                    db.flush()
                    flushed += 1
                network_logs = entry.get("network_logs") or []
                if network_logs:
                    db.execute(delete(NetworkLog).where(NetworkLog.test_result_id == result.id))
                    for log in network_logs:
                        db.add(NetworkLog(
                            id=uuid.uuid4(),
                            test_id=tid,
                            test_result_id=result.id,
                            url=log.get("url", ""),
                            method=log.get("method", "GET"),
                            status_code=log.get("status_code", 0),
                            is_failure=log.get("is_failure", False),
                        ))
            except Exception as exc:
                logger.warning("flush_to_postgres: skipping %s — %s", test_id_str, exc)
                skipped += 1
        db.commit()
    return {"flushed": flushed, "skipped": skipped}


# ── Test-case read helpers ────────────────────────────────────────────────────
# These are plain functions (not MCP tools) — called by the router and
# orchestrator directly. They avoid scattering raw SQLAlchemy queries
# across multiple files and give one authoritative read path per entity.


def get_test_cases_for_run(project_id: str, run_id: str) -> list[dict[str, Any]]:
    """Return all test_cases for a project enriched with scenario_title from HLS.

    NOTE: test_cases do not have a run_id FK — they are scoped to project_id.
    For the current architecture (one plan run per project at a time) this is
    correct. If multi-run support is added later, add plan_run_id to test_cases.
    """
    from app.models.project import CredentialProfile, HighLevelScenario

    with SessionLocal() as db:
        query = select(TestCase).where(TestCase.project_id == uuid.UUID(project_id))
        if run_id:
            query = query.where(TestCase.run_id == uuid.UUID(run_id))
        rows = db.execute(query.order_by(TestCase.created_at)).scalars().all()

        # Build hls_id → title lookup in one query
        hls_ids = list({r.hls_id for r in rows if r.hls_id})
        hls_map: dict[str, str] = {}
        if hls_ids:
            hls_rows = db.execute(
                select(HighLevelScenario.id, HighLevelScenario.title)
                .where(HighLevelScenario.id.in_(hls_ids))
            ).all()
            hls_map = {str(r.id): r.title for r in hls_rows}

        credential_ids = list({r.credential_id for r in rows if r.credential_id})
        credential_map: dict[str, CredentialProfile] = {}
        if credential_ids:
            credential_rows = db.execute(
                select(CredentialProfile).where(CredentialProfile.id.in_(credential_ids))
            ).scalars().all()
            credential_map = {str(profile.id): profile for profile in credential_rows}

        return [
            {
                "test_id":             str(r.test_id),
                "tc_number":           r.tc_number or "",
                "title":               r.title,
                "steps":               r.steps,
                "acceptance_criteria": r.acceptance_criteria or [],
                "assertion_evidence":  r.assertion_evidence or [],
                "target_page":         r.target_page,
                "hls_id":              str(r.hls_id) if r.hls_id else "",
                "scenario_title":      hls_map.get(str(r.hls_id), "") if r.hls_id else "",
                "depends_on":          [str(d) for d in (r.depends_on or [])],
                "approval_status":     r.approval_status or "PENDING",
                "auth_mode":           r.auth_mode or "authenticated",
                "credential_id":       str(r.credential_id) if r.credential_id else None,
                "credential_role":     r.credential_role,
                "credential_username": (
                    credential_map[str(r.credential_id)].username
                    if r.credential_id and str(r.credential_id) in credential_map
                    else None
                ),
                "credential_endpoint": (
                    credential_map[str(r.credential_id)].endpoint
                    if r.credential_id and str(r.credential_id) in credential_map
                    else None
                ),
            }
            for r in rows
        ]


def get_test_case(test_id: str) -> dict[str, Any] | None:
    """Return a single test_case as a plain dict, or None if not found."""
    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
        if not tc:
            return None
        return {
            "test_id":             str(tc.test_id),
            "tc_number":           tc.tc_number or "",
            "title":               tc.title,
            "hls_id":              str(tc.hls_id) if tc.hls_id else "",
            "acceptance_criteria": tc.acceptance_criteria or [],
            "assertion_evidence":  tc.assertion_evidence or [],
            "approval_status":     tc.approval_status or "PENDING",
            "run_id":              str(tc.run_id) if tc.run_id else "",
            "auth_mode":           tc.auth_mode or "authenticated",
            "credential_id":       str(tc.credential_id) if tc.credential_id else None,
            "credential_role":     tc.credential_role,
        }


def get_review_item(review_item_id: str) -> dict[str, Any] | None:
    """Return a review_queue row as a plain dict, or None if not found."""
    with SessionLocal() as db:
        item = db.get(ReviewQueueItem, uuid.UUID(review_item_id))
        if not item:
            return None
        return {
            "id":          str(item.id),
            "test_id":     str(item.test_id),
            "run_id":      str(item.run_id),
            "review_type": item.review_type,
            "evidence":    item.evidence,
            "status":      item.status,
            "jira_ref":    item.jira_ref,
        }


def update_review_item(review_item_id: str, **fields: Any) -> bool:
    """Atomically update status / jira_ref (or any column) on a review_queue row.

    Returns True if the row was found and updated, False if not found.
    """
    with SessionLocal() as db:
        item = db.get(ReviewQueueItem, uuid.UUID(review_item_id))
        if not item:
            logger.warning("update_review_item: id not found: %s", review_item_id)
            return False
        for k, v in fields.items():
            setattr(item, k, v)
        db.commit()
    return True


# ── DOM Tool ─────────────────────────────────────────────────────────────────


@mcp.tool()
def list_pages(project_id: str) -> list[str]:
    """Return all discovered route paths for the given project."""
    with SessionLocal() as db:
        rows = db.execute(
            select(DiscoveredRoute.path).where(
                DiscoveredRoute.project_id == uuid.UUID(project_id)
            )
        ).scalars().all()
    return list(rows)


@mcp.tool()
def get_snapshot(project_id: str, page: str) -> dict[str, Any]:
    """Return a preprocessed DOM snapshot for a page route.

    HTML is minified via dom_preprocessor.minify_html() before being returned
    to reduce LLM token usage.
    """
    with SessionLocal() as db:
        route = db.execute(
            select(DiscoveredRoute).where(
                DiscoveredRoute.project_id == uuid.UUID(project_id),
                DiscoveredRoute.path == page,
            )
        ).scalar_one_or_none()

    if not route:
        raise ValueError(f"No snapshot found for page '{page}' in project {project_id}")

    html_content = ""
    if route.html_path:
        html_file = Path(route.html_path)
        if html_file.exists():
            raw = html_file.read_text(encoding="utf-8", errors="replace")
            html_content = minify_html(raw)

    return {
        "path": route.path,
        "html": html_content,
        "accessibility_tree": route.accessibility_tree or [],
        "interactive_elements": route.interactive_elements or [],
    }


# ── Script Tool ──────────────────────────────────────────────────────────────


def write_script(
    test_id: str,
    code: str,
    *,
    project_id: str | None = None,
    run_id: str | None = None,
) -> str:
    """Write a Playwright .spec.ts file for the given test_id. Returns the file path.

    When project_id and run_id are supplied, the script lands under
    `tests/generated/<project_id>/<run_id>/scripts/<tc>__<slug>__<id>.spec.ts`.
    Without them, the legacy flat layout is preserved for backwards compat.
    Callers that already know the run/project (agents 5/7, worker) should pass
    both so concurrent runs don't trample each other's files.
    """
    tc_number = None
    title = None
    try:
        with SessionLocal() as db:
            tc = db.get(TestCase, uuid.UUID(test_id))
            if tc:
                tc_number = tc.tc_number
                title = tc.title
    except Exception:
        logger.debug("write_script: testcase metadata lookup failed for %s", test_id, exc_info=True)

    filename = (
        testcase_script_name(test_id, tc_number=tc_number, title=title)
        if project_id and run_id
        else f"{test_id}.spec.ts"
    )
    dest = _resolve_script_dir(project_id, run_id) / filename
    dest.write_text(code, encoding="utf-8")
    if project_id and run_id:
        upsert_manifest_entry(project_id, run_id, {
            "test_id": test_id,
            "tc_number": tc_number or "",
            "title": title or "",
            "script_path": str(dest),
        })
        register_artifact(
            project_id=project_id,
            run_id=run_id,
            test_id=test_id,
            artifact_type="SCRIPT",
            path=dest,
        )
    return str(dest)


def read_script(
    test_id: str,
    *,
    project_id: str | None = None,
    run_id: str | None = None,
) -> str:
    """Read and return the Playwright .spec.ts content for the given test_id.

    Falls back from per-project layout → flat layout so a partial migration
    still works (older scripts created before the multi-tenant layout existed
    remain readable).
    """
    candidates: list[Path] = []
    if project_id and run_id:
        candidates.extend(_resolve_script_dir(project_id, run_id).glob(f"*__{test_id.replace('-', '')[:8]}.spec.ts"))
        candidates.append(_resolve_script_dir(project_id, run_id) / f"{test_id}.spec.ts")
        candidates.append(generated_base() / project_id / run_id / f"{test_id}.spec.ts")
    if project_id:
        candidates.append(_resolve_script_dir(project_id, None) / f"{test_id}.spec.ts")
    candidates.append(_scripts_dir() / f"{test_id}.spec.ts")
    for dest in candidates:
        if dest.exists():
            return dest.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Script not found for test_id={test_id}")


# ── State Tool ───────────────────────────────────────────────────────────────


@mcp.tool()
def update_state_local(test_id: str, status: str, extras: dict[str, Any] | None = None) -> bool:
    """Update the in-process state.json entry for a test."""
    state_store.update_state(test_id, status, **(extras or {}))
    return True


@mcp.tool()
def flush_state_to_db(run_id: str) -> dict[str, int]:
    """Mirror execution state to results without clearing live DB state."""
    return flush_to_postgres(run_id)


# ── Queue Tool ───────────────────────────────────────────────────────────────


def purge_queue() -> None:
    """Discard all stale messages from the RabbitMQ queue before a new run."""
    try:
        _conn, ch = _get_producer_channel()
        result = ch.queue_purge(queue=settings.rabbitmq_queue)
        logger.info("purge_queue: purged %s stale message(s)", result.method.message_count)
    except Exception as exc:
        logger.warning("purge_queue: failed to purge queue: %s", exc)


@mcp.tool()
def enqueue(job_id: dict[str, Any]) -> bool:
    """Publish a job_id to RabbitMQ (3× exponential backoff).

    Production jobs must be run-scoped JSON payloads.
    """
    import time
    from app.services.phase3_jobs import serialize_job

    if not isinstance(job_id, dict):
        logger.error("enqueue rejected non-JSON Phase 3 job: %r", job_id)
        return False

    body = serialize_job(job_id)
    for attempt in range(3):
        try:
            _conn, ch = _get_producer_channel()
            ch.basic_publish(
                exchange="",
                routing_key=settings.rabbitmq_queue,
                body=body.encode(),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            logger.info(
                "enqueue: published job_type=%s job_id=%s run_id=%s queue=%s",
                job_id.get("job_type"),
                job_id.get("job_id"),
                job_id.get("run_id"),
                settings.rabbitmq_queue,
            )
            return True
        except Exception as exc:
            logger.warning("enqueue attempt %d failed: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return False


@mcp.tool()
def mark_complete(test_id: str) -> bool:
    """Deprecated no-op; PASS/FAIL state is already written with run_id."""
    _ = test_id
    return True


# ── Credentials Tool ─────────────────────────────────────────────────────────


@mcp.tool()
def get_placeholders(actor: str = "user") -> dict[str, str]:
    """Return ENV placeholder tokens — never real credential values.

    The Script Generator embeds these tokens in generated .spec.ts files.
    The runner substitutes real values from environment variables at execution time.
    """
    return {
        "TEST_USERNAME": "{{TEST_USERNAME}}",
        "TEST_PASSWORD": "{{TEST_PASSWORD}}",
        "TEST_ROLE": "{{TEST_ROLE}}",
        "TEST_LOGIN_URL": "{{TEST_LOGIN_URL}}",
        "BASE_URL": "{{BASE_URL}}",
    }
