/* Administration: users, audit trail, settings & locations, my account. */

import {
    api, badge, confirmModal, formModal, formatDateTime, html, mount, onAction, openModal, pageHeader, sortableTable,
    table, toast, today,
} from "../core.js";
import { userUpdated } from "../app.js";

/* ---------- Users ---------- */

export async function renderUsers(ctx) {
    const [users, roles] = await Promise.all([api("/users"), api("/roles")]);
    if (!ctx.isCurrent()) return;
    const roleOptions = roles.roles.map(r => ({ value: r.role, label: r.label }));

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
        { label: "Role", key: "role_label" },
        { label: "Status", key: "is_active", render: u => html`${badge(u.is_active ? "Active" : "Inactive")}
            ${u.must_change_password ? html` <small>must change password</small>` : ""}
            ${u.locked_until && new Date(u.locked_until) > new Date() ? html` <small>locked</small>` : ""}` },
        { label: "Last sign-in", key: "last_login_at", render: u => formatDateTime(u.last_login_at) },
        { label: "", render: u => html`<button type="button" class="view-btn" data-action="edit" data-id="${u.id}">Edit</button>
            <button type="button" class="view-btn" data-action="reset" data-id="${u.id}">Reset password</button>` },
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
            ],
            onSubmit: async v => { await api("/users", { method: "POST", body: v }); toast("User created."); ctx.reload(); },
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
                ],
                onSubmit: async v => { await api(`/users/${u.id}`, { method: "PUT", body: v }); toast("User updated."); ctx.reload(); },
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
                    ${["medicine", "batch", "supplier", "purchase_order", "user", "location", "setting", "report", "assistant"].map(e => html`<option>${e}</option>`)}</select>
                <input id="a-action" placeholder="Action (e.g. UPDATE, LOGIN_FAILED)" aria-label="Action">
                <label>From <input type="date" id="a-from" value="${today(-30)}"></label>
                <label>To <input type="date" id="a-to" value="${today()}"></label>
                <span class="toolbar-count" id="a-count"></span>
            </div>
            <div id="a-table"></div>
        </section>`);

    const load = async () => {
        const rows = await api("/audit-log", { params: {
            entity_type: ctx.main.querySelector("#a-entity").value,
            action: ctx.main.querySelector("#a-action").value.trim().toUpperCase(),
            date_from: ctx.main.querySelector("#a-from").value,
            date_to: ctx.main.querySelector("#a-to").value,
            limit: 1000,
        } });
        ctx.main.querySelector("#a-count").textContent = `${rows.length} entries`;
        sortableTable(ctx.main.querySelector("#a-table"), "audit-table", [
            { label: "When", key: "occurred_at", render: r => formatDateTime(r.occurred_at) },
            { label: "User", key: "username", render: r => r.username || "system" },
            { label: "Action", key: "action", render: r => html`<strong>${r.action}</strong>` },
            { label: "Entity", key: "entity_type", render: r => `${r.entity_type}${r.entity_id ? ` #${r.entity_id}` : ""}` },
            { label: "Previous", render: r => html`<small>${describe(r.old_value)}</small>` },
            { label: "New", render: r => html`<small>${describe(r.new_value)}</small>` },
            { label: "IP", key: "ip_address", render: r => r.ip_address || "—" },
        ], rows, { empty: "No audit entries match." });
    };
    ctx.main.querySelectorAll(".toolbar select, .toolbar input").forEach(el => el.addEventListener("change", load));
    await load();
}

/* ---------- Settings & locations ---------- */

const LOCATION_TYPES = ["PHARMACY", "STORE", "COLD_CHAIN", "WARD", "BRANCH", "DEPARTMENT"];

export async function renderSettings(ctx) {
    const [settings, locations] = await Promise.all([api("/settings"), api("/locations")]);
    if (!ctx.isCurrent()) return;
    const canManage = ctx.can("settings.manage");
    const canLocations = ctx.can("locations.manage");
    const value = key => settings.find(s => s.key === key)?.value;

    mount(ctx.main, html`
        ${pageHeader("Settings", canManage ? "Organisation settings" : "Organisation settings (read-only for your role)")}
        <section class="section">
            <div class="section-header"><div><h3>Expiry, stock and reorder rules</h3><p>Changes apply immediately to statuses, alerts and reports</p></div>
                ${canManage ? html`<button type="button" class="refresh-btn primary" data-action="edit-settings">Edit</button>` : ""}</div>
            ${table("settings-table", [
                { label: "Setting", render: s => html`<strong>${s.description || s.key}</strong><br><small>${s.key}</small>` },
                { label: "Value", render: s => String(s.value) },
                { label: "Last changed", render: s => `${formatDateTime(s.updated_at)}${s.updated_by ? ` by ${s.updated_by}` : ""}` },
            ], settings)}
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
        "edit-settings": () => {
            const fields = [
                { name: "expiry.critical_days", label: "Critical: days to expiry", type: "number", min: 1, step: 1, value: value("expiry.critical_days"), required: true },
                { name: "expiry.urgent_days", label: "Urgent: days to expiry", type: "number", min: 1, step: 1, value: value("expiry.urgent_days"), required: true },
                { name: "expiry.approaching_days", label: "Approaching: days to expiry", type: "number", min: 1, step: 1, value: value("expiry.approaching_days"), required: true },
                { name: "stock.slow_moving_units_90d", label: "Slow-moving: max units dispensed in 90 days", type: "number", min: 0, step: 1, value: value("stock.slow_moving_units_90d"), required: true },
                { name: "reorder.lead_time_days", label: "Supplier lead time (days)", type: "number", min: 0, step: 1, value: value("reorder.lead_time_days"), required: true },
                { name: "reorder.cover_days", label: "Reorder covers (days)", type: "number", min: 1, step: 1, value: value("reorder.cover_days"), required: true },
                { name: "currency.symbol", label: "Currency symbol", maxlength: 5, value: value("currency.symbol"), required: true },
            ];
            formModal({
                title: "Edit settings",
                fields,
                validate: v => (v["expiry.critical_days"] < v["expiry.urgent_days"] && v["expiry.urgent_days"] < v["expiry.approaching_days"]
                    ? null : "Thresholds must satisfy critical < urgent < approaching."),
                onSubmit: async v => { await api("/settings", { method: "PUT", body: v }); toast("Settings saved."); location.reload(); },
            });
        },
        "add-location": () => locationForm(null),
        "edit-location": el => locationForm(locations.find(l => l.id === Number(el.dataset.id))),
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
        <section class="section">
            <div class="section-header"><div><h3>Your permissions</h3></div></div>
            <ul class="permission-list">${u.permissions.map(p => html`<li>${p}</li>`)}</ul>
        </section>`);

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
