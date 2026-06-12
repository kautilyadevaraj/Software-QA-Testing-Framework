# SQAT — Software QA Testing Framework

SQAT is an AI-assisted end-to-end QA automation platform. It turns your project documents, live application recordings, and AI agents into reviewable test cases, executable Playwright scripts, classified results, and Jira defects — with a human approval gate at every critical step.

## End-to-End Flow

```text
Upload Docs/URLs/Credentials (Phase 1)
        ↓
AI generates High-Level Scenarios + QA Engineer records app (Phase 2)
        ↓
A3 decomposes HLS → TC document (Phase 3 Planning)
        ↓
QA Engineer reviews & approves test cases (Human Gate)
        ↓
A4 builds context · A5 generates Playwright scripts (Phase 3 Execution)
        ↓
Chromium workers execute scripts in parallel
        ↓
A6 classifies results (PASS / APP_ERROR / SCRIPT_ERROR / HUMAN_REVIEW)
        ↓
A7 auto-repairs broken scripts and re-queues
        ↓
Review queue → Raise Jira · Edit & Re-run (Human Gate)
        ↓
Final execution report + X-Ray CSV export (Phase 4)
```

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 16, TypeScript, Tailwind CSS 4 |
| Backend | FastAPI, SQLAlchemy, Alembic |
| Database | PostgreSQL 14+ |
| Message Queue | RabbitMQ |
| Vector Store | Qdrant |
| LLM Providers | Anthropic Claude (primary), Groq (fallback) |
| Test Execution | Playwright / Chromium |
| Ticketing | Jira Cloud REST API v3 |
| Dependency Manager | `uv` (Python), `npm` (Node) |

## Repository Layout

```text
Software-QA-Testing-Framework/
├── client/                         # Next.js 16 frontend
│   ├── app/                        # Next.js App Router pages
│   │   ├── login/ signup/          # Auth pages
│   │   └── projects/               # Project list + [projectId] detail
│   ├── components/                 # Phase 2 & 3 panel components
│   │   ├── scenario-qa-panel.tsx   # Phase 2 HLS generation + recording UI
│   │   ├── phase3-panel.tsx        # Phase 3 plan/approve/execute UI
│   │   ├── phase3-review-queue.tsx # Live SSE review queue
│   │   └── raise-ticket-modal.tsx  # Jira issue creation modal
│   └── lib/                        # API helpers, hooks
│
├── server/                         # FastAPI backend + agent pipeline
│   ├── app/
│   │   ├── agents/                 # AI agent pipeline
│   │   │   ├── agent1_brd.py       # A1: BRD chunking and embedding
│   │   │   ├── agent2_swagger.py   # A2: Swagger/OpenAPI ingestion
│   │   │   ├── agent3_dedup.py     # A3: Scenario deduplication
│   │   │   ├── agent3_planner.py   # A3: HLS → test cases + X-Ray metadata
│   │   │   ├── agent4_context_builder.py # A4: DOM + recording context assembly
│   │   │   ├── agent5_script_generator.py # A5: Playwright .spec.ts generation
│   │   │   ├── agent6_classifier.py       # A6: Result classification (rule-based)
│   │   │   ├── agent7_retry.py            # A7: LLM script repair on SCRIPT_ERROR
│   │   │   ├── scenario_common.py  # Shared utilities for scenario agents
│   │   │   └── xray_csv_generator.py # X-Ray CSV export formatter
│   │   ├── core/                   # Settings, config
│   │   ├── db/                     # SQLAlchemy session, base
│   │   ├── graph/                  # LangGraph orchestration
│   │   │   ├── phase3_graph.py     # Phase 3 planning + execution graph
│   │   │   └── scenario_graph.py   # Phase 2 scenario generation graph
│   │   ├── models/                 # SQLAlchemy ORM models
│   │   ├── routers/                # FastAPI route handlers
│   │   │   ├── auth.py             # JWT auth (login, refresh, signup)
│   │   │   ├── files.py            # Document upload
│   │   │   ├── members.py          # Project members
│   │   │   ├── projects.py         # Project CRUD + credentials
│   │   │   ├── scenarios.py        # HLS management + recording control
│   │   │   ├── recorder.py         # Recorder daemon API (token auth)
│   │   │   └── phase3.py           # Phase 3 plan/approve/execute + SSE
│   │   ├── schemas/                # Pydantic request/response models
│   │   ├── services/               # Business logic services
│   │   │   ├── mcp_server.py       # MCP tool layer (DB, DOM, Script, Queue)
│   │   │   ├── phase3_worker.py    # RabbitMQ consumer + Playwright executor
│   │   │   ├── recorder_service.py # Recording session management
│   │   │   ├── jira_service.py     # Jira Cloud REST integration
│   │   │   ├── credential_service.py # Encrypted credential management
│   │   │   ├── auth_state_service.py # Playwright storageState manager
│   │   │   └── ...                 # Cleanup, cache, queue, progress services
│   │   └── utils/                  # LLM client, rate limiter, helpers
│   ├── migrations/                 # Alembic migration files
│   ├── recorder.py                 # Local recorder daemon (tester runs this)
│   ├── recorder_template.py        # Template served to tester via API
│   └── tests/
│       └── generated/              # Runtime .spec.ts files (gitignored)
│
├── documents/                      # BRD, HLD/LLD, Architecture docs
├── docker-compose.yml              # Full containerized stack
└── README.md
```

