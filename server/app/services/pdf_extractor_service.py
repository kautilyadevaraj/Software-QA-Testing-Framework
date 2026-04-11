import threading
import fitz
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import select, func
import prance
import re
import time

from app.models.project import ProjectFile, FileType, Project, ExtractedText, APIEndpoint
from app.core.config import get_settings
from app.db.session import SessionLocal

PDF_PROGRESS = {}


def _run_extraction(project_id_str: str):
    db = SessionLocal()
    try:
        settings = get_settings()
        project_dir = Path(settings.upload_dir) / project_id_str
        project_dir.mkdir(parents=True, exist_ok=True)

        project = db.query(Project).get(project_id_str)
        if not project:
            PDF_PROGRESS[project_id_str] = {
                "status": "error",
                "progress": 0,
                "logs": ["Project not found"]
            }
            return

        files = db.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project.id,
                ProjectFile.file_type.in_([
                    FileType.BRD,
                    FileType.FSD,
                    FileType.WBS,
                    FileType.ASSUMPTION,
                ])
            )
        ).scalars().all()

        if not files:
            PDF_PROGRESS[project_id_str] = {
                "status": "no_files",
                "progress": 0,
                "logs": []
            }
            return

        total = len(files)
        logs = []

        for i, file in enumerate(files):
            try:
                logs.append(f"Parsing {file.original_filename}...")
                PDF_PROGRESS[project_id_str] = {
                    "status": "processing",
                    "progress": int((i / total) * 100),
                    "logs": logs
                }
                time.sleep(1.5)

                doc = fitz.open(file.absolute_path)

                text_parts = []
                for page_num, page in enumerate(doc, 1):
                    raw_text = page.get_text()
                    cleaned = raw_text.replace('\t', ' ')
                    cleaned = re.sub(r'\n+', '\n', cleaned)
                    cleaned = re.sub(r'\b(\w+)(?:\s+\1\b)+', r'\1', cleaned, flags=re.IGNORECASE)
                    text_parts.append(f"Page {page_num}\n{cleaned.strip()}")
                
                text = "\n\n".join(text_parts)

                existing_count = db.execute(
                    select(func.count()).select_from(ProjectFile).where(
                        ProjectFile.project_id == project.id,
                        ProjectFile.file_type == file.file_type,
                    )
                ).scalar_one()

                output_path = project_dir / (
                    f"extracted_{project.id}_{file.id}_{file.file_type.value}_{existing_count}.txt"
                )

                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(text)
                
                extracted = ExtractedText(
                    file_id=file.id,
                    project_id=project.id,
                    blob_url=str(output_path)
                )
                db.add(extracted)
                db.commit()

                logs.append(f"Successfully parsed {file.original_filename}")

            except Exception as e:
                logs.append(f"Failed {file.original_filename}: {str(e)}")

        swagger_files = db.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project.id,
                ProjectFile.file_type == FileType.SWAGGER_DOCS
            )
        ).scalars().all()

        for file in swagger_files:
            try:
                logs.append(f"Parsing Swagger {file.original_filename}...")
                PDF_PROGRESS[project_id_str] = {
                    "status": "processing",
                    "progress": 90,
                    "logs": logs
                }
                time.sleep(1)
                
                parser = prance.ResolvingParser(file.absolute_path)
                spec = parser.specification
                paths = spec.get("paths", {})
                
                count = 0
                for path, methods in paths.items():
                    for method, details in methods.items():
                        if method.lower() in ["get", "post", "put", "delete", "patch", "options", "head"]:
                            desc = details.get("summary") or details.get("description", "")
                            endpoint = APIEndpoint(
                                project_id=project.id,
                                path=path,
                                method=method.upper(),
                                description=desc[:1000] if desc else ""
                            )
                            db.add(endpoint)
                            count += 1
                db.commit()
                logs.append(f"Successfully parsed Swagger {file.original_filename} ({count} endpoints)")
            except Exception as e:
                logs.append(f"Failed parsing Swagger {file.original_filename}: {str(e)}")

        PDF_PROGRESS[project_id_str] = {
            "status": "completed",
            "progress": 100,
            "logs": logs
        }
    except Exception as e:
        PDF_PROGRESS[project_id_str] = {
            "status": "error",
            "progress": 0,
            "logs": [f"Error: {str(e)}"]
        }
    finally:
        db.close()


def start_pdf_extraction(db: Session, project: Project):
    project_id = str(project.id)

    PDF_PROGRESS[project_id] = {
        "status": "starting",
        "progress": 0,
        "logs": []
    }

    thread = threading.Thread(target=_run_extraction, args=(project_id,))
    thread.start()

    return {"status": "started"}