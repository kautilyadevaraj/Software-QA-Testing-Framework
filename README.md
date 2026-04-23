# SQAT - Software QA Testing Framework

Autonomous QA pipeline: upload project documents, verify the target URL, and generate Playwright E2E test cases from a single manual walkthrough.

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 16 (App Router), TypeScript, Tailwind-compatible Vanilla CSS |
| **Backend** | FastAPI (Python 3.11+), SQLAlchemy 2, Alembic |
| **Database** | PostgreSQL 14+ |
| **Extraction** | PyMuPDF (PDF Text), Prance (Swagger/OpenAPI), Playwright (Auth/E2E Docs) |
| **Auth** | JWT (access + refresh tokens, HTTP-only cookies) |
| **Vector DB** | Qdrant Cloud |
| **Embeddings** | Hugging Face (Sentence Transformers) |
| **File Storage** | Local filesystem (`server/uploads/`) |

## Prerequisites

- **Node.js** 18+
- **Python** 3.11+
- **PostgreSQL** 14+ running locally
- **uv** (Python package manager) - install with `pip install uv` or see [uv docs](https://docs.astral.sh/uv/)

## 1 - Database Setup

### Option A - pgAdmin (GUI)
1. Open pgAdmin → right-click **Databases** → **Create** → **Database**
2. Name it `sqat_db` → **Save**

### Option B - psql (terminal)
```bash
psql -U postgres -c "CREATE DATABASE sqat_db;"
```

## 2 - Backend (FastAPI) Setup

```bash
cd server

# Create and activate the virtual environment
uv venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate # macOS/Linux

# Install dependencies
uv pip install -r requirements.txt
# OR (reads from pyproject.toml)
uv sync

# Install Playwright browsers (Required for E2E Auth features)
playwright install
```

### 2a - Configure `.env`

Copy the example and fill in your values:

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux
```

Edit `server/.env`:

```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/sqat_db
JWT_SECRET_KEY=at_least_24_characters_long_random_string
FRONTEND_ORIGINS=http://localhost:3000

# Optional: For RAG / Semantic Search features
HF_TOKEN=your_huggingface_token
QDRANT_URL=your_qdrant_cloud_url
QDRANT_API_KEY=your_qdrant_api_key
```

> The rest of the values in `.env.example` are fine as defaults for local dev.

### 2b - Run Database Migrations

**Fresh installation** (no tables yet):
```bash
alembic upgrade head
```

**Existing database** (tables already created by a previous `init_db.py` run):
```bash
alembic stamp head
```

> After this, all future schema changes use: `alembic upgrade head`

### 2c - Initialise the Database (alternative to Alembic)

If you prefer not to use Alembic for a fresh local setup you can run:

```bash
python init_db.py
```

This will:
1. Create the `sqat_db` database if it does not exist
2. Create all tables from the SQLAlchemy models

> No seed data is inserted. Register your first user via the UI or `POST /api/v1/auth/signup`.

### 2d - Start the Server

```bash
uvicorn app.main:app --reload --port 8000
```

API is available at: `http://localhost:8000`  
Interactive docs: `http://localhost:8000/docs`

## 3 - Frontend (Next.js) Setup

```bash
cd client

npm install
```

### 3a - Configure `.env`

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux
```

`client/.env`:
```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### 3b - Start the Frontend

```bash
npm run dev
```

App is available at: `http://localhost:3000`

## 4 - Full Startup Checklist

```
1. PostgreSQL is running
2. sqat_db database exists
3. cd server && alembic upgrade head (or alembic stamp head for existing DB)
4. cd server && uvicorn app.main:app --reload --port 8000
5. cd client && npm run dev
6. Open http://localhost:3000
```

## Alembic - DB Migration Cheatsheet

Run all commands from the `server/` directory with the venv activated.

| Task | Command |
|---|---|
| Apply all pending migrations | `alembic upgrade head` |
| Roll back one migration | `alembic downgrade -1` |
| Roll back to the very beginning | `alembic downgrade base` |
| Create a new migration (auto-detect changes) | `alembic revision --autogenerate -m "your description"` |
| Create a blank migration | `alembic revision -m "your description"` |
| Show migration history | `alembic history` |
| Show current DB revision | `alembic current` |
| Mark existing DB as up-to-date (no SQL run) | `alembic stamp head` |

### Workflow for adding a new column

```bash
# 1. Edit the SQLAlchemy model in server/app/models/
# 2. Auto-generate the migration
alembic revision --autogenerate -m "add column to table"
# 3. Review the generated file in server/migrations/versions/
# 4. Apply it
alembic upgrade head
```

### Collaborating with DB Changes (Team workflow)

Alembic guarantees you and your team won't overwrite or unexpectedly break each other's local databases when changing the schema. 

1. **Your teammate modifies code:** They add/edit a SQLAlchemy model in `server/app/models/` (e.g. adding a new table) and run `alembic revision --autogenerate`. Alembic creates a fresh migration file inside `migrations/versions/`.
2. **They commit and push:** They push both the model changes and the new migration file to GitHub.
3. **You pull their code:** When you fetch their code, your local database doesn't magically have their new table yet.
4. **You sync up:** You simply run `alembic upgrade head`. Alembic perfectly runs their migration against your local DB to bring your schema perfectly into alignment with theirs!
> *(If their migration ever causes problems, you can simply run `alembic downgrade -1` to safely roll back your database state!)*

## Project Structure

```
Software-QA-Testing-Framework/
├── client/                  # Next.js frontend
│   ├── app/                 # App Router pages
│   │   ├── login/
│   │   ├── signup/
│   │   └── projects/
│   │       └── [projectId]/ # Project detail & file upload
│   ├── components/          # Shared UI components
│   ├── lib/
│   │   ├── api.ts           # Typed API client
│   │   └── projects.ts      # Project domain types & helpers
│   └── proxy.ts             # Auth guard — Next.js 16 proxy convention (was middleware.ts)
│
└── server/                  # FastAPI backend
    ├── app/
    │   ├── core/            # Config, security (JWT, bcrypt)
    │   ├── db/              # SQLAlchemy engine & session
    │   ├── models/          # ORM models
    │   ├── routers/         # API route handlers
    │   ├── schemas/         # Pydantic request/response models
    │   ├── services/        # Business logic
    │   └── utils/           # Rate limiter
    ├── migrations/          # Alembic migration files
    │   └── versions/
    ├── uploads/             # Uploaded files (gitignored)
    ├── alembic.ini          # Alembic configuration
    ├── database/            # schema.sql — human-readable reference schema
    ├── init_db.py           # Dev utility: creates DB + tables (no seed data)
    └── pyproject.toml       # Python dependencies
```

## Document Upload Rules

| Category | Format | Max Files | Required |
|---|---|---|---|
| BRD | PDF | Multiple | ✅ |
| FSD | PDF | Multiple | - |
| WBS | PDF | Multiple | - |
| Assumptions | PDF | 1 | - |
| Credentials | PDF or TXT | 1 | ✅ |
| Swagger Docs | YAML or JSON | 1 | ✅ |

> **Max file size:** 20 MB per file

### File Storage Convention

Files are stored locally under `server/uploads/` using the following layout:

```
server/uploads/
└── {ProjectID}/                                    ← folder per project
      └── {ProjectID}_{FileID}_{FileType}_{Number}  ← numbered file (no filename)
```

`{Number}` is the **sequential count** of files of the same category within the project at the time of upload (1-based). The original filename is preserved in the database (`project_files.original_filename`).

Example — uploading 2 BRDs and 1 Swagger file to a project:
```
server/uploads/
└── 018e1a2b-.../
      ├── 018e1a2b-..._019f3c4d-..._brd_1
      ├── 018e1a2b-..._019f3c4e-..._brd_2
      └── 018e1a2b-..._019f3c4f-..._swagger_docs_1
```

The full absolute path of each file is stored in the `project_files.absolute_path` column in the database.

### Upload Prerequisites

File uploads are only enabled after:
1. Entering a Project URL and clicking **Launch**
2. Clicking **Proceed** to confirm

## API Reference

Base URL: `http://localhost:8000/api/v1`  
Full interactive docs: `http://localhost:8000/docs`

| Method | Path | Description |
|---|---|---|
| POST | `/auth/signup` | Register a new user |
| POST | `/auth/login` | Login and receive auth cookies |
| POST | `/auth/logout` | Clear auth cookies |
| GET | `/auth/me` | Get current user |
| GET | `/projects` | List projects (paginated) |
| POST | `/projects` | Create a project |
| GET | `/projects/{id}` | Get project by ID |
| PUT | `/projects/{id}` | Update project |
| DELETE | `/projects/{id}` | Delete project |
| GET | `/projects/{id}/members` | List project members |
| POST | `/projects/{id}/members` | Add a member by email |
| DELETE | `/projects/{id}/members/{memberId}` | Remove a member |
| POST | `/projects/{id}/members/{memberId}/transfer` | Transfer ownership |
| GET | `/projects/{id}/documents` | List uploaded documents |
| POST | `/projects/{id}/documents` | Upload document(s) |
| DELETE | `/projects/{id}/documents/{docId}` | Delete a document |
| POST | `/projects/{id}/ingest` | Ingest project processing (Swagger, PDF docs) |
| GET | `/projects/{id}/status` | Poll extraction process status |
