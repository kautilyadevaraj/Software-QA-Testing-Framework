"""Add screenshot_path for assertion evidence on PASS tests.

Revision ID: 0022_assertion_screenshot
Revises: 0021_credential_auth_strategy
Create Date: 2026-06-11

Adds screenshot_path TEXT NULL to:
  - test_results           (written by worker on PASS outcomes)
  - phase3_execution_state (live state mirror used by the UI)

Design note:
  PASS  → screenshot_path is set; trace_path is NULL (no trace generated)
  FAIL  → trace_path is set; screenshot_path is NULL (no assertion reached)
  Both columns are nullable so existing rows remain valid without backfill.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0022_assertion_screenshot"
down_revision = "0021_credential_auth_strategy"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _inspector().has_table(table_name):
        return False
    return any(col["name"] == column_name for col in _inspector().get_columns(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing(
        "test_results",
        sa.Column("screenshot_path", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        "phase3_execution_state",
        sa.Column("screenshot_path", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    for table in ("test_results", "phase3_execution_state"):
        if _column_exists(table, "screenshot_path"):
            op.drop_column(table, "screenshot_path")
