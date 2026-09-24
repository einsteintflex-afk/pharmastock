/* MedCart Tech console (platform owner): organizations, plans, payments,
   usage, security monitoring, platform audit, recovery access. Requires a
   platform administrator signed in with two-step verification; high-risk
   actions ask for a fresh code (step-up). */

import {
    api, badge, busy, emptyState, formatDateTime, formModal, html, money, mount, number, onAction, pageHeader, table,
    toast,
} from "../core.js";
import { renderPlatform as renderOrganizations } from "./organization.js";

const TABS = [
    ["organizations", "Organizations"], ["usage", "Usage"], ["plans", "Plans"], ["payments", "Payments & credits"],
    ["security", "Security monitor"], ["audit", "Platform audit"], ["recovery", "Recovery access"],
];

export async function render(ctx) {
    if (!ctx.user.platform_access) {
        mount(ctx.main, html`${pageHeader("MedCart Console", "Platform administration")}
            <section class="section"><div class="empty-state"><span aria-hidden="true">🔐</span>
                <p>${ctx.user.mfa_enabled
                    ? "Sign out and sign in again with your authenticator code to open the platform console."
                    : "The platform console requires two-step verification. Set it up first."}</p>
                <a class="refresh-btn primary" href="#/account">${ctx.user.mfa_enabled ? "My account" : "Set up two-step verification"}</a>
            </div></section>`);
        return;
    }
    const tab = TABS.find(([key]) => key === ctx.params.query.tab)?.[0] || "organizations";
    mount(ctx.main, html`
        ${pageHeader("MedCart Console", "Platform administration · every action is recorded in the platform audit")}
        <nav class="mode-tabs" aria-label="Console sections">
            ${TABS.map(([key, label]) => html`<a class="mode-tab ${key === tab ? "active" : ""}" href="#/platform?tab=${key}"
                ${key === tab ? html`aria-current="page"` : ""}>${label}</a>`)}
        </nav>
        <div id="platform-body"></div>`);
    const body = ctx.main.querySelector("#platform-body");
    const sub = { ...ctx, main: body };
    await ({ organizations: renderOrganizations, usage: renderUsage, plans: renderPlans, payments: renderPayments,
             security: renderSecurity, audit: renderAudit, recovery: renderRecovery })[tab](sub);
}

