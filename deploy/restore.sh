#!/bin/sh
# Restore a PharmaStock backup into a NEW (empty) database.
#
#   sh deploy/restore.sh backups/pharmastock-YYYYmmdd-HHMMSS.dump pharmastock_restored
#
# Needs a superuser connection (ADMIN_URL, default: local "postgres" database)
# because the tables use row level security. The database's owner role
# (pharmastock_app) must already exist. The target must not exist: this script
# never overwrites a database. To replace production, stop the application,
# restore into a new name, verify it (deploy/verify_backup.sh), then rename.
set -eu

dump="${1:?usage: restore.sh <file.dump> <new-database-name>}"
target="${2:?usage: restore.sh <file.dump> <new-database-name>}"
ADMIN_URL="${ADMIN_URL:-postgresql:///postgres}"

if [ -f "$dump.sha256" ]; then
    ( cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256" )
fi

exists=$(psql "$ADMIN_URL" -Atc "SELECT 1 FROM pg_database WHERE datname = '$target'")
if [ "$exists" = "1" ]; then
    echo "Database $target already exists; choose a new name." >&2
    exit 1
fi

owner="${DB_OWNER:-pharmastock_app}"
psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$target\" OWNER \"$owner\""
target_url=$(echo "$ADMIN_URL" | sed "s#/[^/?]*\(?\|\$\)#/$target\1#")
pg_restore --exit-on-error --no-password --dbname="$target_url" "$dump"
echo "Restored $dump into database $target"
