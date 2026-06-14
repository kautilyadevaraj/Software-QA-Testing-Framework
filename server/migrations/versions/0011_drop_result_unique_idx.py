"""Drop legacy unique test_results.test_id index

Revision ID: 0011_drop_result_unique_idx
Revises: 0010_run_scoped_test_results
Create Date: 2026-05-03
"""
from alembic import op
from sqlalchemy import inspect

revision = "0011_drop_result_unique_idx"
down_revision = "0010_run_scoped_test_results"
branch_labels = None
depends_on = None


def _indexes(table: str) -> dict[str, tuple[list[str], bool]]:
    return {
        idx["name"]: (list(idx.get("column_names") or []), bool(idx.get("unique")))
        for idx in inspect(op.get_bind()).get_indexes(table)
        if idx.get("name")
    }


def upgrade() -> None:
    for name, (columns, is_unique) in _indexes("test_results").items():
        if columns == ["test_id"] and is_unique:
            op.drop_index(name, table_name="test_results")


def downgrade() -> None:
    indexes = _indexes("test_results")
    if "ix_test_results_test_id" not in indexes:
        op.create_index("ix_test_results_test_id", "test_results", ["test_id"], unique=True)