async function renderUsage(ctx) {
    const rows = await api("/platform/usage");
    const limit = (used, max) => `${number(used)} / ${max === null || max === undefined ? "∞" : number(max)}`;
    mount(ctx.main, html`<section class="section">
        <div class="section-header"><div><h3>Usage by organization</h3><p>Counts only; no stock or patient data leaves an organization</p></div></div>
        ${table("usage", [
            { label: "Organization", render: r => html`<strong>${r.name}</strong><br><small>#${r.organization_id}</small>` },
            { label: "Plan", render: r => badge(r.plan) },
            { label: "Status", render: r => html`${badge(r.status)} ${r.subscription_status ? badge(r.subscription_status) : ""}` },
            { label: "Users", className: "num", render: r => limit(r.usage.active_users, r.limits.max_users) },
            { label: "Locations", className: "num", render: r => limit(r.usage.active_locations, r.limits.max_locations) },
            { label: "Medicines", className: "num", render: r => limit(r.usage.medicines, r.limits.max_medicines) },
            { label: "Sales (30 d)", className: "num", render: r => number(r.usage.sales_30_days) },
            { label: "Messages (month)", className: "num", render: r => number(r.usage.messages_this_month) },
            { label: "Credits", className: "num", render: r => number(r.credit_balance) },
            { label: "Messaging", render: r => badge(r.messaging_mode) },
            { label: "", render: r => html`<button type="button" class="view-btn small" data-action="credits" data-id="${r.organization_id}" data-name="${r.name}">± Credits</button>` },
        ], rows)}
    </section>`);
    onAction(ctx.main, {
        credits: button => formModal({
            title: `Messaging credits: ${button.dataset.name}`, submitLabel: "Apply",
            fields: [{ name: "change", label: "Change (+ adds, − removes)", type: "number", step: 1, required: true },
                     { name: "reason", label: "Reason", type: "select", options: ["GRANT", "CORRECTION", "REFUND"].map(v => ({ value: v, label: v.toLowerCase() })) },
                     { name: "note", label: "Note", required: true, full: true }],
            onSubmit: async v => {
                const result = await api(`/platform/organizations/${button.dataset.id}/credits`, { method: "POST", body: v });
                toast(`Balance now ${result.balance}`);
                ctx.reload();
            },
        }),
    });
}

async function renderPlans(ctx) {
    const data = await api("/platform/plans");
    mount(ctx.main, html`<section class="section">
        <div class="section-header"><div><h3>Plan catalogue</h3><p>Capabilities, limits and prices. Leave a price empty to sell the plan only by agreement.</p></div>
            <button type="button" class="refresh-btn primary" data-action="edit" data-code="">＋ New plan</button></div>
        <div class="plan-cards">${data.plans.map(p => html`<div class="plan-card">
            <h4>${p.label} <small>${p.code}</small> ${p.is_active ? "" : badge("DISABLED")}</h4>
            <p>${p.description || ""}</p>
            <p class="price">${p.price_monthly !== null ? `${p.currency} ${number(p.price_monthly, 2)} / month` : "No online price"}</p>
            <p><small>${number(p.organizations)} organization(s)</small></p>
            <ul>${p.features.map(f => html`<li>${data.feature_labels[f] || f}</li>`)}</ul>
            <p><small>${Object.entries(p.limits).map(([k, v]) => `${data.limit_labels[k] || k}: ${v === null ? "∞" : v}`).join(" · ")}</small></p>
            <button type="button" class="refresh-btn" data-action="edit" data-code="${p.code}">✎ Edit</button>
        </div>`)}</div>
    </section>`);
    onAction(ctx.main, {
        edit: button => {
            const plan = data.plans.find(p => p.code === button.dataset.code) || { limits: {}, features: [], is_active: true, sort_order: 10 };
            const fields = [
                ...(plan.code ? [] : [{ name: "code", label: "Code (capitals)", required: true, maxlength: 30 }]),
                { name: "label", label: "Name", required: true, value: plan.label },
                { name: "description", label: "Description", type: "textarea", full: true, value: plan.description },
                ...Object.keys(data.limit_labels).map(k => ({ name: `limit:${k}`, label: `${data.limit_labels[k]} (empty = unlimited)`,
                    type: "number", min: 0, step: 1, value: plan.limits[k] })),
                ...Object.keys(data.feature_labels).map(f => ({ name: `feature:${f}`, label: data.feature_labels[f], type: "checkbox",
                    value: plan.features.includes(f) })),
                { name: "price_monthly", label: "Monthly price", type: "number", min: 0, step: "0.01", value: plan.price_monthly },
                { name: "price_annual", label: "Annual price", type: "number", min: 0, step: "0.01", value: plan.price_annual },
                { name: "currency", label: "Currency (ISO, e.g. GHS)", maxlength: 3, value: plan.currency },
                { name: "sort_order", label: "Order", type: "number", min: 0, step: 1, value: plan.sort_order },
                { name: "is_active", label: "Available", type: "checkbox", value: plan.is_active },
            ];
            formModal({
                title: plan.code ? `Edit ${plan.label}` : "New plan", wide: true, fields, submitLabel: "Save plan",
                intro: "Changes apply to every organization on this plan within 30 seconds. You will be asked for an authenticator code.",
                onSubmit: async v => {
                    const code = plan.code || (v.code || "").toUpperCase();
                    const body = {
                        label: v.label, description: v.description, price_monthly: v.price_monthly, price_annual: v.price_annual,
                        currency: v.currency ? v.currency.toUpperCase() : null, is_active: v.is_active, sort_order: v.sort_order ?? 0,
                        limits: Object.fromEntries(Object.keys(data.limit_labels).map(k => [k, v[`limit:${k}`]])),
                        features: Object.keys(data.feature_labels).filter(f => v[`feature:${f}`]),
                    };
                    await api(`/platform/plans/${encodeURIComponent(code)}`, { method: "PUT", body });
                    toast("Plan saved.");
                    ctx.reload();
                },
            });
        },
    });
}

async function renderPayments(ctx) {
    const [payments, packages] = await Promise.all([api("/platform/payments"), api("/platform/messaging-packages")]);
    mount(ctx.main, html`
        <section class="section">
            <div class="section-header"><div><h3>Payments</h3><p>Online payments are confirmed only by the provider's signed webhook; manual payments by you, here.</p></div></div>
            ${table("platform-payments", [
                { label: "Date", render: p => formatDateTime(p.created_at) },
                { label: "Organization", key: "organization_name" },
                { label: "For", render: p => p.purpose === "SUBSCRIPTION" ? `${p.plan_code} ${(p.billing_cycle || "").toLowerCase()}` : `credits ${p.package_code}` },
                { label: "Amount", className: "num", render: p => `${p.currency} ${number(p.amount, 2)}` },
                { label: "Provider", key: "provider" }, { label: "Reference", key: "reference" },
                { label: "Status", render: p => badge(p.status) },
                { label: "", render: p => p.provider === "manual" && p.status === "PENDING"
                    ? html`<button type="button" class="refresh-btn primary small" data-action="confirm" data-id="${p.id}">✓ Confirm received</button>` : "" },
            ], payments, { empty: "No payments yet." })}
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Messaging credit packages</h3><p>Prices you set; inactive packages are not offered</p></div>
                <button type="button" class="refresh-btn" data-action="package" data-code="">＋ New package</button></div>
            ${table("packages", [
                { label: "Code", key: "code" }, { label: "Name", key: "label" },
                { label: "Credits", className: "num", render: p => number(p.credits) },
                { label: "Price", className: "num", render: p => p.price === null ? "—" : `${p.currency} ${number(p.price, 2)}` },
                { label: "Status", render: p => badge(p.is_active ? "ACTIVE" : "DISABLED") },
                { label: "", render: p => html`<button type="button" class="view-btn small" data-action="package" data-code="${p.code}">Edit</button>` },
            ], packages, { empty: "No packages yet." })}
        </section>`);
    onAction(ctx.main, {
        confirm: button => formModal({
            title: "Confirm manual payment", submitLabel: "Confirm and activate",
            intro: "Only confirm after the money is in MedCart Tech's account.",
            fields: [{ name: "note", label: "Evidence (bank / MoMo reference)", required: true, full: true }],
            onSubmit: async v => {
                await api(`/platform/payments/${button.dataset.id}/confirm`, { method: "POST", body: v });
                toast("Payment confirmed and applied.");
                ctx.reload();
            },
        }),
        package: button => {
            const p = packages.find(x => x.code === button.dataset.code) || { is_active: false };
            formModal({
                title: p.code ? `Edit ${p.label}` : "New credit package", submitLabel: "Save",
                fields: [
                    ...(p.code ? [] : [{ name: "code", label: "Code", required: true, maxlength: 30 }]),
                    { name: "label", label: "Name", required: true, value: p.label },
                    { name: "credits", label: "Credits", type: "number", min: 1, step: 1, required: true, value: p.credits },
                    { name: "price", label: "Price", type: "number", min: 0, step: "0.01", value: p.price },
                    { name: "currency", label: "Currency (ISO)", maxlength: 3, value: p.currency },
                    { name: "is_active", label: "On sale", type: "checkbox", value: p.is_active },
                ],
                onSubmit: async v => {
                    await api(`/platform/messaging-packages/${encodeURIComponent(p.code || v.code)}`, { method: "PUT",
                        body: { label: v.label, credits: v.credits, price: v.price, currency: v.currency ? v.currency.toUpperCase() : null,
                                is_active: v.is_active } });
                    ctx.reload();
                },
            });
        },
    });
}

async function renderSecurity(ctx) {
    const hours = Number(ctx.params.query.hours || 24);
    const [events, adjustments] = await Promise.all([api("/platform/security-events", { params: { hours } }),
        api("/platform/suspicious-adjustments", { params: { days: 30 } })]);
    mount(ctx.main, html`
        <section class="section">
            <div class="section-header"><div><h3>Security events</h3><p>Failed sign-ins and codes, refused access, cross-organization probes, rate limits</p></div>
                <div class="toolbar">${[24, 168, 720].map(h => html`<a class="chip ${h === hours ? "active" : ""}" href="#/platform?tab=security&hours=${h}">${h === 24 ? "24 h" : h === 168 ? "7 days" : "30 days"}</a>`)}</div></div>
            <section class="cards">${events.summary.length ? events.summary.map(s => html`<div class="card"><div><span>${s.event_type.replaceAll("_", " ").toLowerCase()}</span><strong>${number(s.count)}</strong></div></div>`)
                : html`<p>No security events in this period.</p>`}</section>
            ${events.suspected_probes.length ? html`<h4>Users repeatedly asking for records outside their organization</h4>${table("probes", [
                { label: "User", render: p => html`${p.username}<br><small>${p.organization_name || ""}</small>` },
                { label: "Events", className: "num", render: p => number(p.count) },
            ], events.suspected_probes)}` : ""}
            ${events.top_ips.length ? html`<h4>Most active addresses</h4>${table("ips", [
                { label: "IP address", key: "ip_address" }, { label: "Events", className: "num", render: r => number(r.count) },
                { label: "Kinds", className: "num", render: r => number(r.kinds) },
            ], events.top_ips)}` : ""}
            <h4>Latest events</h4>
            ${table("events", [
                { label: "When", render: e => formatDateTime(e.occurred_at) }, { label: "Type", render: e => badge(e.event_type, "WARNING") },
                { label: "Organization", render: e => e.organization_name || "—" }, { label: "User", render: e => e.username || "—" },
                { label: "Request", render: e => html`<small>${e.method || ""} ${e.path || ""} ${e.status_code || ""}</small>` },
                { label: "IP", render: e => e.ip_address || "—" }, { label: "Detail", render: e => e.detail || "" },
            ], events.items, { empty: "Nothing recorded." })}
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Stock adjustments to review (30 days)</h3><p>Large (≥ 100 units), theft / loss and rejected adjustments per organization</p></div></div>
            ${table("platform-adjustments", [
                { label: "Organization", key: "organization_name" },
                { label: "Adjustments", className: "num", render: r => number(r.adjustments) },
                { label: "Large", className: "num", render: r => number(r.large_adjustments) },
                { label: "Theft / loss", className: "num", render: r => number(r.theft_loss) },
                { label: "Rejected", className: "num", render: r => number(r.rejected) },
            ], adjustments, { empty: "No adjustments in the last 30 days." })}
        </section>`);
}

async function renderAudit(ctx) {
    const data = await api("/platform/audit", { params: { limit: 200, action: ctx.params.query.action } });
    mount(ctx.main, html`<section class="section">
        <div class="section-header"><div><h3>Platform audit</h3><p>Append-only record of platform administrators' actions (separate from each organization's audit trail)</p></div></div>
        ${table("platform-audit", [
            { label: "When", render: a => formatDateTime(a.occurred_at) },
            { label: "Who", render: a => a.actor_username || "system" },
            { label: "Action", render: a => html`<strong>${a.action}</strong>` },
            { label: "Organization", render: a => a.organization_name || "—" },
            { label: "Details", render: a => html`<small>${a.details ? JSON.stringify(a.details) : ""}</small>` },
            { label: "Reason", render: a => a.reason || "" },
            { label: "IP", render: a => a.ip_address || "" },
        ], data.items, { empty: "No platform actions yet." })}
    </section>`);
}

async function renderRecovery(ctx) {
    const [links, orgs] = await Promise.all([api("/platform/recovery-access"), api("/platform/organizations")]);
    mount(ctx.main, html`<section class="section">
        <div class="section-header"><div><h3>Recovery access</h3>
            <p>When an organization loses access to its administrator account: a one-time link (1 hour) for ONE verified administrator.
               There is no master password and no impersonation; the organization sees the recovery in its own audit trail.</p></div>
            <button type="button" class="refresh-btn primary" data-action="issue">Issue recovery link</button></div>
        ${table("recovery", [
            { label: "Issued", render: l => formatDateTime(l.created_at) }, { label: "Organization", key: "organization_name" },
            { label: "Administrator", key: "username" }, { label: "By", render: l => l.issued_by || "—" },
            { label: "Expires", render: l => formatDateTime(l.expires_at) },
            { label: "Status", render: l => badge(l.used_at ? "RECEIVED" : l.revoked_at ? "CANCELLED" : new Date(l.expires_at) < new Date() ? "EXPIRED" : "PENDING") },
            { label: "", render: l => !l.used_at && !l.revoked_at && new Date(l.expires_at) > new Date()
                ? html`<button type="button" class="view-btn danger small" data-action="revoke" data-id="${l.id}">Revoke</button>` : "" },
        ], links, { empty: "No recovery links issued." })}
    </section>`);
    onAction(ctx.main, {
        revoke: button => busy(button, async () => { await api(`/platform/recovery-access/${button.dataset.id}`, { method: "DELETE" }); ctx.reload(); }),
        issue: () => formModal({
            title: "Issue recovery link", submitLabel: "Next",
            fields: [{ name: "organization_id", label: "Organization", type: "select", required: true, placeholder: "Select…",
                       options: orgs.map(o => ({ value: o.id, label: o.name })) }],
            onSubmit: async v => {
                const admins = await api(`/platform/organizations/${v.organization_id}/administrators`);
                setTimeout(() => formModal({
                    title: "Issue recovery link", submitLabel: "Issue link",
                    intro: "Verify the requester's identity through a trusted channel first. You will be asked for your authenticator code.",
                    fields: [{ name: "user_id", label: "Administrator", type: "select", required: true, placeholder: "Select…",
                               options: admins.filter(a => a.is_active).map(a => ({ value: a.id, label: `${a.full_name} (${a.username}, ${a.role.toLowerCase()})` })) },
                             { name: "reason", label: "Reason and how identity was verified", type: "textarea", required: true, full: true }],
                    onSubmit: async w => {
                        const result = await api(`/platform/organizations/${v.organization_id}/recovery-access`, { method: "POST",
                            body: { user_id: Number(w.user_id), reason: w.reason } });
                        setTimeout(() => formModal({ title: "Recovery link", fields: [], submitLabel: "Done",
                            intro: `${result.note} Link for ${result.username}: ${result.link}`, onSubmit: async () => ctx.reload() }), 0);
                    },
                }), 0);
            },
        }),
    });
}
