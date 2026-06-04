"""Add Phase 2 step quality columns (is_noise, noise_reason, selector_quality_reason).

Revision ID: 0020_phase2_step_quality
Revises: 0019_phase2_recording_contract
Create Date: 2026-06-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0020_phase2_step_quality"
down_revision = "0019_phase2_recording_contract"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _inspector().has_table(table_name):
        return False
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    # ALTER TABLE scenario_steps ADD COLUMN is_noise BOOLEAN NOT NULL DEFAULT FALSE
    _add_column_if_missing(
        "scenario_steps",
        sa.Column("is_noise", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # ALTER TABLE scenario_steps ADD COLUMN noise_reason TEXT
    _add_column_if_missing(
        "scenario_steps",
        sa.Column("noise_reason", sa.Text(), nullable=True),
    )
    # ALTER TABLE scenario_steps ADD COLUMN selector_quality_reason TEXT
    _add_column_if_missing(
        "scenario_steps",
        sa.Column("selector_quality_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scenario_steps", "selector_quality_reason")
    op.drop_column("scenario_steps", "noise_reason")
    op.drop_column("scenario_steps", "is_noise")
