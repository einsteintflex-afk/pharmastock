# Backup and Recovery

## What to back up

The PostgreSQL database holds everything (data, users, settings, audit trail, outbox).
Configuration lives in the env file (store it in a password manager, not in git).
The application image can always be rebuilt from git.

## Taking backups

`deploy/backup.sh` writes `pharmastock-YYYYmmdd-HHMMSS.dump` (PostgreSQL custom format,
compressed), checks it is readable with `pg_restore --list`, writes a `.sha256` next to
it and deletes dumps older than `BACKUP_RETENTION_DAYS`.

The dump must be taken by a role that sees every organization — the superuser or the
read-only `pharmastock_backup` role. The application role cannot: row level security
is forced, and pg_dump stops with "query would be affected by row-level security policy".
(Verified: see the delivery report.)

- **Docker**: the `backup` service runs it automatically (default every 24 h).
  Manual: `docker compose exec backup sh /usr/local/bin/backup.sh`.
- **Windows / other**: `pg_dump -U postgres -d pharmastock -F c -f backups\pharmastock-DATE.dump`
  from Task Scheduler, or `sh deploy/backup.sh` with `DATABASE_URL` of a superuser.

Copy backups **off the server** daily (another disk / cloud storage). Keep at least
7 daily, 4 weekly and 12 monthly copies.

## Verifying backups (do this weekly)

```sh
ADMIN_URL=postgresql://postgres@localhost/postgres sh deploy/verify_backup.sh backups/pharmastock-….dump
```

It restores into a temporary database, prints the applied migrations and row counts,
checks that every batch reconciles with the stock ledger, and drops the copy. A backup
that has never been restored is not a backup.

## Restoring

```sh
# 1. restore into a NEW database (the script never overwrites one)
ADMIN_URL=postgresql://postgres@localhost/postgres sh deploy/restore.sh backups/pharmastock-….dump pharmastock_restored
# 2. verify it (row counts, reconciliation, sign in against it on staging)
# 3. switch: stop the app, rename databases, start the app
psql -U postgres -c "ALTER DATABASE pharmastock RENAME TO pharmastock_old"
psql -U postgres -c "ALTER DATABASE pharmastock_restored RENAME TO pharmastock"
```

The restore checks the `.sha256` first and refuses a corrupted file. Restoring requires a
superuser (row level security) and the owner role `pharmastock_app` to exist
(`DB_OWNER` overrides the name).

## Recovery objectives

With daily backups the maximum data loss is 24 hours. For less, lower
`BACKUP_INTERVAL_HOURS` or add PostgreSQL WAL archiving / a streaming replica (managed
PostgreSQL services provide point-in-time recovery).
