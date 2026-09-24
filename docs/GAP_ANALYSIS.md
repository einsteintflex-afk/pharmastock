# Gap Analysis — MedCart Tech Master Specification vs. `claude/bold-goldberg-p2dvu3`

Audit of commit `a3ae5a6` (24 Sept 2026), before any change for this specification.
Method: repository inspection, migration status, full test run (221 passed), and a
search of the code for each specified capability.

## 1. Already implemented (verified by tests)

Medicine master (generic / brand / route / manufacturer / GTIN / active), batches with
ACTIVE / QUARANTINED / RECALLED holds (release of a recall needs a manager), configurable
expiry engine used everywhere, expiry risk with value at risk and "cost not recorded",
FEFO with row locks and audited overrides, stock ledger + reconciliation with resolution
actions, dispensing counter (cart, FEFO, prescription / OTC, printable receipt, void),
purchasing (draft → ordered → partial → received / cancelled) and receiving with batch
creation / top-up, suppliers with history / receipts / activity / performance,
hierarchical locations incl. central store / ward / department, transfers and ward
requisitions (request → approve → FEFO dispatch → receive, location-scoped staff),
notifications (in-app) + e-mail / SMS outbox with retries, scheduled reports, 22 reports
in CSV / Excel / PDF, analytics (trends, stock-outs, locations, suppliers, turnover,
forecast with data sufficiency), AI assistant (Claude + fallback, read-only tools,
audited), GS1 barcode parser + lookup, multi-organization with forced RLS, plans with
limits and features enforced on the API, platform administration, sessions / devices,
password reset, lockout, rate limits, CSRF, CSP, audit trail, PWA, Docker + HTTPS,
backups with verification, CI workflow, documentation.

## 2. Partially implemented

| Area | Gap |
|---|---|
| Stock adjustment | ADJUSTMENT movement with free-text reason only; no reason codes, no adjustment record (previous / new quantity, device, request id), no approval thresholds |
| Purchasing | No SUBMITTED / APPROVED states or approval permission; no expected delivery date / overdue detection; no "create PO from reorder recommendations" |
| Receipt | Printable only; no discount, tax, logo, PDF, thermal layout, digital (link) receipt |
| Company branding | Name / address / phone settings only; no logo upload, email, website, registration, tax id, footers |
| Scan | Scan page + scan in counter / receiving / batch form; no Scan Center modes for stock count or transfer; Add Medicine has no scan-first flow |
| Reorder intelligence | No safety stock or reorder point shown; no one-click purchase order |
| Plans | Plan catalogue is code constants (overrides per org only); limits only for users / locations |
| Audit | Company audit complete; platform-owner actions recorded in the platform admin's own organization audit — no separate platform audit |
| Location scoping | Enforced for transfers only; inventory / dispensing not scoped to a user's location |
| Dashboard | Figures + tables; no quick actions, "what needs attention", daily brief, pending purchases, discrepancy KPI |
| Roles | 6 roles; no Owner, Inventory Officer, Purchasing Officer, Cashier, Auditor |
| AI | 19 tools; missing medicine history, supplier outstanding orders, today's priorities, unusual adjustments; tools not filtered by the user's permissions |
| Reports | Missing out-of-stock, fast-moving, stock counts, adjustments; PDF lacks company branding / logo |

## 3. Missing

Stock Count module; MFA (TOTP) and step-up authentication; privileged short platform
sessions; time-limited organization recovery access; platform audit and platform security
monitoring; subscription / billing abstraction with verified payment webhooks; messaging
credits; WhatsApp Business (Cloud API) channel, customer consent, message templates,
delivery-status webhook, provider message ids; provider-independent messaging gateway
with per-organization settings; onboarding wizard; global search; idempotency keys for
critical operations; secure file upload (logo); MedCart Tech brand architecture
("Powered by MedCart Tech"); grouped navigation; performance benchmark.

## 4. Broken / defective

No failing behaviour found by the test suite or the browser test. Design defects to fix:
- The documented production role *owns* the schema; least privilege needs a separate
  migration (owner) role and a non-owner application role with DML grants only.
- Tests run as the schema owner; the application path under a non-owner restricted role
  is not tested.
- `POST /purchase-orders/{id}/items` moves a DRAFT straight to ORDERED (no approval step).

## 5. Security gaps
No MFA; no step-up for high-risk platform actions; no separate platform audit; no
security-event log (authorization failures, cross-tenant probes) or monitoring view;
no idempotency on receiving / dispensing / transfers (double-submit risk); no upload
validation (no uploads yet); least-privilege DB role separation (above).

## 6. Database gaps
Tables needed: stock_counts, stock_count_lines, stock_adjustments, plans,
subscriptions, payments, messaging_credit_ledger, message_consents / customer contact on
dispensations, organization_files (logo), platform_audit, security_events,
idempotency_keys, mfa fields on users; columns: purchase order approval + expected date,
dispensation discount / tax / receipt token; extended role check.

## 7. UI/UX gaps
Flat 29-item navigation (no sections); buttons without icons / loading states in places;
no quick actions or attention panel; no onboarding; no global search; empty states
without actions on several pages; no skeleton loaders.

## 8. SaaS / subscription gaps
Plans not editable by the platform owner; no subscriptions, trials expiry handling,
payment history, verified activation, usage limits beyond users / locations.

## 9. WhatsApp / SMS / e-mail gaps
No WhatsApp; SMS / e-mail not behind a pluggable per-channel provider registry; no
customer receipt messages, consent, templates, per-organization channel settings,
provider message id or delivery-status tracking; no credits.

## 10. Platform-owner gaps
No MFA-protected privileged sessions, no platform audit, no security monitoring, no plan
catalogue management, no recovery access, no usage overview.

## 11. Scalability gaps
No measured benchmark; medicines / inventory pages load all rows (fine to thousands).

## 12. Production-readiness gaps
Role separation (above); SECRET_KEY for encryption of stored provider credentials;
benchmark evidence; documentation of external services.

## Implementation order
1. Data / architecture: migration 0013 (roles, idempotency, security events, platform
   audit, MFA, stock count / adjustment tables, purchasing approval, receipts,
   branding, plans / subscriptions / payments / credits, messaging) — additive only.
2. Security & platform owner: role separation, MFA + step-up, privileged sessions,
   platform audit, security monitoring, recovery access, idempotency.
3. Plans in the database, entitlements, limits, subscriptions, verified payments.
4. Medicine / barcode registration, Scan Center.
5. Stock count, adjustments with reasons and approval thresholds.
6. Receipts (discount / tax / logo / PDF / digital), location scoping for dispensing.
7. Purchasing approval, expected delivery / overdue, PO from reorder, price history.
8. Analytics: safety stock, reorder point, fast / dead movers, attention list, daily brief.
9. Reports: out-of-stock, fast-moving, counts, adjustments, branded PDFs.
10. Messaging gateway: providers (SMTP, SMS webhook, WhatsApp Cloud API), consent,
    templates, webhooks, credits.
11. AI tools and permission filtering.
12. UI: grouped navigation, dashboard command center, onboarding, global search,
    MedCart Tech brand architecture, states.
13. Security tests, benchmark, deployment update, regression, documentation.
