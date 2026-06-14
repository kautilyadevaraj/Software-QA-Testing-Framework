"""SQLAlchemy models for Phase 2 — Scenario Generation & UI Discovery."""

import uuid
from datetime import datetime

import uuid6
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class RecordingSession(Base):
    __tablename__ = "recording_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("high_level_scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # status: 'pending' | 'in_progress' | 'completed' | 'failed'
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'failed')",
            name="ck_recording_sessions_status",
        ),
    )

    # Relationships
    scenario = relationship("HighLevelScenario", back_populates="recording_sessions")
    route_variants: Mapped[list["RouteVariant"]] = relationship(
        back_populates="recording_session", cascade="all, delete-orphan"
    )
    steps: Mapped[list["ScenarioStep"]] = relationship(
        back_populates="recording_session", cascade="all, delete-orphan"
    )
    flows: Mapped[list["RecordingFlow"]] = relationship(
        back_populates="recording_session", cascade="all, delete-orphan"
    )


class RecordingFlow(Base):
    __tablename__ = "recording_flows"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7
    )
    recording_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recording_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hls_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("high_level_scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    flow_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    phase3_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("recording_id", "flow_index", name="uq_recording_flows_recording_flow_index"),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'failed')",
            name="ck_recording_flows_status",
        ),
    )

    recording_session: Mapped["RecordingSession"] = relationship(back_populates="flows")
    route_variants: Mapped[list["RouteVariant"]] = relationship(back_populates="flow")
    steps: Mapped[list["ScenarioStep"]] = relationship(back_populates="flow")


class DiscoveredRoute(Base):
    __tablename__ = "discovered_routes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    full_url: Mapped[str] = mapped_column(Text, nullable=False)
    page_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    accessibility_tree: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    interactive_elements: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("project_id", "path", name="uq_discovered_routes_project_path"),
    )

    # Relationships
    variants: Mapped[list["RouteVariant"]] = relationship(
        back_populates="route", cascade="all, delete-orphan"
    )


class RouteVariant(Base):
    __tablename__ = "route_variants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("discovered_routes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("high_level_scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recording_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recording_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    flow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recording_flows.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    html_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    accessibility_tree: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    interactive_elements: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    network_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    snapshot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    assertion_candidates: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # Relationships
    route: Mapped["DiscoveredRoute"] = relationship(back_populates="variants")
    scenario = relationship("HighLevelScenario", back_populates="route_variants")
    recording_session: Mapped["RecordingSession"] = relationship(
        back_populates="route_variants"
    )
    flow: Mapped["RecordingFlow | None"] = relationship(back_populates="route_variants")


class ScenarioStep(Base):
    __tablename__ = "scenario_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("high_level_scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recording_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recording_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    flow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recording_flows.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # action_type: navigate | click | fill | select | hover | keypress | scroll | check | uncheck | slide | submit
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    selector_candidates: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_value_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    element_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    element_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    selector_stability: Mapped[str | None] = mapped_column(Text, nullable=True)
    playwright_locator: Mapped[str | None] = mapped_column(Text, nullable=True)
    accessible_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    url_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    url_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    caused_navigation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    route_variant_before_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("route_variants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    route_variant_after_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("route_variants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    semantic_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    network_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Phase 2 step-quality fields
    is_noise: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    noise_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    selector_quality_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('navigate','click','fill','select','hover','keypress','scroll','check','uncheck','slide','submit')",
            name="ck_scenario_steps_action_type",
        ),
    )

    # Relationships
    scenario = relationship("HighLevelScenario", back_populates="steps")
    recording_session: Mapped["RecordingSession"] = relationship(
        back_populates="steps"
    )
    flow: Mapped["RecordingFlow | None"] = relationship(back_populates="steps")


class RecordedRouteTransition(Base):
    __tablename__ = "recorded_route_transitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7
    )
    recording_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recording_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    flow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recording_flows.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    hls_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("high_level_scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scenario_steps.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    from_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    element_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    accessible_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    transition_type: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    before_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("route_variants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    after_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("route_variants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("recording_id", "step_index", name="uq_recorded_route_transitions_recording_step"),
    )


class RecordedAssertionCandidate(Base):
    __tablename__ = "recorded_assertion_candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7
    )
    recording_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recording_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    flow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recording_flows.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    hls_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("high_level_scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("route_variants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("snapshot_id", "candidate_index", name="uq_recorded_assertions_snapshot_index"),
    )
