# Security

| Area | Implementation |
|---|---|
| Passwords | scrypt (n=2^14, r=8, p=1, per-user salt); ≥ 10 characters with letters and digits, not containing the username; temporary passwords must be changed at first sign-in |
| Two-step verification | TOTP (RFC 6238, verified against the RFC test vector) with any authenticator app; secret encrypted at rest (Fernet, key from `SECRET_KEY`); replay of a used code refused; 10 one-time recovery codes stored as SHA-256 hashes; sign-in challenge valid 5 minutes and 5 attempts; failed codes count towards account lockout; administrators can reset a user's MFA (audited); organizations can require MFA for owners / administrators |
| Platform owner (MedCart Tech) | Platform console only for platform administrators **in a session verified with two-step verification**; shorter sessions (`PLATFORM_SESSION_HOURS`, default 4); high-risk actions (create / change organization, plan catalogue, payments, credits, recovery access) need a step-up code within `STEP_UP_MINUTES` (default 10) |
| No backdoor | There is **no master password and no impersonation**. If an organization loses its administrator account, the platform issues a **recovery link**: single use, 1 hour, for one chosen administrator of that organization, with a mandatory reason; it resets MFA and lockout, is revocable, and is recorded in the platform audit **and** in the organization's own audit trail |
| Sessions | 256-bit random tokens; only SHA-256 stored; expiry; revoked on sign-out, password change / reset, MFA enable, user deactivation, organization suspension; device list per user |
| Brute force | Account lock after `MAX_FAILED_LOGINS` for `LOCKOUT_MINUTES` (password and MFA failures); per-IP sign-in rate limit; per-session API rate limit (429 + Retry-After); constant-time dummy hash for unknown users |
| Authorization | Permission checked on every endpoint; 11 roles (Owner, Administrator, Manager, Pharmacist, Pharmacy Technician, Storekeeper, Inventory Officer, Purchasing Officer, Cashier, Auditor, Viewer); only an Owner can grant or change the Owner role; plan features and limits enforced on the API; approvals (adjustments, stock counts, purchase orders, transfers) need a different person than the requester where configured |
| Tenant isolation | PostgreSQL row level security FORCED on every tenant table (including stock adjustments, counts, credits, messaging providers, consents, idempotency keys, files); organization set per request after authentication; connection reset before reuse; tests prove API, raw SQL and cross-tenant IDs are isolated |
| Database least privilege | Migrations run as the schema **owner** (`MIGRATION_DATABASE_URL`); the API runs as a separate **application role** with row privileges only: no superuser, no BYPASSRLS, cannot create / alter / drop / truncate, cannot write `schema_migrations`, and can only INSERT / SELECT the audit trails. The automated tests run the API as such a restricted role; production refuses a superuser / BYPASSRLS role |
| Audit | Company audit (`audit_log`) and a separate **platform audit** (`platform_audit`), both append-only (database triggers + no UPDATE / DELETE privilege). Secrets (passwords, tokens, keys, recovery codes) are removed from audit payloads at any depth |
| Security monitoring | `security_events`: failed sign-ins and codes, lockouts, refused access (403), unauthenticated use of a token, requests for record ids outside the caller's organization (possible cross-tenant probes), CSRF refusals, rate limiting, recovery access. Written on a separate connection so they survive rolled-back requests; platform view with summaries, top IPs, repeated probes and a cross-organization review of unusual stock adjustments |
| Idempotency | `Idempotency-Key` header on sales, purchase receipts, transfer dispatch / receipt, stock movements, adjustments and stock-count posting: a retried request returns the stored response and changes nothing; the same key with a different body is refused |
| Payments | Plans and credits activate **only** after a signature-verified provider webhook (Paystack HMAC-SHA512 of the raw body, exact amount and currency match, replay-safe) or a manual confirmation by a platform administrator with step-up; the browser redirect never activates anything; no payment secrets in code |
| Messaging | Official WhatsApp Business Platform (Cloud API) only; webhooks verified with `X-Hub-Signature-256`; organization provider credentials encrypted, masked in responses and never audited; customer consent recorded per number and channel and re-checked at sending; STOP replies opt out; organizations' own SMS / SMTP hosts must be public addresses (https for SMS) so the server cannot be used to reach internal networks (SSRF) |
| File upload (logo) | Size-limited (2 MB), declared type checked, opened with Pillow with a decompression-bomb limit, only PNG / JPEG / WebP accepted, re-encoded to a fresh PNG (drops metadata and appended payloads), stored in the database; corrupt files are refused (415) |
| Public receipt links | 128-bit random token, 90-day expiry, strict CSP (`default-src 'none'`), `noindex`, customer data escaped |
| SQL injection | All queries parameterised (psycopg); dynamic SQL only from fixed whitelists |
| XSS | Every value rendered through the escaping `html``` template; CSP `script-src 'self'; style-src 'self'` (no inline script or style attributes) |
| CSRF | Session cookie `HttpOnly; SameSite=Strict; Secure` (production); state-changing cookie requests must carry `X-Requested-With: PharmaStock`; cross-origin state-changing requests refused |
| Headers | CSP, X-Frame-Options DENY, frame-ancestors 'none', nosniff, Referrer-Policy no-referrer, Permissions-Policy (camera only for the app), HSTS in production, `Cache-Control: no-store` on API responses |
| Secrets | Only in environment variables / env files (git-ignored); `SECRET_KEY` required in production (startup refuses otherwise); API keys never sent to the browser |
| AI | Read-only tools; the assistant only receives the tools the user's role and plan allow; tool results treated as data; questions audited; no clinical decisions |

## Operational checklist for production

- `PHARMASTOCK_ENV=production`, HTTPS in front, `COOKIE_SECURE=true`, `SECRET_KEY` set (48+ random characters, backed up safely: without it stored MFA secrets and provider credentials cannot be decrypted).
- Three database roles: owner (migrations), application (restricted), backup (read-only, BYPASSRLS). See DEPLOYMENT.md.
- Every platform administrator turns on two-step verification before using the console.
- Turn on **Settings → Security → require two-step verification** for owners and administrators.
- Remove `BOOTSTRAP_ADMIN_*` after first sign-in.
- Set `METRICS_TOKEN`, an error webhook, and off-site backups.
- Review the security monitor weekly, the audit trails and user list monthly; deactivate leavers immediately.
