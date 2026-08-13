"""Add test_id to high_level_scenarios.

Revision ID: 0025_high_level_scenario_test_id
Revises: 0024_high_level_scenario_sheet
Create Date: 2026-08-13

Adds test_id so approved scenarios remember the actual Test ID from the
source test document and the Approved Scenarios table can display it.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0025_high_level_scenario_test_id"
down_revision = "0024_high_level_scenario_sheet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("high_level_scenarios", sa.Column("test_id", sa.Text(), nullable=True))
    op.create_index("ix_high_level_scenarios_test_id", "high_level_scenarios", ["test_id"])


def downgrade() -> None:
    op.drop_index("ix_high_level_scenarios_test_id", table_name="high_level_scenarios")
    op.drop_column("high_level_scenarios", "test_id")