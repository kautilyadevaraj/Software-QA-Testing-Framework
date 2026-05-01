"""Agent A4 — Context Builder (no LLM).

Assembles the full execution context for a test case by combining:
  - test steps (from DB via test_id)
  - DOM snapshot for the target page (HTML + accessibility tree + interactive elements)
  - ENV placeholder tokens (credentials)

Entry point: build_context(test_id, project_id) -> dict
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.phase3 import TestCase
from app.services import mcp_server

logger = logging.getLogger(__name__)


async def build_context(test_id: str, project_id: str) -> dict[str, Any]:
    """Return a ContextObject dict for Agent A5 to consume.

    Raises ValueError if test_case or DOM snapshot is not found.

    Returns:
        {
            "test_id": str,
            "title": str,
            "steps": list[str],
            "target_page": str,
            "dom": {
                "path": str,
                "html": str,                   # minified
                "accessibility_tree": list,
                "interactive_elements": list,
            },
            "env_placeholders": {
                "USER_EMAIL": "{{USER_EMAIL}}",
                ...
            },
            "depends_on": list[str],
        }
    """
    # 1. Load test case from DB
    with SessionLocal() as db:
        tc = db.get(TestCase, uuid.UUID(test_id))

    if not tc:
        raise ValueError(f"TestCase not found: test_id={test_id}")

    # 2. Get DOM snapshot (HTML already minified by mcp_server.get_snapshot)
    try:
        dom = mcp_server.get_snapshot(project_id, tc.target_page)
    except ValueError as exc:
        logger.warning("agent4: DOM snapshot missing for page '%s': %s", tc.target_page, exc)
        dom = {
            "path": tc.target_page,
            "html": "",
            "accessibility_tree": [],
            "interactive_elements": [],
        }

    # 3. Get credential placeholders
    env_placeholders = mcp_server.get_placeholders()

    context: dict[str, Any] = {
        "test_id": test_id,
        "title": tc.title,
        "steps": tc.steps,
        "target_page": tc.target_page,
        "dom": dom,
        "env_placeholders": env_placeholders,
        "depends_on": [str(d) for d in (tc.depends_on or [])],
    }

    logger.debug("agent4: built context for test_id=%s page=%s", test_id, tc.target_page)
    return context
