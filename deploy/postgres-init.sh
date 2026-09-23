#!/bin/sh
# Runs once, when the PostgreSQL volume is first created (docker-entrypoint-initdb.d).
# Creates the ordinary application role, a read-only backup role, (no SUPERUSER / BYPASSRLS, so row level
# security isolates organizations) and the database it owns.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE pharmastock_app LOGIN PASSWORD '${APP_DB_PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE pharmastock OWNER pharmastock_app;
-- Backups must see every organization's rows: a read-only role that bypasses
-- row level security (it cannot write anything).
CREATE ROLE pharmastock_backup LOGIN PASSWORD '${BACKUP_DB_PASSWORD}' NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT pg_read_all_data TO pharmastock_backup;
SQL
