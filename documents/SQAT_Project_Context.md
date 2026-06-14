# SQAT — Project Context and Technical Reference

> **Purpose:** This document is the single comprehensive reference for the SQAT (Software QA Testing Framework) project. It synthesises the BRD, Architecture, HLD/LLD across all phases, and the current implementation state as of June 2026. Use this as your starting point when onboarding to the codebase.

---

## 1. Project Overview

SQAT is an AI-assisted QA automation platform that takes project documentation and recorded application behaviour as inputs and produces:

- **Reviewable test cases** with acceptance criteria and X-Ray CSV for Jira import
- **Executable Playwright TypeScript scripts** grounded in real DOM and recorded selectors
- **Classified execution results** (PASS / APP_ERROR / SCRIPT_ERROR / HUMAN_REVIEW)
- **Auto-repaired scripts** via LLM on repairable failures
- **Jira defects** raised directly from the review queue
- **Final execution reports** (CSV) and downloadable Playwright traces/screenshots

The platform is designed around a human-in-the-loop philosophy: AI does the heavy lifting, but QA engineers approve every major output before automation runs.

---

## 2. Architecture

### Deployment Topology (per Architecture.mmd)

```
Phase 1 — Ingestion and Embedding
  ├── Azure Ubuntu VM
  │   ├── FastAPI
  │   ├── Local Embedding Model (all-MiniLM-L6-v2)
  │   └── Qdrant (vector store)
  └── Azure Managed Services
      ├── Azure Blob Storage (raw document files)
      └── PostgreSQL DB (metadata and state)

Phase 2 — Scenario Discovery (Orchestrator Hub)
  ├── React/Next.js UI
  ├── FastAPI + FastMCP
  ├── LangGraph Engine
  │   └── Agents A1, A2, A3
  ├── Python Recorder Daemon (runs on tester's local machine)
  └── Local Qdrant (containerised)

Phase 3 — Code Generation and Execution
  ├── FastAPI Layer (plan / approve / execute endpoints)
  ├── LangGraph Orchestration
  ├── Agent Pipeline (A3 planner → A4 context → A5 script gen)
  ├── MCP Tool Layer (DB / DOM / Script / State / Queue / Credentials)
  ├── RabbitMQ execution queue
  ├── Parallel Chromium workers
  ├── A6 Result Classifier
  ├── A7 Retry Agent
  └── Human Review Queue (SSE-driven)

Phase 4 — Automated Report Generation
  └── PostgreSQL audit tables → CSV export → Jira integration
```

### Current Implementation vs. Architecture Document

The production architecture in the BRD describes Azure-hosted VMs with Temporal orchestration and Azure Blob storage. The current implementation is a self-contained monolith suitable for local development and on-premise deployment:

| Architecture Component | Current Implementation |
| --- | --- |
| Azure Ubuntu VM (Phase 1) | Same FastAPI server handles ingestion |
| Azure Blob Storage | Local `server/uploads/` directory |
| Temporal orchestration | LangGraph graphs run in-process |
| Separate Azure VMs | All services in one repo |
| Qdrant (cloud) | Configurable via `QDRANT_URL` (cloud or local) |
| Temporal | Replaced with FastAPI BackgroundTasks + RabbitMQ |

---

## 3. Tech Stack (Current)

| Layer | Technology | Notes |
| --- | --- | --- |
| Frontend | Next.js 16, React 19, TypeScript | App Router |
| Styling | Tailwind CSS 4, Radix UI | |
| Backend | FastAPI, Python 3.11+ | |
| ORM | SQLAlchemy 2 + Alembic | |
| Database | PostgreSQL 14+ | |
| Message Queue | RabbitMQ 3 | AMQP with DLX/DLQ |
| Vector Store | Qdrant | Cloud or local |
| Embeddings | HuggingFace `all-MiniLM-L6-v2` | Local model |
| LLM (all phases) | Anthropic Claude Sonnet 4.6 | Phase 2 scenario gen + Phase 3 A3/A5/A7 |
| LLM (optional fallback) | Groq `llama-3.3-70b-versatile` | Only if `LLM_FALLBACK_CHAIN=anthropic,groq` |
| Test Execution | Playwright / Chromium | Via subprocess |
| Ticketing | Jira Cloud REST API v3 | |
| Rate Limiting | SlowAPI | |
| Auth | JWT (HTTP-only cookies) | Access + refresh tokens |
| Encryption | Fernet (cryptography) | Credential storage |
| Dependency Mgr | `uv` (Python), `npm` (Node) | |

