"""Add credential auth strategy metadata.

Revision ID: 0021_credential_auth_strategy
Revises: 0020_phase2_step_quality
Create Date: 2026-06-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_credential_auth_strategy"
down_revision = "0020_phase2_step_quality"
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
    _add_column_if_missing(
        "credential_profiles",
        sa.Column(
            "auth_strategy",
            sa.String(length=100),
            nullable=False,
            server_default="inline_login",
        ),
    )
    _add_column_if_missing(
        "credential_profiles",
        sa.Column("auth_script", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    if _column_exists("credential_profiles", "auth_script"):
        op.drop_column("credential_profiles", "auth_script")
    if _column_exists("credential_profiles", "auth_strategy"):
        op.drop_column("credential_profiles", "auth_strategy")
