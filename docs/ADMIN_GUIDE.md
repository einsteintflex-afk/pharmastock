# Administrator Guide

## Roles

| Role | Typical person | Can |
|---|---|---|
| Administrator | Owner / IT | Everything, including users, audit trail and settings |
| Manager | Pharmacy manager | Everything except user accounts; settings, locations, audit, approvals |
| Pharmacist | Pharmacist | Dispense (incl. FEFO override, voids), medicines, suppliers, purchasing, adjustments, approve transfers |
| Pharmacy Technician | Dispensing staff | Dispense, receive deliveries, request and receive transfers |
| Storekeeper | Stores | Register batches, adjustments and write-offs, receive deliveries, request / dispatch / receive transfers |
| Viewer | Auditor, owner | Read-only |

The full matrix is on **Users → Role permissions** (the server enforces it).

## Users (Administration → Users)

- **Add User**: a temporary password is set; the user must change it at first sign-in.
  Optionally assign a **location** (e.g. a ward): the user can then only request,
  dispatch and receive transfers for that location.
- **Reset link**: a one-time link (24 h) that lets the user choose a password; e-mailed
  if they have an address. Prefer it over **Temporary password** (which you see).
- **Devices**: see where the user is signed in and sign them out everywhere.
- Deactivating a user signs them out; the last active administrator cannot be removed.

## Organization & plan

**Organization & Plan** shows the plan (Basic / Professional / Enterprise), usage against
limits (users, locations) and included features. Plans define capabilities, not prices.

## Settings

- Expiry thresholds (critical < urgent < approaching, days), slow-moving threshold,
  supplier lead time and reorder cover days, currency symbol, receipt header.
- *Transfers must be approved by someone other than the requester* (segregation of duties).
- **Locations**: pharmacy, central store, store, cold chain, ward, branch, department; a
  location holding stock cannot be deactivated.

## E-mail, SMS & scheduled reports

- Configure SMTP / SMS on the server (see DEPLOYMENT.md), then **Send test e-mail**.
- The **Outbox** shows every message: sent, pending (retrying), failed (after 5 attempts),
  skipped (channel not configured) — with the reason; **Retry** re-queues.
- **Scheduled reports**: any report, as PDF / Excel / CSV, daily / weekly / monthly, to up
  to 20 addresses; a rolling period (e.g. last 7 days) for date-based reports; **Send now**
  to test.
- Each user chooses e-mail / SMS notifications and the minimum severity under **My Account**.

## Hospitals

Create a **Central Store** and one location per **ward / department** (Settings →
Locations), assign ward staff to their ward, and use **Requisitions**: the ward requests,
a pharmacist or manager approves (quantities may be reduced), the store dispatches
(FEFO, batch numbers and expiry travel with the stock) and the ward receives.

## Platform administration (SaaS operator)

Users flagged as platform administrators (the first administrator of organization 1) see
**Platform**: create organizations (with their first administrator), change plan, status
(trial, active, suspended, cancelled), limit overrides and extra features, and a billing
customer reference. Suspension signs the organization out immediately.
CLI: `python -m backend.manage create-organization`.

## Routine tasks

| When | Task |
|---|---|
| Daily | Check notifications; write off expired stock; review the dispensing report |
| Weekly | Verify a backup (`deploy/verify_backup.sh`); review reorder recommendations; stock count of high-value items (ADJUSTMENT) |
| Monthly | Review users and the audit trail; run the expiry-loss and supplier reports; check **Reconciliation** is clean |

## Command line

```
python -m backend.manage create-admin | create-organization | reset-password
python -m backend.manage refresh-notifications | reconcile | process-deliveries
python -m backend.migrate status | (no argument = apply)
```
