from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
