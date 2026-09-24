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
| Server exits in production: "SECRET_KEY is not set" | Set `SECRET_KEY` (48+ random characters) in the environment; keep a copy with the backups. |
| "Stored secret cannot be decrypted (SECRET_KEY changed?)" | `SECRET_KEY` differs from the one used when MFA / providers were set up. Restore the old key; otherwise reset users' MFA (Users → Reset 2-step) and re-enter provider credentials. |
| Lost authenticator phone | Sign in with a recovery code, then set MFA up again. No codes left: an administrator uses **Reset 2-step**; the owner / last administrator asks MedCart Tech for recovery access. |
| "Incorrect code" although the code is shown in the app | The phone's clock is wrong: turn on automatic time. Each code works once. |
| 403 "requires two-step verification for your role" | The organization requires MFA for owners / administrators: set it up under My account. |
| Platform console says to sign in with a code | Platform access needs a session signed in with MFA; sign out and in again. High-risk actions ask for a fresh code (step-up). |
| 422 "Idempotency-Key was already used for a different request" | The client reused a key for a different operation; generate a new key per user action. |
| WhatsApp messages SKIPPED | The outbox reason says why: messaging mode off, plan without WhatsApp, no consent / opted out, no credits, provider not set up, or no approved template. |
| WhatsApp webhook answers 401 | `WHATSAPP_APP_SECRET` (platform number) or the organization's app secret does not match the Meta app; the verify step needs `WHATSAPP_VERIFY_TOKEN`. |
| Plan did not change after paying | Activation waits for the provider's signed webhook (check the Paystack webhook URL and secret) or, for manual payments, confirmation in the MedCart console. |
| Logo refused (415) | Only PNG, JPEG or WebP images up to 2 MB that open correctly are accepted. |
| Adjustment "waiting for approval" | Above the thresholds in Settings → Controls; a manager approves it under Adjustments (not the requester). |
| Purchase order cannot be received | With approval turned on, the order must be approved (and ideally marked sent) first. |
| Migration error "permission denied" | Run migrations with `MIGRATION_DATABASE_URL` pointing at the schema owner, not the application role. |
