-- =============================================================================
-- SQAT (Software QA Testing Framework) — Database Schema
-- Generated from: app/models/* and migrations/versions/0001_initial.py
-- PostgreSQL 14+
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. ENUM TYPES
-- ---------------------------------------------------------------------------

CREATE TYPE project_status AS ENUM (
    'Active',
    'Draft',
    'Blocked'
);

CREATE TYPE project_role AS ENUM (
    'OWNER',
    'TESTER'
);

CREATE TYPE project_file_type AS ENUM (
    'brd',
    'fsd',
    'wbs',
    'assumption',
    'credentials',
    'swagger_docs'
);


-- ---------------------------------------------------------------------------
-- 2. USERS
-- ---------------------------------------------------------------------------

CREATE TABLE users (
    id            UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    email         VARCHAR(320) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(32)  NOT NULL DEFAULT 'user',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX ix_users_email ON users (email);


-- ---------------------------------------------------------------------------
-- 3. PROJECTS
-- ---------------------------------------------------------------------------

CREATE TABLE projects (
    id          UUID           NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    owner_id    UUID           NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name        VARCHAR(120)   NOT NULL,
    description TEXT           NOT NULL DEFAULT '',
    status      project_status NOT NULL DEFAULT 'Draft',
    url         VARCHAR(2048)  NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX ix_projects_owner_id ON projects (owner_id);
CREATE INDEX ix_projects_name     ON projects (name);


-- ---------------------------------------------------------------------------
-- 4. PROJECT MEMBERS
-- ---------------------------------------------------------------------------

CREATE TABLE project_members (
    id         UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id UUID         NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    user_id    UUID         NOT NULL REFERENCES users    (id) ON DELETE CASCADE,
    role       project_role NOT NULL DEFAULT 'TESTER',
    joined_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX ix_project_members_project_id ON project_members (project_id);
CREATE INDEX ix_project_members_user_id    ON project_members (user_id);


-- ---------------------------------------------------------------------------
-- 5. PROJECT FILES
-- ---------------------------------------------------------------------------
--
-- File naming convention (enforced by the application layer):
--   Local path  : uploads/<project_id>/<project_id>_<file_id>_<file_type>_<file_number>
--   Example     : uploads/proj-uuid/proj-uuid_file-uuid_brd_1
--
--   <file_number> is the sequential count of files of the same file_type
--   within the project at the time of upload (1-based, e.g. 1, 2, 3 …).
--   The original filename is preserved in the  original_filename  column.
--
-- ---------------------------------------------------------------------------

CREATE TABLE project_files (
    id                UUID              NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id        UUID              NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    file_type         project_file_type NOT NULL,
    original_filename VARCHAR(255)      NOT NULL,
    content_type      VARCHAR(255)      NOT NULL DEFAULT 'application/octet-stream',
    size_bytes        INTEGER           NOT NULL DEFAULT 0,
    absolute_path     VARCHAR(2048)     NOT NULL,
    uploaded_at       TIMESTAMPTZ       NOT NULL DEFAULT now()
);

CREATE INDEX ix_project_files_project_id ON project_files (project_id);
CREATE INDEX ix_project_files_file_type  ON project_files (file_type);


-- ---------------------------------------------------------------------------
-- 6. INGESTION STATUS ENUM
-- ---------------------------------------------------------------------------

CREATE TYPE ingestion_status AS ENUM (
    'pending', 'processing', 'completed', 'failed'
);


-- ---------------------------------------------------------------------------
-- 7. API ENDPOINTS (parsed from Swagger/OpenAPI)
-- ---------------------------------------------------------------------------

CREATE TABLE api_endpoints (
    id            UUID            NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id    UUID            NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    file_id       UUID            NOT NULL REFERENCES project_files (id) ON DELETE CASCADE,
    http_method   VARCHAR(10)     NOT NULL,
    path          VARCHAR(2048)   NOT NULL,
    operation_id  VARCHAR(255),
    summary       TEXT            NOT NULL DEFAULT '',
    description   TEXT            NOT NULL DEFAULT '',
    tags          TEXT[]          NOT NULL DEFAULT '{}',
    parameters    JSONB           NOT NULL DEFAULT '{}',
    request_body  JSONB,
    responses     JSONB           NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX ix_api_endpoints_project_id ON api_endpoints (project_id);


-- ---------------------------------------------------------------------------
-- 8. DOCUMENT CHUNKS (text chunks with source metadata)
-- ---------------------------------------------------------------------------

CREATE TABLE document_chunks (
    id              UUID            NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id      UUID            NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    file_id         UUID            NOT NULL REFERENCES project_files (id) ON DELETE CASCADE,
    chunk_index     INTEGER         NOT NULL,
    content         TEXT            NOT NULL,
    token_count     INTEGER         NOT NULL,
    page_number     INTEGER,
    source_type     VARCHAR(32)     NOT NULL,
    chunk_metadata  JSONB           NOT NULL DEFAULT '{}',
    qdrant_point_id UUID,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX ix_document_chunks_project_id ON document_chunks (project_id);
CREATE INDEX ix_document_chunks_file_id    ON document_chunks (file_id);


-- ---------------------------------------------------------------------------
-- 9. INGESTION JOBS (tracks pipeline runs)
-- ---------------------------------------------------------------------------

CREATE TABLE ingestion_jobs (
    id              UUID              NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id      UUID              NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    status          ingestion_status  NOT NULL DEFAULT 'pending',
    total_files     INTEGER           NOT NULL DEFAULT 0,
    processed_files INTEGER           NOT NULL DEFAULT 0,
    total_chunks    INTEGER           NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ       NOT NULL DEFAULT now()
);

CREATE INDEX ix_ingestion_jobs_project_id ON ingestion_jobs (project_id);


-- =============================================================================
-- END OF SCHEMA
-- =============================================================================
