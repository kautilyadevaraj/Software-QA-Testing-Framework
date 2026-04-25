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
    is_verified BOOLEAN        NOT NULL DEFAULT false,
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
-- 6. PROJECT CREDENTIAL VERIFICATIONS
-- ---------------------------------------------------------------------------

CREATE TABLE project_credential_verifications (
    id          UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id  UUID         NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    username    VARCHAR(255) NOT NULL,
    is_verified BOOLEAN      NOT NULL DEFAULT false
);

CREATE INDEX ix_project_credential_verifications_project_id ON project_credential_verifications (project_id);


-- ---------------------------------------------------------------------------
-- 7. EXTRACTED TEXT
-- ---------------------------------------------------------------------------

CREATE TABLE extracted_text (
    id         UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id    UUID        NOT NULL REFERENCES project_files (id) ON DELETE CASCADE,
    project_id UUID        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    blob_url   TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_extracted_text_project_id ON extracted_text (project_id);


-- ---------------------------------------------------------------------------
-- 7.5 CHUNKS
-- ---------------------------------------------------------------------------

CREATE TABLE chunks (
    id                UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id           UUID        NOT NULL REFERENCES project_files (id) ON DELETE CASCADE,
    project_id        UUID        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    extracted_text_id UUID        NOT NULL REFERENCES extracted_text (id) ON DELETE CASCADE,
    chunk_index       INTEGER     NOT NULL,
    start_idx         INTEGER     NOT NULL,
    end_idx           INTEGER     NOT NULL,
    qdrant_point_id   TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_chunks_project_id ON chunks (project_id);
CREATE INDEX ix_chunks_extracted_text_id ON chunks (extracted_text_id);



-- ---------------------------------------------------------------------------
-- 8. API ENDPOINTS
-- ---------------------------------------------------------------------------

CREATE TABLE api_endpoints (
    id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id  UUID        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    path        TEXT,
    method      TEXT,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_api_endpoints_project_id ON api_endpoints (project_id);


-- ---------------------------------------------------------------------------
-- 9. HIGH LEVEL SCENARIOS
-- ---------------------------------------------------------------------------

CREATE TABLE high_level_scenarios (
    id           UUID        NOT NULL PRIMARY KEY,
    project_id   UUID        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    title        TEXT        NOT NULL,
    description  TEXT        NOT NULL DEFAULT '',
    source       TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'pending',
    completed_by UUID        NULL REFERENCES users (id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_high_level_scenarios_source CHECK (source IN ('agent_1', 'agent_2', 'manual')),
    CONSTRAINT ck_high_level_scenarios_status CHECK (status IN ('pending', 'completed'))
);

CREATE INDEX ix_high_level_scenarios_project_id   ON high_level_scenarios (project_id);
CREATE INDEX ix_high_level_scenarios_status       ON high_level_scenarios (status);
CREATE INDEX ix_high_level_scenarios_completed_by ON high_level_scenarios (completed_by);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_high_level_scenarios_updated_at
BEFORE UPDATE ON high_level_scenarios
FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
-- END OF SCHEMA
-- =============================================================================
