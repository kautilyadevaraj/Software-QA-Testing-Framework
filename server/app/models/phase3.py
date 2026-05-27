from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import uuid6
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    VARCHAR,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TestCase(Base):
    __tablename__ = "test_cases"

    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    depends_on: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    target_page: Mapped[str] = mapped_column(Text, nullable=False)
    script_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, index=True)
    context_hash: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, index=True)
    script_generator_version: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    script_status: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True, index=True)
    script_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_runs.run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    auth_mode: Mapped[str] = mapped_column(
        VARCHAR(32), nullable=False, default="authenticated", index=True
    )
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credential_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    credential_role: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)

    # ── Approval flow (migration 0007) ────────────────────────────────────────
    # tc_number:           e.g. "TC-001" — globally unique within a planning run
    # acceptance_criteria: list of verifiable pass conditions (RTM requirement)
    # hls_id:              links this test case back to its parent HLS (Phase 2)
    # approval_status:     PENDING | APPROVED | NEEDS_EDIT | EXCLUDED
    #                      Execute runs APPROVED cases and ignores EXCLUDED.
    tc_number: Mapped[str | None] = mapped_column(
        VARCHAR(20), nullable=True, index=True
    )
    acceptance_criteria: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    assertion_evidence: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    hls_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # Stored as plain UUID — no enforced FK constraint.
        # Rationale: deleting a Phase 2 scenario must never cascade-delete approved
        # test cases. The mcp_server.get_test_cases_for_run() helper does the JOIN.
        nullable=True,
        index=True,
    )
    approval_status: Mapped[str] = mapped_column(
        VARCHAR(20), nullable=False, default="PENDING", index=True
    )

    project = relationship("Project")
    run = relationship("TestRun", foreign_keys=[run_id])
    credential = relationship("CredentialProfile", foreign_keys=[credential_id])
    results = relationship("TestResult", back_populates="test_case", cascade="all, delete-orphan")
    review_items = relationship("ReviewQueueItem", back_populates="test_case", cascade="all, delete-orphan")


class TestRun(Base):
    __tablename__ = "test_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    human_review: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="running")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # run_type: 'plan' = A3 planning only | 'execute' = full Playwright run
    # Frontend uses this to decide which UI panel to show after a POST response.
    run_type: Mapped[str] = mapped_column(
        VARCHAR(20), nullable=False, default="execute"
    )

    project = relationship("Project")
    review_items = relationship("ReviewQueueItem", back_populates="run", cascade="all, delete-orphan")


class TestResult(Base):
    __tablename__ = "test_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_cases.test_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_runs.run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jira_ticket: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("run_id", "test_id", name="uq_test_results_run_test"),
    )

    test_case = relationship("TestCase", back_populates="results")
    run = relationship("TestRun", foreign_keys=[run_id])
    network_logs = relationship("NetworkLog", back_populates="result", cascade="all, delete-orphan")
    retry_history = relationship("RetryHistory", back_populates="result", cascade="all, delete-orphan")


class AuthState(Base):
    __tablename__ = "auth_states"

    __table_args__ = (
        UniqueConstraint("run_id", "credential_id", name="uq_auth_states_run_credential"),
        CheckConstraint(
            "status IN ('pending', 'ready', 'failed', 'expired')",
            name="ck_auth_states_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credential_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    storage_state_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    project = relationship("Project")
    run = relationship("TestRun")
    credential = relationship("CredentialProfile")


class Phase3ExecutionState(Base):
    __tablename__ = "phase3_execution_state"

    __table_args__ = (
        UniqueConstraint("run_id", "test_id", name="uq_phase3_execution_state_run_test"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_cases.test_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="PENDING")
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    jira_ticket: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    network_logs_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    run = relationship("TestRun")
    test_case = relationship("TestCase")


class Phase3HlsGroup(Base):
    """Durable HLS group ordering — replaces state.json `hls:{id}` entries.

    Stores the ordered list of test_ids for a grouped spec so that workers in
    separate containers can match positional test() results to test_ids after
    a node restart.
    """
    __tablename__ = "phase3_hls_groups"

    hls_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    ordered_test_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Phase3JobClaim(Base):
    """Worker idempotency lock — one row per RabbitMQ job_id.

    Worker flow:
      INSERT … ON CONFLICT (job_id) DO NOTHING → if insert succeeded we own
      the job; otherwise another worker already has/had it and we ACK+skip.
    """
    __tablename__ = "phase3_job_claims"

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    status: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="claimed")
    worker_host: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Phase3Artifact(Base):
    """Durable artifact registry for scripts, traces, videos, reports, and CSVs."""
    __tablename__ = "phase3_artifacts"

    __table_args__ = (
        UniqueConstraint("run_id", "test_id", "artifact_type", "path", name="uq_phase3_artifact_identity"),
        CheckConstraint(
            "artifact_type IN ('SCRIPT', 'TRACE', 'VIDEO', 'SCREENSHOT', 'XRAY_CSV', 'MANIFEST', 'REPORT')",
            name="ck_phase3_artifacts_type",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'DELETED')",
            name="ck_phase3_artifacts_status",
        ),
    )

    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    test_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_cases.test_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    artifact_type: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="ACTIVE", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    project = relationship("Project")
    run = relationship("TestRun")
    test_case = relationship("TestCase")


class NetworkLog(Base):
    __tablename__ = "network_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    test_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_results.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    is_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    result = relationship("TestResult", back_populates="network_logs", foreign_keys=[test_result_id])


class RetryHistory(Base):
    __tablename__ = "retry_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    test_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_results.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    error_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    llm_fix_applied: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    result = relationship("TestResult", back_populates="retry_history", foreign_keys=[test_result_id])


class ReviewQueueItem(Base):
    __tablename__ = "review_queue"

    __table_args__ = (
        CheckConstraint("review_type IN ('BUG', 'TASK')", name="ck_review_queue_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_cases.test_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_type: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="pending")
    jira_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    test_case = relationship("TestCase", back_populates="review_items")
    run = relationship("TestRun", back_populates="review_items")
