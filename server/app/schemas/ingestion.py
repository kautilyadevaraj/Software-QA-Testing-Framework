"""Pydantic response schemas for the ingestion pipeline."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class IngestionJobResponse(BaseModel):
    """Returned when an ingestion job is created or queried."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    total_files: int
    processed_files: int
    total_chunks: int
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class DocumentChunkResponse(BaseModel):
    """Single document chunk returned in listing endpoints."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_id: uuid.UUID
    chunk_index: int
    content: str
    token_count: int
    page_number: int | None = None
    source_type: str
    chunk_metadata: dict
    created_at: datetime


class DocumentChunkListResponse(BaseModel):
    items: list[DocumentChunkResponse]
    total: int
    page: int
    page_size: int


class ApiEndpointResponse(BaseModel):
    """Parsed API endpoint from the Swagger specification."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    http_method: str
    path: str
    operation_id: str | None = None
    summary: str
    description: str
    tags: list[str]
    created_at: datetime


class ApiEndpointListResponse(BaseModel):
    items: list[ApiEndpointResponse]
    total: int


class IngestionStatusResponse(BaseModel):
    """Summary of ingestion state for the project."""
    job: IngestionJobResponse | None = None
    total_chunks: int = 0
    total_endpoints: int = 0
