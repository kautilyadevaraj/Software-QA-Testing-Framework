"""Pydantic schemas for Scenario Generation, UI Discovery, and the Recorder API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Shared literals ────────────────────────────────────────────────────────

ScenarioSource = Literal["agent_1", "agent_2", "manual"]
ScenarioStatus = Literal["pending", "completed"]
ScenarioType = Literal[
    "ALL",
    "HLS",
    "Functional",
    "Technical",
    "API",
    "Security",
    "Performance",
    "Integration",
    "Data",
    "Compliance",
    "Usability",
]
ScenarioAccessMode = Literal["UI_ONLY_WEB", "UI_AND_API", "TECHNICAL_REVIEW"]
ScenarioLevel = Literal["HLS", "DETAILED_HLS"]


# ── HLS Generate / Approve (main branch) ──────────────────────────────────

class PreviewScenarioRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    source: ScenarioSource

    @field_validator("title", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class GenerateScenariosResponse(BaseModel):
    scenarios: list[PreviewScenarioRequest]


class GenerateScenariosRequest(BaseModel):
    max_scenarios: int | None = Field(default=20, ge=1, le=500)
    scenario_types: list[ScenarioType] = Field(default_factory=lambda: ["ALL"])
    access_mode: ScenarioAccessMode = "UI_ONLY_WEB"
    scenario_level: ScenarioLevel = "HLS"
    existing_scenarios: list[PreviewScenarioRequest] = Field(default_factory=list)

    @field_validator("scenario_types")
    @classmethod
    def normalize_scenario_types(cls, value: list[ScenarioType]) -> list[ScenarioType]:
        if not value:
            return ["ALL"]
        unique: list[ScenarioType] = []
        for item in value:
            if item == "ALL":
                return ["ALL"]
            if item not in unique:
                unique.append(item)
        return unique


class ApproveScenariosRequest(BaseModel):
    scenarios: list[PreviewScenarioRequest]


class ApproveScenariosResponse(BaseModel):
    saved: int


class ManualScenarioRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""

    @field_validator("title", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ScenarioUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    status: ScenarioStatus | None = None
    current_user_id: uuid.UUID | None = None

    @field_validator("title", "description")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value


class HighLevelScenarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str
    source: ScenarioSource
    status: ScenarioStatus
    completed_by: uuid.UUID | None
    completed_by_name: str | None
    created_at: datetime
    updated_at: datetime


class ScenarioListResponse(BaseModel):
    scenarios: list[HighLevelScenarioResponse]


# ── Trigger response ────────────────────────────────────────────────────────

class TriggerScenarioResponse(BaseModel):
    """Returned when a Launch is triggered from the Web UI."""
    triggered: bool
    scenario_id: uuid.UUID


# ── Phase 2 / Recording (ui-discovery branch) ──────────────────────────────

class ScenarioCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    source: str = Field(default="manual")  # agent_1 | agent_2 | manual


class ScenarioUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = None


class LockScenariosResponse(BaseModel):
    """Returned when the user locks the scenario list."""
    locked: bool
    sessions_created: int  # one recording_session created per scenario


class Phase2StatusResponse(BaseModel):
    """Overall Phase 2 progress, used by the frontend to decide which sub-step to show."""
    phase_2_locked: bool
    total_scenarios: int
    recorded_scenarios: int       # sessions with status=completed
    all_recorded: bool            # True when recorded_scenarios == total_scenarios


class RecordingSetupResponse(BaseModel):
    """The one-time setup command shown to the tester."""
    setup_command: str
    recorder_token: str           # shown so tester can verify it matches the script


class RecordingSessionResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    scenario_id: uuid.UUID
    scenario_title: str           # joined from test_scenarios
    status: str                   # pending | in_progress | completed | failed
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    step_count: int               # count of scenario_steps

    model_config = {"from_attributes": True}


class RecordingSessionListResponse(BaseModel):
    items: list[RecordingSessionResponse]


# ── Recorder API (called by the Python recorder script) ───────────────────

class RecorderProjectInfo(BaseModel):
    """Returned to the recorder script on startup."""
    project_id: uuid.UUID
    project_name: str
    project_url: str
    scenarios: list[RecorderScenarioInfo]


class RecorderScenarioInfo(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    status: str  # pending | completed


class RecorderSessionCreate(BaseModel):
    scenario_id: uuid.UUID


class RecorderSessionResponse(BaseModel):
    id: uuid.UUID
    status: str


class RecorderRouteUpsert(BaseModel):
    session_id: uuid.UUID
    scenario_id: uuid.UUID
    url: str
    title: str | None = None
    html_base64: str | None = None          # base64-encoded page HTML
    accessibility_tree: dict | None = None
    interactive_elements: list[dict] | None = None
    screenshot_base64: str | None = None    # base64-encoded PNG
    network_calls: list[dict] | None = None


class RecorderRouteResponse(BaseModel):
    route_id: uuid.UUID
    variant_id: uuid.UUID
    is_new_route: bool


class RecorderStepCreate(BaseModel):
    step_index: int
    action_type: str   # navigate | click | fill | select | hover | keypress | scroll
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    element_text: str | None = None
    element_type: str | None = None
    screenshot_base64: str | None = None
    network_calls: list[dict] | None = None


class RecorderStepResponse(BaseModel):
    id: uuid.UUID


# ── Pulse endpoint ──────────────────────────────────────────────────────────

class PulseResponse(BaseModel):
    """Polled by the local daemon. Contains scenario_id when a launch was triggered."""
    scenario_id: uuid.UUID | None = None
    scenario_title: str | None = None
    project_url: str | None = None
