/* Administration: users, audit trail, settings & locations, my account. */

import {
    api, apiPage, badge, bindPager, busy, confirmModal, formModal, formatDateTime, html, loadingBlock, mount, onAction,
    openModal, pageHeader,
    pager, sortableTable, table, toast, today,
} from "../core.js";
import { userUpdated } from "../app.js";

/* ---------- Users ---------- */

export async function renderUsers(ctx) {
    const [users, roles, locations] = await Promise.all([api("/users"), api("/roles"), api("/locations")]);
    if (!ctx.isCurrent()) return;
    const roleOptions = roles.roles.map(r => ({ value: r.role, label: r.label }));
    const locationOptions = locations.filter(l => l.is_active).map(l => ({ value: l.id, label: l.name }));
    const locationName = id => locations.find(l => l.id === id)?.name;
    const locationField = value => ({ name: "location_id", label: "Assigned location (optional)", type: "select",
        placeholder: "— all locations —", value, options: locationOptions,
        help: "Staff assigned to a location can only request, dispatch and receive transfers for that location." });

    mount(ctx.main, html`
        ${pageHeader("Users", "Accounts and roles", html`<button type="button" class="refresh-btn primary" data-action="add">+ Add User</button>`)}
        <section class="section"><div id="u-table"></div></section>
        <section class="section">
            <div class="section-header"><div><h3>Role permissions</h3><p>Enforced by the server on every request</p></div></div>
            ${table("role-matrix", [
                { label: "Permission", render: p => html`<strong>${p}</strong><br><small>${roles.permissions[p]}</small>` },
                ...roles.roles.map(r => ({ label: r.label, render: p => r.permissions.includes(p) ? "✓" : "—", className: "center" })),
            ], Object.keys(roles.permissions))}
        </section>`);

    sortableTable(ctx.main.querySelector("#u-table"), "users-table", [
        { label: "Username", key: "username", render: u => html`<strong>${u.username}</strong>` },
        { label: "Name", key: "full_name" },
        { label: "Email", key: "email", render: u => u.email || "—" },
        { label: "Role", key: "role_label", render: u => html`${u.role_label}${u.location_id ? html`<br><small>${locationName(u.location_id)}</small>` : ""}` },
        { label: "Status", key: "is_active", render: u => html`${badge(u.is_active ? "Active" : "Inactive")}
            ${u.must_change_password ? html` <small>must change password</small>` : ""}
            ${u.locked_until && new Date(u.locked_until) > new Date() ? html` <small>locked</small>` : ""}` },
        { label: "2-step", key: "mfa_enabled", render: u => u.mfa_enabled ? badge("ON", "ACTIVE") : html`<small>off</small>` },
        { label: "Last sign-in", key: "last_login_at", render: u => formatDateTime(u.last_login_at) },
        { label: "", render: u => html`<button type="button" class="view-btn" data-action="edit" data-id="${u.id}">Edit</button>
            <button type="button" class="view-btn" data-action="reset-link" data-id="${u.id}">Reset link</button>
            <button type="button" class="view-btn" data-action="reset" data-id="${u.id}">Temporary password</button>
            <button type="button" class="view-btn" data-action="sessions" data-id="${u.id}">Devices</button>
            ${u.mfa_enabled && u.id !== ctx.user.id ? html`<button type="button" class="view-btn" data-action="reset-mfa" data-id="${u.id}">Reset 2-step</button>` : ""}` },
    ], users);

    const find = el => users.find(u => u.id === Number(el.dataset.id));
    onAction(ctx.main, {
        add: () => formModal({
            title: "Add User",
            intro: "The user must change this temporary password at first sign-in. Minimum 10 characters with letters and numbers.",
            fields: [
                { name: "username", label: "Username", required: true, maxlength: 50, help: "3–50 letters, numbers, . _ -", autocomplete: "off" },
                { name: "full_name", label: "Full name", required: true, maxlength: 150 },
                { name: "email", label: "Email", type: "email", maxlength: 150 },
                { name: "role", label: "Role", type: "select", required: true, value: "VIEWER", options: roleOptions },
                { name: "password", label: "Temporary password", type: "password", required: true, autocomplete: "new-password" },
                locationField(null),
            ],
            onSubmit: async v => {
                if (v.location_id !== null) v.location_id = Number(v.location_id);
                await api("/users", { method: "POST", body: v }); toast("User created."); ctx.reload();
            },
        }),
        edit: el => {
            const u = find(el);
            formModal({
                title: `Edit ${u.username}`,
                fields: [
                    { name: "full_name", label: "Full name", required: true, value: u.full_name },
                    { name: "email", label: "Email", type: "email", value: u.email },
                    { name: "role", label: "Role", type: "select", value: u.role, options: roleOptions },
                    { name: "is_active", label: "Active", type: "checkbox", value: u.is_active },
                    locationField(u.location_id),
                ],
                onSubmit: async v => {
                    if (v.location_id !== null) v.location_id = Number(v.location_id);
                    await api(`/users/${u.id}`, { method: "PUT", body: v }); toast("User updated."); ctx.reload();
                },
            });
        },
        reset: async el => {
            const u = find(el);
            if (!(await confirmModal("Reset password", `Generate a temporary password for ${u.username}? Their current sessions will be signed out.`, "Reset"))) return;
            try {
                const result = await api(`/users/${u.id}/reset-password`, { method: "POST", body: {} });
                openModal("Temporary password", html`
                    <p>Give this temporary password to <strong>${u.full_name}</strong>. It is shown only once; they must change it when they sign in.</p>
                    <p class="secret"><code>${result.temporary_password}</code></p>`);
            } catch (error) { toast(error.message, "error"); }
        },
        "reset-mfa": async el => {
            const u = find(el);
            if (!(await confirmModal("Reset two-step verification",
                `${u.full_name} lost their authenticator? Their two-step verification is switched off and they are signed out; they set it up again at next sign-in.`,
                "Reset"))) return;
            await busy(el, async () => {
                await api(`/users/${u.id}/reset-mfa`, { method: "POST" });
                toast("Two-step verification reset");
                ctx.reload();
            });
        },
        "reset-link": async el => {
            const u = find(el);
            try {
                const result = await api(`/users/${u.id}/reset-link`, { method: "POST", body: { send_email: true } });
                openModal("Password reset link", html`
                    <p>${result.emailed ? html`The link was e-mailed to <strong>${u.email}</strong>.` : "The user has no e-mail address: give them this link."}
                       It works once and expires ${formatDateTime(result.expires_at)}. You never see their new password.</p>
                    <p class="secret"><code>${result.link}</code></p>`);
            } catch (error) { toast(error.message, "error"); }
        },
        sessions: async el => {
            const u = find(el);
            const rows = await api(`/users/${u.id}/sessions`);
            const body = openModal(`Signed-in devices — ${u.username}`, html`
                ${table("user-sessions", [
                    { label: "Device", render: x => html`${x.client_name || "Web browser"}<br><small>${x.user_agent || ""}</small>` },
                    { label: "IP", render: x => x.ip_address || "—" },
                    { label: "Last active", render: x => formatDateTime(x.last_seen_at) },
                ], rows, { empty: "No active sessions." })}
                <div class="form-actions"><button type="button" class="refresh-btn danger" id="revoke-all" ${rows.length ? "" : html`disabled`}>Sign out everywhere</button></div>`, { wide: true });
            body.querySelector("#revoke-all").addEventListener("click", async () => {
                await api(`/users/${u.id}/revoke-sessions`, { method: "POST" });
                toast(`${u.username} was signed out of all devices.`);
                ctx.reload();
            });
        },
    });
}