---

## 4. Data Model (Key Tables)

### User and Project

```
users
  id, email, hashed_password, created_at

projects
  id, name, url, owner_id, recorder_token, active_launch_scenario_id, jira_project_key

project_members
  project_id, user_id, role

credential_profiles
  id, project_id, role, username_encrypted, password_encrypted
  # Fernet-encrypted; decrypted at execution time, never passed to LLM

project_jira_config
  project_id, jira_project_key, jira_project_id
```

### Phase 1 — Document Ingestion

```
uploaded_files
  id, project_id, filename, category (brd/fsd/swagger/credentials/…), file_path
  # Qdrant stores the vector chunks; only metadata in PostgreSQL
```

### Phase 2 — Scenarios and Recordings

```
high_level_scenarios (hls)
  id, project_id, title, description, source (ai/manual), status, completed_by

recording_sessions
  id, project_id, scenario_id, status (pending/in_progress/completed/failed)

scenario_steps
  id, recording_session_id, project_id, scenario_id, step_index
  action_type, selector, selector_candidates, playwright_locator
  selector_quality_reason, element_text, accessible_name, role, label
  value, input_value_kind, input_type
  url, url_before, url_after
  is_noise, semantic_context (JSONB)
  screenshot_path, route_variant_before_id, route_variant_after_id

discovered_routes
  id, project_id, path, full_url, page_title, html_path, screenshot_path

route_variants
  id, recording_session_id, route_id, scenario_id
  snapshot_index, snapshot_kind, captured_at
  html_path, screenshot_path
  interactive_elements (JSONB), assertion_candidates (JSONB)

recorded_route_transitions
  id, recording_id, step_index, from_url, to_url, action_type
  selector, element_text, transition_type, confidence

recorded_assertion_candidates
  id, snapshot_id, candidate_index, kind, selector, text, confidence

recording_flows
  id, recording_id, flow_index, phase3_ready, metadata_json (JSONB)
  # quality_failure_reasons stored in metadata_json
```

### Phase 3 — Test Cases and Execution

```
test_runs
  run_id, project_id, run_type (plan/execute), status
  total, passed, failed, skipped, duration

test_cases
  test_id, project_id, run_id, hls_id, tc_number
  title, steps (ARRAY), acceptance_criteria (ARRAY), assertion_evidence (JSONB)
  target_page, auth_mode, depends_on (ARRAY[UUID]), credential_id, credential_role
  approval_status (PENDING/APPROVED/NEEDS_EDIT/EXCLUDED)
  script_path

phase3_hls_groups
  hls_id (FK), project_id, run_id, ordered_test_ids (ARRAY[UUID])
  script_path
  # Groups test cases under a shared describe.serial spec

test_results
  id, test_id, run_id, status, retries, jira_ticket
  trace_path, screenshot_path

network_logs
  id, test_id, run_id, url, method, status_code, is_failure, response_body

retry_history
  id, test_id, test_result_id, attempt_number
  error_snapshot, llm_fix_applied

review_queue_items
  id, test_id, run_id, review_type (BUG/TASK)
  evidence (JSONB), status (pending/reviewed), jira_ref

phase3_execution_states
  test_id, run_id, status, retries, trace_path

phase3_artifacts
  id, project_id, run_id, artifact_type (XRAY_CSV/EXECUTION_REPORT), path

auth_states
  id, project_id, credential_id, run_id, state_path, expires_at, status
  # Playwright storageState (session cookie) files managed per credential
```

