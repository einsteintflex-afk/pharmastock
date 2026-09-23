/* Organization profile and plan, platform administration (all
   organizations), and e-mail / SMS delivery with scheduled reports. */

import {
    api, apiPage, badge, bindPager, confirmModal, formatDate, formatDateTime, formModal, html, mount, number,
    onAction, pageHeader, pager, table, toast,
} from "../core.js";

const ORG_TYPES = [
    ["COMMUNITY_PHARMACY", "Community pharmacy"], ["PHARMACY_CHAIN", "Pharmacy chain"],
    ["HOSPITAL", "Hospital"], ["WHOLESALE", "Wholesale / distributor"],
];
const orgTypeLabel = value => (ORG_TYPES.find(([key]) => key === value) || [value, value])[1];

function limitText(value) { return value === null || value === undefined ? "Unlimited" : number(value); }

/* ---------- My organization ---------- */

export async function renderOrganization(ctx) {
    const [org, plans] = await Promise.all([api("/organization"), api("/plans")]);
    if (!ctx.isCurrent()) return;
    const canEdit = ctx.can("settings.manage");

    mount(ctx.main, html`
        ${pageHeader(org.name, `${orgTypeLabel(org.org_type)} · ${org.plan_label} plan`, canEdit
            ? html`<button type="button" class="refresh-btn" data-action="edit">Edit organization</button>` : "")}
        <section class="cards">
            <div class="card"><div><span>Plan</span><strong>${badge(org.plan)}</strong><small class="card-hint">Status ${org.status.toLowerCase()}${org.trial_ends_at ? ` · trial ends ${formatDate(org.trial_ends_at)}` : ""}</small></div></div>
            <div class="card"><div><span>Users</span><strong>${number(org.usage.active_users)} / ${limitText(org.limits.max_users)}</strong><small class="card-hint">active users / plan limit</small></div></div>
            <div class="card"><div><span>Locations</span><strong>${number(org.usage.active_locations)} / ${limitText(org.limits.max_locations)}</strong><small class="card-hint">active locations / plan limit</small></div></div>
            <div class="card"><div><span>Medicines</span><strong>${number(org.usage.medicines)}</strong><small class="card-hint">registered</small></div></div>
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Included features</h3><p>${plans.note}</p></div></div>
            ${table("org-features", [
                { label: "Feature", render: f => plans.feature_labels[f] || f },
                ...Object.entries(plans.plans).map(([key, plan]) => ({
                    label: plan.label, className: "center",
                    render: f => plan.features.includes(f) ? "✓" : "—",
                })),
                { label: "Your organization", className: "center", render: f => org.features.includes(f) ? html`<strong>✓</strong>` : "—" },
            ], Object.keys(plans.feature_labels))}
        </section>`);

    onAction(ctx.main, {
        edit: () => formModal({
            title: "Edit organization",
            fields: [
                { name: "name", label: "Organization name", required: true, maxlength: 150, value: org.name },
                { name: "org_type", label: "Type", type: "select", required: true, value: org.org_type,
                  options: ORG_TYPES.map(([value, label]) => ({ value, label })) },
            ],
            onSubmit: async v => {
                await api("/organization", { method: "PUT", body: v });
                toast("Organization updated.");
                ctx.reload();
            },
        }),
    });
}

/* ---------- Platform administration ---------- */

