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


-- =============================================================================
-- END OF SCHEMA
-- =============================================================================
