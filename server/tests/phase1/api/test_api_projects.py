"""Integration tests for /api/v1/projects/* endpoints."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


class TestCreateProject:
    def test_success(self, auth_client: TestClient):
        resp = auth_client.post("/api/v1/projects", json={
            "name": "Integration Test Project",
            "description": "Created via test",
            "status": "Draft",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Integration Test Project"
        assert data["status"] == "Draft"
        assert "id" in data

    def test_missing_name(self, auth_client: TestClient):
        resp = auth_client.post("/api/v1/projects", json={"description": "no name"})
        assert resp.status_code == 422

    def test_unauthenticated(self, client: TestClient):
        resp = client.post("/api/v1/projects", json={"name": "Unauth", "description": ""})
        assert resp.status_code == 401


class TestListProjects:
    def test_empty_initially(self, auth_client: TestClient):
        resp = auth_client.get("/api/v1/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_after_create(self, auth_client: TestClient):
        auth_client.post("/api/v1/projects", json={"name": "P1", "description": ""})
        auth_client.post("/api/v1/projects", json={"name": "P2", "description": ""})

        resp = auth_client.get("/api/v1/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_pagination(self, auth_client: TestClient):
        for i in range(5):
            auth_client.post("/api/v1/projects", json={"name": f"Proj{i}", "description": ""})

        resp = auth_client.get("/api/v1/projects?page=1&page_size=2")
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["page"] == 1

    def test_sort_by_name(self, auth_client: TestClient):
        auth_client.post("/api/v1/projects", json={"name": "Zebra", "description": ""})
        auth_client.post("/api/v1/projects", json={"name": "Alpha", "description": ""})

        resp = auth_client.get("/api/v1/projects?sort_by=name&sort_dir=asc")
        items = resp.json()["items"]
        assert items[0]["name"] == "Alpha"
        assert items[1]["name"] == "Zebra"


class TestGetProject:
    def test_found(self, auth_client: TestClient):
        create_resp = auth_client.post("/api/v1/projects", json={"name": "FindMe", "description": ""})
        project_id = create_resp.json()["id"]

        resp = auth_client.get(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "FindMe"

    def test_not_found(self, auth_client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = auth_client.get(f"/api/v1/projects/{fake_id}")
        assert resp.status_code == 404


class TestUpdateProject:
    def test_success(self, auth_client: TestClient):
        create_resp = auth_client.post("/api/v1/projects", json={"name": "Before", "description": ""})
        project_id = create_resp.json()["id"]

        resp = auth_client.put(f"/api/v1/projects/{project_id}", json={
            "name": "After",
            "description": "Updated description",
            "status": "Active",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "After"
        assert data["description"] == "Updated description"


class TestDeleteProject:
    def test_success(self, auth_client: TestClient):
        create_resp = auth_client.post("/api/v1/projects", json={"name": "ToDelete", "description": ""})
        project_id = create_resp.json()["id"]

        resp = auth_client.delete(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 200

        # Verify it's gone
        get_resp = auth_client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.status_code == 404


class TestStubEndpoints:
    def test_launch(self, auth_client: TestClient):
        create_resp = auth_client.post("/api/v1/projects", json={"name": "LaunchTest", "description": ""})
        project_id = create_resp.json()["id"]

        resp = auth_client.post(f"/api/v1/projects/{project_id}/launch", json={"url": "http://example.com"})
        assert resp.status_code == 200
        assert resp.json()["project_id"] == project_id

    def test_verify(self, auth_client: TestClient):
        create_resp = auth_client.post("/api/v1/projects", json={"name": "VerifyTest", "description": ""})
        project_id = create_resp.json()["id"]

        resp = auth_client.post(f"/api/v1/projects/{project_id}/verify", json={"verified": True})
        assert resp.status_code == 200
        assert resp.json()["is_verified"] is True

    def test_create_ticket(self, auth_client: TestClient):
        create_resp = auth_client.post("/api/v1/projects", json={"name": "TicketTest", "description": ""})
        project_id = create_resp.json()["id"]

        resp = auth_client.post(f"/api/v1/projects/{project_id}/tickets", json={
            "title": "Bug found",
            "description": "Steps to reproduce...",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Bug found"
        assert data["status"] == "open"
