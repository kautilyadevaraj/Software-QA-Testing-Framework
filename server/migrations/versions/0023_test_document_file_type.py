"""Add test_document to project_file_type enum.

Revision ID: 0023_test_document_file_type
Revises: 0022_assertion_screenshot
Create Date: 2026-08-13

Adds the "test_document" value to the project_file_type Postgres ENUM so
projects can upload an XLSX "Test Document" (compulsory) alongside the
existing BRD/FSD/WBS/Swagger/Credentials docs.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023_test_document_file_type"
down_revision = "0022_assertion_screenshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    enum_name = "project_file_type"
    values = {"brd", "fsd", "wbs", "assumption", "credentials", "swagger_docs", "test_document"}
    rows = op.get_bind().execute(sa.text(f"SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid = pg_enum.enumtypid WHERE pg_type.typname = '{enum_name}'")).fetchall()
    existing = {row[0] for row in rows}
    missing = values - existing
    if "test_document" in missing:
        op.execute("ALTER TYPE project_file_type ADD VALUE IF NOT EXISTS 'test_document'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value that is in use. Dropping the
    # value would require rewriting the column; leave it in place for the
    # downgrade and simply document that the value remains unused.
    pass