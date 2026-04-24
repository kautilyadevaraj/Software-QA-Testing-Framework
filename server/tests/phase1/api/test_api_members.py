"""Integration tests for /api/v1/projects/{id}/members/* endpoints."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.user import User
from app.core.security import hash_password


def _create_project(client: TestClient) -> str:
    """Helper to create a project and return its ID."""
    resp = client.post("/api/v1/projects", json={"name": "MemberTestProject", "description": ""})
    assert resp.status_code == 200
    return resp.json()["id"]


class TestGetMembers:
    def test_includes_owner(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.get(f"/api/v1/projects/{project_id}/members")
        assert resp.status_code == 200
        members = resp.json()
        assert len(members) >= 1
        roles = [m["role"] for m in members]
        assert "OWNER" in roles


class TestAddMember:
    def test_success(self, auth_client: TestClient, db_session: Session):
        # Create a second user directly in the DB (shared test DB via conftest)
        from tests.conftest import TestingSessionLocal
        session = TestingSessionLocal()
        try:
            user = User(email="member_add@test.com", password_hash=hash_password("Test@1234"), role="user")
            session.add(user)
            session.commit()
        finally:
            session.close()

        project_id = _create_project(auth_client)
        resp = auth_client.post(
            f"/api/v1/projects/{project_id}/members?email=member_add@test.com"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "TESTER"
        assert data["email"] == "member_add@test.com"

    def test_user_not_found(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.post(
            f"/api/v1/projects/{project_id}/members?email=nonexistent@test.com"
        )
        assert resp.status_code == 404


class TestRemoveMember:
    def test_success(self, auth_client: TestClient):
        from tests.conftest import TestingSessionLocal
        session = TestingSessionLocal()
        try:
            user = User(email="member_rm@test.com", password_hash=hash_password("Test@1234"), role="user")
            session.add(user)
            session.commit()
        finally:
            session.close()

        project_id = _create_project(auth_client)
        add_resp = auth_client.post(f"/api/v1/projects/{project_id}/members?email=member_rm@test.com")
        member_id = add_resp.json()["id"]

        resp = auth_client.delete(f"/api/v1/projects/{project_id}/members/{member_id}")
        assert resp.status_code == 200

    def test_not_found(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        fake_id = str(uuid.uuid4())
        resp = auth_client.delete(f"/api/v1/projects/{project_id}/members/{fake_id}")
        assert resp.status_code == 404


class TestSearchUsers:
    def test_returns_matches(self, auth_client: TestClient):
        resp = auth_client.get("/api/v1/projects/users/search?query=testuser")
        assert resp.status_code == 200
        results = resp.json()
        emails = [u["email"] for u in results]
        assert "testuser@example.com" in emails

    def test_short_query_returns_empty(self, auth_client: TestClient):
        resp = auth_client.get("/api/v1/projects/users/search?query=ab")
        # min_length=3 validation should fail
        assert resp.status_code == 422
