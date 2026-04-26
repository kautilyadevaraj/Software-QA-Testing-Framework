"""SQLAlchemy models for Phase 2 — Scenario Generation & UI Discovery."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
    captured_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # Relationships
    route: Mapped["DiscoveredRoute"] = relationship(back_populates="variants")
    scenario = relationship("HighLevelScenario", back_populates="route_variants")
    recording_session: Mapped["RecordingSession"] = relationship(
        back_populates="route_variants"
    )


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
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # action_type: 'navigate' | 'click' | 'fill' | 'select' | 'hover' | 'keypress' | 'scroll'
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    element_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    element_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    network_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('navigate','click','fill','select','hover','keypress','scroll')",
            name="ck_scenario_steps_action_type",
        ),
    )

    # Relationships
    scenario = relationship("HighLevelScenario", back_populates="steps")
    recording_session: Mapped["RecordingSession"] = relationship(
        back_populates="steps"
    )