#!/bin/sh
# Runs once, when the PostgreSQL volume is first created (docker-entrypoint-initdb.d).
# Least privilege, three roles (none is a superuser):
#   pharmastock_owner   owns the database and schema; used ONLY to apply migrations
#   pharmastock_app     the running application: row privileges only (granted by the
#                       migration step), no BYPASSRLS, so row level security isolates
#                       organizations; cannot change the schema or the audit trails
#   pharmastock_backup  read-only, bypasses row level security so backups contain
#                       every organization's rows
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE pharmastock_owner LOGIN PASSWORD '${OWNER_DB_PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE pharmastock_app LOGIN PASSWORD '${APP_DB_PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE pharmastock OWNER pharmastock_owner;
CREATE ROLE pharmastock_backup LOGIN PASSWORD '${BACKUP_DB_PASSWORD}' NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT pg_read_all_data TO pharmastock_backup;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname pharmastock <<SQL
ALTER SCHEMA public OWNER TO pharmastock_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE pharmastock TO pharmastock_app;
SQL
