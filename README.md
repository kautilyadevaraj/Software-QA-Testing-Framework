# SQAT — Software QA Testing Framework

> Autonomous QA pipeline: upload project documents → generate AI-powered test scenarios → execute Playwright scripts → classify results → raise Jira tickets. All from a single unified interface.

---

## Table of Contents

1. [Tech Stack](#tech-stack)
2. [Project Structure](#project-structure)
3. [Prerequisites](#prerequisites)
4. [Quick Start (TL;DR)](#quick-start-tldr)
5. [Detailed Setup](#detailed-setup)
   - [1. Database](#1-database-setup)
   - [2. Backend (FastAPI)](#2-backend-fastapi-setup)
   - [3. Frontend (Next.js)](#3-frontend-nextjs-setup)
   - [4. RabbitMQ (Docker)](#4-rabbitmq-docker)
6. [Environment Variables Reference](#environment-variables-reference)
7. [Database Migrations (Alembic)](#database-migrations-alembic)
8. [Phase Pipeline Overview](#phase-pipeline-overview)
9. [API Reference](#api-reference)
10. [Jira Integration](#jira-integration)
11. [Groq API Setup](#groq-api-setup)
12. [Contributing](#contributing)

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 15 (App Router), TypeScript, Tailwind CSS |
| **Backend** | FastAPI (Python 3.11+), SQLAlchemy 2, Alembic |
| **Database** | PostgreSQL 14+ |
| **Message Queue** | RabbitMQ (via Docker) |
| **LLM / Agents** | Groq (LLaMA 3.3 70B) via LangChain |
| **Vector DB** | Qdrant Cloud |
| **Embeddings** | HuggingFace Sentence Transformers |
| **Test Execution** | Playwright (Chromium workers) |
| **Auth** | JWT (access + refresh tokens, HTTP-only cookies) |
| **Ticketing** | Jira Cloud REST API v3 |
| **Doc Parsing** | PyMuPDF (PDF), Prance (Swagger/OpenAPI) |
| **MCP Server** | FastMCP — tool bus for agent pipeline |

---

## Project Structure

```
Software-QA-Testing-Framework/
├── docker-compose.yml               # RabbitMQ (one-command infra)
│
├── client/                          # Next.js 15 frontend
│   ├── app/
│   │   ├── login/
│   │   ├── signup/
│   │   └── projects/[projectId]/    # Project detail (3 tabs)
│   ├── components/
│   │   ├── ui/                      # Shared UI primitives (Button, Card…)
│   │   ├── phase3-panel.tsx         # Phase 3 — Generate → Approve → Execute
│   │   ├── phase3-review-queue.tsx  # Phase 3 — live SSE review queue
│   │   ├── scenario-qa-panel.tsx    # Phase 2 — scenario QA panel
│   │   └── raise-ticket-modal.tsx   # Jira ticket modal
│   ├── lib/
│   │   ├── api.ts                   # Typed API client (all endpoints)
│   │   └── projects.ts              # Project domain types & helpers
│   ├── .env.example                 # ← copy to .env and fill in values
│   └── package.json
│
└── server/                          # FastAPI backend
    ├── app/
    │   ├── agents/                  # AI agent pipeline (A3–A7)
    │   ├── core/                    # Config, JWT, bcrypt
    │   ├── db/                      # SQLAlchemy engine & session
    │   ├── graph/                   # LangGraph orchestrator
    │   ├── models/                  # SQLAlchemy ORM models
    │   ├── routers/                 # FastAPI route handlers
    │   ├── schemas/                 # Pydantic request/response models
    │   ├── services/                # Business logic (Jira, RabbitMQ, etc.)
    │   └── utils/
    ├── migrations/                  # Alembic migration versions
    ├── tests/generated/             # Auto-generated Playwright .spec.ts (gitignored)
    ├── .env.example                 # ← copy to .env and fill in values
    ├── alembic.ini
    ├── pyproject.toml
    ├── requirements.txt
    └── init_db.py                   # One-shot DB bootstrap (alternative to Alembic)
```

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| **Node.js** | 18+ | [nodejs.org](https://nodejs.org) |
| **Python** | 3.11+ | [python.org](https://python.org) |
| **PostgreSQL** | 14+ | Running locally or via Docker |
| **Docker Desktop** | Latest | Required for RabbitMQ |
| **uv** | Latest | `pip install uv` — fast Python package manager |
| **Playwright** | Bundled | Installed via `playwright install chromium` |

---

## Quick Start (TL;DR)

```bash
# 1. Clone the repo
git clone <repo-url>
cd Software-QA-Testing-Framework

# 2. Start RabbitMQ
docker compose up -d

# 3. Backend
cd server
uv venv && .venv\Scripts\activate        # Windows
# source .venv/bin/activate              # macOS/Linux
uv pip install -r requirements.txt
playwright install chromium
copy .env.example .env                   # then fill in required values
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# 4. Frontend (new terminal)
cd client
npm install
copy .env.example .env                   # already has the right default
npm run dev
```

Open **http://localhost:3000** — register an account and you're in.

---

## Detailed Setup

### 1. Database Setup

Create the `sqat_db` database in PostgreSQL:

**pgAdmin (GUI):**
1. Open pgAdmin → right-click **Databases** → **Create** → **Database**
2. Name it `sqat_db` → **Save**

**psql (terminal):**
```bash
psql -U postgres -c "CREATE DATABASE sqat_db;"
```

---

### 2. Backend (FastAPI) Setup

```bash
cd server

# Create and activate virtualenv
uv venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# Install Python dependencies
uv pip install -r requirements.txt
# OR: uv sync  (reads from pyproject.toml)

# Install Playwright Chromium browser
playwright install chromium
```

#### 2a — Configure `server/.env`

```bash
copy server\.env.example server\.env   # Windows
# cp server/.env.example server/.env   # macOS/Linux
```

Open `server/.env` and set **at minimum**:

```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/sqat_db
JWT_SECRET_KEY=some_random_string_at_least_32_chars
GROQ_API_KEY=gsk_your_groq_key_here
```

See the full [Environment Variables Reference](#environment-variables-reference) below for all options.

#### 2b — Run Database Migrations

**Fresh install** (no tables yet):
```bash
alembic upgrade head
```

**Existing DB** (tables already created by a previous `init_db.py` run):
```bash
alembic stamp head   # marks existing schema as up-to-date
```

> After stamping, all future schema changes use `alembic upgrade head`.

**Alternative — skip Alembic entirely** (one-shot local setup):
```bash
python init_db.py
```

#### 2c — Start the Server

```bash
uvicorn app.main:app --reload --port 8000
```

- API base: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`

---

### 3. Frontend (Next.js) Setup

```bash
cd client
npm install
```

#### 3a — Configure `client/.env`

```bash
copy client\.env.example client\.env   # Windows
# cp client/.env.example client/.env   # macOS/Linux
```

The default value works for local dev:
```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

#### 3b — Start the Frontend

```bash
npm run dev
```

App available at: **http://localhost:3000**

---

### 4. RabbitMQ (Docker)

Required for Phase 3 Playwright test execution.

```bash
# From the repo root
docker compose up -d
```

RabbitMQ management UI: **http://localhost:15672** (guest / guest)

To stop:
```bash
docker compose down
```

---

## Full Startup Checklist

```
☐ 1. PostgreSQL is running
☐ 2. sqat_db database exists
☐ 3. Docker Desktop is running
☐ 4. docker compose up -d              ← starts RabbitMQ
☐ 5. server/.env is configured (DATABASE_URL, JWT_SECRET_KEY, GROQ_API_KEY)
☐ 6. cd server && alembic upgrade head
☐ 7. cd server && uvicorn app.main:app --reload --port 8000
☐ 8. cd client && npm install && npm run dev
☐ 9. Open http://localhost:3000
```

> **Windows tip:** Always use `alembic` and `uvicorn` from inside the activated `.venv` — not system-level commands.

---

## Environment Variables Reference

### `server/.env`

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `JWT_SECRET_KEY` | ✅ | — | Min 32-char random string for signing JWTs |
| `FRONTEND_ORIGINS` | ✅ | `http://localhost:3000` | CORS allowed origins |
| `GROQ_API_KEY` | ✅ | — | Groq API key(s), comma-separated for rotation |
| `GROQ_MODEL` | — | `llama-3.3-70b-versatile` | LLM model used by agents |
| `GROQ_MAX_TOKENS` | — | `1024` | Max tokens per LLM call |
| `HF_TOKEN` | — | — | HuggingFace token for embeddings |
| `QDRANT_URL` | — | — | Qdrant cloud cluster URL |
| `QDRANT_API_KEY` | — | — | Qdrant cloud API key |
| `RABBITMQ_URL` | — | `amqp://guest:guest@localhost:5672/` | RabbitMQ connection |
| `CHROMIUM_WORKERS` | — | `3` | Parallel Playwright worker count |
| `TEST_TIMEOUT_MS` | — | `60000` | Playwright test timeout (ms) |
| `JIRA_BASE_URL` | — | — | Atlassian workspace URL |
| `JIRA_EMAIL` | — | — | Jira account email |
| `JIRA_API_TOKEN` | — | — | Jira API token |
| `JIRA_LEAD_ACCOUNT_ID` | — | — | Jira account ID (not email) |

> Copy `server/.env.example` — it has every variable documented with inline comments.

### `client/.env`

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | ✅ | `http://localhost:8000` | Backend API URL |

---

## Database Migrations (Alembic)

Run all commands from the `server/` directory with the venv **activated**.

| Task | Command |
|---|---|
| Apply all pending migrations | `alembic upgrade head` |
| Roll back one step | `alembic downgrade -1` |
| Roll back to baseline | `alembic downgrade base` |
| Generate migration from model changes | `alembic revision --autogenerate -m "description"` |
| Create blank migration | `alembic revision -m "description"` |
| Show migration history | `alembic history` |
| Show current revision | `alembic current` |
| Mark existing DB as up-to-date | `alembic stamp head` |

### Team workflow for schema changes

```
Teammate:
  1. Edit SQLAlchemy model in server/app/models/
  2. alembic revision --autogenerate -m "add column"
  3. Review generated file in server/migrations/versions/
  4. Commit both the model change AND the migration file

You (after pulling):
  5. alembic upgrade head   ← applies any new migrations
```

---

## Phase Pipeline Overview

```
Phase 1 — Project Setup
  Upload BRD / FSD / Swagger → ingest → extract → embed into Qdrant

Phase 2 — Scenario Generation
  Agent 1 (BRD) + Agent 2 (Swagger) → Agent 3 (dedup)
  → High-Level Scenarios → Human review & approval

Phase 3 — Test Generation & Execution
  A3 Planner  → test cases (from approved HLS)
  A4 Context  → DOM snapshots + execution context
  A5 Generator → .spec.ts Playwright scripts (Groq)
  ↓ RabbitMQ queue → Chromium Workers (parallel)
  A6 Classifier → PASS / SCRIPT_ERROR / APP_ERROR
  A7 Retry     → repairs SCRIPT_ERROR scripts (max 3 retries)
  → Review Queue (APP_ERROR / exhausted retries)
  → Raise Jira ticket from Review Queue
```

### Phase 3 UI (Generate → Approve → Execute tab)

1. **Generate Test Cases** — calls A3 Planner, streams test cases as they appear
2. **Review & Edit** — expand each test case, edit steps/AC inline, approve individually
3. **Approve All** — bulk-approve all test cases
4. **Execute** — triggers A4 → A5 → RabbitMQ → Chromium workers
5. **Live Execution Log** — polls every 2 s, shows PASS / FAIL / HUMAN_REVIEW per test
6. **Review Queue** — items needing human attention stream via SSE; raise Jira directly

---

## API Reference

Base URL: `http://localhost:8000/api/v1`
Interactive docs: `http://localhost:8000/docs`

### Auth

| Method | Path | Description |
|---|---|---|
| POST | `/auth/signup` | Register a new user |
| POST | `/auth/login` | Login → receive auth cookies |
| POST | `/auth/logout` | Clear auth cookies |
| GET | `/auth/me` | Get current user |
| POST | `/auth/refresh` | Refresh access token via refresh cookie |

### Projects

| Method | Path | Description |
|---|---|---|
| GET | `/projects` | List projects (paginated) |
| POST | `/projects` | Create a project |
| GET | `/projects/{id}` | Get project by ID |
| PUT | `/projects/{id}` | Update project |
| DELETE | `/projects/{id}` | Delete project |
| GET | `/projects/{id}/members` | List project members |
| POST | `/projects/{id}/members` | Add member by email |
| DELETE | `/projects/{id}/members/{memberId}` | Remove member |
| POST | `/projects/{id}/members/{memberId}/transfer` | Transfer ownership |
| POST | `/projects/{id}/launch` | Launch project URL context |
| POST | `/projects/{id}/verify` | Mark project as verified |
| GET | `/projects/{id}/documents` | List uploaded documents |
| POST | `/projects/{id}/documents` | Upload document(s) |
| DELETE | `/projects/{id}/documents/{docId}` | Delete a document |
| POST | `/projects/{id}/ingest` | Start ingestion pipeline |
| GET | `/projects/{id}/status` | Poll extraction status |

### High-Level Scenarios (Phase 2)

| Method | Path | Description |
|---|---|---|
| POST | `/projects/{id}/scenarios/generate` | Generate scenario preview |
| POST | `/projects/{id}/scenarios/approve` | Save selected scenarios |
| GET | `/projects/{id}/scenarios` | List saved scenarios |
| POST | `/projects/{id}/scenarios` | Create manual scenario |
| PATCH | `/projects/{id}/scenarios/{scenarioId}` | Update scenario |
| DELETE | `/projects/{id}/scenarios/{scenarioId}` | Delete scenario |

### Phase 3 — Test Automation

| Method | Path | Description |
|---|---|---|
| POST | `/projects/{id}/phase3/plan` | Generate test cases (A3 Planner) |
| POST | `/projects/{id}/phase3/execute` | Execute a planned run |
| POST | `/projects/{id}/phase3/cancel` | Cancel running execution |
| POST | `/projects/{id}/phase3/reset` | Delete all Phase 3 data for project |
| GET | `/projects/{id}/phase3/run-status` | Poll run counters & status |
| GET | `/projects/{id}/phase3/execution-state` | Live per-test state |
| GET | `/projects/{id}/phase3/test-cases/{runId}` | List test cases for a run |
| PATCH | `/projects/{id}/phase3/test-cases/{testId}` | Edit test case |
| PATCH | `/projects/{id}/phase3/test-cases/{testId}/approval` | Set approval status |
| POST | `/projects/{id}/phase3/approve-all/{runId}` | Bulk-approve all test cases |
| GET | `/projects/{id}/phase3/review-queue` | List review queue items |
| GET | `/projects/{id}/phase3/review-queue/stream` | SSE stream of new items |
| PATCH | `/projects/{id}/phase3/review-queue/{itemId}` | Update review item |
| POST | `/projects/{id}/phase3/raise-jira` | Raise Jira issue from review item |

### Jira Integration

| Method | Path | Description |
|---|---|---|
| POST | `/projects/{id}/jira/connect` | Connect project to Jira (idempotent) |
| GET | `/projects/{id}/jira/config` | Get Jira connection status & key |
| POST | `/projects/{id}/tickets` | Raise Jira ticket & save locally |

---

## Jira Integration

### Get Your Credentials

| Variable | Where to Find |
|---|---|
| `JIRA_BASE_URL` | Your Atlassian URL, e.g. `https://yourcompany.atlassian.net` |
| `JIRA_EMAIL` | Email you log in to Jira with |
| `JIRA_API_TOKEN` | [Manage API tokens](https://id.atlassian.com/manage-profile/security/api-tokens) → Create |
| `JIRA_LEAD_ACCOUNT_ID` | Visit `https://yourcompany.atlassian.net/rest/api/3/myself` → copy `accountId` |

### Connect a Project

1. Open any project → **Project Configuration** tab
2. Click **"Connect to Jira"**
3. SQAT auto-creates a Jira project and links it (e.g. `My Shopping App → MSA`)
4. Button turns green: `● Connected · Key: MSA`

> This is **idempotent** — clicking again returns the existing key without creating duplicates.

### Raise a Ticket

- From the **Review Queue**: click **Raise Jira** on any failed test item
- From the **URL / Credentials** section: click **Raise Ticket**
- Edit Title, Description, Issue Type, and Priority before submitting

---

## Groq API Setup

1. Sign in at [console.groq.com](https://console.groq.com)
2. Go to **API Keys** → **Create API Key** → copy the key
3. Add to `server/.env`:

```env
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_MAX_TOKENS=1024
```

**Multiple keys** (for rate-limit failover):
```env
GROQ_API_KEY=gsk_key_one,gsk_key_two,gsk_key_three
```

Restart the backend after editing `.env` to reload settings.

---

## Document Upload Rules

| Category | Format | Notes |
|---|---|---|
| BRD | PDF | Required — business requirements |
| FSD | PDF | Functional specification |
| WBS | PDF | Work breakdown structure |
| Assumptions | PDF | Single file |
| Credentials | PDF or TXT | Required — test user credentials |
| Swagger Docs | YAML or JSON | Required — API specification |

> **Max file size:** 20 MB per file

Files are stored at `server/uploads/{ProjectID}/` (gitignored — never committed).

---

## Contributing

1. **Branch naming:** `feat/<short-description>`, `fix/<short-description>`, `chore/<short-description>`
2. **DB changes:** Always create an Alembic migration alongside your model change and commit both
3. **Secrets:** Never commit `.env` files — use `.env.example` as the template
4. **Generated files:** `server/tests/generated/` and `server/test-results/` are gitignored — don't force-add them
5. **After pulling:** run `alembic upgrade head` if any new migration files were added

```bash
# Standard workflow after pulling
cd server
alembic upgrade head   # apply any new DB migrations
uvicorn app.main:app --reload --port 8000

cd ../client
npm install            # in case new packages were added
npm run dev
```
