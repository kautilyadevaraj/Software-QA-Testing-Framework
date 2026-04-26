"""Phase 2 UI Discovery tables

Revision ID: 0004_phase2_ui_discovery
Revises: 0003_add_high_level_scenarios
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0004_phase2_ui_discovery"
down_revision = "0003_add_high_level_scenarios"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extend projects table ───────────────────────────────────────────────
    op.add_column(
        "projects",
        sa.Column(
            "recorder_token",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "phase_2_locked",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # ── test_scenarios ──────────────────────────────────────────────────────
    op.create_table(
        "test_scenarios",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column(
            "completed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source IN ('agent_1', 'agent_2', 'manual')",
            name="ck_test_scenarios_source",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed')",
            name="ck_test_scenarios_status",
        ),
    )
    op.create_index("ix_test_scenarios_project_id", "test_scenarios", ["project_id"])

    # ── recording_sessions ──────────────────────────────────────────────────
    op.create_table(
        "recording_sessions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scenario_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'failed')",
            name="ck_recording_sessions_status",
        ),
    )
    op.create_index(
        "ix_recording_sessions_project_id", "recording_sessions", ["project_id"]
    )
    op.create_index(
        "ix_recording_sessions_scenario_id", "recording_sessions", ["scenario_id"]
    )

    # ── discovered_routes ───────────────────────────────────────────────────
    #
    # Global route registry per project. Each row represents a unique URL path
    # within the application. Enriched across all scenario recordings — the
    # interactive_elements and accessibility_tree columns are overwritten with
    # the superset of all elements seen across visits.
    #
    op.create_table(
        "discovered_routes",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.Text(), nullable=False),       # e.g. /dashboard
        sa.Column("full_url", sa.Text(), nullable=False),   # full URL at first visit
        sa.Column("page_title", sa.Text(), nullable=True),
        sa.Column("html_path", sa.Text(), nullable=True),   # path on VM disk
        sa.Column("accessibility_tree", JSONB(), nullable=True),
        sa.Column("interactive_elements", JSONB(), nullable=True),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "project_id", "path", name="uq_discovered_routes_project_path"
        ),
    )
    op.create_index(
        "ix_discovered_routes_project_id", "discovered_routes", ["project_id"]
    )

    # ── route_variants ──────────────────────────────────────────────────────
    #
    # Per-scenario visit snapshot. Captures role-specific and state-specific
    # UI differences for the same URL path across different scenario recordings.
    #
    op.create_table(
        "route_variants",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "route_id",
            UUID(as_uuid=True),
            sa.ForeignKey("discovered_routes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scenario_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recording_session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recording_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("html_path", sa.Text(), nullable=True),
        sa.Column("accessibility_tree", JSONB(), nullable=True),
        sa.Column("interactive_elements", JSONB(), nullable=True),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column("network_calls", JSONB(), nullable=True),
        sa.Column(
            "captured_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_route_variants_route_id", "route_variants", ["route_id"])
    op.create_index(
        "ix_route_variants_scenario_id", "route_variants", ["scenario_id"]
    )
    op.create_index(
        "ix_route_variants_project_id", "route_variants", ["project_id"]
    )

    # ── scenario_steps ──────────────────────────────────────────────────────
    op.create_table(
        "scenario_steps",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "scenario_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recording_session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recording_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("selector", sa.Text(), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("element_text", sa.Text(), nullable=True),
        sa.Column("element_type", sa.Text(), nullable=True),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column("network_calls", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "action_type IN ('navigate','click','fill','select','hover','keypress','scroll')",
            name="ck_scenario_steps_action_type",
        ),
    )
    op.create_index(
        "ix_scenario_steps_scenario_id", "scenario_steps", ["scenario_id"]
    )
    op.create_index(
        "ix_scenario_steps_recording_session_id",
        "scenario_steps",
        ["recording_session_id"],
    )


def downgrade() -> None:
    op.drop_table("scenario_steps")
    op.drop_table("route_variants")
    op.drop_table("discovered_routes")
    op.drop_table("recording_sessions")
    op.drop_table("test_scenarios")
    op.drop_column("projects", "phase_2_locked")
    op.drop_column("projects", "recorder_token")