---

## 5. Phase-by-Phase Implementation Details

### Phase 1 — Project Setup and Document Ingestion

**What it does:**
- User creates a project, sets a target URL, uploads documents and credentials.
- `pdf_extractor_service.py` extracts text from PDF, DOCX, and XLSX files.
- Documents are chunked and embedded using HuggingFace `all-MiniLM-L6-v2`.
- Vectors are upserted to Qdrant with metadata (project_id, category, filename).
- Raw files are saved to `server/uploads/`.
- Credential CSV is parsed, values are Fernet-encrypted, stored in `credential_profiles`.

**Key files:**
- `routers/files.py` — upload endpoint
- `routers/projects.py` — project CRUD, credential management, Jira project setup
- `services/pdf_extractor_service.py` — text extraction
- `services/credential_service.py` — encryption/decryption
- `agents/agent1_brd.py` — chunking + Qdrant upsert
- `agents/agent2_swagger.py` — OpenAPI parsing

### Phase 2 — Scenario Generation and Application Recording

#### 2a — Scenario Generation

**What it does:**
- LangGraph `scenario_graph.py` runs agents A1 (BRD context retrieval), A2 (Swagger context), A3 (deduplication).
- LLM (**Anthropic Claude** via `call_llm()`) generates High-Level Scenarios (HLS) from document chunks. Both Phase 2 and Phase 3 now use the same `call_llm()` interface from `app.utils.llm`. Output tokens for Phase 2 calls are capped at `SCENARIO_AGENT_MAX_OUTPUT_TOKENS` (default: 2048).
- A3 dedup removes near-duplicate scenarios using cosine similarity.
- Scenarios saved to `high_level_scenarios` with `source='ai'`.
- QA engineer can add manual scenarios (`source='manual'`), edit descriptions, and delete.
- Each scenario must be marked `status='completed'` before Phase 3 can use it.

**Key files:**
- `graph/scenario_graph.py` — LangGraph wiring
- `agents/agent3_dedup.py` — deduplication
- `agents/scenario_common.py` — shared BRD chunk retrieval, batching
- `routers/scenarios.py` — CRUD + approval endpoints

#### 2b — Application Recording (Recorder Daemon)

**Architecture:**
The recorder daemon (`recorder_template.py`) runs locally on the tester's machine. It is served dynamically by the backend — the tester never edits it.

**Flow:**
1. UI calls `GET /api/v1/projects/{id}/scenarios/recording-setup` → returns a `curl + python` command with the recorder token embedded.
2. Tester runs the command → daemon starts, polls `/recorder/{id}/pulse` every 1 second.
3. Tester clicks **"Launch Recording"** in UI → `active_launch_scenario_id` is set on the Project row.
4. `/pulse` returns `scenario_id` and atomically clears the field → daemon gets exactly one signal per click.
5. Daemon opens Chromium with `recorder_action_capture.js` injected. This JS listener captures every DOM event: `click`, `input`, `change`, `navigate`.
6. For each event: extracts stable selector (data-testid → id → aria-label → placeholder → role + name → CSS), accessible name, element text, URL before/after.
7. Each step is POSTed to `/recorder/{id}/sessions/{sid}/steps`.
8. DOM snapshots (full HTML + interactive elements + assertion candidates) are POSTed to `/recorder/{id}/routes` with path, page title, screenshot.
9. On stop: session status set to `completed`, `RecordingFlow.phase3_ready` evaluated (quality check).

**Quality checks in `recorder_service.py`:**
- Minimum step count
- At least one navigation event
- Selector diversity (not all bare tags)
- Screenshot availability

**What gets stored per step:**
- `selector` — best stable selector found
- `selector_candidates` — all candidates ranked
- `selector_quality_reason` — why this selector was chosen
- `action_type` — click/fill/navigate/select/check
- `element_text`, `accessible_name`, `role`, `label`
- `value` — fill value (scrubbed for credentials)
- `input_value_kind` — text/credential/numeric/postal/etc.
- `url_before`, `url_after` — URL transition
- `is_noise` — flagged by JS heuristics (ads, cookie banners, consent dialogs)
- `semantic_context` — JSONB with field_identity, form context

