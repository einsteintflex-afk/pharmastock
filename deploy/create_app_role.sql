-- ============================================================
-- APPLICATION DATABASE ROLE
-- ============================================================
-- Tenant isolation is enforced by PostgreSQL row level security. Superusers
-- and roles with BYPASSRLS ignore those policies, so the application must
-- connect as an ordinary role. In production the server refuses to start
-- otherwise.
--
-- Run once as a superuser (psql -v app_password='...' -f create_app_role.sql
-- -d pharmastock). The role owns the schema so it can apply migrations;
-- FORCE ROW LEVEL SECURITY makes the policies apply to the owner as well.

\set ON_ERROR_STOP on

CREATE ROLE pharmastock_app LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

-- Hand the database and every object in the public schema to the role.
SELECT format('ALTER DATABASE %I OWNER TO pharmastock_app', current_database()) \gexec
ALTER SCHEMA public OWNER TO pharmastock_app;
SELECT format('ALTER TABLE public.%I OWNER TO pharmastock_app', tablename)
FROM pg_tables WHERE schemaname = 'public' \gexec
SELECT format('ALTER SEQUENCE public.%I OWNER TO pharmastock_app', sequencename)
FROM pg_sequences WHERE schemaname = 'public' \gexec
SELECT format('ALTER VIEW public.%I OWNER TO pharmastock_app', viewname)
FROM pg_views WHERE schemaname = 'public' \gexec
SELECT format('ALTER FUNCTION %s OWNER TO pharmastock_app', p.oid::regprocedure)
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' \gexec

-- Then set DATABASE_URL=postgresql://pharmastock_app:<password>@host:5432/pharmastock
