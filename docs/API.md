# PharmaStock API Reference

Generated from the application's route table by `scripts/gen_api_docs.py` (186 endpoints; the access column is read from the dependencies the server enforces). Interactive documentation with request and
response schemas is served at `/docs` in development (disabled in production).

## Conventions

- **Base URL**: every path works both at the root (`/medicines`) and under the versioned prefix
  `/api/v1` (`/api/v1/medicines`). Mobile and integration clients should use `/api/v1`.
- **Authentication**: `POST /auth/login` returns a session `token`. Send it as
  `Authorization: Bearer <token>` (mobile, integrations). The web app uses the httpOnly, SameSite=Strict
  session cookie set by the same call; cookie-authenticated state-changing requests must also send
  `X-Requested-With: PharmaStock` (CSRF guard). Requests from another web origin are refused.
- **Authorization**: each endpoint requires the permission shown below; the role → permission map is
  returned by `GET /roles`. Some endpoints also need a plan feature of the organization
  (`multi_location`, `advanced_analytics`, `ai_assistant`, `scheduled_reports`) and answer 403 otherwise.
- **Tenancy**: a user only ever sees their own organization's data (enforced by PostgreSQL row level
  security). IDs of other organizations' records return 404.
- **Pagination**: list endpoints accept `limit` and `offset` and return the total in the `X-Total-Count`
  header. Without `limit`, legacy list endpoints return all rows as before.
- **Errors**: JSON `{"detail": ...}`. 400 invalid request / business rule, 401 not signed in,
  403 not allowed, 404 not found, 409 conflict / duplicate, 422 validation (`errors` lists fields),
  423 account locked, 429 rate limited (`Retry-After`), 500 unexpected (response carries `request_id`;
  every response has an `X-Request-ID` header for support).
- **Two-step verification**: when the account has MFA, `/auth/login` answers `{"mfa_required": true,
  "challenge_token": …}` instead of a token; `POST /auth/mfa/verify` with the challenge and the TOTP (or a
  recovery code) returns the session. Platform endpoints need an MFA-verified session; high-risk ones answer
  403 `step_up_required…` until `POST /auth/mfa/step-up` confirms a fresh code.
- **Idempotency**: send `Idempotency-Key: <unique value>` on sales, purchase receipts, stock movements,
  adjustments, stock-count posting and transfer dispatch / receipt. A retry with the same key returns the
  original response (header `Idempotent-Replay: true`) and changes nothing; the same key with a different
  body answers 422.
- **Public, not listed below**: `GET /r/{token}` (digital receipt page), `POST /billing/webhooks/paystack`
  (HMAC-SHA512 signed), `GET|POST /messaging/webhooks/whatsapp` (verify token / `X-Hub-Signature-256`).
- **Dates**: ISO 8601 (`YYYY-MM-DD`, timestamps without time zone = server local time).

## System

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/` | public | Home |
| GET | `/health` | public | Health |
| GET | `/ready` | public | Readiness |

## Authentication

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/auth/login` | public | Login |
| POST | `/auth/mfa/verify` | public | Second step of sign-in |
| GET | `/auth/mfa` | signed in | Mfa status |
| POST | `/auth/mfa/setup` | signed in | Start (or restart) set-up |
| POST | `/auth/mfa/enable` | signed in | Mfa enable |
| POST | `/auth/mfa/recovery-codes` | signed in | Mfa new recovery codes |
| POST | `/auth/mfa/disable` | signed in | Mfa disable |
| POST | `/auth/mfa/step-up` | signed in | Re-confirm the second factor before a high-risk action |
| POST | `/auth/logout` | signed in | Logout |
| GET | `/auth/me` | signed in | Me |
| POST | `/auth/change-password` | signed in | Change password |
| GET | `/auth/sessions` | signed in | My sessions |
| DELETE | `/auth/sessions/{session_id}` | signed in | Revoke my session |
| POST | `/auth/logout-others` | signed in | Logout other sessions |
| POST | `/auth/forgot-password` | public | E-mail a one-time reset link to the user's address, if they have one |
| POST | `/auth/reset-password` | public | Reset password |

