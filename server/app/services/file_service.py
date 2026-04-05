import uuid
from pathlib import Path
from sqlalchemy import func, select

from fastapi import HTTPException, UploadFile, status

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.project import FileType, Project, ProjectFile

_SINGLE_FILE_TYPES = {FileType.SWAGGER_DOCS, FileType.CREDENTIALS, FileType.ASSUMPTION}

_FILE_TYPE_LABELS: dict[FileType, str] = {
    FileType.BRD: "BRD",
    FileType.FSD: "FSD",
    FileType.WBS: "WBS",
    FileType.ASSUMPTION: "Assumptions",
    FileType.CREDENTIALS: "Credentials",
    FileType.SWAGGER_DOCS: "Swagger Docs",
}

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB


def get_project_files(db: Session, project: Project) -> list[ProjectFile]:
    return list(
        db.execute(
            select(ProjectFile)
            .where(ProjectFile.project_id == project.id)
            .order_by(ProjectFile.uploaded_at.desc())
        )
        .scalars()
        .all()
    )


def validate_and_save_upload(db: Session, project: Project, file: UploadFile, file_type: FileType) -> ProjectFile:

    # 1. Single-file constraint (Assumptions, Credentials, Swagger are each limited to 1)
    if file_type in _SINGLE_FILE_TYPES:
        existing = db.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project.id,
                ProjectFile.file_type == file_type,
            )
        ).scalar_one_or_none()
        if existing:
            label = _FILE_TYPE_LABELS.get(file_type, file_type.value)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{label} only allows 1 file. Delete the existing file first.",
            )

    # 2. File Ext & Type Validation
    ext = file.filename.split('.')[-1].lower() if file.filename and '.' in file.filename else ''
    
    if file_type == FileType.SWAGGER_DOCS:
        if ext not in ['json', 'yaml', 'yml']:
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Swagger docs must be JSON or YAML formats.")
    elif file_type == FileType.CREDENTIALS:
        if ext not in ['pdf', 'txt']:
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Credentials must be PDF or TXT formats.")
    else:
        if ext != 'pdf':
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{file_type.value} must be a PDF file.")

    # 3. Read size & content
    file_bytes = file.file.read()
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File size exceeds 20MB limit.")

    settings = get_settings()

    # 4. Determine sequential file number for this file_type within the project
    #    (count BEFORE inserting so the new file gets count+1)
    existing_count: int = db.execute(
        select(func.count()).select_from(ProjectFile).where(
            ProjectFile.project_id == project.id,
            ProjectFile.file_type == file_type,
        )
    ).scalar_one()
    file_number = existing_count + 1

    new_file = ProjectFile(
        project_id=project.id,
        file_type=file_type,
        original_filename=file.filename or "unknown",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(file_bytes),
        absolute_path=""
    )

    db.add(new_file)
    db.commit()
    db.refresh(new_file)

    # Make folder uploads/{ProjectID}
    project_dir = Path(settings.upload_dir) / str(project.id)
    project_dir.mkdir(parents=True, exist_ok=True)

    # Absolute Path: uploads/{ProjectID}/{ProjectID}_{FileID}_{FileType}_{FileNumber}
    final_path = project_dir / f"{project.id}_{new_file.id}_{file_type.value}_{file_number}"
    
    try:
        with open(final_path, "wb") as buffer:
            buffer.write(file_bytes)
    except Exception as e:
        db.delete(new_file)
        db.commit()
        raise HTTPException(status_code=500, detail="Failed to save file physically.")
        
    new_file.absolute_path = str(final_path.resolve())
    db.add(new_file)
    db.commit()
    db.refresh(new_file)
    
    return new_file

def delete_project_file(db: Session, project: Project, file_id: uuid.UUID) -> None:
    file = db.execute(select(ProjectFile).where(ProjectFile.id == file_id, ProjectFile.project_id == project.id)).scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        
    file_path = Path(file.absolute_path)
    if file_path.exists() and file_path.is_file():
        file_path.unlink()
        
    db.delete(file)
    db.commit()
