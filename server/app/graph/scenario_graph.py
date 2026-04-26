from __future__ import annotations

from typing import TypedDict

from app.agents.agent1_brd import run_agent1_brd
from app.agents.agent2_swagger import run_agent2_swagger
from app.agents.agent3_dedup import run_agent3_dedup
from app.agents.scenario_common import PreviewScenario, ScenarioGenerationOptions, limit_scenarios


class ScenarioGraphState(TypedDict, total=False):
    project_id: str
    agent_1_scenarios: list[PreviewScenario]
    agent_2_scenarios: list[PreviewScenario]
    scenarios: list[PreviewScenario]
    generation_options: ScenarioGenerationOptions
    existing_scenarios: list[PreviewScenario]


def build_scenario_graph():
    from langgraph.graph import END, StateGraph

    graph = StateGraph(ScenarioGraphState)
    graph.add_node("agent_1", run_agent1_brd)
    graph.add_node("agent_2", run_agent2_swagger)
    graph.add_node("agent_3", run_agent3_dedup)
    graph.set_entry_point("agent_1")
    graph.add_edge("agent_1", "agent_2")
    graph.add_edge("agent_2", "agent_3")
    graph.add_edge("agent_3", END)
    return graph.compile()


def run_scenario_graph(
    project_id: str,
    generation_options: ScenarioGenerationOptions | None = None,
    existing_scenarios: list[PreviewScenario] | None = None,
) -> list[PreviewScenario]:
    graph = build_scenario_graph()
    options: ScenarioGenerationOptions = generation_options or {
        "max_scenarios": 20,
        "scenario_types": ["ALL"],
        "access_mode": "UI_ONLY_WEB",
        "scenario_level": "HLS",
    }
    result = graph.invoke(
        {
            "project_id": project_id,
            "agent_1_scenarios": [],
            "agent_2_scenarios": [],
            "scenarios": [],
            "generation_options": options,
            "existing_scenarios": existing_scenarios or [],
        }
    )
    return limit_scenarios(result.get("scenarios", []), options.get("max_scenarios"))