---

## Local Development Setup

The recommended setup for local development:

- **PostgreSQL** — installed and running locally
- **RabbitMQ** — running via Docker (single container)
- **Backend and Frontend** — running locally from source

Use the full `docker-compose.yml` stack only when you need a completely containerized environment.

## Prerequisites

| Tool | Version |
| --- | --- |
| Node.js | 20+ |
| Python | 3.11+ |
| PostgreSQL | 14+ (installed locally) |
| Docker Desktop | for RabbitMQ |
| `uv` | latest (Python dependency manager) |

Install `uv`:

```powershell
pip install uv
```

---

## 1. Local PostgreSQL

Create a database named `sqat_db`:

```powershell
psql -U postgres
```

```sql
CREATE DATABASE sqat_db;
```

Use this connection string in `server/.env`:

```env
DATABASE_URL=postgresql://postgres:<YOUR_PASSWORD>@localhost:5432/sqat_db
```

---

## 2. RabbitMQ via Docker

```powershell
docker compose up -d rabbitmq
```

RabbitMQ management UI: `http://localhost:15672` (default: `guest / guest`)

Backend settings:

```env
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
RABBITMQ_QUEUE=phase3_test_jobs
```

> **Worker mode:** For local dev, set `PHASE3_EMBEDDED_WORKERS=true` in `server/.env`.  
> This runs the Phase 3 execution worker inside the uvicorn process — no extra terminal needed.  
> Set `PHASE3_EMBEDDED_WORKERS=false` if you want to run the worker separately:
> ```powershell
> cd server
> python -m app.services.phase3_worker
> ```

---

## 3. Backend

```powershell
cd server
uv venv
.venv\Scripts\activate
uv pip install -r requirements.txt
playwright install chromium
copy .env.example .env
# Edit .env with your values (see Environment Variables section)
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Swagger docs available at: `http://localhost:8000/docs`

### Minimum `.env` for Local Dev

```env
DATABASE_URL=postgresql://postgres:<YOUR_PASSWORD>@localhost:5432/sqat_db
JWT_SECRET_KEY=change_this_to_a_long_random_secret_at_least_32_chars
CREDENTIAL_ENCRYPTION_KEY=<32-byte-base64-encoded-key>

# LLM — Anthropic Claude is the primary provider for Phase 3
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_anthropic_api_key_here
ANTHROPIC_MODEL=claude-sonnet-4-6

# Groq — optional fallback
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile

# Qdrant vector store (cloud or local)
QDRANT_URL=https://your-cluster-id.region.aws.cloud.qdrant.io:6333
QDRANT_API_KEY=your_qdrant_api_key_here

RABBITMQ_URL=amqp://guest:guest@localhost:5672/
PUBLIC_API_URL=http://localhost:8000

# Worker mode (true = embedded in uvicorn, recommended for local dev)
PHASE3_EMBEDDED_WORKERS=true
```

### Playwright Execution Tuning

For visible local demo runs:

```env
PLAYWRIGHT_HEADED=true
PLAYWRIGHT_SLOW_MO_MS=3000
CHROMIUM_WORKERS=1
```

For CI / production-like headless runs:

```env
PLAYWRIGHT_HEADED=false
PLAYWRIGHT_SLOW_MO_MS=0
CHROMIUM_WORKERS=3
```

---

## 4. Frontend

```powershell
cd client
npm install
copy .env.example .env
npm run dev
```

Open: `http://localhost:3000`

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

---

## Optional: Full Docker Stack

```powershell
docker compose up --build
```

This starts PostgreSQL, RabbitMQ, the FastAPI server, a Phase 3 worker container, and the Next.js client — all wired together.

> For normal local development, prefer local PostgreSQL plus Docker RabbitMQ only.

---

## Phase Pipeline

### Phase 1 — Project Setup and Document Ingestion

**Goal:** Upload all project knowledge so agents can ground their outputs.

1. Create a project in the UI and invite team members.
2. Upload documents: BRD, FSD, WBS, assumptions, Swagger/OpenAPI specs.
3. Upload a credentials CSV (roles + username/password pairs for each persona).
4. Set the target application URL.

