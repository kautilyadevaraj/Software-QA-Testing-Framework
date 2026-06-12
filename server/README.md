# SQAT Server — FastAPI Backend

The FastAPI backend for the SQAT platform. Handles document ingestion, scenario generation, application recording, Phase 3 test case planning and execution, result classification, Jira integration, and all persistent storage.

## Prerequisites

| Tool | Version |
| --- | --- |
| Python | 3.11+ |
| PostgreSQL | 14+ (local install) |
| Docker Desktop | for RabbitMQ |
| `uv` | latest |
| Playwright Chromium | installed via `playwright install chromium` |

---

## Setup

```powershell
cd server
uv venv
.venv\Scripts\activate
uv pip install -r requirements.txt
playwright install chromium
copy .env.example .env
# Edit .env with your values
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

API docs: `http://localhost:8000/docs`  
Health check: `http://localhost:8000/health`

---

## Environment Configuration

Full list in [`.env.example`](.env.example). Minimum required for local dev:

```env
# Database
DATABASE_URL=postgresql://postgres:<YOUR_PASSWORD>@localhost:5432/sqat_db

# Auth
JWT_SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
CREDENTIAL_ENCRYPTION_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# LLM — Anthropic is the primary provider for Phase 3
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_key_here
ANTHROPIC_MODEL=claude-sonnet-4-6

# Groq — Phase 2 scenario generation + A3 fallback
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.3-70b-versatile

# Qdrant vector store
QDRANT_URL=https://your-cluster.region.cloud.qdrant.io:6333
QDRANT_API_KEY=your_key_here

# RabbitMQ
RABBITMQ_URL=amqp://guest:guest@localhost:5672/

# Worker mode (true = embedded in uvicorn)
PHASE3_EMBEDDED_WORKERS=true

# Public backend URL (used by the recorder daemon)
PUBLIC_API_URL=http://localhost:8000
```

### Optional: Jira Integration