/* ---------- Audit trail ---------- */

function describe(value) {
    if (!value) return "";
    return Object.entries(value).map(([k, v]) => `${k}: ${typeof v === "object" && v !== null ? JSON.stringify(v) : v}`).join("; ");
}

export async function renderAudit(ctx) {
    mount(ctx.main, html`
        ${pageHeader("Audit Trail", "Who changed what, and when (append-only)")}
        <section class="section">
            <div class="toolbar">
                <select id="a-entity" aria-label="Entity"><option value="">All entities</option>
                    ${["medicine", "batch", "dispensation", "supplier", "purchase_order", "transfer", "user", "location", "setting",
                       "organization", "report", "scheduled_report", "notification_delivery", "assistant"].map(e => html`<option>${e}</option>`)}</select>
                <input id="a-action" placeholder="Action (e.g. UPDATE, LOGIN_FAILED)" aria-label="Action">
                <label>From <input type="date" id="a-from" value="${today(-30)}"></label>
                <label>To <input type="date" id="a-to" value="${today()}"></label>
                <span class="toolbar-count" id="a-count"></span>
            </div>
            <div id="a-table"></div>
        </section>`);

    const size = 100;
    const load = async (page = 0) => {
        const { rows, total } = await apiPage("/audit-log", {
            entity_type: ctx.main.querySelector("#a-entity").value,
            action: ctx.main.querySelector("#a-action").value.trim().toUpperCase(),
            date_from: ctx.main.querySelector("#a-from").value,
            date_to: ctx.main.querySelector("#a-to").value,
        }, page, size);
        if (!ctx.isCurrent()) return;
        ctx.main.querySelector("#a-count").textContent = `${total.toLocaleString()} entries`;
        const container = ctx.main.querySelector("#a-table");
        sortableTable(container, "audit-table", [
            { label: "When", key: "occurred_at", render: r => formatDateTime(r.occurred_at) },
            { label: "User", key: "username", render: r => r.username || "system" },
            { label: "Action", key: "action", render: r => html`<strong>${r.action}</strong>` },
            { label: "Entity", key: "entity_type", render: r => `${r.entity_type}${r.entity_id ? ` #${r.entity_id}` : ""}` },
            { label: "Previous", render: r => html`<small>${describe(r.old_value)}</small>` },
            { label: "New", render: r => html`<small>${describe(r.new_value)}</small>` },
            { label: "IP", key: "ip_address", render: r => r.ip_address || "—" },
        ], rows, { empty: "No audit entries match.", afterRender: c => {
            c.insertAdjacentHTML("beforeend", String(pager("audit-pager", total, page, size)));
            bindPager(c, "audit-pager", load);
        } });
    };
    ctx.main.querySelectorAll(".toolbar select, .toolbar input").forEach(el => el.addEventListener("change", () => load(0)));
    await load(0);
}

/* ---------- Settings & locations ---------- */

const LOCATION_TYPES = ["PHARMACY", "CENTRAL_STORE", "STORE", "COLD_CHAIN", "WARD", "BRANCH", "DEPARTMENT"];

const num = (name, label, extra = {}) => ({ name, label, type: "number", step: 1, min: 0, required: true, ...extra });
const text = (name, label, maxlength, extra = {}) => ({ name, label, maxlength, emptyValue: "", ...extra });
const check = (name, label) => ({ name, label, type: "checkbox", full: true });

const SETTING_GROUPS = [
    { key: "company", icon: "🏥", title: "Company details", summary: "Shown on receipts, reports and customer messages",
      fields: [text("pharmacy.name", "Company / pharmacy name", 150, { required: true }), text("pharmacy.phone", "Phone", 50),
               text("company.email", "E-mail", 150, { type: "email" }), text("company.website", "Website", 150),
               text("company.registration_number", "Registration number", 60), text("company.tax_id", "Tax ID (TIN)", 60),
               text("pharmacy.address", "Address", 255, { full: true })] },
    { key: "receipts", icon: "🧾", title: "Receipts and tax", summary: "Receipt footer, tax on sales, report footer",
      fields: [num("receipt.tax_rate_percent", "Tax rate (%)", { step: "0.01", max: 100, help: "0 = no tax line" }),
               text("receipt.tax_label", "Tax name", 30, { required: true, help: "e.g. VAT, NHIL" }),
               check("receipt.show_batches", "Print batch numbers and expiry dates on receipts"),
               text("receipt.footer", "Receipt footer", 300, { full: true }),
               text("report.footer", "PDF report footer", 300, { full: true })] },
    { key: "stock", icon: "📦", title: "Expiry, stock and reorder rules", summary: "Changes apply immediately to statuses, alerts and reports",
      fields: [num("expiry.critical_days", "Critical: days to expiry", { min: 1 }), num("expiry.urgent_days", "Urgent: days to expiry", { min: 1 }),
               num("expiry.approaching_days", "Approaching: days to expiry", { min: 1 }),
               num("stock.slow_moving_units_90d", "Slow-moving: max units in 90 days"),
               num("reorder.lead_time_days", "Supplier lead time (days)"), num("reorder.cover_days", "Reorder covers (days)", { min: 1 }),
               num("reorder.safety_days", "Safety stock (days of use)"),
               text("currency.symbol", "Currency symbol", 5, { required: true })],
      validate: v => (v["expiry.critical_days"] < v["expiry.urgent_days"] && v["expiry.urgent_days"] < v["expiry.approaching_days"]
          ? null : "Thresholds must satisfy critical < urgent < approaching.") },
    { key: "controls", icon: "✅", title: "Controls and approvals", summary: "Who must approve large adjustments, orders and transfers",
      fields: [num("adjustments.approval_quantity_threshold", "Adjustments above this many units need approval", { help: "0 = never" }),
               num("adjustments.approval_value_threshold", "Adjustments worth more than this need approval", { step: "0.01", help: "0 = never" }),
               check("purchasing.approval_required", "Purchase orders must be submitted and approved (Professional plan and above)"),
               check("transfers.separate_approver", "Transfers must be approved by someone other than the requester")] },
    { key: "messages", icon: "💬", title: "Customer messages", summary: "What customers receive after a sale (with their consent)",
      intro: "Also choose how messages are sent under Messaging.",
      fields: [check("comms.whatsapp_receipts", "Send the receipt on WhatsApp"), check("comms.whatsapp_thank_you", "Send a thank-you on WhatsApp"),
               check("comms.sms_receipts", "Send the receipt link by SMS"), check("comms.email_receipts", "E-mail the receipt"),
               text("comms.thank_you_text", "Thank-you text", 300, { full: true })] },
    { key: "security", icon: "🔐", title: "Security", summary: "Sign-in protection for administrators",
      fields: [check("security.require_mfa_for_admins", "Owners and administrators must use two-step verification")] },
];

function displaySetting(field, value) {
    if (field.type === "checkbox") return value ? "Yes" : "No";
    if (value === "" || value === null || value === undefined) return "—";
    return String(value);
}

export async function renderSettings(ctx) {
    const [settings, locations] = await Promise.all([api("/settings"), api("/locations")]);
    if (!ctx.isCurrent()) return;
    const canManage = ctx.can("settings.manage");
    const canLocations = ctx.can("locations.manage");
    const value = key => settings.find(s => s.key === key)?.value;

    const brand = await api("/branding");
    mount(ctx.main, html`
        ${pageHeader("Settings", canManage ? "Organisation settings" : "Organisation settings (read-only for your role)")}
        <section class="section branding-panel">
            <div class="section-header"><div><h3>Company logo</h3><p>Printed on receipts, PDF reports and the digital receipt. PNG, JPEG or WebP, up to 2 MB.</p></div></div>
            <div class="logo-row">
                ${brand.has_logo ? html`<img class="logo-preview" src="/branding/logo?v=${brand.logo_version}" alt="Current logo">`
                    : html`<div class="logo-placeholder" aria-hidden="true">${(brand.name || "?").slice(0, 1)}</div>`}
                ${canManage ? html`<div class="top-actions">
                    <button type="button" class="refresh-btn primary" data-action="upload-logo">⬆ ${brand.has_logo ? "Replace" : "Upload"} logo</button>
                    ${brand.has_logo ? html`<button type="button" class="view-btn danger" data-action="remove-logo">Remove</button>` : ""}
                    <input type="file" id="logo-file" accept="image/png,image/jpeg,image/webp" hidden>
                </div>` : ""}
            </div>
        </section>
        <section class="settings-groups">
            ${SETTING_GROUPS.map(g => html`<div class="section setting-group">
                <div class="section-header"><div><h3>${g.icon} ${g.title}</h3><p>${g.summary}</p></div>
                    ${canManage ? html`<button type="button" class="refresh-btn" data-action="edit-group" data-group="${g.key}">✎ Edit</button>` : ""}</div>
                <dl class="summary">${g.fields.map(f => html`<div><dt>${f.label}</dt><dd>${displaySetting(f, value(f.name))}</dd></div>`)}</dl>
            </div>`)}
        </section>
        <section class="section">
            <details><summary>All settings and change history</summary>
            ${table("settings-table", [
                { label: "Setting", render: s => html`<strong>${s.description || s.key}</strong><br><small>${s.key}</small>` },
                { label: "Value", render: s => String(s.value) },
                { label: "Last changed", render: s => `${formatDateTime(s.updated_at)}${s.updated_by ? ` by ${s.updated_by}` : ""}` },
            ], settings)}
            </details>
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Stock locations</h3><p>Pharmacy, stores, cold chain, wards, branches and departments</p></div>
                ${canLocations ? html`<button type="button" class="refresh-btn primary" data-action="add-location">+ Add Location</button>` : ""}</div>
            ${table("locations-table", [
                { label: "Name", render: l => html`<strong>${l.name}</strong>` },
                { label: "Type", render: l => l.location_type.replace("_", " ") },
                { label: "Within", render: l => l.parent_name || "—" },
                { label: "Batches in stock", key: "batches_in_stock", className: "num" },
                { label: "Units", key: "units", className: "num" },
                { label: "Status", render: l => badge(l.is_active ? "Active" : "Inactive") },
                { label: "", render: l => canLocations ? html`<button type="button" class="view-btn" data-action="edit-location" data-id="${l.id}">Edit</button>` : "" },
            ], locations)}
        </section>`);

    const locationForm = loc => formModal({
        title: loc ? `Edit ${loc.name}` : "Add Location",
        fields: [
            { name: "name", label: "Name", required: true, maxlength: 150, value: loc?.name },
            { name: "location_type", label: "Type", type: "select", required: true, value: loc?.location_type || "STORE",
              options: LOCATION_TYPES.map(t => ({ value: t, label: t.replace("_", " ") })) },
            { name: "parent_id", label: "Within (optional)", type: "select", placeholder: "— none —", value: loc?.parent_id,
              options: locations.filter(l => l.id !== loc?.id).map(l => ({ value: l.id, label: l.name })) },
            { name: "is_active", label: "Active", type: "checkbox", value: loc ? loc.is_active : true },
        ],
        onSubmit: async v => {
            if (v.parent_id !== null) v.parent_id = Number(v.parent_id);
            await api(loc ? `/locations/${loc.id}` : "/locations", { method: loc ? "PUT" : "POST", body: v });
            toast("Location saved.");
            ctx.reload();
        },
    });

    onAction(ctx.main, {
        "edit-group": el => {
            const group = SETTING_GROUPS.find(g => g.key === el.dataset.group);
            const fields = group.fields.map(f => ({ ...f, value: value(f.name) }));
            formModal({
                title: group.title, fields, wide: fields.length > 6, intro: group.intro || "",
                validate: group.validate,
                onSubmit: async v => {
                    for (const f of group.fields) if (f.type === "number" && v[f.name] === null) v[f.name] = 0;
                    await api("/settings", { method: "PUT", body: v });
                    toast("Settings saved.");
                    location.reload();
                },
            });
        },
        "upload-logo": () => ctx.main.querySelector("#logo-file").click(),
        "remove-logo": button => busy(button, async () => {
            await api("/branding/logo", { method: "DELETE" });
            toast("Logo removed.");
            ctx.reload();
        }),
        "add-location": () => locationForm(null),
        "edit-location": el => locationForm(locations.find(l => l.id === Number(el.dataset.id))),
    });
    ctx.main.querySelector("#logo-file")?.addEventListener("change", async event => {
        const file = event.target.files[0];
        if (!file) return;
        if (file.size > 2 * 1024 * 1024) { toast("The logo must be 2 MB or smaller.", "error"); return; }
        try {
            await api("/branding/logo", { method: "PUT", rawBody: file, headers: { "Content-Type": file.type } });
            toast("Logo uploaded.");
            ctx.reload();
        } catch (error) { toast(error.message, "error"); }
    });
}

/* ---------- My account ---------- */

export async function renderAccount(ctx) {
    const u = ctx.user;
    mount(ctx.main, html`
        ${pageHeader("My Account", `${u.full_name} · ${u.role_label}`)}
        <section class="section">
            ${u.must_change_password ? html`<p class="warning-text" role="alert">You are using a temporary password. Choose a new password to continue.</p>` : ""}
            <form id="pw-form" class="form-grid narrow" novalidate>
                <div class="field full"><label for="pw-current">Current password</label>
                    <input id="pw-current" type="password" autocomplete="current-password" required></div>
                <div class="field full"><label for="pw-new">New password</label>
                    <input id="pw-new" type="password" autocomplete="new-password" required>
                    <small class="help">At least 10 characters, with letters and numbers, not containing your username.</small></div>
                <div class="field full"><label for="pw-repeat">Repeat new password</label>
                    <input id="pw-repeat" type="password" autocomplete="new-password" required></div>
                <div class="form-error full" role="alert" hidden></div>
                <div class="form-actions full"><button type="submit" class="refresh-btn primary">Change password</button></div>
            </form>
        </section>
        ${u.must_change_password ? "" : html`
        <section class="section" id="security">
            <div class="section-header"><div><h3>Two-step verification</h3>
                <p>A code from an authenticator app (Google Authenticator, Microsoft Authenticator, Authy…) in addition to your password.</p></div></div>
            <div id="mfa-box">${loadingBlock()}</div>
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Notifications by e-mail / SMS</h3>
                <p>In-app notifications are always on. Choose extra channels and the minimum severity.</p></div></div>
            <div id="prefs"></div>
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Signed-in devices</h3><p>Sign out any device you do not recognise</p></div>
                <button type="button" class="refresh-btn" id="logout-others">Sign out other devices</button></div>
            <div id="devices"></div>
        </section>`}
        <section class="section">
            <div class="section-header"><div><h3>Your permissions</h3><p>${u.organization?.name || ""}</p></div></div>
            <ul class="permission-list">${u.permissions.map(p => html`<li>${p}</li>`)}</ul>
        </section>`);
    if (!u.must_change_password) await Promise.all([renderPreferences(ctx), renderDevices(ctx), renderMfa(ctx)]);

    const form = ctx.main.querySelector("#pw-form");
    const errorBox = form.querySelector(".form-error");
    form.addEventListener("submit", async event => {
        event.preventDefault();
        errorBox.hidden = true;
        const current = form.querySelector("#pw-current").value;
        const next = form.querySelector("#pw-new").value;
        if (next !== form.querySelector("#pw-repeat").value) {
            errorBox.textContent = "The new passwords do not match.";
            errorBox.hidden = false;
            return;
        }
        try {
            await api("/auth/change-password", { method: "POST", body: { current_password: current, new_password: next } });
            toast("Password changed.");
            form.reset();
            const me = await api("/auth/me");
            userUpdated(me);
            if (u.must_change_password) ctx.navigate(me.permissions.includes("analytics.read") ? "dashboard" : "medicines");
        } catch (error) {
            errorBox.textContent = error.message;
            errorBox.hidden = false;
        }
    });
}


async function renderPreferences(ctx) {
    const prefs = await api("/me/notification-preferences");
    const box = ctx.main.querySelector("#prefs");
    if (!box) return;
    mount(box, html`
        <form class="form-grid" id="prefs-form" novalidate>
            <div class="field"><label for="p-email">E-mail address</label>
                <input id="p-email" name="email" type="email" maxlength="150" value="${prefs.email || ""}"></div>
            <div class="field"><label for="p-phone">Mobile number (SMS)</label>
                <input id="p-phone" name="phone" maxlength="50" value="${prefs.phone || ""}" placeholder="+233 …"></div>
            <div class="field checkbox"><label for="p-notify-email">Send notifications by e-mail</label>
                <input id="p-notify-email" name="notify_email" type="checkbox" ${prefs.notify_email ? html`checked` : ""}></div>
            <div class="field checkbox"><label for="p-notify-sms">Send notifications by SMS</label>
                <input id="p-notify-sms" name="notify_sms" type="checkbox" ${prefs.notify_sms ? html`checked` : ""}></div>
            <div class="field"><label for="p-severity">Minimum severity</label>
                <select id="p-severity" name="notify_min_severity">
                    ${[["CRITICAL", "Critical only (expired, critical expiry, out of stock)"], ["WARNING", "Warnings and critical"],
                       ["INFO", "Everything"]].map(([v, l]) => html`<option value="${v}" ${prefs.notify_min_severity === v ? html`selected` : ""}>${l}</option>`)}
                </select></div>
            <p class="help full">${prefs.channels.email.configured ? "" : "E-mail delivery is not configured on the server yet. "}
                ${prefs.channels.sms.configured ? "" : "SMS delivery is not configured on the server yet."}</p>
            <div class="form-error full" role="alert" hidden></div>
            <div class="form-actions full"><button type="submit" class="refresh-btn primary">Save preferences</button></div>
        </form>`);
    const form = box.querySelector("form");
    form.addEventListener("submit", async event => {
        event.preventDefault();
        const errorBox = form.querySelector(".form-error");
        errorBox.hidden = true;
        try {
            await api("/me/notification-preferences", { method: "PUT", body: {
                email: form.email.value.trim() || null, phone: form.phone.value.trim() || null,
                notify_email: form.notify_email.checked, notify_sms: form.notify_sms.checked,
                notify_min_severity: form.notify_min_severity.value,
            } });
            toast("Notification preferences saved.");
        } catch (error) {
            errorBox.textContent = error.message;
            errorBox.hidden = false;
        }
    });
}

async function renderDevices(ctx) {
    const rows = await api("/auth/sessions");
    const box = ctx.main.querySelector("#devices");
    if (!box) return;
    mount(box, table("my-sessions", [
        { label: "Device", render: x => html`<strong>${x.client_name || "Web browser"}</strong>${x.current ? html` ${badge("This device", "ACTIVE")}` : ""}
            <br><small>${x.user_agent || ""}</small>` },
        { label: "IP address", render: x => x.ip_address || "—" },
        { label: "Signed in", render: x => formatDateTime(x.created_at) },
        { label: "Last active", render: x => formatDateTime(x.last_seen_at) },
        { label: "", render: x => x.current ? "" : html`<button type="button" class="view-btn danger" data-session="${x.id}">Sign out</button>` },
    ], rows));
    box.querySelectorAll("[data-session]").forEach(button => button.addEventListener("click", async () => {
        await api(`/auth/sessions/${button.dataset.session}`, { method: "DELETE" });
        toast("Device signed out.");
        renderDevices(ctx);
    }));
    const others = ctx.main.querySelector("#logout-others");
    others.onclick = async () => {
        await api("/auth/logout-others", { method: "POST" });
        toast("All other devices were signed out.");
        renderDevices(ctx);
    };
}


/* ---------- Two-step verification ---------- */

function codesHtml(codes) {
    return html`<div class="recovery-codes">
        <p class="warning-text">Save these one-time recovery codes somewhere safe (not on this phone). Each works once if you lose your phone.
            They are shown only now.</p>
        <ol>${codes.map(c => html`<li><code>${c}</code></li>`)}</ol>
    </div>`;
}

async function renderMfa(ctx) {
    const status = await api("/auth/mfa");
    const box = ctx.main.querySelector("#mfa-box");
    if (!box) return;
    if (status.enabled) {
        mount(box, html`
            <p>${badge("ON", "ACTIVE")} since ${formatDateTime(status.enabled_at)} · ${status.recovery_codes_remaining} recovery code(s) left.
               ${status.required ? html`<br><small>Required for your role.</small>` : ""}</p>
            <div class="form-actions">
                <button type="button" class="refresh-btn" data-mfa="codes">New recovery codes</button>
                ${status.required ? "" : html`<button type="button" class="refresh-btn danger" data-mfa="disable">Turn off</button>`}
            </div>`);
    } else {
        mount(box, html`
            ${status.required ? html`<p class="warning-text" role="alert">Your role requires two-step verification. Set it up now.</p>` : ""}
            <p>${badge("OFF", "Inactive")} Your account is protected by your password only.</p>
            <button type="button" class="refresh-btn primary" data-mfa="setup">🔐 Set up two-step verification</button>`);
    }
    box.querySelector("[data-mfa=setup]")?.addEventListener("click", () => formModal({
        title: "Set up two-step verification", submitLabel: "Continue",
        fields: [{ name: "password", label: "Confirm your password", type: "password", required: true, autocomplete: "current-password" }],
        onSubmit: async v => {
            const setup = await api("/auth/mfa/setup", { method: "POST", body: v });
            setTimeout(() => showMfaEnable(ctx, setup), 0);
        },
    }));
    box.querySelector("[data-mfa=codes]")?.addEventListener("click", () => formModal({
        title: "New recovery codes", submitLabel: "Create new codes",
        intro: "Your old recovery codes stop working.",
        fields: [{ name: "code", label: "Code from your authenticator app", required: true, autocomplete: "one-time-code" }],
        onSubmit: async v => {
            const result = await api("/auth/mfa/recovery-codes", { method: "POST", body: v });
            setTimeout(() => openModal("Recovery codes", codesHtml(result.recovery_codes)), 0);
        },
    }));
    box.querySelector("[data-mfa=disable]")?.addEventListener("click", () => formModal({
        title: "Turn off two-step verification", submitLabel: "Turn off",
        fields: [{ name: "password", label: "Password", type: "password", required: true, autocomplete: "current-password" },
                 { name: "code", label: "Authenticator or recovery code", required: true, autocomplete: "one-time-code" }],
        onSubmit: async v => { await api("/auth/mfa/disable", { method: "POST", body: v }); toast("Two-step verification is off", "warning"); renderMfa(ctx); },
    }));
}

function showMfaEnable(ctx, setup) {
    const qr = `data:image/svg+xml;base64,${btoa(setup.qr_svg)}`;
    const body = openModal("Scan with your authenticator app", html`
        <div class="mfa-setup">
            <img class="qr" src="${qr}" alt="QR code for your authenticator app" width="220" height="220">
            <p>Can't scan? Enter this key: <code class="secret">${setup.secret.match(/.{1,4}/g).join(" ")}</code></p>
            <form id="mfa-enable" class="form-grid narrow" novalidate>
                <div class="field full"><label for="mfa-code">6-digit code from the app</label>
                    <input id="mfa-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" required></div>
                <div class="form-error full" role="alert" hidden></div>
                <div class="form-actions full"><button type="submit" class="refresh-btn primary">Turn on</button></div>
            </form>
        </div>`);
    const form = body.querySelector("#mfa-enable");
    form.addEventListener("submit", event => {
        event.preventDefault();
        busy(form.querySelector("button[type=submit]"), async () => {
            try {
                const result = await api("/auth/mfa/enable", { method: "POST", body: { code: form.querySelector("#mfa-code").value.trim() } });
                openModal("Two-step verification is on", codesHtml(result.recovery_codes));
                const me = await api("/auth/me");
                userUpdated(me);
                renderMfa(ctx);
            } catch (error) {
                const box = form.querySelector(".form-error");
                box.textContent = error.message;
                box.hidden = false;
            }
        });
    });
}
