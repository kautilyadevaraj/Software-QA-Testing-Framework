import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.project import ProjectMember
from app.models.user import User
from app.schemas.project import ProjectMemberResponse
from app.services.member_service import add_member, remove_member, search_users_by_email, transfer_ownership
from app.services.project_service import get_project_or_404
from app.utils.rate_limiter import limiter

router = APIRouter(prefix="/projects", tags=["project-members"])
settings = get_settings()


def _member_response(member: ProjectMember) -> ProjectMemberResponse:
    """Build a ProjectMemberResponse, pulling email from the related User."""
    return ProjectMemberResponse(
        id=member.id,
        user_id=member.user_id,
        email=member.user.email,
        role=member.role,
        joined_at=member.joined_at,
    )

@router.get("/users/search")
@limiter.limit(settings.rate_limit_api)
def search_users(
    request: Request,
    query: str = Query(..., min_length=3),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    users = search_users_by_email(db, query)
    return [{"id": str(u.id), "email": u.email} for u in users]


@router.get("/{project_id}/members", response_model=list[ProjectMemberResponse])
@limiter.limit(settings.rate_limit_api)
def get_project_members(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProjectMemberResponse]:
    project = get_project_or_404(db, current_user.id, project_id)
    return [_member_response(m) for m in project.members]


@router.post("/{project_id}/members", response_model=ProjectMemberResponse)
@limiter.limit(settings.rate_limit_api)
def add_project_member(
    request: Request,
    project_id: uuid.UUID,
    email: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectMemberResponse:
    project = get_project_or_404(db, current_user.id, project_id)
    member = add_member(db, project, email)
    # Reload the member so the user relationship is available for serialization
    db.refresh(member)
    return _member_response(member)


@router.delete("/{project_id}/members/{member_id}")
@limiter.limit(settings.rate_limit_api)
def remove_project_member(
    request: Request,
    project_id: uuid.UUID,
    member_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    remove_member(db, project, current_user.id, member_id)
    return {"status": "Member removed"}


@router.post("/{project_id}/members/{member_id}/transfer")
@limiter.limit(settings.rate_limit_api)
def transfer_project_ownership(
    request: Request,
    project_id: uuid.UUID,
    member_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    transfer_ownership(db, project, member_id)
    return {"status": "Ownership transferred successfully"}