export async function renderPlatform(ctx) {
    const [rows, plans] = await Promise.all([api("/platform/organizations"), api("/plans")]);
    if (!ctx.isCurrent()) return;
    const planOptions = Object.entries(plans.plans).map(([value, p]) => ({ value, label: p.label }));
    const statusOptions = ["TRIAL", "ACTIVE", "SUSPENDED", "CANCELLED"].map(s => ({ value: s, label: s.toLowerCase() }));

    mount(ctx.main, html`
        ${pageHeader("Platform: Organizations", "Every tenant on this PharmaStock installation. Data of each organization is isolated by the database.",
            html`<button type="button" class="refresh-btn primary" data-action="create">+ New organization</button>`)}
        <section class="section">
            ${table("platform-orgs", [
                { label: "Organization", render: o => html`<strong>${o.name}</strong><br><small>#${o.id} · ${orgTypeLabel(o.org_type)}</small>` },
                { label: "Plan", render: o => badge(o.plan) },
                { label: "Status", render: o => badge(o.status) },
                { label: "Users", render: o => `${number(o.active_users)} / ${limitText(o.limits.max_users)}`, className: "num" },
                { label: "Locations limit", render: o => limitText(o.limits.max_locations), className: "num" },
                { label: "Last activity", render: o => formatDateTime(o.last_activity) },
                { label: "Billing ref", render: o => o.billing_customer_ref || "—" },
                { label: "", render: o => html`<button type="button" class="view-btn" data-action="edit" data-id="${o.id}">Manage</button>` },
            ], rows)}
            <p class="method">${plans.note}</p>
        </section>`);

    onAction(ctx.main, {
        create: () => formModal({
            title: "New organization",
            intro: "Creates the organization with default settings, its first location and an administrator who must change the password at first sign-in.",
            fields: [
                { name: "name", label: "Organization name", required: true, maxlength: 150 },
                { name: "org_type", label: "Type", type: "select", required: true, value: "COMMUNITY_PHARMACY",
                  options: ORG_TYPES.map(([value, label]) => ({ value, label })) },
                { name: "plan", label: "Plan", type: "select", required: true, value: "BASIC", options: planOptions },
                { name: "status", label: "Status", type: "select", required: true, value: "TRIAL", options: statusOptions },
                { name: "admin_username", label: "Administrator username", required: true, maxlength: 50, autocomplete: "off" },
                { name: "admin_full_name", label: "Administrator full name", required: true, maxlength: 150 },
                { name: "admin_password", label: "Temporary password", type: "password", required: true, autocomplete: "new-password",
                  help: "At least 10 characters with letters and digits; the administrator must change it." },
            ],
            wide: true,
            onSubmit: async v => {
                const created = await api("/platform/organizations", { method: "POST", body: v });
                toast(`Organization ${created.organization.name} created.`);
                ctx.reload();
            },
        }),
        edit: el => {
            const org = rows.find(o => o.id === Number(el.dataset.id));
            const overrides = org.limit_overrides || {};
            formModal({
                title: `Manage ${org.name}`,
                intro: "Suspending an organization signs all its users out and blocks sign-in. Limits left empty use the plan default.",
                fields: [
                    { name: "plan", label: "Plan", type: "select", required: true, value: org.plan, options: planOptions },
                    { name: "status", label: "Status", type: "select", required: true, value: org.status, options: statusOptions },
                    { name: "max_users", label: "User limit override", type: "number", min: 1, step: 1, value: overrides.max_users },
                    { name: "max_locations", label: "Location limit override", type: "number", min: 1, step: 1, value: overrides.max_locations },
                    { name: "extra_features", label: "Extra features (comma separated)", value: (overrides.extra_features || []).join(", "),
                      help: `Available: ${Object.keys(plans.feature_labels).join(", ")}` },
                    { name: "billing_customer_ref", label: "Billing customer reference", maxlength: 100, value: org.billing_customer_ref },
                ],
                wide: true,
                onSubmit: async v => {
                    const limits = {};
                    if (v.max_users) limits.max_users = v.max_users;
                    if (v.max_locations) limits.max_locations = v.max_locations;
                    const extra = (v.extra_features || "").split(",").map(x => x.trim()).filter(Boolean);
                    if (extra.length) limits.extra_features = extra;
                    if (overrides.disabled_features) limits.disabled_features = overrides.disabled_features;
                    await api(`/platform/organizations/${org.id}`, { method: "PUT", body: {
                        plan: v.plan, status: v.status, limits, billing_customer_ref: v.billing_customer_ref,
                    } });
                    toast("Organization updated.");
                    ctx.reload();
                },
            });
        },
    });
}

/* ---------- Delivery (outbox) and scheduled reports ---------- */

const FREQUENCIES = [["DAILY", "Daily"], ["WEEKLY", "Weekly"], ["MONTHLY", "Monthly"]];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function scheduleText(s) {
    const hour = `${String(s.hour).padStart(2, "0")}:00`;
    if (s.frequency === "WEEKLY") return `Every ${WEEKDAYS[s.day_of_week]} at ${hour}`;
    if (s.frequency === "MONTHLY") return `Monthly on day ${s.day_of_month} at ${hour}`;
    return `Daily at ${hour}`;
}

export async function renderDelivery(ctx) {
    const scheduling = ctx.hasFeature("scheduled_reports");
    const [first, schedules, reports] = await Promise.all([
        apiPage("/notification-deliveries", {}, 0, 50),
        scheduling ? api("/scheduled-reports") : Promise.resolve([]),
        scheduling ? api("/reports") : Promise.resolve([]),
    ]);
    if (!ctx.isCurrent()) return;
    const channels = first.body.channels;
    const reportName = key => reports.find(r => r.key === key)?.name || key;

    mount(ctx.main, html`
        ${pageHeader("E-mail, SMS & Scheduled Reports", "Outgoing messages are queued and sent in the background with retries",
            html`<button type="button" class="refresh-btn" data-action="test" data-channel="EMAIL">Send test e-mail</button>
                 <button type="button" class="refresh-btn" data-action="test" data-channel="SMS">Send test SMS</button>`)}
        <section class="cards">
            <div class="card"><div><span>E-mail</span><strong>${channels.email.configured ? "Configured" : "Not configured"}</strong>
                <small class="card-hint">${channels.email.configured ? "SMTP server set" : "Set SMTP_HOST / SMTP_FROM on the server"}</small></div></div>
            <div class="card"><div><span>SMS</span><strong>${channels.sms.configured ? "Configured" : "Not configured"}</strong>
                <small class="card-hint">provider: ${channels.sms.provider}</small></div></div>
        </section>
        ${scheduling ? html`
        <section class="section">
            <div class="section-header"><div><h3>Scheduled reports</h3><p>Reports e-mailed automatically (CSV, Excel or PDF)</p></div>
                <button type="button" class="refresh-btn primary" data-action="new-schedule">+ Schedule a report</button></div>
            ${table("schedules", [
                { label: "Name", render: s => html`<strong>${s.name}</strong><br><small>${reportName(s.report_key)} · ${s.format.toUpperCase()}</small>` },
                { label: "When", render: s => scheduleText(s) },
                { label: "Recipients", render: s => s.recipients.join(", ") },
                { label: "Next run", render: s => s.is_active ? formatDateTime(s.next_run_at) : badge("Inactive") },
                { label: "Last run", render: s => html`${formatDateTime(s.last_run_at)}<br><small>${s.last_status || ""}</small>` },
                { label: "", render: s => html`
                    <button type="button" class="view-btn" data-action="run" data-id="${s.id}">Send now</button>
                    <button type="button" class="view-btn" data-action="edit-schedule" data-id="${s.id}">Edit</button>
                    <button type="button" class="view-btn danger" data-action="delete-schedule" data-id="${s.id}">Delete</button>` },
            ], schedules, { empty: "No scheduled reports." })}
        </section>` : html`<section class="section"><div class="loading">Scheduled reports are not included in your plan.</div></section>`}
        <section class="section">
            <div class="section-header"><div><h3>Outbox</h3><p>Recent e-mail and SMS messages</p></div>
                <select id="dl-status" aria-label="Status"><option value="">All</option>
                    ${["PENDING", "SENT", "FAILED", "SKIPPED"].map(s => html`<option value="${s}">${s.toLowerCase()}</option>`)}</select></div>
            <div id="outbox"></div>
        </section>`);

    const outbox = ctx.main.querySelector("#outbox");
    const statusSelect = ctx.main.querySelector("#dl-status");
    const drawOutbox = (rows, total, page) => {
        mount(outbox, html`${table("outbox-table", [
            { label: "Created", render: d => formatDateTime(d.created_at) },
            { label: "Channel", render: d => d.channel },
            { label: "To", render: d => html`${d.recipient}<br><small>${d.user_name || ""}</small>` },
            { label: "Subject", render: d => html`${d.subject || "—"}${d.attachment_name ? html`<br><small>📎 ${d.attachment_name}</small>` : ""}` },
            { label: "Status", render: d => html`${badge(d.status)}<br><small>${d.attempts} attempt(s)</small>` },
            { label: "Detail", render: d => d.last_error || (d.sent_at ? `Sent ${formatDateTime(d.sent_at)}` : "") },
            { label: "", render: d => ["FAILED", "SKIPPED"].includes(d.status)
                ? html`<button type="button" class="view-btn" data-action="retry" data-id="${d.id}">Retry</button>` : "" },
        ], rows, { empty: "No messages yet." })}${pager("outbox-pager", total, page, 50)}`);
        bindPager(outbox, "outbox-pager", loadOutbox);
    };
    const loadOutbox = async (page = 0) => {
        const { rows, total } = await apiPage("/notification-deliveries", { status: statusSelect.value || null }, page, 50);
        if (ctx.isCurrent()) drawOutbox(rows, total, page);
    };
    statusSelect.addEventListener("change", () => loadOutbox(0));
    drawOutbox(first.rows, first.total, 0);

    const scheduleForm = existing => formModal({
        title: existing ? `Edit ${existing.name}` : "Schedule a report",
        intro: "Rolling period: for date-based reports, each run covers the last N days up to yesterday.",
        wide: true,
        fields: [
            { name: "name", label: "Name", required: true, maxlength: 150, value: existing?.name },
            { name: "report_key", label: "Report", type: "select", required: true, value: existing?.report_key,
              options: reports.map(r => ({ value: r.key, label: r.name })) },
            { name: "format", label: "Format", type: "select", required: true, value: existing?.format || "pdf",
              options: [["pdf", "PDF"], ["xlsx", "Excel"], ["csv", "CSV"]].map(([value, label]) => ({ value, label })) },
            { name: "frequency", label: "Frequency", type: "select", required: true, value: existing?.frequency || "WEEKLY",
              options: FREQUENCIES.map(([value, label]) => ({ value, label })) },
            { name: "day_of_week", label: "Day of week (weekly)", type: "select", value: existing?.day_of_week ?? 0,
              options: WEEKDAYS.map((label, value) => ({ value, label })) },
            { name: "day_of_month", label: "Day of month (monthly, 1-28)", type: "number", min: 1, max: 28, step: 1,
              value: existing?.day_of_month ?? 1 },
            { name: "hour", label: "Hour (0-23, server time)", type: "number", min: 0, max: 23, step: 1, required: true, value: existing?.hour ?? 7 },
            { name: "period_days", label: "Rolling period in days (optional)", type: "number", min: 1, max: 366, step: 1,
              value: existing?.filters?.period_days },
            { name: "recipients", label: "Recipients (comma separated e-mails)", required: true, full: true,
              value: existing?.recipients?.join(", ") },
            { name: "is_active", label: "Active", type: "checkbox", value: existing ? existing.is_active : true },
        ],
        onSubmit: async v => {
            const body = {
                name: v.name, report_key: v.report_key, format: v.format, frequency: v.frequency,
                day_of_week: v.frequency === "WEEKLY" ? Number(v.day_of_week) : null,
                day_of_month: v.frequency === "MONTHLY" ? v.day_of_month : null,
                hour: v.hour, is_active: v.is_active,
                recipients: v.recipients.split(/[,;\s]+/).map(x => x.trim()).filter(Boolean),
                filters: v.period_days ? { period_days: v.period_days } : {},
            };
            await api(existing ? `/scheduled-reports/${existing.id}` : "/scheduled-reports",
                { method: existing ? "PUT" : "POST", body });
            toast("Schedule saved.");
            ctx.reload();
        },
    });

    onAction(ctx.main, {
        test: async el => {
            try {
                await api("/notification-deliveries/test", { method: "POST", body: { channel: el.dataset.channel } });
                toast("Test message queued; it is sent within a minute.");
                loadOutbox(0);
            } catch (error) { toast(error.message, "error"); }
        },
        retry: async el => {
            await api(`/notification-deliveries/${el.dataset.id}/retry`, { method: "POST" });
            toast("Queued for another attempt.");
            loadOutbox(0);
        },
        "new-schedule": () => scheduleForm(null),
        "edit-schedule": el => scheduleForm(schedules.find(s => s.id === Number(el.dataset.id))),
        "delete-schedule": async el => {
            const s = schedules.find(x => x.id === Number(el.dataset.id));
            if (!await confirmModal("Delete schedule", `Stop sending "${s.name}"?`, "Delete")) return;
            await api(`/scheduled-reports/${s.id}`, { method: "DELETE" });
            toast("Schedule deleted.");
            ctx.reload();
        },
        run: async el => {
            const result = await api(`/scheduled-reports/${el.dataset.id}/run`, { method: "POST" });
            toast(result.status);
            ctx.reload();
        },
    });
}
