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
|-- docker-compose.yml       # Optional full local Docker stack
|-- README.md
```

## Local Development Setup

The recommended handoff setup is:

- PostgreSQL installed and running locally.
- RabbitMQ running through Docker.
- Backend and frontend running locally from the repo.

Use the full `docker-compose.yml` stack only when you intentionally want containerized PostgreSQL, backend, worker, and frontend.

## Prerequisites

- Node.js 20+
- Python 3.11+
- PostgreSQL 14+ installed locally
- Docker Desktop for RabbitMQ
- `uv` for Python dependency management

## 1. Local PostgreSQL

Create a local database named `sqat_db`.

Using `psql`:

```powershell
psql -U postgres
```

```sql
CREATE DATABASE sqat_db;
```

Your backend `server/.env` should use:

```env
DATABASE_URL=postgresql://postgres:<YOUR_PASSWORD>@localhost:5432/sqat_db
```

If your local PostgreSQL username, password, host, port, or database name is different, update the connection string accordingly.

## 2. RabbitMQ Through Docker

Start only RabbitMQ for normal local development:

```powershell
docker compose up -d rabbitmq
```

RabbitMQ management UI:

```text
http://localhost:15672
```

Default local credentials:

```text
guest / guest
```

Backend RabbitMQ settings:

```env
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
RABBITMQ_QUEUE=phase3_test_jobs
```

If you already have another RabbitMQ container using ports `5672` or `15672`, stop the old container or update the compose port mapping before starting this one.

## 3. Backend

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

At minimum, configure these values in `server/.env`:

```env
DATABASE_URL=postgresql://postgres:<YOUR_PASSWORD>@localhost:5432/sqat_db
JWT_SECRET_KEY=change_this_to_a_long_random_secret_at_least_32_chars
QDRANT_URL=https://your-qdrant-cluster:6333
QDRANT_API_KEY=your_qdrant_api_key_here
GROQ_API_KEY=your_groq_api_key_here
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
PUBLIC_API_URL=http://localhost:8000
```

For visible local demo execution:

```env
PHASE3_EMBEDDED_WORKERS=true
PLAYWRIGHT_HEADED=true
PLAYWRIGHT_SLOW_MO_MS=3000
CHROMIUM_WORKERS=1
```

For CI or production-like execution:

```env
PLAYWRIGHT_HEADED=false
PLAYWRIGHT_SLOW_MO_MS=0
CHROMIUM_WORKERS=3
```

## 4. Frontend

```powershell
cd client
npm install
copy .env.example .env
npm run dev
```

Open:

```text
http://localhost:3000
```

Client API setting:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

## Optional Full Docker Stack

The repo keeps a full `docker-compose.yml` for local containerized runs:

```powershell
docker compose up --build
```

For the Dev 2 handoff workflow, prefer local PostgreSQL plus:

```powershell
docker compose up -d rabbitmq
```

Do not commit local root `.env` files or `docker-compose.override.yml`.

## Phase Pipeline

### Phase 1 - Project Setup And Ingestion

Uploads documents such as BRD, FSD, WBS, assumptions, credentials, and Swagger/OpenAPI files. Extracted text and metadata are stored for retrieval.

### Phase 2 - High-Level Scenarios And Recording Context

Generates and reviews high-level scenarios from ingested documents. Recorded browser/application context should include stable selectors, accessible names, routes, screenshots, route variants, and DOM snapshots so Phase 3 can ground automation safely.

After recorder changes, restart the backend and create fresh recordings before validating Phase 3 behavior. Existing DB recordings can still contain old selector or navigation noise.

### Phase 3 - Test Generation And Execution

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

Backend checks:

```powershell
cd server
uv run pytest
alembic current
alembic upgrade head
```

Frontend checks:

```powershell
cd client
npm run lint
npm run build
```

External Phase 3 worker mode:

```powershell
cd server
python -m app.services.phase3_worker
```

## Generated Artifacts

These are intentionally gitignored:

- `server/uploads/`
- `server/tests/generated/`
- `server/tests/.auth/`
- `server/test-results/`
- `server/test_docs/`
- `server/models/`
- Playwright traces, videos, screenshots, reports, and auth state

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
- Confirm `server/tests/.auth/` is not staged.
- Rotate any API key that was ever committed, pasted into logs, or shared externally.
- Keep Docker Compose defaults as local-only development credentials; override them for real deployments.

## Git Workflow

Avoid `git add .` until you have reviewed the dirty worktree, because this project creates many runtime artifacts during local QA runs.

Recommended handoff staging pattern:

```powershell
git status --short
git diff --stat
git add README.md .gitignore client/README.md server/tsconfig.json
git add server/app server/tests client/components client/lib
git status --short
git commit -m "chore: prepare repository for github handoff"
git remote add origin <github-url>
git push -u origin <branch-name>
```
