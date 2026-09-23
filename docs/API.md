# PharmaStock API Reference

Generated from the application's route table (116 endpoints). Interactive documentation with request and
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
- **Dates**: ISO 8601 (`YYYY-MM-DD`, timestamps without time zone = server local time).

## System

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/health` | public | Liveness: database reachable |
| GET | `/ready` | public | Readiness: database reachable and no pending migrations |
| GET | `/metrics` | `METRICS_TOKEN` bearer (404 in production without a token) | Prometheus metrics |
| GET | `/app/` | public | Web application |

## Authentication

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/auth/login` | public | Login |
| POST | `/auth/logout` | signed in | Logout |
| GET | `/auth/me` | signed in | Me |
| POST | `/auth/change-password` | signed in | Change password |
| GET | `/auth/sessions` | signed in | My sessions |
| DELETE | `/auth/sessions/{session_id}` | signed in | Revoke my session |
| POST | `/auth/logout-others` | signed in | Logout other sessions |
| POST | `/auth/forgot-password` | public | E-mail a one-time reset link to the user's address, if they have one. |
| POST | `/auth/reset-password` | public | Reset password |

## Users

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/roles` | inventory.read | List roles |
| GET | `/users` | users.manage | List users |
| POST | `/users` | users.manage | Create user |
| PUT | `/users/{user_id}` | users.manage | Update user |
| POST | `/users/{user_id}/reset-password` | users.manage | Reset password |
| POST | `/users/{user_id}/reset-link` | users.manage | One-time link (24 hours) with which the user sets their own password; |
| GET | `/users/{user_id}/sessions` | users.manage | User sessions |
| POST | `/users/{user_id}/revoke-sessions` | users.manage | Revoke user sessions |

## Organization

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/organization` | signed in | My organization |
| PUT | `/organization` | settings.manage | Update my organization |
| GET | `/plans` | signed in | List plans |
| GET | `/platform/organizations` | platform admin | Platform list |
| POST | `/platform/organizations` | platform admin | Platform create |
| PUT | `/platform/organizations/{organization_id}` | platform admin | Platform update |

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
| GET | `/expiry-alerts` | inventory.read | Batches with stock that are expired or inside a warning threshold. |
| POST | `/batches` | batches.write | Register a batch directly (opening stock, donation, stock outside a |
| GET | `/batches/{batch_id}` | inventory.read | Batch detail |
| POST | `/batches/{batch_id}/status` | stock.adjust | Quarantine, recall or release a batch. |
| PUT | `/batches/{batch_id}` | batches.write | Correct batch details. Quantity is never edited here: it changes only |
| GET | `/stock-alerts` | inventory.read | Per-medicine stock levels. |
| GET | `/fefo/{medicine_id}` | inventory.read | Which batches FEFO would use to supply `quantity` units (no changes made). |
| POST | `/dispense` | stock.dispense | Dispense a quantity of a medicine using FEFO across batches. |
| GET | `/stock-reconciliation` | inventory.read | Batches whose quantity does not match their movement history. Empty |
| POST | `/stock-reconciliation/{batch_id}/resolve` | stock.adjust | Resolve one mismatched batch (audited; see stock.resolve_mismatch). |

## Stock movements

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/stock-movements` | inventory.read | Create stock movement |
| GET | `/stock-movements` | inventory.read | Get stock movements |
| GET | `/stock-movements/types` | signed in | Movement types |

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
| GET | `/transfers` | multi_location + inventory.read | List transfers |
| POST | `/transfers` | multi_location + transfers.request | Create transfer |
| GET | `/transfers/{transfer_id}` | multi_location + inventory.read | Transfer detail |
| POST | `/transfers/{transfer_id}/approve` | multi_location + transfers.approve | Approve transfer |
| POST | `/transfers/{transfer_id}/reject` | multi_location + transfers.approve | Reject transfer |
| POST | `/transfers/{transfer_id}/cancel` | multi_location + transfers.request | Cancel transfer |
| POST | `/transfers/{transfer_id}/dispatch` | multi_location + transfers.dispatch | Dispatch transfer |
| POST | `/transfers/{transfer_id}/receive` | multi_location + transfers.receive | Receive transfer |

## Suppliers

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/suppliers` | suppliers.write | Create supplier |
| GET | `/suppliers` | inventory.read | Get suppliers |
| GET | `/suppliers/{supplier_id}` | inventory.read | Supplier detail |
| PUT | `/suppliers/{supplier_id}` | suppliers.write | Update supplier |
| POST | `/suppliers/{supplier_id}/activate` | suppliers.write | Activate supplier |
| POST | `/suppliers/{supplier_id}/deactivate` | suppliers.write | Inactive suppliers keep their history but cannot receive new orders. |

## Purchasing

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/purchase-orders/next-number` | inventory.read | Suggest the next free order number in the PO-YYYY-NNN pattern. |
| POST | `/purchase-orders` | purchasing.write | Create purchase order |
| GET | `/purchase-orders` | inventory.read | Get purchase orders |
| GET | `/purchase-orders/{purchase_order_id}` | inventory.read | Purchase order detail |
| PUT | `/purchase-orders/{purchase_order_id}` | purchasing.write | Update purchase order |
| POST | `/purchase-orders/{purchase_order_id}/cancel` | purchasing.write | Cancel an open order. Stock already received stays in inventory; the |
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
| GET | `/analytics/forecast` | advanced_analytics + analytics.read | Forecast |
| GET | `/analytics/turnover` | advanced_analytics + analytics.read | Turnover |
| GET | `/analytics/purchasing` | advanced_analytics + analytics.read | Purchasing |
| GET | `/analytics/stock-trends` | analytics.read | Stock trends |
| GET | `/analytics/expiry-trends` | analytics.read | Expiry trends |
| GET | `/analytics/locations` | analytics.read | Location analytics |
| GET | `/analytics/stockouts` | advanced_analytics + analytics.read | Stockouts |
| GET | `/analytics/suppliers` | advanced_analytics + analytics.read | Supplier performance |
| GET | `/analytics/purchasing-trends` | advanced_analytics + analytics.read | Purchasing trends |

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
| POST | `/notification-deliveries/test` | settings.manage | Queue a test message to your own e-mail address / phone. |
| POST | `/notification-deliveries/{delivery_id}/retry` | settings.manage | Retry delivery |
| GET | `/scheduled-reports` | scheduled_reports + settings.manage | List schedules |
| POST | `/scheduled-reports` | scheduled_reports + settings.manage | Create schedule |
| PUT | `/scheduled-reports/{schedule_id}` | scheduled_reports + settings.manage | Update schedule |
| DELETE | `/scheduled-reports/{schedule_id}` | scheduled_reports + settings.manage | Delete schedule |
| POST | `/scheduled-reports/{schedule_id}/run` | scheduled_reports + settings.manage | Run schedule now |

## Notifications

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/notifications` | notifications.read | List notifications |
| GET | `/notifications/unread-count` | notifications.read | Unread count |
| POST | `/notifications/{notification_id}/read` | notifications.read | Mark read |
| POST | `/notifications/read-all` | notifications.read | Mark all read |
| POST | `/notifications/refresh` | notifications.read | Re-check expiry and stock conditions now (also runs automatically). |

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
| GET | `/assistant/status` | ai_assistant + assistant.use | Assistant status |
| POST | `/assistant/ask` | ai_assistant + assistant.use | Ask |

