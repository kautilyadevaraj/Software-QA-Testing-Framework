from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field, TypeAdapter, field_validator

from app.models.project import FileType, ProjectRole, ProjectStatus


class ProjectMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: ProjectRole
    joined_at: datetime


class ProjectFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    category: str
    original_filename: str
    content_type: str
    size_bytes: int
    created_at: datetime

    @classmethod
    def from_project_file(cls, pf: object) -> "ProjectFileResponse":
        from app.models.project import FileType, ProjectFile
        assert isinstance(pf, ProjectFile)
        # Map internal FileType enum to frontend category names
        _FILETYPE_TO_CATEGORY: dict[FileType, str] = {
            FileType.BRD: "BRD",
            FileType.FSD: "FSD",
            FileType.WBS: "WBS",
            FileType.ASSUMPTION: "Assumptions",
            FileType.CREDENTIALS: "Credentials",
            FileType.SWAGGER_DOCS: "SwaggerDocs",
        }
        return cls(
            id=pf.id,
            category=_FILETYPE_TO_CATEGORY.get(pf.file_type, pf.file_type.value),
            original_filename=pf.original_filename,
            content_type=pf.content_type,
            size_bytes=pf.size_bytes,
            created_at=pf.uploaded_at,
        )


class ProjectPayloadBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=5000)
    status: ProjectStatus = ProjectStatus.DRAFT
    url: AnyHttpUrl | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Project name is required")
        return stripped

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> ProjectStatus:
        if isinstance(value, ProjectStatus):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            for candidate in ProjectStatus:
                if candidate.value.lower() == normalized:
                    return candidate
        raise ValueError("Invalid project status")

    @field_validator("url", mode="before")
    @classmethod
    def empty_url_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ProjectCreateRequest(ProjectPayloadBase):
    pass


class ProjectUpdateRequest(ProjectPayloadBase):
    pass


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    status: ProjectStatus
    url: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("url", mode="before")
    @classmethod
    def empty_url_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int
    page: int
    page_size: int


class ProjectSortField(str):
    ID = "id"
    NAME = "name"
    CREATED_AT = "created_at"
    STATUS = "status"
