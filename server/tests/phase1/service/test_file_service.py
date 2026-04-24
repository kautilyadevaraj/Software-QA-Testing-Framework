"""Unit tests for app.services.file_service — upload validation, constraints, deletion."""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.models.project import FileType, Project
from app.services.file_service import (
    MAX_FILE_SIZE_BYTES,
    delete_project_file,
    get_project_files,
    validate_and_save_upload,
)


def _make_upload_file(filename: str, content: bytes = b"dummy pdf content", content_type: str = "application/pdf") -> UploadFile:
    """Create a mock UploadFile with the given attributes."""
    file_obj = io.BytesIO(content)
    return UploadFile(filename=filename, file=file_obj, headers={"content-type": content_type})


class TestValidateAndSave:
    def test_pdf_upload_success(self, db_session: Session, test_project: Project):
        upload = _make_upload_file("document.pdf")
        result = validate_and_save_upload(db_session, test_project, upload, FileType.BRD)
        assert result.id is not None
        assert result.original_filename == "document.pdf"
        assert result.file_type == FileType.BRD
        assert Path(result.absolute_path).exists()

    def test_swagger_json_upload_success(self, db_session: Session, test_project: Project):
        upload = _make_upload_file("openapi.json", b'{"openapi":"3.0.0"}', "application/json")
        result = validate_and_save_upload(db_session, test_project, upload, FileType.SWAGGER_DOCS)
        assert result.file_type == FileType.SWAGGER_DOCS

    def test_swagger_yaml_upload_success(self, db_session: Session, test_project: Project):
        upload = _make_upload_file("openapi.yaml", b"openapi: 3.0.0", "application/yaml")
        result = validate_and_save_upload(db_session, test_project, upload, FileType.SWAGGER_DOCS)
        assert result.file_type == FileType.SWAGGER_DOCS

    def test_reject_wrong_extension_for_brd(self, db_session: Session, test_project: Project):
        upload = _make_upload_file("readme.txt")
        with pytest.raises(HTTPException) as exc_info:
            validate_and_save_upload(db_session, test_project, upload, FileType.BRD)
        assert exc_info.value.status_code == 400
        assert "PDF" in exc_info.value.detail

    def test_reject_wrong_extension_for_swagger(self, db_session: Session, test_project: Project):
        upload = _make_upload_file("spec.pdf")
        with pytest.raises(HTTPException) as exc_info:
            validate_and_save_upload(db_session, test_project, upload, FileType.SWAGGER_DOCS)
        assert exc_info.value.status_code == 400

    def test_reject_duplicate_single_file_type(self, db_session: Session, test_project: Project):
        first = _make_upload_file("openapi.json", b'{"a":1}', "application/json")
        validate_and_save_upload(db_session, test_project, first, FileType.SWAGGER_DOCS)

        second = _make_upload_file("openapi2.json", b'{"b":2}', "application/json")
        with pytest.raises(HTTPException) as exc_info:
            validate_and_save_upload(db_session, test_project, second, FileType.SWAGGER_DOCS)
        assert exc_info.value.status_code == 400
        assert "1 file" in exc_info.value.detail

    def test_reject_oversize_file(self, db_session: Session, test_project: Project):
        big_content = b"x" * (MAX_FILE_SIZE_BYTES + 1)
        upload = _make_upload_file("big.pdf", big_content)
        with pytest.raises(HTTPException) as exc_info:
            validate_and_save_upload(db_session, test_project, upload, FileType.BRD)
        assert exc_info.value.status_code == 413

    def test_multiple_brd_files_allowed(self, db_session: Session, test_project: Project):
        """BRD is not in the single-file-type set, so multiple uploads should be fine."""
        for i in range(3):
            upload = _make_upload_file(f"brd_{i}.pdf")
            result = validate_and_save_upload(db_session, test_project, upload, FileType.BRD)
            assert result.id is not None


class TestGetProjectFiles:
    def test_returns_uploaded_files(self, db_session: Session, test_project: Project):
        upload1 = _make_upload_file("a.pdf")
        upload2 = _make_upload_file("b.pdf")
        validate_and_save_upload(db_session, test_project, upload1, FileType.BRD)
        validate_and_save_upload(db_session, test_project, upload2, FileType.FSD)

        files = get_project_files(db_session, test_project)
        assert len(files) == 2

    def test_empty_project(self, db_session: Session, test_project: Project):
        files = get_project_files(db_session, test_project)
        assert files == []


class TestDeleteProjectFile:
    def test_removes_from_disk_and_db(self, db_session: Session, test_project: Project):
        upload = _make_upload_file("delete_me.pdf")
        saved = validate_and_save_upload(db_session, test_project, upload, FileType.BRD)
        file_path = Path(saved.absolute_path)
        assert file_path.exists()

        delete_project_file(db_session, test_project, saved.id)
        assert not file_path.exists()

    def test_not_found_raises_404(self, db_session: Session, test_project: Project):
        with pytest.raises(HTTPException) as exc_info:
            delete_project_file(db_session, test_project, uuid.uuid4())
        assert exc_info.value.status_code == 404
