import uuid
from typing import List

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File as FastAPIFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.project import FileType, ProjectFile
from app.models.user import User
from app.schemas.project import ProjectFileResponse
from app.services.file_service import delete_project_file, get_project_files, validate_and_save_upload
from app.services.project_service import get_project_or_404
from app.services.xlsx_service import parse_test_document_sheets
from app.utils.rate_limiter import limiter

router = APIRouter(prefix="/projects", tags=["project-files"])
settings = get_settings()

# Map frontend category names to backend FileType enum values
_CATEGORY_TO_FILETYPE: dict[str, FileType] = {
    "BRD": FileType.BRD,
    "FSD": FileType.FSD,
    "WBS": FileType.WBS,
    "Assumptions": FileType.ASSUMPTION,
    "Credentials": FileType.CREDENTIALS,
    "SwaggerDocs": FileType.SWAGGER_DOCS,
    "TestDocument": FileType.TEST_DOCUMENT,
}


@router.get("/{project_id}/documents")
@limiter.limit(settings.rate_limit_api)
def list_documents(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    files = get_project_files(db, project)
    return {"items": [ProjectFileResponse.from_project_file(f) for f in files]}


@router.get("/{project_id}/test-document-rows")
@limiter.limit(settings.rate_limit_api)
def read_test_document_rows(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Parse the uploaded Test Document (xlsx) and return its scenario rows."""
    project = get_project_or_404(db, current_user.id, project_id)

    file = db.execute(
        select(ProjectFile)
        .where(
            ProjectFile.project_id == project.id,
            ProjectFile.file_type == FileType.TEST_DOCUMENT,
        )
        .order_by(ProjectFile.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if file is None:
        return {"items": [], "sheets": [], "file": None}

    sheets = parse_test_document_sheets(file.absolute_path)

    return {
        "items": [row for sheet in sheets for row in sheet["items"]],
        "sheets": sheets,
        "file": ProjectFileResponse.from_project_file(file).model_dump(),
    }


@router.post("/{project_id}/documents")
@limiter.limit(settings.rate_limit_api)
def upload_documents(
    request: Request,
    project_id: uuid.UUID,
    category: str = Form(...),
    files: List[UploadFile] = FastAPIFile(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    file_type = _CATEGORY_TO_FILETYPE.get(category)
    if file_type is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown category '{category}'. Valid values: {list(_CATEGORY_TO_FILETYPE.keys())}",
        )

    project = get_project_or_404(db, current_user.id, project_id)

    results = []
    for file in files:
        saved = validate_and_save_upload(db, project, file, file_type)
        results.append(ProjectFileResponse.from_project_file(saved))

    return {"items": results}


@router.delete("/{project_id}/documents/{document_id}")
@limiter.limit(settings.rate_limit_api)
def delete_document(
    request: Request,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    delete_project_file(db, project, document_id)
    return {"message": "Document deleted"}
