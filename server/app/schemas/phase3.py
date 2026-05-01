from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class TriggerRunResponse(BaseModel):
    run_id: uuid.UUID
    status: str


# Returned by POST /phase3/plan
class PlanRunResponse(BaseModel):
    run_id: uuid.UUID
    status: str          # always "planned"
    total_test_cases: int


# Body for POST /phase3/execute
class ExecuteRequest(BaseModel):
    run_id: uuid.UUID    # the planning run_id from PlanRunResponse


# Body for PATCH /phase3/test-cases/{test_id}/approval
class ApprovalPatch(BaseModel):
    status: Literal["APPROVED", "NEEDS_EDIT"]


# Body for PATCH /phase3/approve-all
class ApproveAllRequest(BaseModel):
    run_id: uuid.UUID


# Shape returned by tc-document/json and approval endpoints
class TestCaseApprovalResponse(BaseModel):
    test_id: uuid.UUID
    tc_number: str | None
    title: str
    steps: list[Any]
    acceptance_criteria: list[Any]
    target_page: str
    hls_id: uuid.UUID | None
    scenario_title: str | None
    approval_status: str
    depends_on_titles: list[str]

    model_config = {"from_attributes": True}


# Body for PATCH /phase3/test-cases/{test_id}/content  (inline human edits)
class UpdateTestCaseRequest(BaseModel):
    title: str | None = None
    steps: list[str] | None = None
    acceptance_criteria: list[str] | None = None


class RunStatusResponse(BaseModel):
    run_id: uuid.UUID
    project_id: uuid.UUID
    total: int
    passed: int
    failed: int
    skipped: int
    human_review: int
    duration_seconds: int | None
    status: str
    # 'plan' = A3 planning run | 'execute' = full Playwright execution run
    # Frontend uses this to know which UI panel to render.
    run_type: str = "execute"
    created_at: datetime


class ReviewQueueItem(BaseModel):
    id: uuid.UUID
    test_id: uuid.UUID
    run_id: uuid.UUID
    review_type: Literal["BUG", "TASK"]
    evidence: dict[str, Any]
    status: str
    jira_ref: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewQueuePatch(BaseModel):
    jira_ref: str | None = None
    status: str | None = None


class ScriptResponse(BaseModel):
    test_id: str
    script_content: str


class RerunRequest(BaseModel):
    script_content: str


class RaiseJiraRequest(BaseModel):
    review_queue_id: uuid.UUID
    issue_type: Literal["Bug", "Task"] = "Bug"
    summary: str
    description: str | None = None
