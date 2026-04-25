from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
except ImportError:
    QdrantClient = None
    models = None

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models.project import Project, ProjectMember, ProjectRole, ProjectStatus
from app.models.user import User
from app.schemas.project import ProjectCreateRequest, ProjectUpdateRequest


def _base_project_query(user_id: uuid.UUID) -> Select[tuple[Project]]:
    return (
        select(Project)
        .outerjoin(ProjectMember, Project.id == ProjectMember.project_id)
        .where((Project.owner_id == user_id) | (ProjectMember.user_id == user_id))
        .distinct()
    )


def get_project_or_404(db: Session, user_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    query = _base_project_query(user_id).where(Project.id == project_id)
    project = db.execute(query).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found or access denied")
    return project


def list_projects(
    db: Session,
    user: User,
    sort_by: str,
    sort_dir: str,
    page: int,
    page_size: int,
) -> tuple[list[Project], int]:
    sort_mapping = {
        "id": Project.id,
        "name": Project.name,
        "created_at": Project.created_at,
        "status": Project.status,
    }
    order_column = sort_mapping.get(sort_by, Project.created_at)
    order_expression = order_column.asc() if sort_dir == "asc" else order_column.desc()

    count_query = select(func.count(Project.id.distinct())).outerjoin(ProjectMember, Project.id == ProjectMember.project_id).where((Project.owner_id == user.id) | (ProjectMember.user_id == user.id))
    total = db.execute(count_query).scalar_one()
    offset = (page - 1) * page_size

    items = (
        db.execute(
            _base_project_query(user.id)
            .options(selectinload(Project.members).selectinload(ProjectMember.user))
            .order_by(order_expression)
            .offset(offset)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(items), int(total)


def create_project(db: Session, user: User, payload: ProjectCreateRequest) -> Project:
    project = Project(
        owner_id=user.id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        status=ProjectStatus(payload.status),
        url=str(payload.url or ""),
    )

    owner_member = ProjectMember(user_id=user.id, role=ProjectRole.OWNER)
    project.members.append(owner_member)

    db.add(project)
    db.commit()
    db.refresh(project)
    return get_project_or_404(db, user.id, project.id)


def update_project(db: Session, project: Project, payload: ProjectUpdateRequest) -> Project:
    project.name = payload.name.strip()
    project.description = payload.description.strip()
    project.status = ProjectStatus(payload.status)
    project.url = str(payload.url or "")



    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project: Project) -> None:
    settings = get_settings()
    project_dir = Path(settings.upload_dir) / str(project.id)
    
    db.delete(project)
    db.commit()

    if QdrantClient and settings.qdrant_url and settings.qdrant_api_key:
        try:
            qdrant_client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
            project_id_str = str(project.id)
            collections_to_delete = {project_id_str}

            for collection in qdrant_client.get_collections().collections:
                collection_name = getattr(collection, "name", "")
                if isinstance(collection_name, str) and collection_name.startswith(f"{project_id_str}_"):
                    collections_to_delete.add(collection_name)

            for collection_name in collections_to_delete:
                if qdrant_client.collection_exists(collection_name):
                    qdrant_client.delete_collection(collection_name)
        except Exception as e:
            print(f"Failed to clear Qdrant data for project {project.id}: {e}")

    # Clean up files physically
    if project_dir.exists() and project_dir.is_dir():
        shutil.rmtree(project_dir, ignore_errors=True)
