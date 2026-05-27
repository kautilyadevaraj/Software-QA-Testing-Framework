"""Run-scoped script cache for Agent A5.

The cache avoids repeated LLM script generation when the approved testcase and
its grounded A4 context have not changed. On a hit we copy the cached script
into the current run directory so Playwright evidence remains run-scoped.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from app.db.session import SessionLocal
from app.models.phase3 import TestCase
from app.services import mcp_server

logger = logging.getLogger(__name__)

SCRIPT_GENERATOR_VERSION = "a5-independent-grounded-v6"


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep only deterministic, non-secret context fields for cache invalidation."""
    env_placeholders = context.get("env_placeholders") or {}
    return {
        "title": context.get("title") or "",
        "steps": context.get("steps") or [],
        "target_page": context.get("target_page") or "",
        "auth_mode": context.get("auth_mode") or "",
        "credential_id": context.get("credential_id") or "",
        "credential_role": context.get("credential_role") or "",
        "auth_login_path": context.get("auth_login_path") or "",
        "test_id_attribute": context.get("test_id_attribute") or "",
        "recorded_steps": context.get("recorded_steps") or [],
        "recorded_variant_elements": context.get("recorded_variant_elements") or [],
        "route_map": context.get("route_map") or {},
        "route_snapshots": context.get("route_snapshots") or {},
        "dom": context.get("dom") or {},
        "env_placeholder_keys": sorted(str(k) for k in env_placeholders.keys()),
    }


def _compute_hashes(context: dict[str, Any], tc: TestCase) -> tuple[str, str]:
    content_hash = _sha256({
        "title": tc.title,
        "steps": tc.steps or [],
        "acceptance_criteria": tc.acceptance_criteria or [],
        "target_page": tc.target_page,
        "depends_on": [str(dep) for dep in (tc.depends_on or [])],
        "auth_mode": tc.auth_mode or "",
        "credential_id": str(tc.credential_id) if tc.credential_id else "",
        "credential_role": tc.credential_role or "",
    })
    context_hash = _sha256(_safe_context(context))
    return content_hash, context_hash


def cache_key_for_context(context: dict[str, Any]) -> dict[str, str]:
    """Return cache hashes for tests/debugging without mutating DB state."""
    test_id = str(context.get("test_id") or "")
    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
        if not tc:
            raise ValueError(f"TestCase not found: test_id={test_id}")
        content_hash, context_hash = _compute_hashes(context, tc)
    return {
        "content_hash": content_hash,
        "context_hash": context_hash,
        "script_generator_version": SCRIPT_GENERATOR_VERSION,
    }


def materialize_cached_script(context: dict[str, Any]) -> str | None:
    """Return a current-run script path on cache hit, otherwise None."""
    test_id = str(context.get("test_id") or "")
    project_id = str(context.get("project_id") or "")
    run_id = str(context.get("run_id") or "")
    if not test_id:
        return None

    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
        if not tc:
            return None
        content_hash, context_hash = _compute_hashes(context, tc)
        old_path = Path(tc.script_path) if tc.script_path else None
        cache_hit = (
            tc.script_status == "GENERATED"
            and tc.script_generator_version == SCRIPT_GENERATOR_VERSION
            and tc.content_hash == content_hash
            and tc.context_hash == context_hash
            and old_path is not None
            and old_path.exists()
        )
        if not cache_hit:
            return None
        script = old_path.read_text(encoding="utf-8")

    new_path = mcp_server.write_script(
        test_id,
        script,
        project_id=project_id or None,
        run_id=run_id or None,
    )
    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
        if tc:
            tc.script_path = new_path
            tc.script_status = "GENERATED"
            tc.script_error = None
            db.commit()
    logger.info("script_cache: hit test_id=%s materialized=%s", test_id[:8], new_path)
    return new_path


def store_generated_script(context: dict[str, Any], script_path: str) -> None:
    test_id = str(context.get("test_id") or "")
    if not test_id:
        return
    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
        if not tc:
            return
        content_hash, context_hash = _compute_hashes(context, tc)
        tc.script_path = script_path
        tc.content_hash = content_hash
        tc.context_hash = context_hash
        tc.script_generator_version = SCRIPT_GENERATOR_VERSION
        tc.script_status = "GENERATED"
        tc.script_error = None
        db.commit()
    logger.info("script_cache: stored test_id=%s path=%s", test_id[:8], script_path)


def mark_generation_failed(context: dict[str, Any], reason: str) -> None:
    test_id = str(context.get("test_id") or "")
    if not test_id:
        return
    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))
        if not tc:
            return
        tc.script_generator_version = SCRIPT_GENERATOR_VERSION
        tc.script_status = "HUMAN_REVIEW"
        tc.script_error = reason[:4000]
        db.commit()
