from __future__ import annotations

import logging
from uuid import UUID
from typing import Any

from sqlalchemy import select

from app.agents.scenario_common import (
    PreviewScenario,
    build_chunk_text_batches,
    build_text_batches,
    generate_scenarios_from_batches,
    scroll_chunks,
)
from app.db.session import SessionLocal
from app.models.project import APIEndpoint

logger = logging.getLogger(__name__)

API_CATEGORIES = ["swagger", "openapi", "swagger_docs"]

API_PROMPT = """You are Agent 2 in a QA scenario-generation graph.
Your task is to study API documentation chunks and produce up to {max_scenarios}
high-level testing scenarios grounded only in those chunks.

{shared_generation_instruction}

Source-specific focus:
- Treat endpoints, paths, methods, summaries, request fields, response meanings, status codes, and auth requirements as evidence of product capabilities.
- Infer user-facing workflows from those capabilities, then translate them into UI-interactable HLS scenarios.
- Group related endpoints into meaningful product flows when they describe one tester journey.
- Endpoint actions that indicate create/update/delete/search/list/export/import/upload/download should become corresponding UI journeys only when the endpoint text supports that feature.
- Do not output curl/API-call scenarios, raw payload checks, database checks, server-log checks, or implementation checks.
- Do not assume any domain, resource, module, or feature unless the endpoint text says so.

{access_mode_instruction}
{scenario_level_instruction}
{scenario_type_instruction}
{existing_scenarios_instruction}
No preamble, no explanation, no markdown backticks.

API documentation:
{document_text}
"""


def _load_api_endpoint_batches(project_id: str) -> list[str]:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(APIEndpoint)
            .where(APIEndpoint.project_id == UUID(project_id))
            .order_by(APIEndpoint.path.asc(), APIEndpoint.method.asc())
        ).scalars().all()
        fragments = [
            f"{endpoint.method or 'METHOD'} {endpoint.path or '/'}\n{endpoint.description or ''}"
            for endpoint in rows
        ]
        return build_text_batches(fragments)
    finally:
        db.close()


def run_agent2_swagger(state: dict[str, Any]) -> dict[str, list[PreviewScenario]]:
    project_id = str(state["project_id"])
    try:
        options = state.get("generation_options", {})
        max_scenarios = options.get("max_scenarios") if isinstance(options, dict) else None
        scenario_types = options.get("scenario_types") if isinstance(options, dict) else None
        access_mode = options.get("access_mode") if isinstance(options, dict) else None
        scenario_level = options.get("scenario_level") if isinstance(options, dict) else None
        existing_scenarios = state.get("existing_scenarios", [])
        try:
            chunks = scroll_chunks(project_id, API_CATEGORIES)
        except Exception as scroll_error:
            logger.warning("agent_2 could not read API chunks from Qdrant; falling back to stored API endpoints: %s", scroll_error)
            chunks = []

        if not chunks:
            logger.info("agent_2 found no Qdrant chunks for project_id=%s categories=%s", project_id, API_CATEGORIES)

        text_batches = build_chunk_text_batches(chunks)
        if not text_batches:
            text_batches = _load_api_endpoint_batches(project_id)

        if not text_batches:
            return {"agent_2_scenarios": []}

        scenarios = generate_scenarios_from_batches(
            API_PROMPT,
            text_batches,
            agent_name="agent_2",
            source="agent_2",
            max_scenarios=max_scenarios,
            scenario_types=scenario_types,
            access_mode=access_mode,
            scenario_level=scenario_level,
            existing_scenarios=existing_scenarios,
        )
        return {"agent_2_scenarios": scenarios}
    except Exception as error:
        logger.exception("agent_2 failed for project_id=%s: %s", project_id, error)
        return {"agent_2_scenarios": []}