### Phase 3 — Test Case Planning, Script Generation, and Execution

#### Planning Flow

**POST /phase3/plan triggers:**

1. For each `HighLevelScenario` with `status='completed'` that has a `completed` `RecordingSession`:
   - A3 Planner constructs context: HLS title/description, available pages list, BRD/FSD document chunks (Qdrant retrieval), recorded steps (formatted).
   - LLM call with the `_PLAN_PROMPT` — returns a JSON array of test cases.
   - Each test case is validated: proper steps, acceptance criteria, auth_mode, target_page not invented.
   - `_ensure_inline_login_setup()` — adds login step if auth_mode=authenticated but steps omit it.
   - `_infer_credential_role()` — matches a credential profile role to the test case from uploaded profiles.
   - Test cases saved to `test_cases` with `approval_status='PENDING'`.

2. After all HLS are processed, A3 runs X-Ray metadata enrichment:
   - LLM call with `_XRAY_METADATA_PROMPT` — returns labels, priority, requirement, pre-conditions per TC.
   - `xray_csv_generator.py` renders the final X-Ray CSV.
   - CSV saved to disk and registered in `phase3_artifacts`.

3. Test run status set to `planning` → `completed`.

#### Human Review and Approval

- `GET /phase3/tc-document/json` — returns TC list grouped by HLS for the accordion UI.
- `PATCH /phase3/test-cases/{id}/approval` — set `APPROVED`, `EXCLUDED`, or `NEEDS_EDIT`.
- `PATCH /phase3/test-cases/{id}/content` — inline edit (auto-resets to `NEEDS_EDIT`).
- `PATCH /phase3/approve-all` — bulk approve all non-excluded TCs.
- Any content edit triggers X-Ray CSV regeneration.

#### Execution Flow

**POST /phase3/execute triggers preflight, then:**

For each APPROVED test case:

1. **A4 builds context** (`agent4_context_builder.py`):
   - Fetches `TestCase` from DB.
   - Finds latest `RecordingSession` for the parent HLS.
   - Loads `ScenarioStep` records (non-noise only).
   - Resolves `target_page` — overrides A3's declaration with the Phase-2 recording's first step URL when they differ.
   - For `auth_mode=authenticated`, resolves the first post-auth route.
   - Loads `RecordedRouteTransition` rows for navigation evidence.
   - Loads `RouteVariant` snapshots for all visited pages.
   - Loads `RecordedAssertionCandidate` per snapshot.
   - Builds `route_map` (path → link text) and `route_patterns` (dynamic route shapes).
   - Detects `testIdAttribute` (data-testid vs data-cy etc.) from recorded selectors.
   - Synthesises per-project few-shot example from actual recorded steps.
   - Returns a `ContextObject` dict.

2. **A5 generates script** (`agent5_script_generator.py`):

   **Single test mode** (each HLS TC independent):
   - Calls LLM with `_SCRIPT_PROMPT` + full context.
   - Post-processes output (deterministic fixers applied regardless of LLM output).
   - Validates: no invented selectors, no hardcoded URLs, no banned patterns.
   - Retries up to 3 times on validation failure.
   - Writes to `tests/generated/{test_id}.spec.ts`.

   **Grouped test mode** (HLS TCs that form a serial flow):
   - All test cases in an HLS group share one `describe.serial` spec.
   - `sharedPage` persists browser state across tests.
   - First test navigates; later tests continue from current state.
   - Writes to `tests/generated/{hls_id}.spec.ts`.
   - `Phase3HlsGroup` row tracks the group and ordered test IDs.

3. **Test job enqueued** to RabbitMQ via `queue_topology.py`.