```env
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@yourcompany.com
JIRA_API_TOKEN=your_jira_api_token
JIRA_LEAD_ACCOUNT_ID=712020:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### Playwright Tuning

| Use Case | Settings |
| --- | --- |
| Local demo (visible) | `PLAYWRIGHT_HEADED=true`, `PLAYWRIGHT_SLOW_MO_MS=3000`, `CHROMIUM_WORKERS=1` |
| CI / production | `PLAYWRIGHT_HEADED=false`, `PLAYWRIGHT_SLOW_MO_MS=0`, `CHROMIUM_WORKERS=3` |

---

## Application Structure

```text
server/
├── app/
│   ├── main.py                     # FastAPI app setup, CORS, middleware, routers
│   ├── agents/                     # AI agent pipeline
│   │   ├── agent1_brd.py           # Document chunking, embedding, Qdrant upsert
│   │   ├── agent2_swagger.py       # Swagger/OpenAPI spec parsing
│   │   ├── agent3_dedup.py         # Scenario deduplication via LLM
│   │   ├── agent3_planner.py       # HLS → test cases + X-Ray metadata enrichment
│   │   ├── agent4_context_builder.py # DOM + recording context assembly (no LLM)
│   │   ├── agent5_script_generator.py # Playwright .spec.ts generation
│   │   ├── agent6_classifier.py    # Result classification (rule-based, no LLM)
│   │   ├── agent7_retry.py         # LLM-based script repair on SCRIPT_ERROR
│   │   ├── scenario_common.py      # Shared batching/chunking utilities
│   │   └── xray_csv_generator.py   # X-Ray CSV renderer
│   │
│   ├── core/
│   │   └── config.py               # Pydantic settings (reads .env)
│   │
│   ├── db/
│   │   ├── base.py                 # SQLAlchemy declarative base
│   │   └── session.py              # Engine, SessionLocal, get_db dep
│   │
│   ├── dependencies/
│   │   └── auth.py                 # JWT cookie validation dep
│   │
│   ├── graph/
│   │   ├── phase3_graph.py         # LangGraph Phase 3 planning + execution graph
│   │   └── scenario_graph.py       # LangGraph Phase 2 scenario generation graph
│   │
│   ├── models/                     # SQLAlchemy ORM models
│   │   ├── user.py                 # User accounts
│   │   ├── project.py              # Projects, HighLevelScenarios, CredentialProfiles, Jira config
│   │   ├── scenario.py             # RecordingSession, ScenarioStep, RouteVariant, DiscoveredRoute
│   │   └── phase3.py               # TestCase, TestResult, TestRun, ReviewQueueItem, RetryHistory, AuthState
│   │
│   ├── routers/                    # FastAPI route handlers
│   │   ├── auth.py                 # POST /auth/login, /auth/signup, /auth/refresh, /auth/logout
│   │   ├── files.py                # POST /projects/{id}/files/upload
│   │   ├── members.py              # Project member management
│   │   ├── projects.py             # Project CRUD, credentials, Jira project setup
│   │   ├── scenarios.py            # HLS CRUD, recording control, scenario approval
│   │   ├── recorder.py             # Recorder daemon API (token auth, not JWT)
│   │   └── phase3.py               # Phase 3 plan/approve/execute/results/Jira
│   │
│   ├── schemas/                    # Pydantic request/response models
│   │
│   ├── services/
│   │   ├── mcp_server.py           # MCP tool layer: DB tools, DOM tools, Script tools, Queue tools
│   │   ├── phase3_worker.py        # RabbitMQ consumer + Playwright subprocess runner
│   │   ├── recorder_service.py     # Recording session lifecycle, DOM/step ingestion
│   │   ├── auth_state_service.py   # Playwright storageState (session cookie) manager
│   │   ├── auth_state_cleanup_scheduler.py # APScheduler: sweeps expired auth states
│   │   ├── credential_service.py   # Fernet-encrypted credential storage/retrieval
│   │   ├── jira_service.py         # Jira Cloud REST API v3 client
│   │   ├── pdf_extractor_service.py # PDF / DOCX / XLSX text extraction
│   │   ├── phase3_preflight.py     # Pre-execution env var + credential checks
│   │   ├── phase3_progress.py      # Execution progress counters
│   │   ├── queue_topology.py       # RabbitMQ queue + DLX setup
│   │   ├── execution_state_service.py # Phase3ExecutionState read/write
│   │   ├── state_store.py          # state.json read/write (real-time execution state)
│   │   ├── script_cache_service.py # Script content caching
│   │   ├── script_cleanup_service.py # Scheduled deletion of old generated scripts
│   │   ├── artifact_paths.py       # Deterministic file paths for generated artifacts
│   │   ├── artifact_registry.py    # Phase3Artifact DB registry
│   │   ├── hls_group_service.py    # Phase3HlsGroup management (grouped serial specs)
│   │   └── file_service.py         # Uploaded file management
│   │
│   └── utils/
│       ├── llm.py                  # LLM provider chain (Anthropic → Groq fallback)
│       └── rate_limiter.py         # SlowAPI rate limiter
│
├── migrations/                     # Alembic migration files
├── recorder.py                     # Local recorder daemon (dev reference copy)
├── recorder_template.py            # Template served to testers via GET /recorder/{id}/script
├── tests/
│   └── generated/                  # Runtime .spec.ts files (gitignored)
├── alembic.ini                     # Alembic configuration
├── pyproject.toml                  # Python project metadata
├── requirements.txt                # Pinned dependencies
└── Dockerfile                      # Production container image
```

---

## API Endpoints

All endpoints are mounted under `/api/v1`. JWT cookie auth is required unless noted.

### Auth

| Method | Path | Description |
| --- | --- | --- |
| POST | `/auth/signup` | Create new account |
| POST | `/auth/login` | Login (sets JWT cookies) |
| POST | `/auth/refresh` | Refresh access token |
| POST | `/auth/logout` | Clear JWT cookies |

### Projects

| Method | Path | Description |
| --- | --- | --- |
| GET | `/projects` | List user's projects |
| POST | `/projects` | Create project |
| GET | `/projects/{id}` | Get project details |
| PATCH | `/projects/{id}` | Update project |
| DELETE | `/projects/{id}` | Delete project |
| POST | `/projects/{id}/credentials` | Upload credentials CSV |
| POST | `/projects/{id}/jira-config` | Configure Jira project |

### Files

| Method | Path | Description |
| --- | --- | --- |
| POST | `/projects/{id}/files/upload` | Upload BRD/FSD/Swagger etc. |
| GET | `/projects/{id}/files` | List uploaded files |
| DELETE | `/projects/{id}/files/{file_id}` | Delete file |

### Scenarios (Phase 2)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/projects/{id}/scenarios/generate` | AI-generate HLS from documents |
| POST | `/projects/{id}/scenarios/approve` | Save approved HLS batch |
| GET | `/projects/{id}/scenarios` | List all HLS |
| POST | `/projects/{id}/scenarios` | Create manual scenario |
| PATCH | `/projects/{id}/scenarios/{sid}` | Update scenario (title/status) |
| DELETE | `/projects/{id}/scenarios/{sid}` | Delete scenario |
| GET | `/projects/{id}/scenarios/recording-setup` | Get recorder daemon command |
| POST | `/projects/{id}/scenarios/{sid}/trigger` | Launch recording in daemon |
| GET | `/projects/{id}/scenarios/{sid}/recording-status` | Poll recording status |
| POST | `/projects/{id}/scenarios/{sid}/stop-recording` | Stop active recording |
| DELETE | `/projects/{id}/scenarios/{sid}/recording` | Clear recording data |

