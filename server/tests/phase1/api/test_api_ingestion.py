"""Integration tests for /api/v1/projects/{id}/ingest/* endpoints.

External services (Qdrant, sentence-transformers, PyMuPDF) are mocked so
these tests validate the API layer and DB logic without infrastructure deps.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient


def _create_project(client: TestClient) -> str:
    resp = client.post("/api/v1/projects", json={"name": "IngestProject", "description": ""})
    assert resp.status_code == 200
    return resp.json()["id"]


def _upload_pdf(client: TestClient, project_id: str, filename: str = "doc.pdf") -> str:
    resp = client.post(
        f"/api/v1/projects/{project_id}/documents",
        data={"category": "BRD"},
        files={"files": (filename, b"fake pdf content", "application/pdf")},
    )
    assert resp.status_code == 200
    return resp.json()["items"][0]["id"]


def _upload_swagger(client: TestClient, project_id: str) -> str:
    spec = json.dumps({
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/test": {
                "get": {
                    "operationId": "getTest",
                    "summary": "Test endpoint",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    })
    resp = client.post(
        f"/api/v1/projects/{project_id}/documents",
        data={"category": "SwaggerDocs"},
        files={"files": ("api.json", spec.encode(), "application/json")},
    )
    assert resp.status_code == 200
    return resp.json()["items"][0]["id"]


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_fitz_doc(pages: list[str]):
    """Create a mock fitz.Document."""
    doc = MagicMock()
    doc.__len__ = MagicMock(return_value=len(pages))

    def _load_page(idx):
        page = MagicMock()
        page.get_text.return_value = pages[idx]
        return page

    doc.load_page = _load_page
    doc.close = MagicMock()
    return doc


def _mock_embed_texts(texts, **kwargs):
    """Return fake 384-dim vectors for any input texts."""
    return np.random.rand(len(texts), 384).astype(np.float32)


def _mock_model():
    model = MagicMock()
    model.get_sentence_embedding_dimension.return_value = 384
    model.encode = _mock_embed_texts
    return model


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStartIngestion:
    def test_no_files_returns_400(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.post(f"/api/v1/projects/{project_id}/ingest")
        assert resp.status_code == 400
        assert "no files" in resp.json()["detail"].lower()

    @patch("app.services.embedding_service._get_model")
    @patch("app.services.pdf_service.fitz")
    @patch("app.services.ingestion_service.get_qdrant_client")
    def test_ingest_pdf_success(self, mock_qdrant, mock_fitz, mock_model, auth_client: TestClient):
        mock_model.return_value = _mock_model()
        mock_fitz.open.return_value = _mock_fitz_doc(["Page one content for testing", "Page two with more content"])
        mock_qdrant.return_value = MagicMock()

        project_id = _create_project(auth_client)
        _upload_pdf(auth_client, project_id)

        resp = auth_client.post(f"/api/v1/projects/{project_id}/ingest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["processed_files"] == 1
        assert data["total_chunks"] > 0

    @patch("app.services.embedding_service._get_model")
    @patch("app.services.ingestion_service.get_qdrant_client")
    def test_ingest_swagger_success(self, mock_qdrant, mock_model, auth_client: TestClient):
        mock_model.return_value = _mock_model()
        mock_qdrant.return_value = MagicMock()

        project_id = _create_project(auth_client)
        _upload_swagger(auth_client, project_id)

        resp = auth_client.post(f"/api/v1/projects/{project_id}/ingest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["processed_files"] == 1


class TestIngestionStatus:
    def test_no_prior_ingestion(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.get(f"/api/v1/projects/{project_id}/ingest/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job"] is None
        assert data["total_chunks"] == 0
        assert data["total_endpoints"] == 0

    @patch("app.services.embedding_service._get_model")
    @patch("app.services.pdf_service.fitz")
    @patch("app.services.ingestion_service.get_qdrant_client")
    def test_after_ingestion(self, mock_qdrant, mock_fitz, mock_model, auth_client: TestClient):
        mock_model.return_value = _mock_model()
        mock_fitz.open.return_value = _mock_fitz_doc(["Some test content here"])
        mock_qdrant.return_value = MagicMock()

        project_id = _create_project(auth_client)
        _upload_pdf(auth_client, project_id)
        auth_client.post(f"/api/v1/projects/{project_id}/ingest")

        resp = auth_client.get(f"/api/v1/projects/{project_id}/ingest/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job"] is not None
        assert data["job"]["status"] == "completed"
        assert data["total_chunks"] > 0


class TestListChunks:
    @patch("app.services.embedding_service._get_model")
    @patch("app.services.pdf_service.fitz")
    @patch("app.services.ingestion_service.get_qdrant_client")
    def test_paginated_chunks(self, mock_qdrant, mock_fitz, mock_model, auth_client: TestClient):
        mock_model.return_value = _mock_model()
        mock_fitz.open.return_value = _mock_fitz_doc(["Content " * 100])
        mock_qdrant.return_value = MagicMock()

        project_id = _create_project(auth_client)
        _upload_pdf(auth_client, project_id)
        auth_client.post(f"/api/v1/projects/{project_id}/ingest")

        resp = auth_client.get(f"/api/v1/projects/{project_id}/ingest/chunks?page=1&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["page"] == 1
        assert len(data["items"]) <= 5

    def test_empty_before_ingestion(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.get(f"/api/v1/projects/{project_id}/ingest/chunks")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestListEndpoints:
    @patch("app.services.embedding_service._get_model")
    @patch("app.services.ingestion_service.get_qdrant_client")
    def test_after_swagger_ingest(self, mock_qdrant, mock_model, auth_client: TestClient):
        mock_model.return_value = _mock_model()
        mock_qdrant.return_value = MagicMock()

        project_id = _create_project(auth_client)
        _upload_swagger(auth_client, project_id)
        auth_client.post(f"/api/v1/projects/{project_id}/ingest")

        resp = auth_client.get(f"/api/v1/projects/{project_id}/ingest/endpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(ep["http_method"] == "GET" for ep in data["items"])

    def test_empty_before_ingestion(self, auth_client: TestClient):
        project_id = _create_project(auth_client)
        resp = auth_client.get(f"/api/v1/projects/{project_id}/ingest/endpoints")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
