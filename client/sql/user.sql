-- Run from the client folder with:
-- psql -U postgres -h localhost -p 5432 -d postgres -f sql/user.sql

-- Create application role if it does not exist.
DO
$$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'authuser') THEN
    CREATE ROLE authuser LOGIN PASSWORD 'password';
  END IF;
END
$$;

ALTER ROLE authuser CREATEDB;

-- Create database if it does not exist (psql \gexec usage).
SELECT 'CREATE DATABASE authdb OWNER authuser'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'authdb')
\gexec

GRANT ALL PRIVILEGES ON DATABASE authdb TO authuser;

\connect authdb

ALTER SCHEMA public OWNER TO authuser;
GRANT ALL ON SCHEMA public TO authuser;

-- Needed for UUID generation used by Prisma model defaults.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Mirrors the Prisma User model shape.
CREATE TABLE IF NOT EXISTS "User" (
  id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  email TEXT NOT NULL UNIQUE,
  password TEXT NOT NULL,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