The backend extracts text, chunks it, embeds it using `all-MiniLM-L6-v2`, and stores vectors in Qdrant for retrieval by later agents.

**Key endpoints:** `POST /api/v1/projects/{id}/files/upload`, `POST /api/v1/projects/{id}/credentials`

---

### Phase 2 — High-Level Scenario Generation and Application Recording

**Goal:** Generate High-Level Scenarios (HLS) and capture real application behavior for Phase 3 grounding.

**2a — HLS Generation:**

1. Click "Generate Scenarios" in the UI.
2. Agents A1 (BRD context), A2 (Swagger), and A3 (deduplication) run via the LangGraph scenario graph.
3. Review the generated scenarios, edit them inline, add manual scenarios, and mark each one as **Completed**.

**2b — Application Recording (critical for Phase 3 quality):**

1. Get the one-time recorder setup command from the UI (`GET /recording-setup`).
2. Run the command locally — this downloads and starts the Python recorder daemon.
3. Click **"Launch Recording"** next to a scenario in the UI.
4. The daemon opens Chromium and captures every interaction: clicks, fills, navigation, selectors, DOM snapshots, screenshots.
5. Click **"Stop Recording"** in the UI when done.
6. The daemon uploads structured step + DOM data to the backend.

The recorder captures: stable selectors, accessible names, element roles, URL transitions, DOM snapshots per page, and route variants — all stored in PostgreSQL and used by A4/A5 in Phase 3.

> **Recording quality matters.** Fresh recordings with good coverage of the application's pages produce the best Playwright scripts. Re-record if the app changes significantly.

**Key endpoints:** `GET /api/v1/projects/{id}/scenarios/recording-setup`, `POST /api/v1/recorder/{id}/sessions`, `POST /api/v1/recorder/{id}/sessions/{sid}/steps`

---

### Phase 3 — Test Case Planning, Script Generation, and Execution

Phase 3 is a three-step workflow with a human gate between planning and execution.

#### Step 1 — Plan (`POST /api/v1/projects/{id}/phase3/plan`)

- Agent A3 reads each approved HLS and its recording evidence.
- Decomposes each HLS into 1–4 concrete, executable QA test cases.
- Each test case includes: title, steps, acceptance criteria, auth mode, target page, TC number (for Jira traceability).
- A3 also enriches test cases with X-Ray metadata (labels, priority, pre-conditions) from BRD/FSD document context.
- Generates a downloadable X-Ray CSV for Jira import.
- Result: TC document appears in the UI as an approval accordion.

#### Step 2 — Review and Approve (Human Gate)

- Review each test case: steps, acceptance criteria, target page.
- Inline edit any test case (resets to `NEEDS_EDIT` status).
- Per-test case actions: **Approve**, **Exclude**, or leave as **Needs Edit**.
- Bulk **Approve All** to approve everything at once.
- Download the X-Ray CSV for Jira import.
- All non-excluded test cases must be `APPROVED` before execution can start.

#### Step 3 — Execute (`POST /api/v1/projects/{id}/phase3/execute`)

The backend runs a preflight check (env vars, credentials) and then:

1. **A4 — Context Builder:** Assembles full execution context per test case: DB test case data, DOM snapshot for target page, Phase-2 recorded steps, selectors, route transitions, variant elements, route map.

2. **A5 — Script Generator:** Takes A4 context and generates a Playwright TypeScript `.spec.ts` file. Every script includes:
   - `NetworkMonitor` — captures all 4xx/5xx API responses
   - `smartFind()` — selector resolution with fallbacks
   - `navigateWithFallback()` — retry navigation helper
   - `env()` — safe env var resolver (fail-fast on missing vars)
   - Screenshot capture at PASS outcome
   - Network evidence attachment for A6 classification

3. **RabbitMQ Worker** — Enqueues test jobs and runs them in parallel Chromium instances.

4. **A6 — Result Classifier (rule-based, no LLM):**

   | Classification | Condition | Action |
   | --- | --- | --- |
   | `PASS` | All assertions pass, no network errors | Saved to DB, screenshot attached |
   | `APP_ERROR` | 4xx/5xx in network logs (unexpected) | → Review Queue as BUG |
   | `SCRIPT_ERROR` | Playwright fail, locator/selector issue | → A7 for repair |
   | `HUMAN_REVIEW` | Auth error, infra error, assertion mismatch | → Review Queue as TASK |

5. **A7 — Retry Agent (LLM):** Repairs broken Playwright scripts using the error log + fresh DOM snapshot. Up to 3 repair attempts. On exhaustion, routes to HUMAN_REVIEW. Supports both single-test and grouped (describe.serial) scripts.

