"""Pydantic schemas for Phase 2 — Scenario Generation & UI Discovery."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Test Scenario ──────────────────────────────────────────────────────────

class ScenarioCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    source: str = Field(default="manual")  # agent_1 | agent_2 | manual


class ScenarioUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = None


class ScenarioResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str | None
    source: str
    status: str
    completed_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    # Derived — populated by the service layer
    recording_status: str | None = None  # pending | in_progress | completed | failed

    model_config = {"from_attributes": True}


class ScenarioListResponse(BaseModel):
    items: list[ScenarioResponse]
    total: int


# ── Lock ───────────────────────────────────────────────────────────────────

class LockScenariosResponse(BaseModel):
    """Returned when the user locks the scenario list."""
    locked: bool
    sessions_created: int  # one recording_session created per scenario


# ── Phase 2 Status ─────────────────────────────────────────────────────────

class Phase2StatusResponse(BaseModel):
    """Overall Phase 2 progress, used by the frontend to decide which sub-step to show."""
    phase_2_locked: bool
    total_scenarios: int
    recorded_scenarios: int       # sessions with status=completed
    all_recorded: bool            # True when recorded_scenarios == total_scenarios


# ── Recording Setup ────────────────────────────────────────────────────────

class RecordingSetupResponse(BaseModel):
    """The one-time setup command shown to the tester."""
    setup_command: str
    recorder_token: str           # shown so tester can verify it matches the script


# ── Recording Session (frontend view) ──────────────────────────────────────

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