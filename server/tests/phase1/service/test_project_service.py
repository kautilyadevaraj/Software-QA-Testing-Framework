"""Unit tests for app.services.project_service — CRUD, access control, deletion."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectMember, ProjectRole, ProjectStatus
from app.models.user import User
from app.schemas.project import ProjectCreateRequest, ProjectUpdateRequest
from app.services.project_service import (
    create_project,
    delete_project,
    get_project_or_404,
    list_projects,
    update_project,
)


class TestCreateProject:
    def test_persists(self, db_session: Session, test_user: User):
        payload = ProjectCreateRequest(name="My Project", description="desc", status="Draft")
        project = create_project(db_session, test_user, payload)
        assert project.id is not None
        assert project.name == "My Project"
        assert project.owner_id == test_user.id

    def test_creates_owner_member(self, db_session: Session, test_user: User):
        payload = ProjectCreateRequest(name="Owned", description="")
        project = create_project(db_session, test_user, payload)
        assert len(project.members) >= 1
        owner_member = [m for m in project.members if m.role == ProjectRole.OWNER]
        assert len(owner_member) == 1
        assert owner_member[0].user_id == test_user.id

    def test_strips_whitespace(self, db_session: Session, test_user: User):
        payload = ProjectCreateRequest(name="  Spaced  ", description="  desc  ")
        project = create_project(db_session, test_user, payload)
        assert project.name == "Spaced"


class TestGetProjectOr404:
    def test_found_for_owner(self, db_session: Session, test_user: User, test_project: Project):
        result = get_project_or_404(db_session, test_user.id, test_project.id)
        assert result.id == test_project.id

    def test_not_found_raises_404(self, db_session: Session, test_user: User):
        with pytest.raises(HTTPException) as exc_info:
            get_project_or_404(db_session, test_user.id, uuid.uuid4())
        assert exc_info.value.status_code == 404

    def test_wrong_user_raises_404(self, db_session: Session, test_project: Project, second_user: User):
        with pytest.raises(HTTPException) as exc_info:
            get_project_or_404(db_session, second_user.id, test_project.id)
        assert exc_info.value.status_code == 404


class TestListProjects:
    def test_empty(self, db_session: Session, test_user: User):
        items, total = list_projects(db_session, test_user, "created_at", "desc", 1, 20)
        assert items == []
        assert total == 0

    def test_with_projects(self, db_session: Session, test_user: User):
        for i in range(3):
            payload = ProjectCreateRequest(name=f"Project {i}", description="")
            create_project(db_session, test_user, payload)

        items, total = list_projects(db_session, test_user, "created_at", "desc", 1, 20)
        assert total == 3
        assert len(items) == 3

    def test_pagination(self, db_session: Session, test_user: User):
        for i in range(5):
            payload = ProjectCreateRequest(name=f"P{i}", description="")
            create_project(db_session, test_user, payload)

        items, total = list_projects(db_session, test_user, "name", "asc", 1, 2)
        assert total == 5
        assert len(items) == 2


class TestUpdateProject:
    def test_updates_fields(self, db_session: Session, test_project: Project):
        payload = ProjectUpdateRequest(
            name="Updated Name",
            description="Updated desc",
            status="Active",
            url="http://example.com",
        )
        updated = update_project(db_session, test_project, payload)
        assert updated.name == "Updated Name"
        assert updated.description == "Updated desc"


class TestDeleteProject:
    def test_removes_from_db(self, db_session: Session, test_user: User):
        payload = ProjectCreateRequest(name="To Delete", description="")
        project = create_project(db_session, test_user, payload)
        project_id = project.id

        delete_project(db_session, project)

        with pytest.raises(HTTPException) as exc_info:
            get_project_or_404(db_session, test_user.id, project_id)
        assert exc_info.value.status_code == 404