### Recorder Daemon (token auth, not JWT)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/recorder/{id}/script` | Download recorder script |
| GET | `/recorder/{id}/pulse` | Poll for pending scenario launch |
| POST | `/recorder/{id}/sessions` | Create recording session |
| PUT | `/recorder/{id}/sessions/{sid}/start` | Mark session in_progress |
| PUT | `/recorder/{id}/sessions/{sid}/complete` | Mark session completed |
| POST | `/recorder/{id}/sessions/{sid}/steps` | Append a recorded step |
| POST | `/recorder/{id}/routes` | Upsert discovered route + DOM snapshot |

### Phase 3

| Method | Path | Description |
| --- | --- | --- |
| POST | `/projects/{id}/phase3/plan` | Step 1: Run A3, generate TC document |
| GET | `/projects/{id}/phase3/tc-document` | Download X-Ray CSV |
| GET | `/projects/{id}/phase3/tc-document/json` | TC list as JSON (approval UI) |
| PATCH | `/projects/{id}/phase3/approve-all` | Bulk approve all pending TCs |
| PATCH | `/projects/{id}/phase3/test-cases/{tc_id}/approval` | Per-TC approval |
| PATCH | `/projects/{id}/phase3/test-cases/{tc_id}/content` | Inline edit TC |
| POST | `/projects/{id}/phase3/execute` | Step 3: Run A4+A5+workers |
| GET | `/projects/{id}/phase3/run-status` | Latest run counters |
| GET | `/projects/{id}/phase3/execution-state` | Live per-test status |
| GET | `/projects/{id}/phase3/execution-report.csv` | Final execution report |
| GET | `/projects/{id}/phase3/review-queue` | List review items |
| GET | `/projects/{id}/phase3/review-queue/stream` | SSE live review items |
| PATCH | `/projects/{id}/phase3/review-queue/{item_id}` | Mark reviewed / add Jira ref |
| POST | `/projects/{id}/phase3/review-queue/{item_id}/rerun` | Edit script + re-enqueue |
| GET | `/projects/{id}/phase3/script/{test_id}` | Fetch generated .spec.ts |
| GET | `/projects/{id}/phase3/trace/{test_id}` | Download Playwright trace zip |
| GET | `/projects/{id}/phase3/screenshot/{test_id}` | Download PASS screenshot |
| POST | `/projects/{id}/phase3/raise-jira` | Raise Jira Bug/Task |

---

## Agent Pipeline Reference

### A1 — BRD/Document Agent (`agent1_brd.py`)

Reads uploaded BRD, FSD, WBS, and assumption documents. Chunks text and upserts embedding vectors into Qdrant for retrieval by A3 and later agents.

### A2 — Swagger Agent (`agent2_swagger.py`)

Parses uploaded Swagger/OpenAPI specs. Extracts endpoint summaries for scenario generation context.

### A3 — Dedup + Planner Agent (`agent3_dedup.py`, `agent3_planner.py`)

**Dedup:** Removes duplicate or highly similar generated scenarios.  
**Planner:** Decomposes each HLS into 1–4 executable QA test cases with:
- `tc_number` (TC-001…) for Jira RTM traceability
- `steps` — action-oriented, Playwright-executable
- `acceptance_criteria` — verifiable pass conditions
- `auth_mode` — `authenticated` | `login_flow` | `anonymous`
- `target_page` — grounded in recording evidence
- `assertion_evidence` — extracted observable UI outcomes
- X-Ray metadata (labels, priority, requirement, pre-conditions) from BRD context

### A4 — Context Builder (`agent4_context_builder.py`)

Deterministic (no LLM). Assembles full context per test case:
- DB test case (steps, criteria, target page, auth mode, credentials)
- DOM snapshot for target page + all visited pages from recording
- Phase-2 recorded steps (actions, selectors, values, URL transitions)
- Recorded variant elements (real interactive elements from DOM)
- Route map (path → navigation element)
- Per-project few-shot Playwright example (from actual Phase-2 recordings)
- Test ID attribute detection (data-testid vs data-cy vs data-test etc.)

### A5 — Script Generator (`agent5_script_generator.py`)

Generates Playwright TypeScript `.spec.ts` files. Two modes:

- **Single test:** One `test()` block with `{ page }` fixture — independent execution
- **Grouped test (describe.serial):** Multiple `test()` blocks sharing `sharedPage` — for HLS scenarios where test cases must run sequentially

Every generated script includes:
- `NetworkMonitor` — captures all 4xx/5xx API failures
- `smartFind()` — selector fallback chain
- `navigateWithFallback()` — navigation retry helper
- `env()` — fail-fast env var resolver
- Assertion screenshot capture (PASS outcomes)
- Network evidence attachment for A6 classification

