from __future__ import annotations

import enum
import uuid
from datetime import datetime

import uuid6
from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ProjectStatus(str, enum.Enum):
    ACTIVE = "Active"
    DRAFT = "Draft"
    BLOCKED = "Blocked"


class ProjectRole(str, enum.Enum):
    OWNER = "OWNER"
    TESTER = "TESTER"


class FileType(str, enum.Enum):
    BRD = "brd"
    FSD = "fsd"
    WBS = "wbs"
    ASSUMPTION = "assumption"
    CREDENTIALS = "credentials"
    SWAGGER_DOCS = "swagger_docs"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus, name="project_status"), nullable=False, default=ProjectStatus.DRAFT)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    owner = relationship("User", back_populates="projects")
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    files = relationship("ProjectFile", back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[ProjectRole] = mapped_column(Enum(ProjectRole, name="project_role"), nullable=False, default=ProjectRole.TESTER)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    project = relationship("Project", back_populates="members")
    user = relationship("User")


class ProjectFile(Base):
    __tablename__ = "project_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    file_type: Mapped[FileType] = mapped_column(Enum(FileType, name="project_file_type"), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(nullable=False, default=0)
    absolute_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    project = relationship("Project", back_populates="files")
