import threading
import fitz
from pathlib import Path
import os
from sqlalchemy.orm import Session
from sqlalchemy import select, func
import prance
import re
import time
import json
import uuid

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
except ImportError:
    QdrantClient = None
    models = None

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

from app.models.project import ProjectFile, FileType, Project, ExtractedText, APIEndpoint, Chunk
from app.core.config import get_settings
from app.db.session import SessionLocal

PDF_PROGRESS = {}
_EMBEDDER = None
_EMBEDDER_LOCK = threading.Lock()


def _get_models_root() -> Path:
    settings = get_settings()
    configured_path = Path(settings.hf_models_dir)
    if configured_path.is_absolute():
        return configured_path

    server_root = Path(__file__).resolve().parents[2]
    return server_root / configured_path


def _load_sentence_transformer(model_ref: str, cache_dir: Path, token: str | None, local_only: bool = False):
    if SentenceTransformer is None:
        return None

    load_kwargs = {
        "cache_folder": str(cache_dir),
        "local_files_only": local_only,
    }
    if token:
        load_kwargs["token"] = token

    try:
        return SentenceTransformer(model_ref, **load_kwargs)
    except TypeError:
        if token:
            load_kwargs.pop("token", None)
            load_kwargs["use_auth_token"] = token
            return SentenceTransformer(model_ref, **load_kwargs)
        raise


def _is_local_model_ready(model_dir: Path) -> bool:
    # SentenceTransformer.save() outputs these files at model root.
    return (model_dir / "modules.json").exists() and (model_dir / "config_sentence_transformers.json").exists()


def get_embedder():
    global _EMBEDDER

    if _EMBEDDER is not None:
        return _EMBEDDER

    with _EMBEDDER_LOCK:
        if _EMBEDDER is not None:
            return _EMBEDDER

        settings = get_settings()
        model_name = settings.hf_model_name
        model_slug = model_name.split("/")[-1]

        models_root = _get_models_root()
        model_dir = models_root / model_slug
        cache_dir = models_root / "hf-cache"

        models_root.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)

        token = settings.hf_token
        if token:
            os.environ.setdefault("HF_TOKEN", token)
            os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", token)

        os.environ.setdefault("HF_HOME", str(cache_dir))
        os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_dir))

        has_local_model = _is_local_model_ready(model_dir)

        if has_local_model:
            try:
                _EMBEDDER = _load_sentence_transformer(str(model_dir), cache_dir, token, local_only=True)
                return _EMBEDDER
            except Exception as exc:
                print(f"Failed to load local model from '{model_dir}': {exc}")

        try:
            _EMBEDDER = _load_sentence_transformer(model_name, cache_dir, token, local_only=False)
            if _EMBEDDER is not None:
                try:
                    model_dir.mkdir(parents=True, exist_ok=True)
                    _EMBEDDER.save(str(model_dir))
                except Exception as exc:
                    print(f"Failed to persist model to '{model_dir}': {exc}")
        except Exception as exc:
            print(f"Failed to load sentence-transformers model '{model_name}': {exc}")
            _EMBEDDER = None

        return _EMBEDDER


def chunk_text(text: str, chunk_size: int = 1000, overlap_ratio: float = 0.2):
    overlap = int(chunk_size * overlap_ratio)
    step = chunk_size - overlap
    chunks = []
    start_indices = []
    end_indices = []
    
    if not text:
        return chunks, start_indices, end_indices
        
    for i in range(0, len(text), step):
        chunk = text[i:i+chunk_size]
        chunks.append(chunk)
        start_indices.append(i)
        end_indices.append(i + len(chunk))
        if i + chunk_size >= len(text):
            break
    return chunks, start_indices, end_indices


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

        import re
        safe_project_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', project.name)
        collection_name = f"{project.id}_{safe_project_name}"

        qdrant_client = None
        if QdrantClient and settings.qdrant_url and settings.qdrant_api_key:
            qdrant_client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60.0)
            if not qdrant_client.collection_exists(collection_name):
                qdrant_client.create_collection(
                    collection_name=collection_name,
                    vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
                )
            try:
                qdrant_client.create_payload_index(
                    collection_name=collection_name,
                    field_name="project_id",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception as exc:
                print(f"Qdrant payload index setup skipped/failed for project_id: {exc}")

        files = db.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project.id,
                ProjectFile.file_type.in_([
                    FileType.BRD,
                    FileType.FSD,
                    FileType.WBS,
                    FileType.ASSUMPTION,
                ])
            ).order_by(ProjectFile.file_type, ProjectFile.uploaded_at)
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
        embedder = get_embedder()
        if embedder is None:
            logs.append("Embeddings unavailable: model could not be loaded. Continuing without vector embeddings.")

        for i, file in enumerate(files):
            try:
                logs.append(f"Parsing {file.original_filename}...")
                PDF_PROGRESS[project_id_str] = {
                    "status": "processing",
                    "progress": int((i / total) * 100),
                    "logs": logs
                }
                time.sleep(1.0)


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
                db.refresh(extracted)

                logs.append(f"Generating chunks and embeddings for {file.original_filename}...")
                PDF_PROGRESS[project_id_str] = {
                    "status": "processing",
                    "progress": int((i / total) * 100) + 5,
                    "logs": logs
                }
                time.sleep(1.5)

                
                chunks_text, start_idx_list, end_idx_list = chunk_text(text, chunk_size=1000, overlap_ratio=0.2)
                
                if embedder and chunks_text:
                    embeddings = embedder.encode(chunks_text, convert_to_numpy=True).tolist()
                    qdrant_points = []
                    
                    for idx, (chunk_str, start_i, end_i, emb) in enumerate(zip(chunks_text, start_idx_list, end_idx_list, embeddings)):
                        # Deterministic ID using text hash for deduplication
                        qdrant_point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_str))
                        
                        db_chunk = Chunk(
                            file_id=file.id,
                            project_id=project.id,
                            extracted_text_id=extracted.id,
                            chunk_index=idx,
                            start_idx=start_i,
                            end_idx=end_i,
                            qdrant_point_id=qdrant_point_id
                        )
                        db.add(db_chunk)
                        
                        if qdrant_client:
                            qdrant_points.append(
                                models.PointStruct(
                                    id=qdrant_point_id,
                                    vector=emb,
                                    payload={
                                        "chunk_id": qdrant_point_id,
                                        "file_id": str(file.id),
                                        "project_id": str(project.id),
                                        "start_idx": start_i,
                                        "end_idx": end_i,
                                        "category": file.file_type.value,
                                        "text": chunk_str
                                    }
                                )
                            )
                    
                    db.commit()
                    
                    if qdrant_client and qdrant_points:
                        batch_size = 50
                        for i in range(0, len(qdrant_points), batch_size):
                            batch = qdrant_points[i:i + batch_size]
                            qdrant_client.upsert(
                                collection_name=collection_name,
                                points=batch
                            )

                logs.append(f"Successfully parsed {file.original_filename}")

            except Exception as e:
                logs.append(f"Failed {file.original_filename}: {str(e)}")

        swagger_files = db.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project.id,
                ProjectFile.file_type == FileType.SWAGGER_DOCS
            ).order_by(ProjectFile.uploaded_at)
        ).scalars().all()

        for file in swagger_files:
            try:
                logs.append(f"Parsing Swagger {file.original_filename}...")
                PDF_PROGRESS[project_id_str] = {
                    "status": "processing",
                    "progress": 90,
                    "logs": logs
                }

                
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