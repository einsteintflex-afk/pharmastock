# Mobile Readiness and Architecture

## Available now

- **Responsive, installable web app (PWA)**: drawer navigation, touch-sized controls,
  tables that scroll inside their card, web app manifest and icons, a service worker that
  caches the application shell (API data is never cached). Works on Android and iOS
  browsers; "Add to home screen" installs it.
- **Barcode scanning**: keyboard-wedge / Bluetooth scanners everywhere a scan field is
  shown; camera scanning where the browser supports BarcodeDetector.
- **Mobile-ready API**:
  - versioned base path `/api/v1/…`;
  - token authentication: `POST /api/v1/auth/login` with `client_name` (e.g.
    "Android – Ward 3 tablet") returns a Bearer token; devices appear under *My Account*
    and can be revoked remotely;
  - `limit` / `offset` pagination with `X-Total-Count` on list endpoints;
  - compact scan endpoint `POST /api/v1/barcode/lookup` (medicine, batch, FEFO batch,
    open orders, warnings in one call);
  - consistent JSON errors, rate limits with `Retry-After`, request ids;
  - `Idempotency-Key` on sales, receipts, adjustments, count posting and transfers, so a
    phone that loses the connection mid-request can safely retry;
  - Scan Center on the phone camera: look up, receive, count, sell, adjust, transfer.

## Planned native app (recommended architecture)

```
React Native (Expo) app
 ├── Auth: username/password → Bearer token in the OS secure store (Keychain/Keystore);
 │         biometric unlock of the stored token; sign-out revokes the session
 ├── Screens: counter (scan-first), receive delivery, stock count, requisition (ward),
 │            approvals inbox, expiry list, notifications
 ├── Scanner: expo-camera / ML Kit (DataMatrix, EAN, GS1-128) → /barcode/lookup
 ├── Data: TanStack Query cache; read-only offline view of last-synced stock
 └── Push: FCM/APNs token registered per session (see "server work" below)
                         │ HTTPS /api/v1 (same API as the web app)
                         ▼
                   PharmaStock API
```

Stock-changing actions stay **online-only** by design: FEFO allocation, stock levels and
approvals must be decided by the server at the moment of the transaction, so two devices
can never dispense the same last units. Offline mode is limited to reading and to
queueing *requests* (e.g. a stock count sheet) that the user confirms when back online.

### Server work needed for the native app

1. Push notifications: a `device_tokens` table and a push channel in the delivery
   outbox (the outbox already supports additional channels).
2. Refresh tokens (long-lived device sessions with short access tokens).
3. OpenAPI client generation from `/openapi.json` for typed API access.
