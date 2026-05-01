"""Phase 3 MCP Server — exposes 6 tool groups to the agent pipeline.

Tools:
  Database : generate_id, save_test_case, update_script_path,
             save_test_result, flush_to_postgres
  DOM      : list_pages, get_snapshot
  Script   : write_script, read_script
  State    : update_state_local, check_dependencies, flush_state_to_db
  Queue    : enqueue, requeue, mark_complete
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
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.phase3 import NetworkLog, ReviewQueueItem, TestCase, TestResult, TestRun
from app.models.scenario import DiscoveredRoute
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
    global _rmq_producer_conn
    with _rmq_lock:
        try:
            if _rmq_producer_conn is None or _rmq_producer_conn.is_closed:
                params = pika.URLParameters(settings.rabbitmq_url)
                params.heartbeat = 600
                params.blocked_connection_timeout = 300
                _rmq_producer_conn = pika.BlockingConnection(params)
            ch = _rmq_producer_conn.channel()
            ch.queue_declare(queue=settings.rabbitmq_queue, durable=True)
            return _rmq_producer_conn, ch
        except Exception:
            _rmq_producer_conn = None   # force reconnect next call
            raise

# ── Helpers ──────────────────────────────────────────────────────────────────


def _scripts_dir() -> Path:
    p = Path(settings.generated_scripts_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p




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
    tc_number: str = "",
    acceptance_criteria: list[str] | None = None,
) -> str:
    """Persist a test case to the database. Idempotent — skips if test_id already exists.

    Args:
        hls_id:              UUID string of the parent High-Level Scenario (Phase 2).
        tc_number:           Human-readable number e.g. 'TC-001' for RTM traceability.
        acceptance_criteria: List of verifiable pass conditions shown in TC document.
    """
    with SessionLocal() as db:
        existing = db.get(TestCase, uuid.UUID(test_id))
        if existing:
            return test_id
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
            tc_number=tc_number or None,
            acceptance_criteria=acceptance_criteria or [],
        )
        db.add(tc)
        db.commit()
    state_store.init_test(test_id)
    return test_id


@mcp.tool()
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
    retries: int = 0,
    jira_ticket: str | None = None,
    trace_path: str | None = None,
    network_logs: list[dict[str, Any]] | None = None,
) -> bool:
    """Immediately write a test result to the DB (used for APP_ERROR classification)."""
    with SessionLocal() as db:
        existing = db.execute(
            select(TestResult).where(TestResult.test_id == uuid.UUID(test_id))
        ).scalar_one_or_none()
        if existing:
            existing.status = status
            existing.retries = retries
            existing.jira_ticket = jira_ticket
            existing.trace_path = trace_path
        else:
            result = TestResult(
                id=uuid.uuid4(),
                test_id=uuid.UUID(test_id),
                status=status,
                retries=retries,
                jira_ticket=jira_ticket,
                trace_path=trace_path,
            )
            db.add(result)
            db.flush()
            for log in (network_logs or []):
                db.add(NetworkLog(
                    id=uuid.uuid4(),
                    test_id=uuid.UUID(test_id),
                    url=log.get("url", ""),
                    method=log.get("method", "GET"),
                    status_code=log.get("status_code", 0),
                    is_failure=log.get("is_failure", False),
                ))
        db.commit()
    state_store.update_state(test_id, status, retries=retries)
    return True


@mcp.tool()
def flush_to_postgres(run_id: str) -> dict[str, int]:
    """Bulk-flush state.json entries to test_results / network_logs / retry_history.

    Already-persisted test_ids (UNIQUE constraint) are skipped gracefully.
    Returns counts of flushed and skipped rows.
    """
    snapshot = state_store.get_all()  # already excludes hls: metadata keys
    flushed = 0
    skipped = 0
    with SessionLocal() as db:
        for test_id_str, entry in snapshot.items():
            try:
                tid = uuid.UUID(test_id_str)
                existing = db.execute(
                    select(TestResult).where(TestResult.test_id == tid)
                ).scalar_one_or_none()
                if existing:
                    existing.status = entry.get("status", existing.status)
                    existing.retries = entry.get("retries", existing.retries)
                    existing.jira_ticket = entry.get("jira_ticket") or existing.jira_ticket
                    existing.trace_path = entry.get("trace_path") or existing.trace_path
                    flushed += 1
                    continue
                result = TestResult(
                    id=uuid.uuid4(),
                    test_id=tid,
                    status=entry.get("status", "UNKNOWN"),
                    retries=entry.get("retries", 0),
                    jira_ticket=entry.get("jira_ticket"),
                    trace_path=entry.get("trace_path"),
                )
                db.add(result)
                db.flush()
                for log in entry.get("network_logs", []):
                    db.add(NetworkLog(
                        id=uuid.uuid4(),
                        test_id=tid,
                        url=log.get("url", ""),
                        method=log.get("method", "GET"),
                        status_code=log.get("status_code", 0),
                        is_failure=log.get("is_failure", False),
                    ))
                flushed += 1
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
    from app.models.project import HighLevelScenario

    with SessionLocal() as db:
        rows = db.execute(
            select(TestCase).where(
                TestCase.project_id == uuid.UUID(project_id)
            ).order_by(TestCase.created_at)
        ).scalars().all()

        # Build hls_id → title lookup in one query
        hls_ids = list({r.hls_id for r in rows if r.hls_id})
        hls_map: dict[str, str] = {}
        if hls_ids:
            hls_rows = db.execute(
                select(HighLevelScenario.id, HighLevelScenario.title)
                .where(HighLevelScenario.id.in_(hls_ids))
            ).all()
            hls_map = {str(r.id): r.title for r in hls_rows}

        return [
            {
                "test_id":             str(r.test_id),
                "tc_number":           r.tc_number or "",
                "title":               r.title,
                "steps":               r.steps,
                "acceptance_criteria": r.acceptance_criteria or [],
                "target_page":         r.target_page,
                "hls_id":              str(r.hls_id) if r.hls_id else "",
                "scenario_title":      hls_map.get(str(r.hls_id), "") if r.hls_id else "",
                "depends_on":          [str(d) for d in (r.depends_on or [])],
                "approval_status":     r.approval_status or "PENDING",
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
            "approval_status":     tc.approval_status or "PENDING",
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


@mcp.tool()
def write_script(test_id: str, code: str) -> str:
    """Write a Playwright .spec.ts file for the given test_id. Returns the file path."""
    dest = _scripts_dir() / f"{test_id}.spec.ts"
    dest.write_text(code, encoding="utf-8")
    return str(dest)


@mcp.tool()
def read_script(test_id: str) -> str:
    """Read and return the Playwright .spec.ts content for the given test_id."""
    dest = _scripts_dir() / f"{test_id}.spec.ts"
    if not dest.exists():
        raise FileNotFoundError(f"Script not found for test_id={test_id}")
    return dest.read_text(encoding="utf-8")


# ── State Tool ───────────────────────────────────────────────────────────────


@mcp.tool()
def update_state_local(test_id: str, status: str, extras: dict[str, Any] | None = None) -> bool:
    """Update the in-process state.json entry for a test."""
    state_store.update_state(test_id, status, **(extras or {}))
    return True


@mcp.tool()
def check_dependencies(test_id: str) -> bool:
    """Return True only if all depends_on tests are in PASS status.

    Reads depends_on from the DB and checks state.json for each dependency.
    """
    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
    if not tc or not tc.depends_on:
        return True
    for dep_id in tc.depends_on:
        entry = state_store.get_status(str(dep_id))
        if not entry or entry.get("status") != "PASS":
            return False
    return True


@mcp.tool()
def flush_state_to_db(run_id: str) -> dict[str, int]:
    """Delegate to flush_to_postgres then clear state.json."""
    result = flush_to_postgres(run_id)
    state_store.clear()
    return result


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
def enqueue(job_id: str) -> bool:
    """Publish a job_id to RabbitMQ (3× exponential backoff).

    job_id is either a plain test_id UUID string (single-test legacy path)
    or 'hls:{hls_id}' for a grouped HLS spec run.
    """
    import time
    for attempt in range(3):
        try:
            _conn, ch = _get_producer_channel()
            ch.basic_publish(
                exchange="",
                routing_key=settings.rabbitmq_queue,
                body=job_id.encode(),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            return True
        except Exception as exc:
            logger.warning("enqueue attempt %d failed: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return False


@mcp.tool()
def requeue(job_id: str, delay_s: int = 15) -> bool:
    """Re-publish a job_id after a delay (dependency retry)."""
    import time
    time.sleep(delay_s)
    return enqueue(job_id)


@mcp.tool()
def mark_complete(test_id: str) -> bool:
    """Mark a test as complete in state.json (ACK is handled by the worker consumer)."""
    state_store.update_state(test_id, "COMPLETE")
    return True


# ── Credentials Tool ─────────────────────────────────────────────────────────


@mcp.tool()
def get_placeholders(actor: str = "user") -> dict[str, str]:
    """Return ENV placeholder tokens — never real credential values.

    The Script Generator embeds these tokens in generated .spec.ts files.
    The runner substitutes real values from environment variables at execution time.
    """
    return {
        "USER_EMAIL": "{{USER_EMAIL}}",
        "USER_PASSWORD": "{{USER_PASSWORD}}",
        "ADMIN_EMAIL": "{{ADMIN_EMAIL}}",
        "ADMIN_PASSWORD": "{{ADMIN_PASSWORD}}",
        "BASE_URL": "{{BASE_URL}}",
    }