4. **Phase 3 Worker** (`phase3_worker.py`) processes jobs:
   - Resolves auth state: `auth_state_service.py` sets up or reuses Playwright `storageState`.
   - For `login_flow` tests: starts fresh (no storage state).
   - For `authenticated` tests: attempts to reuse existing auth state or creates one via headless login.
   - Runs `playwright test {script.spec.ts}` as subprocess.
   - Parses stdout for result (PASS/FAIL/ERROR).
   - Reads `network_logs` attachment from test output.
   - Calls A6 classifier.

5. **A6 Classification** (`agent6_classifier.py`):
   - Checks network logs for 4xx/5xx failures.
   - Checks if test is negative-intent (expected to fail with 4xx).
   - Checks error log for: infra errors → HUMAN_REVIEW, auth errors → HUMAN_REVIEW, repairable script errors → A7, assertion mismatches → HUMAN_REVIEW.
   - Saves `TestResult` and `Phase3ExecutionState`.

6. **A7 Repair** (`agent7_retry.py`) on SCRIPT_ERROR:
   - Checks retry attempt count against `PHASE3_MAX_ATTEMPTS`.
   - Calls A4 for fresh context (updated DOM snapshot).
   - Grouped mode: finds failing `test()` block by title using a paren-balanced walker.
   - Single mode: replaces entire file.
   - LLM repairs only the broken block; validates against grounding (no hallucinated selectors).
   - Saves `RetryHistory`.
   - Re-enqueues via RabbitMQ.
   - On exhaustion or grounding failure: HUMAN_REVIEW + review_queue TASK.

### Phase 4 — Reporting and Jira Integration

**Execution Report:**
- `GET /phase3/execution-report.csv` — all test cases for the run with columns: TC number, title, status, retries, Jira ticket, trace available, screenshot available, target page.

**X-Ray CSV:**
- `GET /phase3/tc-document` — downloadable X-Ray-compatible CSV with full test case metadata for Jira/X-Ray import.

**Review Queue:**
- Items appear via SSE stream (`GET /phase3/review-queue/stream`).
- Each item includes: review_type (BUG/TASK), evidence (error log, failing requests, category), TC number, title.
- **Raise Jira (`POST /phase3/raise-jira`):**
  - `jira_service.py` calls Jira Cloud REST API v3.
  - Creates issue with `[TC-XXX]` prefix in summary.
  - Bug issues for APP_ERROR; Task issues for HUMAN_REVIEW.
  - Stores `jira_ticket` key in `TestResult` and `ReviewQueueItem.jira_ref`.
- **Edit & Re-run (`POST /phase3/review-queue/{id}/rerun`):**
  - Saves edited script content to disk.
  - Re-enqueues the test job via RabbitMQ.

**Artifacts:**
- `GET /phase3/trace/{test_id}` — Playwright trace `.zip` (for FAIL/HUMAN_REVIEW).
- `GET /phase3/screenshot/{test_id}` — PASS outcome screenshot `.png`.

---

## 6. Key Design Decisions

### Recorder Token Auth (not JWT)
The recorder daemon runs on the tester's local machine, not in a browser. HTTP-only cookies are inaccessible from non-browser clients. A project-scoped `recorder_token` UUID in the `X-Recorder-Token` header is used instead.

### A4 as the Single Context Source
Both A5 (generate) and A7 (repair) consume A4 context. This makes context deterministic and testable per `(test_id, target_page)` pair. A5 stays a thin LLM call + post-processor; A7 gets the same evidence as the original generator.

### Grouped vs. Single Playwright Scripts
Some HLS scenarios decompose into test cases that must run sequentially (e.g., Create → Edit → Delete). These are grouped in a `describe.serial` spec with a `sharedPage` that persists browser state. The `Phase3HlsGroup` table tracks membership. A7 can repair individual blocks within a grouped spec via a paren-balanced parser.

### X-Ray CSV Generation (Two-Pass)
Pass 1 (A3 planning): generates the TC document with steps and acceptance criteria.  
Pass 2 (A3 X-Ray mode): enriches with labels, priority, requirements from BRD/FSD document context.  
This separation means the TC document is available immediately after planning; X-Ray metadata is added asynchronously.

