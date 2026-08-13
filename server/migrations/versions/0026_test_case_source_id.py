"""Add source test_id and sheet_name to test_cases.

Revision ID: 0026_test_case_source_id
Revises: 0025_high_level_scenario_test_id
Create Date: 2026-08-13

Adds source_test_id and source_sheet_name so generated test cases keep a
traceable link back to the Test Document (xlsx) row they were planned from,
matching the high_level_scenarios.test_id / sheet_name columns.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0026_test_case_source_id"
down_revision = "0025_high_level_scenario_test_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("source_test_id", sa.Text(), nullable=True))
    op.add_column("test_cases", sa.Column("source_sheet_name", sa.Text(), nullable=True))
    op.create_index("ix_test_cases_source_test_id", "test_cases", ["source_test_id"])


def downgrade() -> None:
    op.drop_index("ix_test_cases_source_test_id", table_name="test_cases")
    op.drop_column("test_cases", "source_sheet_name")
    op.drop_column("test_cases", "source_test_id")