Post-generation fixers (deterministic patches applied to every LLM output):
- Injects `testInfo` signature for evidence attachment
- Rewrites login goto to recording-derived path
- Removes `networkidle` waits
- Injects screenshot block
- Normalises bare `waitForURL` patterns

### A6 — Classifier (`agent6_classifier.py`)

Rule-based (no LLM). Classifies each test result:

| Classification | Detection |
| --- | --- |
| `PASS` | Playwright passes + no unexpected 4xx/5xx |
| `APP_ERROR` | 4xx/5xx in network logs (non-static, unexpected) → Review Queue as BUG |
| `SCRIPT_ERROR` → A7 | Playwright fails with repairable error (locator, selector, timeout) |
| `HUMAN_REVIEW` | Auth error (401/403), infra error (ECONNREFUSED etc.), assertion mismatch, A7 exhaustion |

Negative-intent tests (invalid/validation/rejected in title) that receive expected 4xx are correctly classified as `PASS`.

### A7 — Retry Agent (`agent7_retry.py`)

LLM-based (Anthropic Claude). On `SCRIPT_ERROR`:

1. Reads the broken script + error log + fresh A4 context (DOM snapshot, recorded selectors)
2. Calls LLM to repair ONLY the failing `test()` block
3. Validates repair against grounding (no hallucinated selectors)
4. Splices repaired block back into file (grouped mode) or replaces file (single mode)
5. Re-enqueues via RabbitMQ
6. Up to `PHASE3_MAX_ATTEMPTS` (default: 3) total attempts
7. On exhaustion → `HUMAN_REVIEW` + review_queue TASK entry

---

## MCP Tool Layer (`services/mcp_server.py`)

Internal service API used by agents. Groups:

| Tool Group | Functions |
| --- | --- |
| DB Tools | `save_test_case`, `get_test_case`, `get_test_cases_for_run`, `save_test_result`, `update_script_path` |
| DOM Tools | `list_pages`, `get_snapshot` |
| Script Tools | `write_script`, `read_script`, `delete_script` |
| State Tools | `update_state_local`, `flush_state_to_db` |
| Queue Tools | `enqueue`, `requeue`, `mark_complete` |
| Credential Tools | `get_credential_by_id` (zero exposure to LLM) |

---

## Phase 3 Worker (`services/phase3_worker.py`)

The RabbitMQ consumer. For each job:

1. Deserialises job (test_id, script_path, project_id, credential_id)
2. Resolves auth state (Playwright storageState) — sets up or re-uses browser session
3. Runs `playwright test <script.spec.ts>` as a subprocess
4. Reads Playwright output and network logs from test attachment
5. Calls A6 classifier
6. On SCRIPT_ERROR → A6 signals A7 for repair

Worker mode is controlled by `PHASE3_EMBEDDED_WORKERS`:
- `true` — worker thread starts inside the uvicorn process (local dev default)
- `false` — run `python -m app.services.phase3_worker` in a separate terminal or container

---

## Recorder Daemon (`recorder.py` / `recorder_template.py`)

The Python recorder daemon runs on the tester's local machine. It:

1. Polls `GET /recorder/{project_id}/pulse` every second.
2. When a `scenario_id` is pending, opens a Chromium browser with a JS action capture script injected.
3. Captures every user action: clicks, fills, navigations, select changes.
4. On each action, extracts: selector, accessible name, element text, role, URL before/after, screenshot.
5. Uploads steps to `POST /recorder/{project_id}/sessions/{sid}/steps`.
6. Uploads DOM snapshots to `POST /recorder/{project_id}/routes`.
7. On stop signal, finalises the session.

The recorder script is served dynamically — tokens and project IDs are baked in by `recorder_service.get_recorder_script()` from `recorder_template.py`. This means testers always get a fresh, pre-configured script without having to edit anything.

---

## Database Migrations

```powershell
# Apply all pending migrations
alembic upgrade head

# Check current migration
alembic current

# Create a migration after model changes
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

Always commit model changes and migration files together.

---

## Running Tests

```powershell
cd server
uv run pytest
```

---

## Security Notes

- `CREDENTIAL_ENCRYPTION_KEY` is a 32-byte Fernet key. Never lose it — all stored test credentials are encrypted with this key.
- `JWT_SECRET_KEY` must be at least 32 characters and random.
- Recorder daemon tokens are project-scoped UUIDs stored in the `Project` table.
- `server/tests/.auth/` contains Playwright auth state files (real session cookies) — always gitignored.
- Never expose real `.env` files. Rotate any key that was committed.

---

## Docker

Build and run the server container:

```powershell
docker compose up server phase3-worker
```

Or run the full stack:

```powershell
docker compose up --build
```

See the root `docker-compose.yml` for service definitions and environment variable forwarding.
