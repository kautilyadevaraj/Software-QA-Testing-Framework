"""API router for the ingestion pipeline (Knowledge Base Construction)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.project import ApiEndpoint, DocumentChunk, IngestionJob
from app.models.user import User
from app.schemas.ingestion import (
    ApiEndpointListResponse,
    ApiEndpointResponse,
    DocumentChunkListResponse,
    DocumentChunkResponse,
    IngestionJobResponse,
    IngestionStatusResponse,
)
from app.services.ingestion_service import run_ingestion
from app.services.project_service import get_project_or_404
from app.utils.rate_limiter import limiter

router = APIRouter(prefix="/projects", tags=["ingestion"])
settings = get_settings()


@router.post("/{project_id}/ingest")
@limiter.limit(settings.rate_limit_api)
def start_ingestion(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IngestionJobResponse:
    """Trigger the knowledge-base ingestion pipeline for a project.

    This runs **synchronously** — the response is returned only after
    all files have been processed, chunked, embedded, and stored.
    """
    project = get_project_or_404(db, current_user.id, project_id)

    # Check that the project has files to ingest
    from app.models.project import ProjectFile
    file_count = db.execute(
        select(func.count()).select_from(ProjectFile).where(ProjectFile.project_id == project.id)
    ).scalar_one()
    if file_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files uploaded. Upload documents before ingesting.",
        )

    job = run_ingestion(db, project.id)
    return IngestionJobResponse.model_validate(job)


@router.get("/{project_id}/ingest/status")
@limiter.limit(settings.rate_limit_api)
def get_ingestion_status(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IngestionStatusResponse:
    """Get the latest ingestion job status and knowledge-base summary."""
    project = get_project_or_404(db, current_user.id, project_id)

    # Get the most recent ingestion job
    job = db.execute(
        select(IngestionJob)
        .where(IngestionJob.project_id == project.id)
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    # Count chunks and endpoints
    total_chunks = db.execute(
        select(func.count()).select_from(DocumentChunk).where(DocumentChunk.project_id == project.id)
    ).scalar_one()

    total_endpoints = db.execute(
        select(func.count()).select_from(ApiEndpoint).where(ApiEndpoint.project_id == project.id)
    ).scalar_one()

    return IngestionStatusResponse(
        job=IngestionJobResponse.model_validate(job) if job else None,
        total_chunks=total_chunks,
        total_endpoints=total_endpoints,
    )


@router.get("/{project_id}/ingest/chunks")
@limiter.limit(settings.rate_limit_api)
def list_document_chunks(
    request: Request,
    project_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentChunkListResponse:
    """List document chunks for a project (paginated)."""
    project = get_project_or_404(db, current_user.id, project_id)

    total = db.execute(
        select(func.count()).select_from(DocumentChunk).where(DocumentChunk.project_id == project.id)
    ).scalar_one()

    offset = (page - 1) * page_size
    chunks = list(
        db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.project_id == project.id)
            .order_by(DocumentChunk.created_at, DocumentChunk.chunk_index)
            .offset(offset)
            .limit(page_size)
        ).scalars().all()
    )

    return DocumentChunkListResponse(
        items=[DocumentChunkResponse.model_validate(c) for c in chunks],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{project_id}/ingest/endpoints")
@limiter.limit(settings.rate_limit_api)
def list_api_endpoints(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiEndpointListResponse:
    """List parsed API endpoints for a project."""
    project = get_project_or_404(db, current_user.id, project_id)

    endpoints = list(
        db.execute(
            select(ApiEndpoint)
            .where(ApiEndpoint.project_id == project.id)
            .order_by(ApiEndpoint.path, ApiEndpoint.http_method)
        ).scalars().all()
    )

    total = len(endpoints)

    return ApiEndpointListResponse(
        items=[ApiEndpointResponse.model_validate(ep) for ep in endpoints],
        total=total,
    )