### Auth State Management
Playwright `storageState` (session cookies) is managed by `auth_state_service.py`:
- One auth state per `(project_id, credential_id, run_id)`.
- Reused across test cases for the same credential profile within a run.
- Auto-expired after `AUTH_STATE_RETENTION_HOURS`.
- `auth_state_cleanup_scheduler.py` runs APScheduler to sweep expired files and DB rows.

### Credential Encryption
Test credentials are stored Fernet-encrypted. The `CREDENTIAL_ENCRYPTION_KEY` never leaves the server process. The LLM never sees credential values — A5 uses `env('TEST_USERNAME')` / `env('TEST_PASSWORD')` placeholder tokens, and the worker injects actual values at runtime via process environment.

### Phase-2 Recording as Grounding, Not Replay
Phase-2 recorded selectors and steps are **evidence** of what the app supports — not a script to replay. A3, A4, and A5 all have explicit instructions to use recording data to understand the app's structure and selectors, but to generate QA test cases from HLS/BRD intent. Dynamic business-object routes (e.g., `/records/2`) are abstracted to patterns (e.g., `a[href*="/records/"]`).

---

## 7. LLM Provider Chain

Configured via `LLM_PROVIDER` and `LLM_FALLBACK_CHAIN`:

```env
LLM_PROVIDER=anthropic
LLM_FALLBACK_CHAIN=anthropic
```

`utils/llm.py` implements the chain: calls the primary provider, falls back to the next on rate limit or error. All LLM calls in both Phase 2 and Phase 3 use the same `call_llm(prompt, max_tokens)` interface.

| Provider | Use Case | Output Tokens | Model |
| --- | --- | --- | --- |
| Anthropic Claude Sonnet 4.6 | **All phases** — Phase 2 scenario gen (A1/A2/A3 dedup) + Phase 3 A3 planner, A5 script gen, A7 repair | Phase 2: 2048 (`SCENARIO_AGENT_MAX_OUTPUT_TOKENS`) · Phase 3: 4096 (`ANTHROPIC_MAX_TOKENS`) | `claude-sonnet-4-6` |
| Groq Llama 3.3 70B | **Optional fallback only** — only active when `LLM_FALLBACK_CHAIN=anthropic,groq` and Claude fails | 1024 (`GROQ_MAX_TOKENS`) | `llama-3.3-70b-versatile` |

> **Note:** Phase 2 and Phase 3 both call `call_llm()` from `app.utils.llm`. Phase 2 passes `max_tokens=_scenario_output_tokens()` (2048 by default). Phase 3 agents pass their own `max_tokens` or fall back to `ANTHROPIC_MAX_TOKENS` (4096).

---

## 8. Rate Limiting

SlowAPI rate limits applied at the FastAPI middleware level:

```env
RATE_LIMIT_AUTH=300/minute       # auth endpoints
RATE_LIMIT_API=5000/minute       # all other API endpoints
```

---

## 9. Cleanup Services

### Script Cleanup (`script_cleanup_service.py`)
- Runs on a schedule (configurable).
- Deletes generated `.spec.ts` files older than `SCRIPT_RETENTION_HOURS` (default: 72h).
- Does not delete scripts for tests with `HUMAN_REVIEW` status (they may still be needed for re-run).

### Auth State Cleanup (`auth_state_cleanup_scheduler.py` + `auth_state_cleanup_service.py`)
- APScheduler job runs every `AUTH_STATE_CLEANUP_INTERVAL_MINUTES` (default: 60).
- Deletes expired `storageState` JSON files from disk.
- Marks corresponding `AuthState` DB rows as `expired`.

---

## 10. Environment Variables — Full Reference

