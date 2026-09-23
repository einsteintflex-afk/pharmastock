/* Notification centre: expiry, low stock, purchasing and receiving events. */

import { api, badge, formatDateTime, html, mount, onAction, pageHeader, toast } from "../core.js";

const LINKS = {
    batch: id => `#/batches/${id}`,
    medicine: id => `#/medicines/${id}`,
    purchase_order: id => `#/purchasing/${id}`,
};

export async function render(ctx) {
    const includeResolved = ctx.params.query?.all === "1";
    const notes = await api("/notifications", { params: { include_resolved: includeResolved } });
    if (!ctx.isCurrent()) return;
    const unread = notes.filter(n => !n.is_read).length;

    mount(ctx.main, html`
        ${pageHeader("Notifications", `${unread} unread`, html`
            <a class="view-btn" href="#/notifications${includeResolved ? "" : "?all=1"}">${includeResolved ? "Hide resolved" : "Show resolved"}</a>
            <button type="button" class="refresh-btn" data-action="refresh">↻ Re-check now</button>
            <button type="button" class="refresh-btn primary" data-action="read-all" ${unread ? "" : html`disabled`}>Mark all read</button>`)}
        <section class="section">
            <div class="toolbar">
                <select id="n-category" aria-label="Category"><option value="">All categories</option>
                    ${["EXPIRY", "LOW_STOCK", "PURCHASING", "RECEIVING"].map(c => html`<option value="${c}">${c.replace("_", " ")}</option>`)}</select>
            </div>
            <ul class="notification-list" id="n-list"></ul>
        </section>`);

    const list = ctx.main.querySelector("#n-list");
    const category = ctx.main.querySelector("#n-category");
    const draw = () => {
        const rows = notes.filter(n => !category.value || n.category === category.value);
        mount(list, rows.length ? rows.map(n => html`
            <li class="notification ${n.is_read ? "read" : "unread"} ${n.resolved_at && !["PURCHASING", "RECEIVING"].includes(n.category) ? "resolved" : ""}">
                <div class="notification-meta">${badge(n.severity, n.severity === "CRITICAL" ? "CRITICAL_SEV" : n.severity)}
                    <small>${n.category.replace("_", " ")} · ${formatDateTime(n.updated_at)}</small></div>
                <div class="notification-body">
                    <strong>${n.title}</strong>
                    <p>${n.message}</p>
                    ${n.resolved_at && !["PURCHASING", "RECEIVING"].includes(n.category) ? html`<small>Resolved ${formatDateTime(n.resolved_at)}</small>` : ""}
                </div>
                <div class="notification-actions">
                    ${LINKS[n.entity_type] ? html`<a class="view-btn" href="${LINKS[n.entity_type](n.entity_id)}" data-action="open" data-id="${n.id}">Open</a>` : ""}
                    ${n.is_read ? "" : html`<button type="button" class="view-btn" data-action="read" data-id="${n.id}">Mark read</button>`}
                </div>
            </li>`) : html`<li class="loading">No notifications.</li>`);
    };
    category.addEventListener("change", draw);
    draw();

    const markRead = async id => {
        await api(`/notifications/${id}/read`, { method: "POST" });
        const note = notes.find(n => n.id === Number(id));
        if (note) note.is_read = true;
        ctx.refreshUnread();
    };
    onAction(ctx.main, {
        read: async el => { await markRead(el.dataset.id); draw(); },
        open: async el => { await markRead(el.dataset.id); location.hash = el.getAttribute("href"); },
        "read-all": async () => { await api("/notifications/read-all", { method: "POST" }); ctx.refreshUnread(); ctx.reload(); },
        refresh: async () => {
            const result = await api("/notifications/refresh", { method: "POST" });
            toast(`${result.active_conditions} active conditions; ${result.resolved} resolved.`);
            ctx.refreshUnread();
            ctx.reload();
        },
    });
}
