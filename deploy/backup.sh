#!/bin/sh
# PharmaStock backup: compressed custom-format dump + SHA-256 checksum,
# checked with pg_restore --list, old dumps removed after the retention.
#
#   DATABASE_URL=postgresql://pharmastock_backup:...@host/pharmastock \
#   BACKUP_DIR=/var/backups/pharmastock sh deploy/backup.sh
#
# Use a role that can read every organization (superuser, or the read-only
# BYPASSRLS role created by deploy/postgres-init.sh): the application role is
# subject to row level security and cannot dump other organizations' rows.
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
mkdir -p "$BACKUP_DIR"

stamp=$(date +%Y%m%d-%H%M%S)
file="$BACKUP_DIR/pharmastock-$stamp.dump"
tmp="$file.partial"
trap 'rm -f "$tmp"' EXIT          # never leave a half-written dump behind

pg_dump --format=custom --compress=6 --no-password --file="$tmp" "$DATABASE_URL"
pg_restore --list "$tmp" > /dev/null          # the archive is readable
mv "$tmp" "$file"
( cd "$BACKUP_DIR" && sha256sum "$(basename "$file")" > "$(basename "$file").sha256" )

find "$BACKUP_DIR" -name 'pharmastock-*.dump*' -type f -mtime +"$RETENTION_DAYS" -delete
echo "Backup written: $file ($(du -h "$file" | cut -f1))"
