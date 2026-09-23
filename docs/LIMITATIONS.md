# Known Limitations and Remaining Work

Stated plainly so nothing is assumed to work that was not verified.

## Verification limits (this delivery)

| Item | Status |
|---|---|
| PostgreSQL version | Tested on PostgreSQL 16. The existing installation runs 18; the SQL used (row level security, `pg_read_all_data`, generated defaults) exists since 14, but run the upgrade on a copy first (DEPLOYMENT.md B). |
| Windows | The existing Windows installation was not available here; the upgrade steps were tested on Linux. |
| Claude API | The tool loop is tested with a mocked client (no API key in this environment); the built-in engine is tested live. |
| E-mail | Tested against a real SMTP server running locally (message and attachment verified), not a commercial provider. |
| SMS | Generic JSON webhook tested against a local HTTP gateway; no provider-specific adapter (Twilio, Africa's Talking, Hubtel) yet — a small adapter or a relay is needed. |
| Camera barcode scanning | Depends on the browser's BarcodeDetector (Chrome / Edge on Android, Chrome desktop); not testable headless. Keyboard / Bluetooth scanners and GS1 parsing are tested. |
| Docker | Image and compose stack built and run here (HTTPS, migrations, backups verified). Docker Hub was rate-limited, so base images came from the `mirror.gcr.io` mirror, and the sandbox needed its proxy CA for pip in a test-only copy of the Dockerfile; the committed Dockerfile is unchanged by this. |
| GitHub Actions | Workflow written and validated (YAML, compose config); it has not yet run on GitHub. |

## Functional limitations

- **Transfers** are received all-or-nothing; a transit loss is recorded afterwards as an
  ADJUSTMENT / DAMAGED movement at the destination (there is no partial-receipt
  discrepancy step yet).
- **Hospital**: ward stock, requisitions and approvals are supported; patient-level ward
  administration (MAR), unit-dose and bedside dispensing are not.
- **Forecasting** is exponential smoothing of weekly dispensing: no seasonality, no
  unmet-demand estimation (stated with every forecast).
- **Stock-out history** treats quarantined/recalled stock as usable in the past (hold
  status is not historised).
- **Rate limits** are per application process; the reverse proxy provides the shared limit.
- **Offline**: the PWA caches the application shell only; stock transactions require a
  connection by design (see MOBILE.md).
- **Billing**: plans and limits are enforced, but there is no payment-provider integration
  (by design, no prices in code).
- **Time zone**: timestamps are server local time; run the server in the pharmacy's zone.
- **Medicines and inventory lists** load all rows and filter in the browser (fine for
  thousands of items); movements, audit trail, transfers and the outbox page on the server.
- **Reads are not audited** except report views, exports and AI questions.

## Remaining work (suggested order)

1. Run the upgrade on a copy of the production database on PostgreSQL 18 / Windows; then
   production (with backup).
2. Enable GitHub Actions and make the CI green on GitHub.
3. SMS provider adapter for the provider chosen (local gateway), and SMTP account.
4. Partial transfer receipt with discrepancy reasons.
5. Push notifications + refresh tokens, then the native mobile app (MOBILE.md).
6. Billing provider integration (subscriptions → organization plan / status).
7. Seasonality-aware forecasting once 12+ months of dispensing history exist.
8. Hospital clinical modules (ward MAR / unit dose) if required.
