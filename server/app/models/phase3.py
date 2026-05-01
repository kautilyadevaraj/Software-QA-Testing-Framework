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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Approval flow (migration 0007) ────────────────────────────────────────
    # tc_number:           e.g. "TC-001" — globally unique within a planning run
    # acceptance_criteria: list of verifiable pass conditions (RTM requirement)
    # hls_id:              links this test case back to its parent HLS (Phase 2)
    # approval_status:     PENDING | APPROVED | NEEDS_EDIT
    #                      Execute is gated until ALL are APPROVED
    tc_number: Mapped[str | None] = mapped_column(
        VARCHAR(20), nullable=True, index=True
    )
    acceptance_criteria: Mapped[list[Any]] = mapped_column(
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
    result = relationship("TestResult", back_populates="test_case", uselist=False, cascade="all, delete-orphan")
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
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jira_ticket: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    test_case = relationship("TestCase", back_populates="result")
    network_logs = relationship("NetworkLog", back_populates="result", cascade="all, delete-orphan")
    retry_history = relationship("RetryHistory", back_populates="result", cascade="all, delete-orphan")


class NetworkLog(Base):
    __tablename__ = "network_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_results.test_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    is_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    result = relationship("TestResult", back_populates="network_logs")


class RetryHistory(Base):
    __tablename__ = "retry_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_results.test_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    error_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    llm_fix_applied: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    result = relationship("TestResult", back_populates="retry_history")


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
