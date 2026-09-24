# Administrator Guide

## Roles

| Role | Typical person | Can |
|---|---|---|
| Organization Owner | Business owner | Everything, including **billing** and the owner role itself |
| Administrator | IT / operations | Everything except billing; cannot create, change or reset an owner |
| Manager | Pharmacy manager | Everything except user accounts; settings, locations, audit, approvals |
| Pharmacist | Pharmacist | Dispense (incl. FEFO override, voids), medicines, suppliers, purchasing, adjustments, approve transfers |
| Pharmacy Technician | Dispensing staff | Dispense, receive deliveries, request and receive transfers |
| Storekeeper | Stores | Register batches, adjustments and write-offs, receive deliveries, request / dispatch / receive transfers |
| Inventory Officer | Stock controller | Medicines, batches, adjustments, stock counts, receiving, transfers |
| Purchasing Officer | Buyer | Suppliers, purchase orders, receiving |
| Cashier | Till | Dispensing counter only |
| Auditor | Internal / external auditor | Read-only plus the audit trail and exports (no AI assistant) |
| Viewer | Read-only staff | Read-only |

Approvals need `stock.approve` (adjustments, stock counts) and `purchasing.approve`
(purchase orders): Manager, Administrator, Owner. An approver can never approve their own request.

The full matrix is on **Users → Role permissions** (the server enforces it).

## Users (Administration → Users)

- **Add User**: a temporary password is set; the user must change it at first sign-in.
  Optionally assign a **location** (e.g. a ward): the user can then only request,
  dispatch and receive transfers for that location.
- **Reset link**: a one-time link (24 h) that lets the user choose a password; e-mailed
  if they have an address. Prefer it over **Temporary password** (which you see).
- **Devices**: see where the user is signed in and sign them out everywhere.
- Deactivating a user signs them out; the last active administrator cannot be removed.

- **Reset 2-step**: for a user who lost their phone and recovery codes (audited).

## First set-up (Set-up Guide)

A new organization's owner starts on the **Set-up Guide**: company profile, logo,
locations, team, suppliers, medicines, opening stock (or a stock count), receipts and
tax, two-step verification. Each step is ticked from real records; **Finish set-up**
removes the guide from the menu.

## Plan & Billing (owner)

Plan, trial / paid-until date, usage against limits (users, locations, medicines), plan
comparison, payments, and messaging credits. Paying starts a checkout; the plan or
credits change only when the payment provider (or MedCart Tech for a bank / mobile-money
transfer) confirms the payment.

## Settings (grouped)

- **Company details**: name, phone, e-mail, website, registration number, tax ID,
  address; **logo** upload (PNG / JPEG / WebP ≤ 2 MB).
- **Receipts and tax**: tax rate and name (e.g. VAT 15 %), batch numbers on receipts,
  receipt and PDF-report footers.
- **Expiry, stock and reorder rules**: expiry thresholds, slow-moving threshold, lead
  time, cover days, **safety stock days**, currency.
- **Controls and approvals**: adjustment approval thresholds (units / value; 0 = never),
  purchase order approval, separate approver for transfers.
- **Customer messages**: which receipts / thank-you messages go out (with consent).
- **Security**: require two-step verification for owners and administrators.
- **Locations**: pharmacy, central store, store, cold chain, ward, branch, department; a
  location holding stock cannot be deactivated.

## Messaging (WhatsApp, SMS, e-mail receipts)

Choose how customer messages are sent: **Off**, **Through MedCart Tech** (1 credit per
WhatsApp / SMS message; buy credits under Plan & Billing) or **Own accounts** (your
WhatsApp Business Cloud API number with approved templates, your SMS gateway and SMTP
server). Customers must agree at the counter; they can reply STOP on WhatsApp, and you
can record a choice under **Customer consent**. Every message and why it was or was not
sent is in **E-mail & Schedules → Outbox**.

## Stock control

- **Adjustments**: every manual change needs a reason code; adjustments above the
  thresholds wait for approval (Command Center → What needs attention).
- **Stock counts**: start a count per location, scan or type each item, submit; a
  manager reviews the differences (quantity and value) and posts them as adjustments.
- **Purchase approval** (if turned on): draft → submit → approve (another person) → mark
  sent → receive. Overdue deliveries are flagged from the expected delivery date.

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

## MedCart console (platform owner)

Platform administrators must turn on two-step verification; the console then opens in a
session signed in with a code (sessions last `PLATFORM_SESSION_HOURS`). High-risk actions
ask for a fresh code.

- **Organizations**: create (the first user becomes the Owner), plan, status, limit
  overrides, extra features. Suspension signs the organization out immediately.
- **Usage**: users / locations / medicines against limits, sales, messages, credits;
  grant or correct credits.
- **Plans**: edit features, limits and prices of each plan (applies within 30 seconds).
- **Payments & credits**: confirm manual payments (with the bank / MoMo reference);
  define credit packages and prices.
- **Security monitor**: failed sign-ins and codes, refused access, cross-organization
  probes, rate limits, top IPs; unusual stock adjustments per organization.
- **Platform audit**: every platform action (separate, append-only).
- **Recovery access**: when an organization is locked out of its administrator account,
  verify the requester through a trusted channel, then issue a one-time 1-hour link for
  one chosen administrator. There is no master password; the organization sees the
  recovery in its own audit trail.

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
