"""Ingestion orchestrator — ties PDF extraction, Swagger parsing, chunking, and embedding together."""

from __future__ import annotations

import logging
import traceback
import uuid
from datetime import datetime, timezone

from qdrant_client.models import PointStruct
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.qdrant import get_qdrant_client
from app.models.project import (
    ApiEndpoint,
    DocumentChunk,
    FileType,
    IngestionJob,
    IngestionStatus,
    ProjectFile,
)
from app.services.chunking_service import Chunk, chunk_text
from app.services.embedding_service import embed_texts
from app.services.pdf_service import extract_text_from_pdf, save_extracted_text
from app.services.swagger_service import EndpointRecord, endpoint_to_text, parse_swagger

logger = logging.getLogger(__name__)

# File types that contain PDF documents for text extraction
_PDF_FILE_TYPES = {FileType.BRD, FileType.FSD, FileType.WBS, FileType.ASSUMPTION}


def _wipe_project_knowledge(db: Session, project_id: uuid.UUID) -> None:
    """Delete all existing knowledge-base data for a project (idempotent re-ingestion)."""
    # 1. Delete document chunks from PostgreSQL
    db.execute(delete(DocumentChunk).where(DocumentChunk.project_id == project_id))
    # 2. Delete API endpoints from PostgreSQL
    db.execute(delete(ApiEndpoint).where(ApiEndpoint.project_id == project_id))
    db.flush()

    # 3. Delete vectors from Qdrant (filter by project_id in payload)
    settings = get_settings()
    try:
        client = get_qdrant_client()
        client.delete(
            collection_name=settings.qdrant_collection,
            points_selector={
                "filter": {
                    "must": [
                        {"key": "project_id", "match": {"value": str(project_id)}}
                    ]
                }
            },
        )
        logger.info("Wiped Qdrant vectors for project %s.", project_id)
    except Exception as e:
        logger.warning("Failed to wipe Qdrant vectors for project %s: %s", project_id, e)


def _upsert_vectors(
    chunks: list[Chunk],
    embeddings: list[list[float]],
    point_ids: list[uuid.UUID],
) -> None:
    """Batch-upsert vectors into Qdrant."""
    settings = get_settings()
    client = get_qdrant_client()

    points = []
    for chunk, embedding, point_id in zip(chunks, embeddings, point_ids):
        points.append(
            PointStruct(
                id=str(point_id),
                vector=embedding,
                payload={
                    **chunk.metadata,
                    "content": chunk.content,
                    "chunk_index": chunk.chunk_index,
                    "token_count": chunk.token_count,
                },
            )
        )

    # Upsert in batches of 100
    batch_size = 100
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        client.upsert(collection_name=settings.qdrant_collection, points=batch)

    logger.info("Upserted %d vectors to Qdrant.", len(points))


def _process_pdf_file(
    db: Session,
    file: ProjectFile,
    project_id: uuid.UUID,
) -> list[DocumentChunk]:
    """Extract → chunk → embed → store a single PDF file."""
    file_path = file.absolute_path
    file_id = file.id

    # 1. Extract text page-by-page
    pages = extract_text_from_pdf(file_path)
    save_extracted_text(str(project_id), str(file_id), pages)

    # 2. Chunk with page-level metadata
    all_chunks: list[Chunk] = []
    for page in pages:
        if not page.text.strip():
            continue
        page_chunks = chunk_text(
            page.text,
            source_metadata={
                "project_id": str(project_id),
                "file_id": str(file_id),
                "file_type": file.file_type.value,
                "page_number": page.page_number,
                "source_type": "pdf",
                "original_filename": file.original_filename,
            },
        )
        # Re-index chunk_index globally within this file
        offset = len(all_chunks)
        for c in page_chunks:
            c.chunk_index = offset + c.chunk_index
        all_chunks.extend(page_chunks)

    if not all_chunks:
        return []

    # 3. Generate embeddings
    texts = [c.content for c in all_chunks]
    embeddings = embed_texts(texts)

    # 4. Prepare Qdrant point IDs and DB records
    db_chunks: list[DocumentChunk] = []
    point_ids: list[uuid.UUID] = []

    for chunk in all_chunks:
        point_id = uuid.uuid4()
        point_ids.append(point_id)

        db_chunk = DocumentChunk(
            project_id=project_id,
            file_id=file_id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            token_count=chunk.token_count,
            page_number=chunk.metadata.get("page_number"),
            source_type="pdf",
            chunk_metadata=chunk.metadata,
            qdrant_point_id=point_id,
        )
        db_chunks.append(db_chunk)

    # 5. Save to PostgreSQL (always persisted)
    db.add_all(db_chunks)
    db.flush()

    # 6. Upsert to Qdrant (non-fatal — vectors can be backfilled)
    try:
        _upsert_vectors(all_chunks, embeddings, point_ids)
    except Exception as e:
        logger.warning("Qdrant upsert failed for file %s (non-fatal): %s", file.original_filename, e)

    logger.info(
        "Processed PDF file %s: %d pages → %d chunks.",
        file.original_filename, len(pages), len(db_chunks),
    )
    return db_chunks


