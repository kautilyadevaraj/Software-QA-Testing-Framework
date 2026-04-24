"""Unit tests for app.services.member_service — add/remove members, ownership transfer."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectMember, ProjectRole
from app.models.user import User
from app.services.member_service import (
    add_member,
    remove_member,
    search_users_by_email,
    transfer_ownership,
)


class TestAddMember:
    def test_success(self, db_session: Session, test_project: Project, second_user: User):
        member = add_member(db_session, test_project, second_user.email)
        assert member.user_id == second_user.id
        assert member.role == ProjectRole.TESTER

    def test_user_not_found_raises_404(self, db_session: Session, test_project: Project):
        with pytest.raises(HTTPException) as exc_info:
            add_member(db_session, test_project, "nonexistent@test.com")
        assert exc_info.value.status_code == 404

    def test_already_member_raises_400(self, db_session: Session, test_project: Project, second_user: User):
        add_member(db_session, test_project, second_user.email)
        with pytest.raises(HTTPException) as exc_info:
            add_member(db_session, test_project, second_user.email)
        assert exc_info.value.status_code == 400


class TestRemoveMember:
    def test_success(self, db_session: Session, test_project: Project, second_user: User):
        member = add_member(db_session, test_project, second_user.email)
        remove_member(db_session, test_project, test_project.owner_id, member.id)
        # Verify member is gone
        from sqlalchemy import select
        result = db_session.execute(
            select(ProjectMember).where(ProjectMember.id == member.id)
        ).scalar_one_or_none()
        assert result is None

    def test_not_found_raises_404(self, db_session: Session, test_project: Project):
        with pytest.raises(HTTPException) as exc_info:
            remove_member(db_session, test_project, test_project.owner_id, uuid.uuid4())
        assert exc_info.value.status_code == 404

    def test_cannot_remove_owner(self, db_session: Session, test_project: Project, test_user: User):
        owner_member = [m for m in test_project.members if m.role == ProjectRole.OWNER][0]
        with pytest.raises(HTTPException) as exc_info:
            remove_member(db_session, test_project, test_user.id, owner_member.id)
        assert exc_info.value.status_code == 400
        assert "owner" in exc_info.value.detail.lower()


class TestTransferOwnership:
    def test_swaps_roles(self, db_session: Session, test_project: Project, test_user: User, second_user: User):
        new_member = add_member(db_session, test_project, second_user.email)
        new_owner, old_owner = transfer_ownership(db_session, test_project, new_member.id)
        assert new_owner.role == ProjectRole.OWNER
        assert old_owner.role == ProjectRole.TESTER
        assert test_project.owner_id == second_user.id

    def test_member_not_found_raises_404(self, db_session: Session, test_project: Project):
        with pytest.raises(HTTPException) as exc_info:
            transfer_ownership(db_session, test_project, uuid.uuid4())
        assert exc_info.value.status_code == 404


class TestSearchUsersByEmail:
    def test_returns_matching_users(self, db_session: Session, test_user: User, second_user: User):
        results = search_users_by_email(db_session, "example")
        emails = [u.email for u in results]
        assert test_user.email in emails

    def test_short_query_returns_empty(self, db_session: Session, test_user: User):
        results = search_users_by_email(db_session, "ab")
        assert results == []

    def test_no_match_returns_empty(self, db_session: Session, test_user: User):
        results = search_users_by_email(db_session, "zzzznotfound")
        assert list(results) == []
