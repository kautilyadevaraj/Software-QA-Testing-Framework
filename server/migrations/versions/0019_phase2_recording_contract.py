"""Add Phase 2 ordered recording contract.

Revision ID: 0019_phase2_recording_contract
Revises: 0018_step_route_links
Create Date: 2026-05-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0019_phase2_recording_contract"
down_revision = "0018_step_route_links"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    inspector = _inspector()
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name)) or any(
        constraint["name"] == index_name for constraint in inspector.get_unique_constraints(table_name)
    )


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    inspector = _inspector()
    constraints = (
        inspector.get_unique_constraints(table_name)
        + inspector.get_check_constraints(table_name)
        + inspector.get_foreign_keys(table_name)
    )
    return any(constraint["name"] == constraint_name for constraint in constraints)


def _foreign_key_exists(table_name: str, constrained_columns: list[str], referred_table: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(
        fk.get("constrained_columns") == constrained_columns
        and fk.get("referred_table") == referred_table
        for fk in _inspector().get_foreign_keys(table_name)
    )


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        op.add_column(table_name, column)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str], unique: bool = False) -> None:
    if not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    if not _table_exists("recording_flows"):
        op.create_table(
            "recording_flows",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("recording_id", UUID(as_uuid=True), sa.ForeignKey("recording_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("hls_id", UUID(as_uuid=True), sa.ForeignKey("high_level_scenarios.id", ondelete="CASCADE"), nullable=False),
            sa.Column("flow_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
            sa.Column("phase3_ready", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
            sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
            sa.Column("metadata_json", JSONB(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.CheckConstraint(
                "status IN ('pending', 'in_progress', 'completed', 'failed')",
                name="ck_recording_flows_status",
            ),
            sa.UniqueConstraint("recording_id", "flow_index", name="uq_recording_flows_recording_flow_index"),
        )
    _create_index_if_missing("recording_flows", "ix_recording_flows_recording_id", ["recording_id"])
    _create_index_if_missing("recording_flows", "ix_recording_flows_project_id", ["project_id"])
    _create_index_if_missing("recording_flows", "ix_recording_flows_hls_id", ["hls_id"])

    _add_column_if_missing("route_variants", sa.Column("flow_id", UUID(as_uuid=True), nullable=True))
    _add_column_if_missing("route_variants", sa.Column("snapshot_index", sa.Integer(), nullable=True))
    _add_column_if_missing("route_variants", sa.Column("snapshot_kind", sa.Text(), nullable=True))
    _add_column_if_missing("route_variants", sa.Column("assertion_candidates", JSONB(), nullable=True))
    _add_column_if_missing("route_variants", sa.Column("metadata_json", JSONB(), nullable=True))
    if not _foreign_key_exists("route_variants", ["flow_id"], "recording_flows"):
        op.create_foreign_key(
            "fk_route_variants_flow_id",
            "route_variants",
            "recording_flows",
            ["flow_id"],
            ["id"],
            ondelete="CASCADE",
        )
    _create_index_if_missing("route_variants", "ix_route_variants_flow_id", ["flow_id"])
    _create_index_if_missing(
        "route_variants",
        "ix_route_variants_recording_snapshot_index",
        ["recording_session_id", "snapshot_index"],
    )
    if not _constraint_exists("route_variants", "ux_route_variants_recording_snapshot_index"):
        op.create_unique_constraint(
            "ux_route_variants_recording_snapshot_index",
            "route_variants",
            ["recording_session_id", "snapshot_index"],
        )

    _add_column_if_missing("scenario_steps", sa.Column("flow_id", UUID(as_uuid=True), nullable=True))
    _add_column_if_missing("scenario_steps", sa.Column("selector_candidates", JSONB(), nullable=True))
    _add_column_if_missing("scenario_steps", sa.Column("input_value_kind", sa.Text(), nullable=True))
    if not _foreign_key_exists("scenario_steps", ["flow_id"], "recording_flows"):
        op.create_foreign_key(
            "fk_scenario_steps_flow_id",
            "scenario_steps",
            "recording_flows",
            ["flow_id"],
            ["id"],
            ondelete="CASCADE",
        )
    _create_index_if_missing("scenario_steps", "ix_scenario_steps_flow_id", ["flow_id"])

    if not _table_exists("recorded_route_transitions"):
        op.create_table(
            "recorded_route_transitions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("recording_id", UUID(as_uuid=True), sa.ForeignKey("recording_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("flow_id", UUID(as_uuid=True), sa.ForeignKey("recording_flows.id", ondelete="CASCADE"), nullable=True),
            sa.Column("hls_id", UUID(as_uuid=True), sa.ForeignKey("high_level_scenarios.id", ondelete="CASCADE"), nullable=False),
            sa.Column("step_id", UUID(as_uuid=True), sa.ForeignKey("scenario_steps.id", ondelete="CASCADE"), nullable=True),
            sa.Column("step_index", sa.Integer(), nullable=False),
            sa.Column("from_url", sa.Text(), nullable=True),
            sa.Column("from_path", sa.Text(), nullable=True),
            sa.Column("to_url", sa.Text(), nullable=True),
            sa.Column("to_path", sa.Text(), nullable=True),
            sa.Column("action_type", sa.Text(), nullable=False),
            sa.Column("selector", sa.Text(), nullable=True),
            sa.Column("element_text", sa.Text(), nullable=True),
            sa.Column("accessible_name", sa.Text(), nullable=True),
            sa.Column("transition_type", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("before_snapshot_id", UUID(as_uuid=True), sa.ForeignKey("route_variants.id", ondelete="SET NULL"), nullable=True),
            sa.Column("after_snapshot_id", UUID(as_uuid=True), sa.ForeignKey("route_variants.id", ondelete="SET NULL"), nullable=True),
            sa.Column("metadata_json", JSONB(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("recording_id", "step_index", name="uq_recorded_route_transitions_recording_step"),
        )
    _create_index_if_missing("recorded_route_transitions", "ix_recorded_route_transitions_recording_id", ["recording_id"])
    _create_index_if_missing("recorded_route_transitions", "ix_recorded_route_transitions_flow_id", ["flow_id"])
    _create_index_if_missing("recorded_route_transitions", "ix_recorded_route_transitions_hls_id", ["hls_id"])
    _create_index_if_missing("recorded_route_transitions", "ix_recorded_route_transitions_step_id", ["step_id"])
    _create_index_if_missing("recorded_route_transitions", "ix_recorded_route_transitions_before_snapshot_id", ["before_snapshot_id"])
    _create_index_if_missing("recorded_route_transitions", "ix_recorded_route_transitions_after_snapshot_id", ["after_snapshot_id"])
    _create_index_if_missing(
        "recorded_route_transitions",
        "ix_recorded_route_transitions_flow_step",
        ["flow_id", "step_index"],
    )

    if not _table_exists("recorded_assertion_candidates"):
        op.create_table(
            "recorded_assertion_candidates",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("recording_id", UUID(as_uuid=True), sa.ForeignKey("recording_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("flow_id", UUID(as_uuid=True), sa.ForeignKey("recording_flows.id", ondelete="CASCADE"), nullable=True),
            sa.Column("hls_id", UUID(as_uuid=True), sa.ForeignKey("high_level_scenarios.id", ondelete="CASCADE"), nullable=False),
            sa.Column("snapshot_id", UUID(as_uuid=True), sa.ForeignKey("route_variants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("candidate_index", sa.Integer(), nullable=False),
            sa.Column("kind", sa.Text(), nullable=False),
            sa.Column("selector", sa.Text(), nullable=True),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.7"),
            sa.Column("metadata_json", JSONB(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("snapshot_id", "candidate_index", name="uq_recorded_assertions_snapshot_index"),
        )
    _create_index_if_missing("recorded_assertion_candidates", "ix_recorded_assertion_candidates_recording_id", ["recording_id"])
    _create_index_if_missing("recorded_assertion_candidates", "ix_recorded_assertion_candidates_flow_id", ["flow_id"])
    _create_index_if_missing("recorded_assertion_candidates", "ix_recorded_assertion_candidates_hls_id", ["hls_id"])
    _create_index_if_missing("recorded_assertion_candidates", "ix_recorded_assertion_candidates_snapshot_id", ["snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_recorded_assertion_candidates_snapshot_id", table_name="recorded_assertion_candidates")
    op.drop_index("ix_recorded_assertion_candidates_hls_id", table_name="recorded_assertion_candidates")
    op.drop_index("ix_recorded_assertion_candidates_flow_id", table_name="recorded_assertion_candidates")
    op.drop_index("ix_recorded_assertion_candidates_recording_id", table_name="recorded_assertion_candidates")
    op.drop_table("recorded_assertion_candidates")

    op.drop_index("ix_recorded_route_transitions_flow_step", table_name="recorded_route_transitions")
    op.drop_index("ix_recorded_route_transitions_after_snapshot_id", table_name="recorded_route_transitions")
    op.drop_index("ix_recorded_route_transitions_before_snapshot_id", table_name="recorded_route_transitions")
    op.drop_index("ix_recorded_route_transitions_step_id", table_name="recorded_route_transitions")
    op.drop_index("ix_recorded_route_transitions_hls_id", table_name="recorded_route_transitions")
    op.drop_index("ix_recorded_route_transitions_flow_id", table_name="recorded_route_transitions")
    op.drop_index("ix_recorded_route_transitions_recording_id", table_name="recorded_route_transitions")
    op.drop_table("recorded_route_transitions")

    op.drop_index("ix_scenario_steps_flow_id", table_name="scenario_steps")
    op.drop_constraint("fk_scenario_steps_flow_id", "scenario_steps", type_="foreignkey")
    op.drop_column("scenario_steps", "input_value_kind")
    op.drop_column("scenario_steps", "selector_candidates")
    op.drop_column("scenario_steps", "flow_id")

    op.drop_constraint("ux_route_variants_recording_snapshot_index", "route_variants", type_="unique")
    op.drop_index("ix_route_variants_recording_snapshot_index", table_name="route_variants")
    op.drop_index("ix_route_variants_flow_id", table_name="route_variants")
    op.drop_constraint("fk_route_variants_flow_id", "route_variants", type_="foreignkey")
    op.drop_column("route_variants", "metadata_json")
    op.drop_column("route_variants", "assertion_candidates")
    op.drop_column("route_variants", "snapshot_kind")
    op.drop_column("route_variants", "snapshot_index")
    op.drop_column("route_variants", "flow_id")

    op.drop_index("ix_recording_flows_hls_id", table_name="recording_flows")
    op.drop_index("ix_recording_flows_project_id", table_name="recording_flows")
    op.drop_index("ix_recording_flows_recording_id", table_name="recording_flows")
    op.drop_table("recording_flows")
