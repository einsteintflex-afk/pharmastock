/* Administration: users, audit trail, settings & locations, my account. */

import {
    api, apiPage, badge, bindPager, confirmModal, formModal, formatDateTime, html, mount, onAction, openModal, pageHeader,
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
        { label: "Last sign-in", key: "last_login_at", render: u => formatDateTime(u.last_login_at) },
        { label: "", render: u => html`<button type="button" class="view-btn" data-action="edit" data-id="${u.id}">Edit</button>
            <button type="button" class="view-btn" data-action="reset-link" data-id="${u.id}">Reset link</button>
            <button type="button" class="view-btn" data-action="reset" data-id="${u.id}">Temporary password</button>
            <button type="button" class="view-btn" data-action="sessions" data-id="${u.id}">Devices</button>` },
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
                { name: "pharmacy.name", label: "Pharmacy name (receipts)", maxlength: 150, value: value("pharmacy.name"), required: true },
                { name: "pharmacy.phone", label: "Pharmacy phone (receipts)", maxlength: 50, value: value("pharmacy.phone"), emptyValue: "" },
                { name: "pharmacy.address", label: "Pharmacy address (receipts)", maxlength: 255, value: value("pharmacy.address"), emptyValue: "", full: true },
                { name: "transfers.separate_approver", label: "Transfers must be approved by someone other than the requester",
                  type: "checkbox", value: value("transfers.separate_approver"), full: true },
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
        ${u.must_change_password ? "" : html`
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
    if (!u.must_change_password) await Promise.all([renderPreferences(ctx), renderDevices(ctx)]);

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
