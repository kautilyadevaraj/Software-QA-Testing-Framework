"""Integration tests for /api/v1/projects/{id}/documents/* endpoints."""

from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient


def _create_project(client: TestClient) -> str:
    resp = client.post("/api/v1/projects", json={"name": "FileTestProject", "description": ""})
    assert resp.status_code == 200
    return resp.json()["id"]


class TestUploadDocuments:
    def test_upload_pdf_brd(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.post(
            f"/api/v1/projects/{project_id}/documents",
            data={"category": "BRD"},
            files={"files": ("test_doc.pdf", b"fake pdf content", "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["category"] == "BRD"
        assert data["items"][0]["original_filename"] == "test_doc.pdf"

    def test_upload_swagger_json(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.post(
            f"/api/v1/projects/{project_id}/documents",
            data={"category": "SwaggerDocs"},
            files={"files": ("api.json", b'{"openapi":"3.0"}', "application/json")},
        )
        assert resp.status_code == 200
        assert resp.json()["items"][0]["category"] == "SwaggerDocs"

    def test_invalid_category(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.post(
            f"/api/v1/projects/{project_id}/documents",
            data={"category": "InvalidCategory"},
            files={"files": ("test.pdf", b"content", "application/pdf")},
        )
        assert resp.status_code == 422

    def test_wrong_extension(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.post(
            f"/api/v1/projects/{project_id}/documents",
            data={"category": "BRD"},
            files={"files": ("readme.txt", b"text content", "text/plain")},
        )
        assert resp.status_code == 400

    def test_multiple_files(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        files = [
            ("files", ("doc1.pdf", b"pdf1", "application/pdf")),
            ("files", ("doc2.pdf", b"pdf2", "application/pdf")),
        ]
        resp = auth_client.post(
            f"/api/v1/projects/{project_id}/documents",
            data={"category": "BRD"},
            files=files,
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 2


class TestListDocuments:
    def test_empty_project(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.get(f"/api/v1/projects/{project_id}/documents")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_after_upload(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        auth_client.post(
            f"/api/v1/projects/{project_id}/documents",
            data={"category": "BRD"},
            files={"files": ("uploaded.pdf", b"content", "application/pdf")},
        )

        resp = auth_client.get(f"/api/v1/projects/{project_id}/documents")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["original_filename"] == "uploaded.pdf"


class TestDeleteDocument:
    def test_success(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        upload_resp = auth_client.post(
            f"/api/v1/projects/{project_id}/documents",
            data={"category": "FSD"},
            files={"files": ("to_delete.pdf", b"content", "application/pdf")},
        )
        doc_id = upload_resp.json()["items"][0]["id"]

        resp = auth_client.delete(f"/api/v1/projects/{project_id}/documents/{doc_id}")
        assert resp.status_code == 200

        # Verify it's gone
        list_resp = auth_client.get(f"/api/v1/projects/{project_id}/documents")
        assert len(list_resp.json()["items"]) == 0

    def test_not_found(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        fake_id = str(uuid.uuid4())
        resp = auth_client.delete(f"/api/v1/projects/{project_id}/documents/{fake_id}")
        assert resp.status_code == 404
