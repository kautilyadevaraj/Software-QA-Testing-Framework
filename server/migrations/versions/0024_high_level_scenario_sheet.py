"""Add sheet_name and structured scenario fields to high_level_scenarios.

Revision ID: 0024_high_level_scenario_sheet
Revises: 0023_test_document_file_type
Create Date: 2026-08-13

Adds sheet_name, pre_conditions, test_steps and expected_result columns so
approved scenarios remember which xlsx worksheet they came from and keep the
structured Test Document fields available for the Approved Scenarios table.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0024_high_level_scenario_sheet"
down_revision = "0023_test_document_file_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("high_level_scenarios", sa.Column("sheet_name", sa.Text(), nullable=True))
    op.add_column("high_level_scenarios", sa.Column("pre_conditions", sa.Text(), nullable=True))
    op.add_column("high_level_scenarios", sa.Column("test_steps", sa.Text(), nullable=True))
    op.add_column("high_level_scenarios", sa.Column("expected_result", sa.Text(), nullable=True))
    op.create_index("ix_high_level_scenarios_sheet_name", "high_level_scenarios", ["sheet_name"])


def downgrade() -> None:
    op.drop_index("ix_high_level_scenarios_sheet_name", table_name="high_level_scenarios")
    op.drop_column("high_level_scenarios", "expected_result")
    op.drop_column("high_level_scenarios", "test_steps")
    op.drop_column("high_level_scenarios", "pre_conditions")
    op.drop_column("high_level_scenarios", "sheet_name")
