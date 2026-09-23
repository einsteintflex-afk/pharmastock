# Security

| Area | Implementation |
|---|---|
| Passwords | scrypt (n=2^14, r=8, p=1, per-user salt); ≥ 10 characters with letters and digits, not containing the username; temporary passwords must be changed at first sign-in |
| Sessions | 256-bit random tokens; only SHA-256 stored; expiry (`SESSION_HOURS`); revoked on sign-out, password change / reset, user deactivation, organization suspension; users can list and revoke their devices; admins can revoke a user's sessions |
| Brute force | Account lock after `MAX_FAILED_LOGINS` for `LOCKOUT_MINUTES`; per-IP sign-in / reset rate limit; per-session API rate limit (429 + Retry-After); constant-time dummy hash for unknown users |
| Password reset | One-time tokens (hash stored), 1 h self-service via e-mail (identical response whether the account exists), 24 h administrator links; previous tokens voided; all sessions revoked after reset |
| Authorization | Permission checked on every endpoint (116 endpoints; only sign-in, forgot and reset password are public); 6 roles; plan features checked on the API; platform administration separate |
| Tenant isolation | PostgreSQL row level security FORCED on every business table; organization set per request after authentication; connection state reset before reuse; users/sessions queries filter by organization explicitly; production refuses superuser / BYPASSRLS database roles; tests prove API, raw SQL and cross-tenant IDs are isolated |
| SQL injection | All queries parameterised (psycopg); dynamic SQL only from fixed whitelists (sort columns, report keys) |
| XSS | Every value rendered through the escaping `html```` template; CSP `script-src 'self'; style-src 'self'` (no inline script or style); no `innerHTML` with unescaped data |
| CSRF | Session cookie `HttpOnly; SameSite=Strict; Secure` (production); state-changing cookie requests must carry `X-Requested-With: PharmaStock`; cross-origin state-changing requests refused (Origin check) |
| Headers | CSP, X-Frame-Options DENY, frame-ancestors 'none', nosniff, Referrer-Policy no-referrer, Permissions-Policy (camera only for the app, for barcode scanning), HSTS in production, `Cache-Control: no-store` on API responses |
| Transport | HTTPS via Caddy (automatic certificates) or nginx; HTTP redirected |
| Audit | Append-only audit log (database trigger) of changes, sign-ins and failures, exports, report views, AI questions, transfers, reconciliations, platform actions |
| Secrets | Only in environment variables / env files (git-ignored); no secrets in the image; API keys never sent to the browser |
| Exports | CSV/Excel formula injection neutralised; exports need `reports.export` and are audited |
| Errors | No stack traces or SQL in responses; request id for correlation; optional webhook / Sentry |
| AI | Read-only tools over the same service functions; tool results treated as data (prompt-injection resistant system prompt); questions audited; no clinical decisions |

## Operational checklist for production

- `PHARMASTOCK_ENV=production`, HTTPS in front, `COOKIE_SECURE=true`.
- Application connects as `pharmastock_app` (startup refuses superuser / BYPASSRLS).
- Remove `BOOTSTRAP_ADMIN_*` after first sign-in.
- Set `METRICS_TOKEN`, an error webhook, and off-site backups.
- Review the audit trail and user list monthly; deactivate leavers immediately.
