"""Link recorded steps to before and after route variants.

Revision ID: 0018_step_route_links
Revises: 0017_phase2_rich_step_metadata
Create Date: 2026-05-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0018_step_route_links"
down_revision = "0017_phase2_rich_step_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scenario_steps",
        sa.Column("route_variant_before_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "scenario_steps",
        sa.Column("route_variant_after_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_scenario_steps_route_variant_before_id",
        "scenario_steps",
        "route_variants",
        ["route_variant_before_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_scenario_steps_route_variant_after_id",
        "scenario_steps",
        "route_variants",
        ["route_variant_after_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_scenario_steps_route_variant_before_id",
        "scenario_steps",
        ["route_variant_before_id"],
    )
    op.create_index(
        "ix_scenario_steps_route_variant_after_id",
        "scenario_steps",
        ["route_variant_after_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_scenario_steps_route_variant_after_id", table_name="scenario_steps")
    op.drop_index("ix_scenario_steps_route_variant_before_id", table_name="scenario_steps")
    op.drop_constraint(
        "fk_scenario_steps_route_variant_after_id",
        "scenario_steps",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_scenario_steps_route_variant_before_id",
        "scenario_steps",
        type_="foreignkey",
    )
    op.drop_column("scenario_steps", "route_variant_after_id")
    op.drop_column("scenario_steps", "route_variant_before_id")
