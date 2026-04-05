import uuid
from typing import Sequence

from fastapi import HTTPException, status
from sqlalchemy import Select, select, or_
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectMember, ProjectRole
from app.models.user import User

def search_users_by_email(db: Session, query: str, limit: int = 10) -> Sequence[User]:
    if not query or len(query) < 3:
        return []
    
    stmt = select(User).where(User.email.ilike(f"%{query}%")).limit(limit)
    return db.execute(stmt).scalars().all()

def add_member(db: Session, project: Project, email: str) -> ProjectMember:
    user = db.execute(select(User).where(User.email.ilike(email.strip()))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
    existing = db.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is already a member")
        
    member = ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.TESTER)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member

def remove_member(db: Session, project: Project, current_user_id: uuid.UUID, member_id: uuid.UUID) -> None:
    member = db.execute(select(ProjectMember).where(ProjectMember.id == member_id, ProjectMember.project_id == project.id)).scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
        
    if member.role == ProjectRole.OWNER:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot remove the owner")
        
    db.delete(member)
    db.commit()

def transfer_ownership(db: Session, project: Project, new_owner_member_id: uuid.UUID) -> tuple[ProjectMember, ProjectMember]:
    new_owner = db.execute(select(ProjectMember).where(ProjectMember.id == new_owner_member_id, ProjectMember.project_id == project.id)).scalar_one_or_none()
    if not new_owner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
        
    old_owner = db.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.role == ProjectRole.OWNER)).scalar_one()
    
    old_owner.role = ProjectRole.TESTER
    new_owner.role = ProjectRole.OWNER
    project.owner_id = new_owner.user_id
    
    db.add(old_owner)
    db.add(new_owner)
    db.add(project)
    db.commit()
    db.refresh(old_owner)
    db.refresh(new_owner)
    return new_owner, old_owner
