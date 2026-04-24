import uuid

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.project import ProjectCreateRequest, ProjectListResponse, ProjectResponse, ProjectUpdateRequest
from app.services.project_service import create_project, delete_project, get_project_or_404, list_projects, update_project
from app.utils.rate_limiter import limiter


router = APIRouter(prefix="/projects", tags=["projects"])
settings = get_settings()


def _to_project_response(project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        url=project.url,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=ProjectListResponse)
@limiter.limit(settings.rate_limit_api)
def get_projects(
    request: Request,
    sort_by: str = Query(default="created_at", pattern="^(id|name|created_at|status)$"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectListResponse:
    items, total = list_projects(db, current_user, sort_by=sort_by, sort_dir=sort_dir, page=page, page_size=page_size)
    return ProjectListResponse(items=[_to_project_response(item) for item in items], total=total, page=page, page_size=page_size)


@router.post("", response_model=ProjectResponse)
@limiter.limit(settings.rate_limit_api)
def add_project(
    request: Request,
    payload: ProjectCreateRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectResponse:
    project = create_project(db, current_user, payload)
    return _to_project_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
@limiter.limit(settings.rate_limit_api)
def get_project(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectResponse:
    project = get_project_or_404(db, current_user.id, project_id)
    return _to_project_response(project)


@router.put("/{project_id}", response_model=ProjectResponse)
@limiter.limit(settings.rate_limit_api)
def edit_project(
    request: Request,
    project_id: uuid.UUID,
    payload: ProjectUpdateRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectResponse:
    project = get_project_or_404(db, current_user.id, project_id)
    project = update_project(db, project, payload)
    return _to_project_response(project)


@router.delete("/{project_id}")
@limiter.limit(settings.rate_limit_api)
def remove_project(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    project = get_project_or_404(db, current_user.id, project_id)
    delete_project(db, project)
    return {"message": "Project deleted"}


# ---------------------------------------------------------------------------
# Stub endpoints – required by the frontend, not yet fully implemented
# ---------------------------------------------------------------------------

@router.post("/{project_id}/launch")
@limiter.limit(settings.rate_limit_api)
def launch_project(
    request: Request,
    project_id: uuid.UUID,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    url = payload.get("url", project.url or "")
    from datetime import datetime, timezone
    return {
        "project_id": str(project.id),
        "launched_url": url,
        "is_verified": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verified_at": None,
    }


@router.post("/{project_id}/verify")
@limiter.limit(settings.rate_limit_api)
def verify_project(
    request: Request,
    project_id: uuid.UUID,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    return {
        "project_id": str(project.id),
        "launched_url": project.url or "",
        "is_verified": payload.get("verified", True),
        "created_at": now,
        "verified_at": now,
    }





@router.post("/{project_id}/tickets")
@limiter.limit(settings.rate_limit_api)
def create_ticket(
    request: Request,
    project_id: uuid.UUID,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    from datetime import datetime, timezone
    return {
        "id": str(uuid.uuid4()),
        "project_id": str(project.id),
        "title": payload.get("title", ""),
        "description": payload.get("description", ""),
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

