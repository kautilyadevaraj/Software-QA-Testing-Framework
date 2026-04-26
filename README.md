# SQAT - Software QA Testing Framework

Autonomous QA pipeline: upload project documents, verify the target URL, configure credentials, and raise Jira tickets - all from a single unified interface.

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 16 (App Router), TypeScript, Vanilla CSS |
| **Backend** | FastAPI (Python 3.11+), SQLAlchemy 2, Alembic |
| **Database** | PostgreSQL 14+ |
| **Extraction** | PyMuPDF (PDF), Prance (Swagger/OpenAPI), Playwright (Auth/E2E) |
| **Auth** | JWT (access + refresh tokens, HTTP-only cookies) |
| **Vector DB** | Qdrant Cloud |
| **Embeddings** | Hugging Face (Sentence Transformers) |
| **Ticketing** | Jira Cloud REST API v3 |
| **File Storage** | Local filesystem (`server/uploads/`) |

## Prerequisites

- **Node.js** 18+
- **Python** 3.11+
- **PostgreSQL** 14+ running locally
- **uv** (Python package manager) - install with `pip install uv` or see [uv docs](https://docs.astral.sh/uv/)

## Project Structure

```
Software-QA-Testing-Framework/
├── client/                          # Next.js frontend
│   ├── app/                         # App Router pages
│   │   ├── login/
│   │   ├── signup/
│   │   └── projects/
│   │       └── [projectId]/         # Project detail page
│   ├── components/                  # Shared UI components
│   │   └── raise-ticket-modal.tsx   # Jira ticket creation modal
│   ├── lib/
│   │   ├── api.ts                   # Typed API client (all endpoints)
│   │   └── projects.ts              # Project domain types & helpers
│   ├── .env                         # Local env (gitignored)
│   ├── .env.example                 # Template (tracked)
│   └── proxy.ts                     # Auth guard (Next.js 16)
│
└── server/                          # FastAPI backend
      ├── app/
      │   ├── core/                    # Config (settings), JWT, bcrypt
      │   ├── db/                      # SQLAlchemy engine & session
      │   ├── models/                  # ORM models
      │   │   └── project.py           # Project, ProjectJiraConfig, JiraTicket
      │   ├── routers/                 # API route handlers
      │   │   └── projects.py          # Includes Jira connect & ticket endpoints
      │   ├── schemas/                 # Pydantic request/response models
      │   ├── services/                # Business logic
      │   │   └── jira_service.py      # Jira Cloud REST API integration
      │   └── utils/                   # Rate limiter
      ├── migrations/                  # Alembic migration files
      │   └── versions/
      │       ├── 0001_consolidated.py
      │       ├── 0002_add_jira_tables.py
      │       └── 0003_add_high_level_scenarios.py
      ├── uploads/                     # User-uploaded files (gitignored)
      ├── .env                         # Local env (gitignored)
      ├── .env.example                 # Template (tracked)
      ├── alembic.ini
      ├── init_db.py                   # Dev utility: creates DB + all tables
      └── pyproject.toml
```

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

# Install Playwright browsers (required for E2E Auth features)
playwright install
```

### 2a - Configure `server/.env`

```bash
copy server\.env.example server\.env   # Windows
# cp server/.env.example server/.env   # macOS/Linux
```

Edit `server/.env` and fill in required and commonly used optional values:

```env
# Required
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/sqat_db
JWT_SECRET_KEY=at_least_24_characters_long_random_string
FRONTEND_ORIGINS=http://localhost:3000

# Optional: Scenario generation (Groq)
GROQ_API_KEY=gsk_key_one,gsk_key_two
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_MAX_TOKENS=1024

# Optional: RAG and semantic search
HF_TOKEN=your_huggingface_token
HF_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
HF_MODELS_DIR=models
QDRANT_URL=your_qdrant_cloud_url
QDRANT_API_KEY=your_qdrant_api_key

# Optional: Jira integration
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@yourcompany.com
JIRA_API_TOKEN=your_jira_api_token
JIRA_LEAD_ACCOUNT_ID=your_jira_account_id
```

> `GROQ_API_KEY` supports comma-separated keys for rotation and failover. All other values in `.env.example` are fine as defaults for local development.

### 2b - Run Database Migrations

**Fresh installation** (no tables yet):
```bash
cd server
alembic upgrade head
```

**Existing database** (tables already created by a previous `init_db.py` run):
```bash
cd server
alembic stamp head
```

> After stamping, all future schema changes use `alembic upgrade head`.

### 2c - Alternative: Initialise Database Without Alembic

If you prefer not to use Alembic for a fresh local setup:

```bash
cd server
python init_db.py
```

This creates `sqat_db` (if it doesn't exist) and all tables from the SQLAlchemy models.  
No seed data is inserted - register your first user via the UI or `POST /api/v1/auth/signup`.

### 2d - Start the Server

```bash
uvicorn app.main:app --reload --port 8000
```

- API base: `http://localhost:8000`
- Interactive docs: `http://localhost:8000/docs`

## 3 - Frontend (Next.js) Setup

```bash
cd client
npm install
```

### 3a - Configure `client/.env`

```bash
copy client\.env.example client\.env   # Windows
# cp client/.env.example client/.env   # macOS/Linux
```

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
3. server/.env is configured (DATABASE_URL, JWT_SECRET_KEY)
4. cd server && alembic upgrade head
5. cd server && uvicorn app.main:app --reload --port 8000
6. cd client && npm run dev
7. Open http://localhost:3000
```

## 5 - Jira Integration Setup

The Jira integration allows you to raise Jira tickets directly from the URL verification and credentials verification sections of any project.

### 5a - Get Your Credentials

| Variable | Where to Find It |
|---|---|
| `JIRA_BASE_URL` | Your Atlassian workspace URL, e.g. `https://yourcompany.atlassian.net` |
| `JIRA_EMAIL` | The email address you use to log in to Jira |
| `JIRA_API_TOKEN` | [Manage API tokens](https://id.atlassian.com/manage-profile/security/api-tokens) → **Create API token** |
| `JIRA_LEAD_ACCOUNT_ID` | Visit `https://yourcompany.atlassian.net/rest/api/3/myself` while logged in → copy the `accountId` field |

### 5b - Add to `.env`

```env
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@yourcompany.com
JIRA_API_TOKEN=your_api_token_here
JIRA_LEAD_ACCOUNT_ID=712020:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

> Restart the backend server after editing `.env` to reload the cached settings.

### 5c - Connect a Project to Jira

1. Open any project → **Project Configuration** tab
2. Click **"Connect to Jira"** in the top-right of the card
3. SQAT auto-generates a Jira project key from the project name (e.g. `My Shopping App → MSA`) and creates the Jira project
4. The button changes to a green badge: `● Connected · Key: MSA`
5. This is a **one-time action per project** - clicking again returns the existing key without creating duplicates

### 5d - Raise a Ticket

**From the URL section:**
- Launch a URL → click **"Raise Ticket"**
- The modal opens pre-filled with URL verification context

**From the Credentials section:**
- Each credential card has its own **"Raise Ticket"** button
- The modal opens pre-filled with that credential's username, role, auth type, and endpoint

Both modals let you edit: **Title**, **Description**, **Issue Type** (Bug / Task / Story), **Priority** (High / Medium / Low).

On submit: the ticket is created in Jira AND saved locally to the `jira_tickets` database table.

> The "Raise Ticket" buttons are **disabled** until the project is connected to Jira.

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

### Adding a New Column

```bash
# 1. Edit the SQLAlchemy model in server/app/models/
# 2. Auto-generate the migration
alembic revision --autogenerate -m "add column to table"
# 3. Review the generated file in server/migrations/versions/
# 4. Apply it
alembic upgrade head
```

### Team Collaboration with DB Changes

1. **Teammate modifies a model** → runs `alembic revision --autogenerate` → commits both the model change and the migration file
2. **You pull their code** → your local DB doesn't have the new table yet
3. **You sync** → run `alembic upgrade head` - done

> Roll back safely at any time with `alembic downgrade -1`.

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

Files are stored under `server/uploads/` using the layout:

```
server/uploads/
└── {ProjectID}/
      └── {ProjectID}_{FileID}_{FileType}_{Number}
```

`{Number}` is the sequential count of files of the same category within the project (1-based). The original filename is preserved in `project_files.original_filename`.

Example - 2 BRDs + 1 Swagger file:
```
server/uploads/
└── 018e1a2b-.../
      ├── 018e1a2b-..._019f3c4d-..._brd_1
      ├── 018e1a2b-..._019f3c4e-..._brd_2
      └── 018e1a2b-..._019f3c4f-..._swagger_docs_1
```

### Upload Prerequisites

File uploads are only enabled after:
1. Entering a Project URL and clicking **Launch**
2. Clicking **Proceed** to confirm

## 6 - GROQ API Setup

Use Groq API keys for scenario generation agents used by the backend.

### 6a - Create a Groq API Key

1. Open https://console.groq.com/
2. Sign in or create an account
3. Go to API Keys
4. Click Create API Key
5. Copy the key and store it safely

### 6b - Add GROQ values to `server/.env`

```env
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_MAX_TOKENS=1024
```

You can also configure multiple keys for failover and rotation.

```env
GROQ_API_KEY=gsk_key_one,gsk_key_two,gsk_key_three
```

### 6c - Restart backend after env change

After updating `server/.env`, restart the backend server so new settings are loaded.

### 6d - Verify configuration

- Start backend normally.
- Trigger scenario generation from the UI.
- If key is missing, backend raises: `GROQ_API_KEY is not configured`.

### 6e - Common notes

- `GROQ_API_KEY` is required for scenario generation.
- `GROQ_MODEL` is optional. Default is `llama-3.3-70b-versatile`.
- `GROQ_MAX_TOKENS` is optional. Default is `1024`.

## 8 - API Reference

Base URL: `http://localhost:8000/api/v1`  
Full interactive docs: `http://localhost:8000/docs`

### Auth

| Method | Path | Description |
|---|---|---|
| POST | `/auth/signup` | Register a new user |
| POST | `/auth/login` | Login and receive auth cookies |
| POST | `/auth/logout` | Clear auth cookies |
| GET | `/auth/me` | Get current authenticated user |

### Projects

| Method | Path | Description |
|---|---|---|
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
| POST | `/projects/{id}/ingest` | Start ingestion pipeline |
| GET | `/projects/{id}/status` | Poll extraction status |

### Jira Integration

| Method | Path | Description |
|---|---|---|
| POST | `/projects/{id}/jira/connect` | Connect project to Jira (creates Jira project, idempotent) |
| GET | `/projects/{id}/jira/config` | Get current Jira connection status & project key |
| POST | `/projects/{id}/tickets` | Raise a Jira ticket and save locally |

## 9 - Additional and New Endpoints

Base URL: `http://localhost:8000/api/v1`

### 9a - Project Runtime and Verification Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{id}/launch` | Launch project URL context and reset verification state |
| POST | `/projects/{id}/verify` | Mark launched project as verified |
| POST | `/projects/{id}/ingest` | Start ingestion workflow |
| GET | `/projects/{id}/credentials` | Read credential rows from uploaded Credentials file |
| POST | `/projects/{id}/mark-verified` | Toggle credential verified flag |
| POST | `/projects/{id}/run-playwright` | Open Playwright browser helper for credential testing |
| POST | `/projects/{id}/extract-pdfs` | Start extraction processing job |
| GET | `/projects/{id}/extract-status` | Poll extraction progress and logs |

### 9b - Jira Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{id}/jira/connect` | Create and connect Jira project for SQAT project |
| GET | `/projects/{id}/jira/config` | Get Jira connection state and Jira key |
| POST | `/projects/{id}/tickets` | Create Jira issue and store local ticket record |

### 9c - Scenario Endpoints (High Level Scenarios)

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{id}/scenarios/generate` | Generate preview scenarios using agent graph |
| POST | `/projects/{id}/scenarios/approve` | Save selected generated scenarios to DB |
| GET | `/projects/{id}/scenarios` | List saved scenarios |
| POST | `/projects/{id}/scenarios` | Create manual scenario |
| PATCH | `/projects/{id}/scenarios/{scenarioId}` | Update title/description/status/completed_by |
| DELETE | `/projects/{id}/scenarios/{scenarioId}` | Delete scenario |

### 9d - Auth Endpoint to Note

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/refresh` | Refresh access token using refresh cookie |

### 9e - Project Response Fields Added

Project response includes:

- `owner_id`
- `tester_ids`
- `tester_emails`

These fields are used by frontend permission and display logic, including owner-only delete controls and tester email tags in project row expansion.

## 10 - High Level Scenarios Generation and Agent Pipeline

This feature generates tester-facing High Level Scenarios from ingested project documents and API documentation.

### 10a - Prerequisites for Scenario Generation

1. Configure Groq in `server/.env`:
       - `GROQ_API_KEY`
       - Optional: `GROQ_MODEL`, `GROQ_MAX_TOKENS`
2. Configure Qdrant in `server/.env`:
       - `QDRANT_URL`
       - `QDRANT_API_KEY`
3. Upload project documents and run ingestion/extraction so chunks are available.

### 10b - Generation Flow

1. Call `POST /projects/{id}/scenarios/generate` to produce preview scenarios.
2. Review output in UI.
3. Call `POST /projects/{id}/scenarios/approve` to save selected scenarios.
4. Use `GET /projects/{id}/scenarios` for listing.
5. Use `PATCH` and `DELETE` endpoints for maintenance.

### 10c - Agent Graph Architecture

The scenario graph runs three agents in sequence:

1. `agent_1` (business docs)
2. `agent_2` (Swagger/OpenAPI docs + endpoint fallback)
3. `agent_3` (deduplication and canonicalization)

Execution order:

`agent_1 -> agent_2 -> agent_3 -> final scenarios`

### 10d - Agent Responsibilities

- `agent1_brd.py`
      - Reads business-side chunks such as BRD/FSD/WBS/Assumptions
      - Produces user-journey oriented HLS candidates

- `agent2_swagger.py`
      - Reads API documentation chunks (Swagger/OpenAPI)
      - If chunks are unavailable, falls back to `api_endpoints` table
      - Produces workflow-level HLS candidates grounded in API capabilities

- `agent3_dedup.py`
      - Combines outputs from agent 1 and agent 2
      - Removes semantic duplicates
      - Filters against existing scenarios
      - Applies max scenario limit

### 10e - Generate Request Example

```json
{
      "max_scenarios": 20,
      "scenario_types": ["ALL"],
      "access_mode": "UI_ONLY_WEB",
      "scenario_level": "HLS",
      "existing_scenarios": []
}
```

### 10f - Scenario State and Sources

- `source` values: `agent_1`, `agent_2`, `manual`
- `status` values: `pending`, `completed`
- When marking completed via `PATCH`, `current_user_id` is required.

### 10g - Database Objects

- Table: `high_level_scenarios`
- Migration: `0003_add_high_level_scenarios.py`
- Includes indexes on `project_id`, `status`, `completed_by`