| Variable | Default | Description |
| --- | --- | --- |
| `APP_NAME` | `SQAT Backend Service` | App name in FastAPI docs |
| `APP_ENV` | `development` | Environment tag |
| `API_PREFIX` | `/api/v1` | API route prefix |
| `DATABASE_URL` | — | PostgreSQL connection string |
| `JWT_SECRET_KEY` | — | HMAC secret for JWT signing (32+ chars) |
| `CREDENTIAL_ENCRYPTION_KEY` | — | Fernet key for credential encryption |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token TTL |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token TTL |
| `COOKIE_SECURE` | `false` | Set to `true` in production (HTTPS) |
| `COOKIE_SAMESITE` | `lax` | Cookie SameSite policy |
| `FRONTEND_ORIGINS` | `http://localhost:3000` | CORS allowed origins |
| `MAX_UPLOAD_MB` | `20` | Max document upload size |
| `UPLOAD_DIR` | `uploads` | Uploaded file storage directory |
| `RECORDINGS_BASE_PATH` | `recordings` | Recorder DOM/HTML storage |
| `RECORDER_STORE_PASSWORD_VALUES` | `false` | Whether to store credential values in recording |
| `BASE_URL` | `http://localhost:3000` | Target application base URL |
| `USER_EMAIL` / `USER_PASSWORD` | — | Default test user credentials |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | — | Default admin credentials |
| `PUBLIC_API_URL` | `http://localhost:8000` | Backend URL (used in recorder script) |
| `RATE_LIMIT_AUTH` | `300/minute` | Auth endpoint rate limit |
| `RATE_LIMIT_API` | `5000/minute` | API endpoint rate limit |
| `HF_TOKEN` | — | HuggingFace token for embedding model |
| `HF_MODEL_NAME` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |
| `HF_MODELS_DIR` | `models` | Local model cache directory |
| `QDRANT_URL` | — | Qdrant cluster URL |
| `QDRANT_API_KEY` | — | Qdrant API key |
| `LLM_PROVIDER` | `anthropic` | Primary LLM provider |
| `LLM_FALLBACK_CHAIN` | `anthropic,groq` | Fallback chain (comma-separated) |
| `LLM_MAX_CONCURRENT` | `4` | Max concurrent LLM requests |
| `LLM_RETRY_ATTEMPTS` | `3` | Per-provider retry count |
| `LLM_RETRY_BACKOFF_BASE_S` | `2.0` | Exponential backoff base |
| `LLM_RATE_LIMIT_SLEEP` | `15.0` | Sleep on rate limit (seconds) |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Claude model name |
| `ANTHROPIC_MAX_TOKENS` | `2048` | Max output tokens |
| `GROQ_API_KEY` | — | Groq API key |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model name |
| `GROQ_MAX_TOKENS` | `1024` | Max output tokens |
| `SCENARIO_AGENT_BATCH_CHARS` | `4500` | Max chars per scenario batch |
| `SCENARIO_AGENT_BATCH_SIZE` | `4` | Scenarios per LLM batch |
| `SCENARIO_AGENT_MAX_SCENARIOS_PER_BATCH` | `5` | Max scenarios generated per batch |
| `SCENARIO_AGENT_BATCH_DELAY_SECONDS` | `1.0` | Delay between batches |
| `SCENARIO_DEDUP_MAX_CHARS` | `8000` | Max chars for dedup context |
| `JIRA_BASE_URL` | — | Jira Cloud instance URL |
| `JIRA_EMAIL` | — | Jira account email |
| `JIRA_API_TOKEN` | — | Jira API token |
| `JIRA_LEAD_ACCOUNT_ID` | — | Jira project lead account ID |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | RabbitMQ connection URL |
| `RABBITMQ_QUEUE` | `phase3_test_jobs` | Main execution queue |
| `RABBITMQ_DLX` | `phase3.dlx` | Dead letter exchange |
| `RABBITMQ_DLQ` | `phase3_test_jobs.dead` | Dead letter queue |
| `PHASE3_MAX_ATTEMPTS` | `3` | Max test execution attempts |
| `PHASE3_AGENT_RETRY_ATTEMPTS` | `3` | A7 LLM repair attempts |
| `CHROMIUM_WORKERS` | `3` | Parallel Chromium workers |
| `PLAYWRIGHT_HEADED` | `false` | Run browser with UI visible |
| `PLAYWRIGHT_SLOW_MO_MS` | `3000` | Slow motion delay in ms |
| `PLAYWRIGHT_TEST_TIMEOUT_MS` | `30000` | Per-test timeout |
| `WORKER_SUBPROCESS_TIMEOUT_MS` | `600000` | Max worker subprocess time |
| `REQUEUE_DELAY_MS` | `15000` | Delay before re-enqueue |
| `VISION_FALLBACK` | `false` | Enable vision-based selector fallback |
| `PHASE3_EMBEDDED_WORKERS` | `true` | Run workers in-process |
| `PHASE3_EXTERNAL_RUN_TIMEOUT_S` | `3600` | External worker timeout |
| `STATE_JSON_PATH` | `state.json` | Real-time execution state file |
| `GENERATED_SCRIPTS_DIR` | `tests/generated` | Playwright spec output directory |
| `PHASE3_TEST_DATA_NAME` | `Test User` | Default name for form fills |
| `PHASE3_TEST_DATA_POSTAL_CODE` | `12345` | Default postal code |
| `PHASE3_TEST_DATA_PHONE` | `9000000000` | Default phone number |
| `PHASE3_TEST_DATA_SEARCH` | `test` | Default search keyword |
| `PHASE3_TEST_DATA_EMAIL` | `qa.user@example.test` | Default test email |
| `A4_STRICT_SNAPSHOT` | `false` | Route HUMAN_REVIEW on missing DOM snapshot |
| `SCRIPT_RETENTION_HOURS` | `72` | Delete generated scripts after N hours |
| `SCRIPT_CLEANUP_ENABLED` | `true` | Enable script cleanup scheduler |
| `AUTH_STATE_RETENTION_HOURS` | `24` | Auth state file TTL |
| `AUTH_SETUP_TIMEOUT_S` | `90` | Timeout for headless login |
| `AUTH_STATE_CLEANUP_INTERVAL_MINUTES` | `60` | Auth state cleanup interval |
| `AUTH_STATE_CLEANUP_ENABLED` | `true` | Enable auth state cleanup |

