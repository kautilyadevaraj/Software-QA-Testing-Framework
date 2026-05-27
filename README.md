# SQAT - Software QA Testing Framework

SQAT is an AI-assisted QA automation platform for turning project documents and recorded application context into reviewable test cases, Playwright scripts, execution results, review items, and Jira defects.

The core flow is:

```text
BRD/FSD/Swagger/Credentials -> Phase 2 HLS -> Phase 3 test cases
-> manual approval -> Playwright script generation -> execution
-> result classification -> review queue -> Jira/reporting
```

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js, TypeScript, Tailwind CSS |
| Backend | FastAPI, SQLAlchemy, Alembic |
| Database | PostgreSQL |
| Queue | RabbitMQ |
| Vector Store | Qdrant |
| LLM Providers | Groq, Gemini, OpenRouter, NVIDIA NIM |
| Test Execution | Playwright / Chromium |
| Ticketing | Jira Cloud |

## Repository Layout

```text
Software-QA-Testing-Framework/
|-- client/                  # Next.js frontend
|-- server/                  # FastAPI backend and agent pipeline
|   |-- app/agents/          # A3-A7 planning, context, script, classify, retry
|   |-- app/graph/           # Phase orchestration
|   |-- app/routers/         # API routes
|   |-- app/services/        # RabbitMQ, Jira, cache, cleanup, execution services
|   |-- migrations/          # Alembic migrations
|   |-- tests/generated/     # Runtime Playwright specs (gitignored)
|-- documents/               # Project docs/examples if intentionally tracked
|-- docker-compose.yml       # Local PostgreSQL, RabbitMQ, server, worker, client
|-- README.md
```

## Prerequisites

- Node.js 20+
- Python 3.11+
- PostgreSQL 14+ or Docker
- Docker Desktop for RabbitMQ/PostgreSQL containers
- `uv` for Python dependency management

## Quick Start

### 1. Start infrastructure

```powershell
docker compose up -d postgres rabbitmq
```

RabbitMQ management UI runs at `http://localhost:15672` with the local default `guest` / `guest`.

### 2. Backend

```powershell
cd server
uv venv
.venv\Scripts\activate
uv pip install -r requirements.txt
playwright install chromium
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Edit `server/.env` before starting the backend. At minimum configure:

```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/sqat_db
JWT_SECRET_KEY=change_this_to_a_long_random_secret_at_least_32_chars
QDRANT_URL=https://your-qdrant-cluster:6333
QDRANT_API_KEY=your_qdrant_api_key_here
GROQ_API_KEY=your_groq_api_key_here
```

If you use the Docker PostgreSQL service from this repo, the local development database URL is:

```env
DATABASE_URL=postgresql://sqat:sqat_password@localhost:5432/sqat
```

### 3. Frontend

```powershell
cd client
npm install
copy .env.example .env
npm run dev
```

Open `http://localhost:3000`.

## Docker Compose

For a containerized local stack:

```powershell
docker compose up --build
```

Provide real secrets through a local root `.env` file or `docker-compose.override.yml`. Do not commit either file.

Important Docker variables:

```env
PUBLIC_API_URL=http://localhost:8000
GROQ_API_KEY=your_groq_api_key_here
GEMINI_API_KEY=
JWT_SECRET_KEY=change_this_to_a_long_random_secret_at_least_32_chars
BASE_URL=http://localhost:3000
```

## Phase Pipeline

### Phase 1 - Project setup and ingestion

Uploads documents such as BRD, FSD, WBS, assumptions, credentials, and Swagger/OpenAPI files. Extracted text and metadata are stored for retrieval.

### Phase 2 - High-level scenarios

Generates and reviews high-level scenarios from ingested documents. Recorded browser/application context should include stable selectors, accessible names, routes, screenshots, and DOM snapshots so Phase 3 can ground automation safely.

### Phase 3 - Test generation and execution

- A3 creates QA test cases from approved HLS plus retrieved document/app context.
- A4 builds DOM and recorded-action context for each approved test case.
- A5 generates grounded Playwright specs.
- Workers execute approved/generated specs through RabbitMQ.
- A6 classifies results as pass, script error, app error, assertion review, auth error, infra error, or human review.
- A7 retries only repairable automation failures.
- Review Queue and Final Report expose failure reasons, evidence, and Jira status.

## Environment Files

Tracked templates:

- `client/.env.example`
- `server/.env.example`

Ignored real local files:

- `.env`
- `client/.env`
- `server/.env`
- any `.env.*` file except `.env.example`

Never commit API keys, Jira tokens, user credentials, auth storage state, uploaded documents, generated scripts, traces, or test results.

## Useful Commands

### Backend checks

```powershell
cd server
uv run pytest
alembic current
alembic upgrade head
```

### Frontend checks

```powershell
cd client
npm run lint
npm run build
```

### Phase 3 worker

For external worker mode:

```powershell
cd server
python -m app.services.phase3_worker
```

For visible local demo execution, set:

```env
PHASE3_EMBEDDED_WORKERS=true
PLAYWRIGHT_HEADED=true
PLAYWRIGHT_SLOW_MO_MS=3000
CHROMIUM_WORKERS=1
```

For CI or production-like execution, prefer:

```env
PLAYWRIGHT_HEADED=false
PLAYWRIGHT_SLOW_MO_MS=0
CHROMIUM_WORKERS=3
```

## Generated Artifacts

These are intentionally gitignored:

- `server/uploads/`
- `server/tests/generated/`
- `server/test-results/`
- `server/test_docs/`
- `server/models/`
- Playwright traces, videos, screenshots, and auth state

Do not use `git add -f` for these unless you are intentionally adding a tiny sanitized fixture.

## Database Migrations

Run migrations from the `server/` directory:

```powershell
alembic upgrade head
```

For schema changes:

```powershell
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

Commit model changes and migration files together.

## Security Notes Before Pushing

- Confirm no real `.env` files are staged.
- Confirm no generated scripts, traces, videos, uploaded PDFs, credentials CSVs, or auth state files are staged.
- Rotate any API key that was ever committed, pasted into logs, or shared externally.
- Keep Docker Compose defaults as local-only development credentials; override them for real deployments.

## Git Workflow

```powershell
git status --short
git diff
git add <specific files>
git commit -m "chore: prepare repository for github"
git remote add origin <github-url>
git push -u origin <branch-name>
```

Avoid `git add .` until you have reviewed the dirty worktree, because this project creates many runtime artifacts during local QA runs.
