# Known Limitations and Remaining Work

Stated plainly so nothing is assumed to work that was not verified.

## Verification limits (this delivery)

| Item | Status |
|---|---|
| PostgreSQL version | Tested on PostgreSQL 16. The existing installation runs 18; the SQL used (row level security, `pg_read_all_data`, generated defaults) exists since 14, but run the upgrade on a copy first (DEPLOYMENT.md B). |
| Windows | The existing Windows installation was not available here; the upgrade steps were tested on Linux. |
| Claude API | Tested live against the Claude API (model `claude-opus-5`) on the project data: FEFO / expiry, monthly summary + reorder and valuation questions answered correctly using the read-only tools; the automated tests use a mocked client so they cost nothing. |
| E-mail | Tested against a real SMTP server running locally (message and attachment verified), not a commercial provider. |
| SMS | Generic JSON webhook tested against a local HTTP gateway; no provider-specific adapter (Twilio, Africa's Talking, Hubtel) yet — a small adapter or a relay is needed. |
| Camera barcode scanning | Depends on the browser's BarcodeDetector (Chrome / Edge on Android, Chrome desktop); not testable headless. Keyboard / Bluetooth scanners and GS1 parsing are tested. |
| Docker | Image and compose stack built and run here (HTTPS, migrations, backups verified). Docker Hub was rate-limited, so base images came from the `mirror.gcr.io` mirror, and the sandbox needed its proxy CA for pip in a test-only copy of the Dockerfile; the committed Dockerfile is unchanged by this. |
| GitHub Actions | Workflow written and validated (YAML, compose config); it has not yet run on GitHub. |
| WhatsApp Business Platform | Cloud API request format, template parameters, `X-Hub-Signature-256` verification, delivery statuses and STOP handling are implemented to Meta's published contract and tested against a simulated API. **Not tested against Meta's live API**: this needs a verified business, a phone number and an approved template. |
| Paystack | Checkout initialisation and signed webhooks are implemented and tested with simulated signed events. **Not tested against Paystack's live or test API.** Manual (bank / mobile money) confirmation is fully tested. |
| Two-step verification | TOTP is verified against the RFC 6238 test vector and in the browser test with a computed code. The authenticator apps were not tried on a phone here. |
| Performance | Measured up to 1,000 organizations, one request at a time (docs/PERFORMANCE.md). Concurrent load was **not** measured; no user-count claim is made. |
| Least-privilege roles | The API test suite runs as a restricted role. The Docker init script creates owner / app / backup roles; the **full compose stack was not rebuilt after this change** (Docker Hub rate limits here). Converting an existing installation (DEPLOYMENT.md B) was not run on the real Windows database. |

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
- **Billing**:
  - Prices are set by MedCart Tech in the console; nothing is priced in code.
  - Ended trials and paid periods are only flagged. Organizations are not suspended
    automatically; MedCart Tech decides.
  - There are no automatic renewals or card-on-file charges: each period is a new
    checkout.
  - Only one online provider is implemented (Paystack).
- **Messaging**:
  - One credit is charged per WhatsApp or SMS message, whatever its length or destination.
    Adjust package prices to match real provider costs.
  - SMS goes through a generic JSON gateway. There are no delivery reports for SMS.
  - Business-initiated WhatsApp messages need templates approved by Meta before they can be
    sent.
- **API access** (`api_access` plan feature) is reserved. Bearer tokens work for every
  plan; per-plan API keys and quotas are not implemented.
- **Location scoping** restricts selling, receiving, adjusting and counting to a user's
  assigned location. Reading other locations' stock is still allowed, so staff can request
  transfers.
- **Stock counts** only adjust the batches that were counted. Uncounted batches are listed
  but left unchanged, unless you count them as 0.
- **Offline**: no offline sales or counts, by design (see MOBILE.md). Idempotency keys make
  retries after a network drop safe.
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
6. Live tests with Meta (WhatsApp templates) and Paystack (test mode) before selling
   messaging and online plans; load test on the production-size server.
7. Seasonality-aware forecasting once 12+ months of dispensing history exist.
8. Hospital clinical modules (ward MAR / unit dose) if required.
