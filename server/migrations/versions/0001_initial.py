"""Initial schema — users, projects, project_members, project_files

Revision ID: 0001
Revises:
Create Date: 2026-04-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="user"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── enums ──────────────────────────────────────────────────────────────────
    project_status = postgresql.ENUM(
        "Active", "Draft", "Blocked", name="project_status", create_type=False
    )
    project_role = postgresql.ENUM(
        "OWNER", "TESTER", name="project_role", create_type=False
    )
    file_type_enum = postgresql.ENUM(
        "brd", "fsd", "wbs", "assumption", "credentials", "swagger_docs",
        name="project_file_type",
        create_type=False,
    )

    project_status.create(op.get_bind(), checkfirst=True)
    project_role.create(op.get_bind(), checkfirst=True)
    file_type_enum.create(op.get_bind(), checkfirst=True)

    # ── projects ───────────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(120), nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.Enum("Active", "Draft", "Blocked", name="project_status"),
            nullable=False,
            server_default="Draft",
        ),
        sa.Column("url", sa.String(2048), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── project_members ────────────────────────────────────────────────────────
    op.create_table(
        "project_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "role",
            sa.Enum("OWNER", "TESTER", name="project_role"),
            nullable=False,
            server_default="TESTER",
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── project_files ──────────────────────────────────────────────────────────
    op.create_table(
        "project_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "file_type",
            sa.Enum(
                "brd", "fsd", "wbs", "assumption", "credentials", "swagger_docs",
                name="project_file_type",
            ),
            nullable=False,
            index=True,
        ),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column(
            "content_type",
            sa.String(255),
            nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("absolute_path", sa.String(2048), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("project_files")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS project_file_type")
    op.execute("DROP TYPE IF EXISTS project_role")
    op.execute("DROP TYPE IF EXISTS project_status")