## Users

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/roles` | inventory.read | List roles |
| GET | `/users` | users.manage | List users |
| POST | `/users` | users.manage | Create user |
| PUT | `/users/{user_id}` | users.manage | Update user |
| POST | `/users/{user_id}/reset-password` | users.manage | Reset password |
| POST | `/users/{user_id}/reset-link` | users.manage | One-time link (24 hours) with which the user sets their own password; the administrator never learns it |
| GET | `/users/{user_id}/sessions` | users.manage | User sessions |
| POST | `/users/{user_id}/revoke-sessions` | users.manage | Revoke user sessions |
| POST | `/users/{user_id}/reset-mfa` | users.manage | For a user who lost their authenticator and recovery codes |

## Organization

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/organization` | signed in | My organization |
| PUT | `/organization` | settings.manage | Update my organization |
| GET | `/plans` | signed in | List plans |
| GET | `/platform/organizations` | platform admin + MFA session | Platform list |
| POST | `/platform/organizations` | platform admin + MFA + step-up | Platform create |
| PUT | `/platform/organizations/{organization_id}` | platform admin + MFA + step-up | Platform update |
| GET | `/onboarding` | signed in | Nine set-up steps, each checked against real data (nothing is ticked by clicking alone, except the optional re… |
| POST | `/onboarding/complete` | settings.manage | Onboarding complete |

## Platform

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/platform/audit` | platform admin + MFA session | Platform audit |
| GET | `/platform/security-events` | platform admin + MFA session | Platform security events |
| GET | `/platform/suspicious-adjustments` | platform admin + MFA session | Across organizations |
| GET | `/platform/organizations/{organization_id}/administrators` | platform admin + MFA session | Organization administrators |
| POST | `/platform/organizations/{organization_id}/recovery-access` | platform admin + MFA + step-up | Create recovery access |
| GET | `/platform/recovery-access` | platform admin + MFA session | List recovery access |
| DELETE | `/platform/recovery-access/{token_id}` | platform admin + MFA session | Revoke recovery access |
| GET | `/platform/plans` | platform admin + MFA session | Platform plans |
| PUT | `/platform/plans/{code}` | platform admin + MFA + step-up | Platform save plan |
| GET | `/platform/messaging-packages` | platform admin + MFA session | Platform packages |
| PUT | `/platform/messaging-packages/{code}` | platform admin + MFA + step-up | Platform save package |
| GET | `/platform/payments` | platform admin + MFA session | Platform payments |
| POST | `/platform/payments/{payment_id}/confirm` | platform admin + MFA + step-up | Manual payments only (bank transfer / mobile money received by MedCart Tech) |
| POST | `/platform/organizations/{organization_id}/credits` | platform admin + MFA + step-up | Platform grant credits |
| GET | `/platform/usage` | platform admin + MFA session | Per organization |

## Billing

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/billing` | billing.manage | Billing overview |
| GET | `/billing/credits` | billing.manage | Credit history |
| POST | `/billing/checkout` | billing.manage | Start checkout |

## Branding and receipts

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/branding` | signed in | Get branding |
| GET | `/branding/logo` | signed in | Get logo |
| PUT | `/branding/logo` | settings.manage | Body |
| DELETE | `/branding/logo` | settings.manage | Delete logo |
| GET | `/dispensations/{dispensation_id}/receipt.pdf` | inventory.read | Receipt pdf |
| GET | `/dispensations/{dispensation_id}/receipt-link` | inventory.read | Receipt link |

## Messaging

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/messaging` | communications.manage | Messaging settings |
| PUT | `/messaging/mode` | communications.manage | Explicit activation |
| PUT | `/messaging/providers/{channel}` | communications.manage | Save provider |
| DELETE | `/messaging/providers/{channel}` | communications.manage | Delete provider |
| POST | `/messaging/test` | communications.manage | Queue a test customer message through the organization's messaging mode (consent is recorded for the test numb… |
| GET | `/messaging/consents` | communications.manage | List consents |
| POST | `/messaging/consents` | stock.dispense | Record a customer's choice (e.g |

## Medicines

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/medicines` | inventory.read | Get medicines |
| POST | `/medicines` | medicines.write | Create medicine |
| PUT | `/medicines/{medicine_id}` | medicines.write | Update medicine |
| GET | `/medicines/{medicine_id}` | inventory.read | Medicine detail |

## Barcode

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/barcode/parse` | inventory.read | Parse code |
| POST | `/barcode/lookup` | inventory.read | Lookup |

## Inventory

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/inventory` | inventory.read | Get inventory |
| GET | `/expiry-alerts` | inventory.read | Batches with stock that are expired or inside a warning threshold |
| POST | `/batches` | batches.write | Register a batch directly (opening stock, donation, stock outside a purchase order) |
| GET | `/batches/{batch_id}` | inventory.read | Batch detail |
| POST | `/batches/{batch_id}/status` | stock.adjust | Quarantine, recall or release a batch |
| PUT | `/batches/{batch_id}` | batches.write | Correct batch details |
| GET | `/stock-alerts` | inventory.read | Per-medicine stock levels |
| GET | `/fefo/{medicine_id}` | inventory.read | Which batches FEFO would use to supply `quantity` units (no changes made) |
| POST | `/dispense` | stock.dispense | Dispense a quantity of a medicine using FEFO across batches |
| GET | `/stock-reconciliation` | inventory.read | Batches whose quantity does not match their movement history |
| POST | `/stock-reconciliation/{batch_id}/resolve` | stock.adjust | Resolve one mismatched batch (audited; see stock.resolve_mismatch) |

## Stock movements

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/stock-movements` | inventory.read | Create stock movement |
| GET | `/stock-movements` | inventory.read | Get stock movements |
| GET | `/stock-movements/types` | signed in | Movement types |

## Stock control

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/adjustments/reason-codes` | stock_count + None | Reason codes |
| GET | `/adjustments` | stock_count + None | List adjustments |
| POST | `/adjustments` | stock_count + None | Create adjustment |
| GET | `/adjustments/{adjustment_id}` | stock_count + None | Get adjustment |
| POST | `/adjustments/{adjustment_id}/decision` | stock_count + None | Decide adjustment |
| POST | `/adjustments/{adjustment_id}/cancel` | stock_count + None | Cancel adjustment |
| GET | `/stock-counts` | stock_count + None | List counts |
| POST | `/stock-counts` | stock_count + None | Create count |
| GET | `/stock-counts/{count_id}` | stock_count + None | Get count |
| POST | `/stock-counts/{count_id}/lines` | stock_count + None | Count line |
| DELETE | `/stock-counts/{count_id}/lines/{line_id}` | stock_count + None | Delete count line |
| POST | `/stock-counts/{count_id}/submit` | stock_count + None | Submit count |
| POST | `/stock-counts/{count_id}/reopen` | stock_count + None | Reopen count |
| POST | `/stock-counts/{count_id}/post` | stock_count + None | Post count |
| POST | `/stock-counts/{count_id}/cancel` | stock_count + None | Cancel count |

## Dispensing

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/dispensations` | stock.dispense | Create dispensation |
| GET | `/dispensations` | inventory.read | List dispensations |
| GET | `/dispensations/summary` | inventory.read | Dispensing summary |
| GET | `/dispensations/{dispensation_id}` | inventory.read | Get dispensation |
| POST | `/dispensations/{dispensation_id}/void` | dispensing.void | Void dispensation |

## Transfers

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/transfers` | multi_location + None | List transfers |
| POST | `/transfers` | multi_location + None | Create transfer |
| GET | `/transfers/{transfer_id}` | multi_location + None | Transfer detail |
| POST | `/transfers/{transfer_id}/approve` | multi_location + None | Approve transfer |
| POST | `/transfers/{transfer_id}/reject` | multi_location + None | Reject transfer |
| POST | `/transfers/{transfer_id}/cancel` | multi_location + None | Cancel transfer |
| POST | `/transfers/{transfer_id}/dispatch` | multi_location + None | Dispatch transfer |
| POST | `/transfers/{transfer_id}/receive` | multi_location + None | Receive transfer |

## Suppliers

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/suppliers` | suppliers.write | Create supplier |
| GET | `/suppliers` | inventory.read | Get suppliers |
| GET | `/suppliers/{supplier_id}` | inventory.read | Supplier detail |
| PUT | `/suppliers/{supplier_id}` | suppliers.write | Update supplier |
| POST | `/suppliers/{supplier_id}/activate` | suppliers.write | Activate supplier |
| POST | `/suppliers/{supplier_id}/deactivate` | suppliers.write | Inactive suppliers keep their history but cannot receive new orders |

## Purchasing

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/purchase-orders/next-number` | inventory.read | Suggest the next free order number in the PO-YYYY-NNN pattern |
| POST | `/purchase-orders` | purchasing.write | Create purchase order |
| GET | `/purchase-orders` | inventory.read | Get purchase orders |
| GET | `/purchase-orders/{purchase_order_id}` | inventory.read | Purchase order detail |
| PUT | `/purchase-orders/{purchase_order_id}` | purchasing.write | Update purchase order |
| POST | `/purchase-orders/{purchase_order_id}/cancel` | purchasing.write | Cancel an open order |
| POST | `/purchase-orders/{purchase_order_id}/submit` | purchasing.write | Submit purchase order |
| POST | `/purchase-orders/{purchase_order_id}/approve` | purchasing.approve | Approve purchase order |
| POST | `/purchase-orders/{purchase_order_id}/reject` | purchasing.approve | Back to DRAFT for changes, with the reason |
| POST | `/purchase-orders/{purchase_order_id}/mark-ordered` | purchasing.write | The approved order was sent to the supplier |
| POST | `/purchase-orders/from-reorder` | purchasing.write | Create a draft order from chosen reorder recommendations |
| GET | `/price-history` | inventory.read | Unit costs paid over time, per medicine and supplier (from purchase orders) |
| POST | `/purchase-orders/{purchase_order_id}/items` | purchasing.write | Add purchase order item |
| GET | `/purchase-orders/{purchase_order_id}/items` | inventory.read | Get purchase order items |
| PUT | `/purchase-orders/{purchase_order_id}/items/{item_id}` | purchasing.write | Update purchase order item |
| DELETE | `/purchase-orders/{purchase_order_id}/items/{item_id}` | purchasing.write | Delete purchase order item |
| POST | `/purchase-receipts` | purchasing.receive | Receive purchase order item |
| GET | `/purchase-receipts` | inventory.read | Get purchase receipts |

## Analytics

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/dashboard` | analytics.read | Dashboard |
| GET | `/slow-moving-products` | analytics.read | Slow moving products |
| GET | `/consumption` | analytics.read | Consumption |
| GET | `/analytics/consumption` | analytics.read | Consumption analysis |
| GET | `/analytics/reorder` | analytics.read | Reorder |
| GET | `/analytics/expiry-risk` | analytics.read | Expiry risk |
| GET | `/analytics/valuation` | analytics.read | Valuation |
| GET | `/analytics/forecast` | advanced_analytics + None | Forecast |
| GET | `/analytics/turnover` | advanced_analytics + None | Turnover |
| GET | `/analytics/purchasing` | advanced_analytics + None | Purchasing |
| GET | `/analytics/stock-trends` | analytics.read | Stock trends |
| GET | `/analytics/expiry-trends` | analytics.read | Expiry trends |
| GET | `/analytics/locations` | analytics.read | Location analytics |
| GET | `/analytics/stockouts` | advanced_analytics + None | Stockouts |
| GET | `/analytics/suppliers` | advanced_analytics + None | Supplier performance |
| GET | `/analytics/purchasing-trends` | advanced_analytics + None | Purchasing trends |
| GET | `/attention` | inventory.read | Attention |
| GET | `/daily-brief` | analytics.read | Daily brief |
| GET | `/analytics/movers` | analytics.read | Movers |
| GET | `/analytics/stockout-risk` | analytics.read | Stockout risk |
| GET | `/search` | inventory.read | Global search |

## Reports

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/reports` | analytics.read | List reports |
| GET | `/reports/{report_key}` | analytics.read | Run report |

## Notification delivery

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/me/notification-preferences` | notifications.read | Get preferences |
| PUT | `/me/notification-preferences` | notifications.read | Set preferences |
| GET | `/notification-deliveries` | settings.manage | List deliveries |
| POST | `/notification-deliveries/test` | settings.manage | Queue a test message to your own e-mail address / phone |
| POST | `/notification-deliveries/{delivery_id}/retry` | settings.manage | Retry delivery |
| GET | `/scheduled-reports` | scheduled_reports + None | List schedules |
| POST | `/scheduled-reports` | scheduled_reports + None | Create schedule |
| PUT | `/scheduled-reports/{schedule_id}` | scheduled_reports + None | Update schedule |
| DELETE | `/scheduled-reports/{schedule_id}` | scheduled_reports + None | Delete schedule |
| POST | `/scheduled-reports/{schedule_id}/run` | scheduled_reports + None | Run schedule now |

## Notifications

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/notifications` | notifications.read | List notifications |
| GET | `/notifications/unread-count` | notifications.read | Unread count |
| POST | `/notifications/{notification_id}/read` | notifications.read | Mark read |
| POST | `/notifications/read-all` | notifications.read | Mark all read |
| POST | `/notifications/refresh` | notifications.read | Re-check expiry and stock conditions now (also runs automatically) |

## Audit

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/audit-log` | audit.read | Audit log |

## Settings

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/settings` | inventory.read | Get settings |
| PUT | `/settings` | settings.manage | Update settings |
| GET | `/system/migrations` | settings.manage | Migrations |

## Locations

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/locations` | inventory.read | List locations |
| POST | `/locations` | locations.manage | Create location |
| PUT | `/locations/{location_id}` | locations.manage | Update location |

## Assistant

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/assistant/status` | ai_assistant + None | Assistant status |
| POST | `/assistant/ask` | ai_assistant + None | Ask |
