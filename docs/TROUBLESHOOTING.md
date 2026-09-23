# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Server exits: "Database migrations are pending" | Back up, then `python -m backend.migrate`. |
| Server exits in production: "superuser or has BYPASSRLS" | Connect as `pharmastock_app` (`deploy/create_app_role.sql`). In development this is only a warning. |
| `/ready` returns 503 | Database unreachable (check `DATABASE_URL`, PostgreSQL service) or migrations pending (listed in the response). |
| "Session expired" after every action on HTTP | `COOKIE_SECURE=true` needs HTTPS. Use HTTPS, or set `COOKIE_SECURE=false` only for local testing. |
| 403 "Missing request header (CSRF protection)" | A custom client uses the cookie without `X-Requested-With: PharmaStock`. Send the header or use a Bearer token. |
| 403 "Cross-origin request refused" | The request came from another web origin. Add it to `ALLOWED_ORIGINS` if intended. |
| 403 "not included in your organization's plan" | Feature outside the plan; a platform administrator can change the plan or add the feature. |
| 429 "Too many requests" | Rate limit; wait `Retry-After` seconds. Raise `RATE_LIMIT_*` for busy counters behind one IP. |
| 423 "Account temporarily locked" | Too many failed sign-ins; wait `LOCKOUT_MINUTES` or reset the password. |
| E-mails not arriving | E-mail & Schedules → Outbox shows SKIPPED (not configured) or FAILED with the SMTP error; fix settings and **Retry**. |
| Dispensing refused "Insufficient usable stock" | Only non-expired, ACTIVE batches count; check Expiry Alerts and batch hold status. |
| FEFO 409 when dispensing from a batch | An earlier-expiring batch exists; use it, or record a FEFO override reason (pharmacist). |
| Reconciliation shows mismatches | Someone changed quantities outside the app; resolve per batch (Trust ledger / Trust count). |
| pg_dump: "query would be affected by row-level security policy" | Back up with a superuser or `pharmastock_backup`, not the application role. |
| AI answers say "built-in" | No `ANTHROPIC_API_KEY`, or the API was unreachable (the answer then says so); figures are still from your data. |
| Barcode not recognised | Add the GTIN to the medicine (Medicines → Edit). Codes without batch / expiry still identify the medicine. |
| Camera button missing on Scan | The browser lacks BarcodeDetector (use Chrome/Edge on Android or a USB/Bluetooth scanner). |
| A user reports an error | Ask for the `request_id` shown / in `X-Request-ID`; search the logs for it. |
