from __future__ import annotations

import logging
from typing import Any

from app.agents.scenario_common import (
    PreviewScenario,
    build_chunk_text_batches,
    generate_scenarios_from_batches,
    scroll_chunks,
)

logger = logging.getLogger(__name__)

BUSINESS_CATEGORIES = ["brd", "wsp", "wbs", "fsd", "assumptions", "assumption"]

BUSINESS_PROMPT = """You are Agent 1 in a QA scenario-generation graph.
Your task is to study business-side documentation chunks and produce up to {max_scenarios}
high-level testing scenarios grounded only in those chunks.

{shared_generation_instruction}

Source-specific focus:
- Use BRD/FSD/WBS/assumption-style content to identify business goals, user journeys, roles, workflow stages, validations, decisions, constraints, exceptions, and acceptance expectations.
- Convert documented business behavior into tester-executable HLS items.
- Do not create tiny UI steps, field-level validations, generic platform checks, or duplicate wording.

{access_mode_instruction}
{scenario_level_instruction}
{scenario_type_instruction}
{existing_scenarios_instruction}
No preamble, no explanation, no markdown backticks.

Business documentation:
{document_text}
"""


def run_agent1_brd(state: dict[str, Any]) -> dict[str, list[PreviewScenario]]:
    project_id = str(state["project_id"])
    try:
        options = state.get("generation_options", {})
        max_scenarios = options.get("max_scenarios") if isinstance(options, dict) else None
        scenario_types = options.get("scenario_types") if isinstance(options, dict) else None
        access_mode = options.get("access_mode") if isinstance(options, dict) else None
        scenario_level = options.get("scenario_level") if isinstance(options, dict) else None
        existing_scenarios = state.get("existing_scenarios", [])
        chunks = scroll_chunks(project_id, BUSINESS_CATEGORIES)
        if not chunks:
            logger.info("agent_1 found no Qdrant chunks for project_id=%s categories=%s", project_id, BUSINESS_CATEGORIES)
            return {"agent_1_scenarios": []}

        text_batches = build_chunk_text_batches(chunks)
        if not text_batches:
            return {"agent_1_scenarios": []}

        scenarios = generate_scenarios_from_batches(
            BUSINESS_PROMPT,
            text_batches,
            agent_name="agent_1",
            source="agent_1",
            max_scenarios=max_scenarios,
            scenario_types=scenario_types,
            access_mode=access_mode,
            scenario_level=scenario_level,
            existing_scenarios=existing_scenarios,
        )
        return {"agent_1_scenarios": scenarios}
    except Exception as error:
        logger.exception("agent_1 failed for project_id=%s: %s", project_id, error)
        return {"agent_1_scenarios": []}
