-- ============================================================
-- LEAST-PRIVILEGE DATABASE ROLES (existing installations)
-- ============================================================
-- Tenant isolation is enforced by PostgreSQL row level security, which
-- superusers and BYPASSRLS roles ignore. PharmaStock therefore uses:
--
--   * an OWNER role that owns the schema and applies migrations
--     (MIGRATION_DATABASE_URL), e.g. the role that owns the database today;
--   * an APPLICATION role (DATABASE_URL) with row privileges only: it cannot
--     create, alter or drop tables, cannot bypass row level security, and can
--     only INSERT / SELECT the audit trails (append-only).
--
-- Run once as a superuser:
--   psql -v app_password='...' -f deploy/create_app_role.sql -d pharmastock
-- then apply migrations as the owner with APP_DB_ROLE=pharmastock_app, which
-- (re)grants the application role's privileges on every table:
--   MIGRATION_DATABASE_URL=postgresql://<owner>:<pw>@host/pharmastock \
--   APP_DB_ROLE=pharmastock_app python -m backend.migrate
-- and run the server with
--   DATABASE_URL=postgresql://pharmastock_app:<pw>@host/pharmastock
--
-- Installations where pharmastock_app already OWNS the schema (earlier
-- versions of this script): create a new owner role, transfer ownership to
-- it (ALTER ... OWNER TO), then follow the steps above. See docs/DEPLOYMENT.md.

\set ON_ERROR_STOP on

CREATE ROLE pharmastock_app LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
SELECT format('GRANT CONNECT ON DATABASE %I TO pharmastock_app', current_database()) \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
