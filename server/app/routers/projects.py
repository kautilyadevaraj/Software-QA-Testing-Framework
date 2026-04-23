import uuid
import csv
from app.models.project import ProjectFile, FileType, ProjectCredentialVerification, ExtractedText, APIEndpoint
from playwright.sync_api import sync_playwright
import threading

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
except ImportError:
    QdrantClient = None
    models = None

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.project import ProjectCreateRequest, ProjectListResponse, ProjectResponse, ProjectUpdateRequest
from app.services.project_service import create_project, delete_project, get_project_or_404, list_projects, update_project
from app.utils.rate_limiter import limiter
from app.services.pdf_extractor_service import start_pdf_extraction, PDF_PROGRESS
import fitz
from pathlib import Path
from sqlalchemy import func




router = APIRouter(prefix="/projects", tags=["projects"])
settings = get_settings()


def _to_project_response(project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        url=project.url,
        created_at=project.created_at,
        updated_at=project.updated_at,
        is_verified=project.is_verified,
    )


@router.get("", response_model=ProjectListResponse)
@limiter.limit(settings.rate_limit_api)
def get_projects(
    request: Request,
    sort_by: str = Query(default="created_at", pattern="^(id|name|created_at|status)$"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectListResponse:
    items, total = list_projects(db, current_user, sort_by=sort_by, sort_dir=sort_dir, page=page, page_size=page_size)
    return ProjectListResponse(items=[_to_project_response(item) for item in items], total=total, page=page, page_size=page_size)


@router.post("", response_model=ProjectResponse)
@limiter.limit(settings.rate_limit_api)
def add_project(
    request: Request,
    payload: ProjectCreateRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectResponse:
    project = create_project(db, current_user, payload)
    return _to_project_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
@limiter.limit(settings.rate_limit_api)
def get_project(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectResponse:
    project = get_project_or_404(db, current_user.id, project_id)
    return _to_project_response(project)


@router.put("/{project_id}", response_model=ProjectResponse)
@limiter.limit(settings.rate_limit_api)
def edit_project(
    request: Request,
    project_id: uuid.UUID,
    payload: ProjectUpdateRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectResponse:
    project = get_project_or_404(db, current_user.id, project_id)
    project = update_project(db, project, payload)
    return _to_project_response(project)


@router.delete("/{project_id}")
@limiter.limit(settings.rate_limit_api)
def remove_project(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    project = get_project_or_404(db, current_user.id, project_id)
    delete_project(db, project)
    return {"message": "Project deleted"}


# ---------------------------------------------------------------------------
# Stub endpoints – required by the frontend, not yet fully implemented
# ---------------------------------------------------------------------------

@router.post("/{project_id}/launch")
@limiter.limit(settings.rate_limit_api)
def launch_project(
    request: Request,
    project_id: uuid.UUID,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    url = payload.get("url", project.url or "")
    from datetime import datetime, timezone
    project.is_verified = False
    db.commit()
    return {
        "project_id": str(project.id),
        "launched_url": url,
        "is_verified": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verified_at": None,
    }


@router.post("/{project_id}/verify")
@limiter.limit(settings.rate_limit_api)
def verify_project(
    request: Request,
    project_id: uuid.UUID,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    project.is_verified = True      # ✅ ADD
    db.commit() 
    return {
        "project_id": str(project.id),
        "launched_url": project.url or "",
        "is_verified": payload.get("verified", True),
        "created_at": now,
        "verified_at": now,
    }


@router.post("/{project_id}/ingest")
@limiter.limit(settings.rate_limit_api)
def ingest_project(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    
    # Reset all credential verification to false
    db.query(ProjectCredentialVerification).filter(
        ProjectCredentialVerification.project_id == project.id
    ).update({"is_verified": False})

    # Find and delete local extracted files from disk
    import os
    extracted_records = db.query(ExtractedText).filter(ExtractedText.project_id == project.id).all()
    for record in extracted_records:
        try:
            if os.path.exists(record.blob_url):
                os.remove(record.blob_url)
        except Exception:
            pass

    # Delete corresponding table entries
    db.query(ExtractedText).filter(ExtractedText.project_id == project.id).delete()
    db.query(APIEndpoint).filter(APIEndpoint.project_id == project.id).delete()
    db.commit()

    if QdrantClient and settings.qdrant_url and settings.qdrant_api_key:
        try:
            qdrant_client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
            qdrant_client.delete(
                collection_name="project_documents",
                points_selector=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="project_id",
                            match=models.MatchValue(value=str(project.id))
                        )
                    ]
                )
            )
        except Exception as e:
            print(f"Failed to clear Qdrant data: {e}")

    project_id_str = str(project.id)
    if project_id_str in PDF_PROGRESS:
        PDF_PROGRESS[project_id_str] = {
            "status": "idle",
            "progress": 0,
            "logs": []
        }

    from datetime import datetime, timezone
    return {
        "id": str(uuid.uuid4()),
        "project_id": str(project.id),
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/{project_id}/tickets")
@limiter.limit(settings.rate_limit_api)
def create_ticket(
    request: Request,
    project_id: uuid.UUID,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_or_404(db, current_user.id, project_id)
    from datetime import datetime, timezone
    return {
        "id": str(uuid.uuid4()),
        "project_id": str(project.id),
        "title": payload.get("title", ""),
        "description": payload.get("description", ""),
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{project_id}/credentials")
def get_project_credentials(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_or_404(db, current_user.id, project_id)

    # 2. Find credentials file
    file = (
        db.query(ProjectFile)
        .filter(
            ProjectFile.project_id == project.id,
            ProjectFile.file_type == FileType.CREDENTIALS,
        )
        .first()
    )

    if not file:
        return []

    credentials = []

    verifications = db.query(ProjectCredentialVerification).filter_by(project_id=project.id).all()
    verified_map = {v.username: v.is_verified for v in verifications}

    # 3. Read CSV
    try:
        with open(file.absolute_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)

            for row in reader:
                username = row.get("username")

                credentials.append({
                    "username": username,
                    "role": row.get("role"),
                    "auth_type": row.get("authtype"),
                    "endpoint": row.get("api endpoint"),
                    "verified": verified_map.get(username, False),
                })

    except Exception as e:
        return {"error": str(e)}

    return credentials

@router.post("/{project_id}/mark-verified")
def mark_verified(
    request: Request,
    project_id: uuid.UUID,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    username = payload.get("username")
    verification = db.query(ProjectCredentialVerification).filter_by(project_id=project_id, username=username).first()
    if verification:
        verification.is_verified = not verification.is_verified
    else:
        verification = ProjectCredentialVerification(project_id=project_id, username=username, is_verified=True)
        db.add(verification)
    db.commit()
    return {"status": "verified"}

@router.post("/{project_id}/run-playwright")
def run_playwright(
    request: Request,
    project_id: uuid.UUID,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    url = payload.get("endpoint")
    username = payload.get("username")
    role = payload.get("role")
    auth_type = payload.get("auth_type")

    project = get_project_or_404(db, current_user.id, project_id)
    file = db.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.file_type == FileType.CREDENTIALS).first()
    password = ""
    if file:
        with open(file.absolute_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row.get("username") == username:
                    password = row.get("password", "")
                    break

    def run():
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            page.goto(url)

            page.evaluate(f"""
    // 1. Define the logic globally first
    window.copyToClipboard = (event, text) => {{
        const btn = event.currentTarget;
        if (!btn) return;

        navigator.clipboard.writeText(text).then(() => {{
            const originalText = btn.innerText;
            btn.innerText = 'Copied!';
            btn.style.background = '#dcfce7';
            btn.style.borderColor = '#86efac';
            
            setTimeout(() => {{
                btn.innerText = originalText;
                btn.style.background = 'white';
                btn.style.borderColor = '#e2e8f0';
            }}, 1500);
        }}).catch(err => console.error("Copy failed", err));
    }};

    const box = document.createElement('div');
    box.id = 'playwright-helper-box';

    // 2. Updated HTML using 'event' instead of 'this'
    box.innerHTML = `
        <div id="drag-handle" style="background:#2563eb;color:white;padding:8px 12px;cursor:move;font-weight:bold;font-family:sans-serif;font-size:13px;border-top-left-radius:8px;border-top-right-radius:8px;display:flex;justify-content:space-between;align-items:center;">
            <span>Credentials Helper</span>
            <span style="opacity:0.7;font-size:10px;">⠿</span>
        </div>
        <div style="padding:15px;font-family:system-ui,sans-serif;font-size:13px;color:#1e293b;">
            <div style="margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#64748b;font-weight:600;">Username</span>
                <div>
                    <code style="background:#f1f5f9;padding:2px 6px;border-radius:4px;font-family:monospace;">{username}</code>
                    <button onclick="window.copyToClipboard(event, '{username}')" style="margin-left:8px;cursor:pointer;border:1px solid #e2e8f0;background:white;border-radius:4px;padding:2px 8px;">Copy</button>
                </div>
            </div>

            <div style="margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#64748b;font-weight:600;">Password</span>
                <div>
                    <code style="background:#f1f5f9;padding:2px 6px;border-radius:4px;font-family:monospace;">••••••••</code>
                    <button onclick="window.copyToClipboard(event, '{password}')" style="margin-left:8px;cursor:pointer;border:1px solid #e2e8f0;background:white;border-radius:4px;padding:2px 8px;">Copy</button>
                </div>
            </div>

            <hr style="border:0;border-top:1px solid #f1f5f9;margin:12px 0;"/>
            <div style="font-size:11px;color:#94a3b8;">
                <b>ROLE:</b> {role} | <b>AUTH:</b> {auth_type}
            </div>
        </div>
    `;

    // 3. Styling
    Object.assign(box.style, {{
        position: 'fixed', top: '50px', left: '50px', width: '280px',
        backgroundColor: 'white', boxShadow: '0 10px 25px rgba(0,0,0,0.2)',
        borderRadius: '8px', border: '1px solid #e2e8f0', zIndex: '2147483647',
        userSelect: 'none'
    }});

    document.body.appendChild(box);

    // 4. Improved Drag Logic
    let isDragging = false;
    let startX, startY, initialLeft, initialTop;
    const header = box.querySelector('#drag-handle');

    header.addEventListener('mousedown', (e) => {{
        isDragging = true;
        startX = e.clientX;
        startY = e.clientY;
        initialLeft = box.offsetLeft;
        initialTop = box.offsetTop;
        header.style.background = '#1d4ed8';
        e.preventDefault();
    }});

    document.addEventListener('mousemove', (e) => {{
        if (!isDragging) return;
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        box.style.left = (initialLeft + dx) + 'px';
        box.style.top = (initialTop + dy) + 'px';
    }});

    document.addEventListener('mouseup', () => {{
        isDragging = false;
        header.style.background = '#2563eb';
    }});
""")

            # ✅ KEEP BROWSER OPEN
            try:
                page.wait_for_timeout(600000)
            except Exception as e:
                print("Browser closed by user, ignoring...")
    threading.Thread(target=run).start()

    return {"status": "Playwright started"}

@router.post("/{project_id}/extract-pdfs")
def extract_pdfs(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_or_404(db, current_user.id, project_id)

    return start_pdf_extraction(db, project)

@router.get("/{project_id}/extract-status")
def get_extract_status(
    project_id: uuid.UUID,
):
    return PDF_PROGRESS.get(str(project_id), {
        "status": "idle",
        "progress": 0,
        "logs": []
    })
