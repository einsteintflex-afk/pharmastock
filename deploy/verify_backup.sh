#!/bin/sh
# Verify a backup end to end: restore into a throw-away database, check the
# migration history, row counts and that every batch reconciles with its
# stock ledger (per organization), then drop the copy.
#
#   ADMIN_URL=postgresql://postgres@localhost/postgres sh deploy/verify_backup.sh backups/<file>.dump
set -eu

dump="${1:?usage: verify_backup.sh <file.dump>}"
ADMIN_URL="${ADMIN_URL:-postgresql:///postgres}"
name="pharmastock_verify_$(date +%s)"
here=$(dirname "$0")

cleanup() { psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS \"$name\" WITH (FORCE)" >/dev/null 2>&1 || true; }
trap cleanup EXIT

sh "$here/restore.sh" "$dump" "$name" >/dev/null
url=$(echo "$ADMIN_URL" | sed "s#/[^/?]*\(?\|\$\)#/$name\1#")

echo "Migrations applied: $(psql "$url" -Atc "SELECT string_agg(version, ', ' ORDER BY version) FROM schema_migrations")"
psql "$url" -Atc "
    SELECT 'organizations: ' || COUNT(*) FROM organizations
    UNION ALL SELECT 'medicines: ' || COUNT(*) FROM medicines
    UNION ALL SELECT 'batches: ' || COUNT(*) FROM batches
    UNION ALL SELECT 'stock movements: ' || COUNT(*) FROM stock_movements
    UNION ALL SELECT 'users: ' || COUNT(*) FROM users
    UNION ALL SELECT 'audit entries: ' || COUNT(*) FROM audit_log"

mismatches=$(psql "$url" -Atc "
    SELECT COUNT(*) FROM batches b
    WHERE b.quantity <> COALESCE((SELECT SUM(CASE WHEN m.movement_type IN
            ('RECEIVED','RETURNED','ADJUSTMENT','TRANSFER_IN') THEN m.quantity ELSE -m.quantity END)
          FROM stock_movements m WHERE m.batch_id = b.id), 0)")
if [ "$mismatches" != "0" ]; then
    echo "FAILED: $mismatches batches do not match their stock ledger" >&2
    exit 1
fi
echo "Stock ledger reconciled for every batch."
echo "Backup verified: $dump"