---

## 11. Local Development Quick Reference

```powershell
# 1. Start RabbitMQ
docker compose up -d rabbitmq

# 2. Start backend
cd server
uv venv && .venv\Scripts\activate
uv pip install -r requirements.txt
playwright install chromium
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# 3. Start frontend
cd client
npm install
npm run dev

# 4. Access
# Frontend: http://localhost:3000
# API docs: http://localhost:8000/docs
# RabbitMQ: http://localhost:15672 (guest/guest)
```

---

## 12. Implementation Status

All four phases described in the BRD are implemented and functional as of June 2026:

| Phase | Status | Notes |
| --- | --- | --- |
| Phase 1 — Ingestion and Embedding | ✅ Complete | PDF/DOCX/XLSX, Qdrant, credentials |
| Phase 2 — HLS Generation | ✅ Complete | A1+A2+A3 via LangGraph |
| Phase 2 — Application Recording | ✅ Complete | Daemon + JS capture + quality checks |
| Phase 3 — TC Planning (A3) | ✅ Complete | HLS → TCs + X-Ray CSV |
| Phase 3 — Human Approval UI | ✅ Complete | Inline edit, per-TC + bulk |
| Phase 3 — Script Generation (A4+A5) | ✅ Complete | Single + grouped modes |
| Phase 3 — Execution (RabbitMQ + workers) | ✅ Complete | Embedded + external modes |
| Phase 3 — Classification (A6) | ✅ Complete | Rule-based, no LLM |
| Phase 3 — Auto-repair (A7) | ✅ Complete | LLM repair, grouped block splice |
| Phase 3 — Review Queue + Jira | ✅ Complete | SSE, raise Bug/Task, edit+rerun |
| Phase 4 — Report Generation | ✅ Complete | CSV download, trace/screenshot |
| Phase 4 — X-Ray Export | ✅ Complete | Two-pass generation with BRD enrichment |