**Live monitoring:** The Phase 3 panel shows live counters via `GET /phase3/execution-state` and SSE via `GET /phase3/review-queue/stream`.

#### Review Queue

Failed and human-review tests appear in the Review Queue panel:
- **Raise Jira** — creates a Jira Bug or Task issue prefixed with `[TC-XXX]`
- **Edit Script & Re-run** — opens the CodeMirror script editor, save and re-enqueue

#### Final Report

`GET /api/v1/projects/{id}/phase3/execution-report.csv` — a CSV with all test case results, statuses, Jira ticket refs, and retry counts.

---

### Phase 4 — Automated Report Generation

After execution completes, all results are persisted in PostgreSQL:

- `test_cases` — TC number, title, steps, target page, script path
- `test_results` — status, retries, Jira ticket, trace path, screenshot path
- `network_logs` — per-request failure log
- `retry_history` — LLM fix applied per attempt
- `test_runs` — total, passed, failed, skipped, duration
- `review_queue` — review type, evidence, Jira ref, status

Download artifacts:
- `GET /phase3/tc-document` — X-Ray CSV
- `GET /phase3/execution-report.csv` — Final execution report CSV
- `GET /phase3/screenshot/{test_id}` — Pass screenshot
- `GET /phase3/trace/{test_id}` — Playwright trace zip (for failures)

---

## Environment Variables Reference

See [`server/.env.example`](server/.env.example) for the full annotated list. Key sections:

| Section | Variables |
| --- | --- |
| Database | `DATABASE_URL` |
| Auth | `JWT_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES` |
| LLM | `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY` |
| Qdrant | `QDRANT_URL`, `QDRANT_API_KEY` |
| RabbitMQ | `RABBITMQ_URL`, `RABBITMQ_QUEUE` |
| Playwright | `PLAYWRIGHT_HEADED`, `PLAYWRIGHT_SLOW_MO_MS`, `CHROMIUM_WORKERS` |
| Jira | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_LEAD_ACCOUNT_ID` |
| Workers | `PHASE3_EMBEDDED_WORKERS`, `PHASE3_MAX_ATTEMPTS` |
| Test Data | `PHASE3_TEST_DATA_NAME`, `PHASE3_TEST_DATA_EMAIL`, etc. |
| Cleanup | `SCRIPT_RETENTION_HOURS`, `AUTH_STATE_RETENTION_HOURS` |

---

## Useful Commands

### Backend

```powershell
cd server

# Run tests
uv run pytest

# Database migrations
alembic current           # show current migration
alembic upgrade head      # apply all pending migrations
alembic revision --autogenerate -m "describe change"  # create migration

# External Phase 3 worker (if PHASE3_EMBEDDED_WORKERS=false)
python -m app.services.phase3_worker

# Generate encryption key for CREDENTIAL_ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Generate JWT secret
python -c "import secrets; print(secrets.token_hex(32))"
```

### Frontend

```powershell
cd client
npm run lint
npm run build
```

---

## Database Migrations

Run from `server/`:

```powershell
alembic upgrade head
```

For schema changes (after modifying SQLAlchemy models):

```powershell
alembic revision --autogenerate -m "describe your change"
alembic upgrade head
```

Always commit model changes and the generated migration file together.

---

## Generated Artifacts (gitignored)

These directories are created at runtime and intentionally excluded from git:

- `server/uploads/` — uploaded document files
- `server/tests/generated/` — Playwright `.spec.ts` files
- `server/tests/.auth/` — Playwright auth state JSON files
- `server/test-results/` — Playwright trace zips, videos, screenshots
- `server/recordings/` — Phase 2 DOM snapshots and HTML captures
- `server/state.json` — real-time execution state store

Do not use `git add -f` for these unless adding a tiny sanitized fixture.

---

## Security Notes

- Never commit `.env` files or API keys.
- Never commit `server/tests/.auth/` (contains real session cookies).
- Rotate any API key that was ever committed, pasted into logs, or shared externally.
- `CREDENTIAL_ENCRYPTION_KEY` must be a valid 32-byte Fernet key — losing it means losing access to all stored test credentials.
- Docker Compose uses default credentials suitable only for local development. Override all secrets for real deployments.

Before every push:

```powershell
git status --short        # confirm no .env files staged
git diff --stat           # confirm no secrets or runtime artifacts staged
```

---

## Git Workflow

This project creates many runtime artifacts during local QA runs. Avoid `git add .` until you have reviewed the dirty worktree:

```powershell
git status --short
git diff --stat
git add README.md client/README.md server/README.md
git add server/app server/migrations server/alembic.ini
git add client/app client/components client/lib
git commit -m "chore: your message here"
```