def _process_swagger_file(
    db: Session,
    file: ProjectFile,
    project_id: uuid.UUID,
) -> tuple[list[ApiEndpoint], list[DocumentChunk]]:
    """Parse → store endpoints → chunk → embed → store a Swagger file."""
    file_path = file.absolute_path
    file_id = file.id

    # 1. Parse Swagger spec
    endpoint_records = parse_swagger(file_path)

    # 2. Store API endpoint rows
    db_endpoints: list[ApiEndpoint] = []
    for ep in endpoint_records:
        db_ep = ApiEndpoint(
            project_id=project_id,
            file_id=file_id,
            http_method=ep.http_method,
            path=ep.path,
            operation_id=ep.operation_id,
            summary=ep.summary,
            description=ep.description,
            tags=ep.tags,
            parameters=ep.parameters,
            request_body=ep.request_body,
            responses=ep.responses,
        )
        db_endpoints.append(db_ep)

    db.add_all(db_endpoints)
    db.flush()

    # 3. Generate text representations and chunk them
    all_chunks: list[Chunk] = []
    for ep in endpoint_records:
        ep_text = endpoint_to_text(ep)
        ep_chunks = chunk_text(
            ep_text,
            source_metadata={
                "project_id": str(project_id),
                "file_id": str(file_id),
                "file_type": FileType.SWAGGER_DOCS.value,
                "source_type": "swagger",
                "endpoint_method": ep.http_method,
                "endpoint_path": ep.path,
                "original_filename": file.original_filename,
            },
        )
        offset = len(all_chunks)
        for c in ep_chunks:
            c.chunk_index = offset + c.chunk_index
        all_chunks.extend(ep_chunks)

    if not all_chunks:
        return db_endpoints, []

    # 4. Generate embeddings
    texts = [c.content for c in all_chunks]
    embeddings = embed_texts(texts)

    # 5. Prepare DB records
    db_chunks: list[DocumentChunk] = []
    point_ids: list[uuid.UUID] = []

    for chunk in all_chunks:
        point_id = uuid.uuid4()
        point_ids.append(point_id)

        db_chunk = DocumentChunk(
            project_id=project_id,
            file_id=file_id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            token_count=chunk.token_count,
            page_number=None,
            source_type="swagger",
            chunk_metadata=chunk.metadata,
            qdrant_point_id=point_id,
        )
        db_chunks.append(db_chunk)

    # 6. Save chunks to PostgreSQL (always persisted)
    db.add_all(db_chunks)
    db.flush()

    # 7. Upsert vectors to Qdrant (non-fatal — vectors can be backfilled)
    try:
        _upsert_vectors(all_chunks, embeddings, point_ids)
    except Exception as e:
        logger.warning("Qdrant upsert failed for file %s (non-fatal): %s", file.original_filename, e)

    logger.info(
        "Processed Swagger file %s: %d endpoints → %d chunks.",
        file.original_filename, len(db_endpoints), len(db_chunks),
    )
    return db_endpoints, db_chunks


def run_ingestion(db: Session, project_id: uuid.UUID) -> IngestionJob:
    """Execute the full ingestion pipeline for a project (synchronous).

    1. Creates an IngestionJob record.
    2. Wipes any existing knowledge-base data for this project.
    3. Processes all uploaded files (PDFs + Swagger).
    4. Updates the job status to completed or failed.
    """
    # Fetch all project files
    files = list(
        db.execute(
            select(ProjectFile).where(ProjectFile.project_id == project_id)
        ).scalars().all()
    )

    # Create the job record
    job = IngestionJob(
        project_id=project_id,
        status=IngestionStatus.PROCESSING,
        total_files=len(files),
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        # Wipe existing data for idempotent re-ingestion
        _wipe_project_knowledge(db, project_id)

        total_chunks = 0

        for file in files:
            if file.file_type in _PDF_FILE_TYPES:
                chunks = _process_pdf_file(db, file, project_id)
                total_chunks += len(chunks)
            elif file.file_type == FileType.SWAGGER_DOCS:
                _, chunks = _process_swagger_file(db, file, project_id)
                total_chunks += len(chunks)
            else:
                # Credentials files are not ingested into the knowledge base
                logger.info("Skipping file %s (type=%s).", file.original_filename, file.file_type.value)

            job.processed_files += 1
            db.commit()

        # Mark as completed
        job.status = IngestionStatus.COMPLETED
        job.total_chunks = total_chunks
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)

        logger.info(
            "Ingestion completed for project %s: %d files → %d chunks.",
            project_id, len(files), total_chunks,
        )

    except Exception as e:
        logger.error("Ingestion failed for project %s: %s", project_id, e)
        logger.error(traceback.format_exc())
        job.status = IngestionStatus.FAILED
        job.error_message = str(e)
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)

    return job
