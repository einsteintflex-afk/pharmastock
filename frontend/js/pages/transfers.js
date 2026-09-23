/* Stock transfers and ward / department requisitions:
   REQUESTED -> APPROVED -> DISPATCHED -> RECEIVED (or REJECTED / CANCELLED). */

import {
    api, apiPage, badge, bindPager, closeModal, formatDate, formatDateTime, formModal, html, mount, number, onAction,
    openModal, pageHeader, pager, table, toast,
} from "../core.js";

const PAGE_SIZE = 50;
const STATUSES = ["REQUESTED", "APPROVED", "DISPATCHED", "RECEIVED", "REJECTED", "CANCELLED"];

export async function renderList(ctx) {
    const locations = await api("/locations");
    if (!ctx.isCurrent()) return;
    const query = ctx.params.query || {};
    const hospital = ctx.user.organization?.type === "HOSPITAL";

    mount(ctx.main, html`
        ${pageHeader(hospital ? "Requisitions & Transfers" : "Transfers", hospital
            ? "Wards and departments request stock from the store; approved requests are dispatched FEFO and received"
            : "Move stock between locations with approval, FEFO dispatch and receipt", html`
            ${ctx.can("transfers.request") ? html`
                <button type="button" class="refresh-btn" data-action="new" data-type="REQUISITION">+ New requisition</button>
                <button type="button" class="refresh-btn primary" data-action="new" data-type="TRANSFER">+ New transfer</button>` : ""}`)}
        <section class="section">
            <div class="toolbar">
                <select id="tr-status" aria-label="Status">
                    <option value="">All statuses</option>
                    <option value="open" ${query.open ? html`selected` : ""}>Open (not finished)</option>
                    ${STATUSES.map(s => html`<option value="${s}" ${query.status === s ? html`selected` : ""}>${s.toLowerCase()}</option>`)}
                </select>
                <select id="tr-type" aria-label="Type"><option value="">Transfers and requisitions</option>
                    <option value="TRANSFER">Transfers</option><option value="REQUISITION">Requisitions</option></select>
                <select id="tr-location" aria-label="Location"><option value="">All locations</option>
                    ${locations.map(l => html`<option value="${l.id}">${l.name}</option>`)}</select>
            </div>
            <div id="tr-table"></div>
        </section>`);

    const status = ctx.main.querySelector("#tr-status");
    const type = ctx.main.querySelector("#tr-type");
    const location = ctx.main.querySelector("#tr-location");
    const container = ctx.main.querySelector("#tr-table");

    const load = async (page = 0) => {
        const params = {
            status: STATUSES.includes(status.value) ? status.value : null,
            open_only: status.value === "open" ? true : null,
            request_type: type.value || null, location_id: location.value || null,
        };
        const { rows, total } = await apiPage("/transfers", params, page, PAGE_SIZE);
        if (!ctx.isCurrent()) return;
        mount(container, html`
            ${table("transfers-table", [
                { label: "Number", render: t => html`<a href="#/transfers/${t.id}"><strong>${t.transfer_number}</strong></a>
                    <br><small>${t.request_type.toLowerCase()}</small>` },
                { label: "Status", render: t => html`${badge(t.status)} ${t.priority === "URGENT" ? badge("URGENT", "URGENT_PRIORITY") : ""}` },
                { label: "From → To", render: t => html`${t.from_location} → <strong>${t.to_location}</strong>` },
                { label: "Lines / units", render: t => `${t.items} / ${number(t.units_requested)}`, className: "num" },
                { label: "Requested", render: t => html`${formatDateTime(t.requested_at)}<br><small>${t.requested_by_name || "—"}</small>` },
                { label: "Last step", render: t => lastStep(t) },
            ], rows, { empty: "No transfers match." })}
            ${pager("tr-pager", total, page, PAGE_SIZE)}`);
        bindPager(container, "tr-pager", load);
    };
    [status, type, location].forEach(el => el.addEventListener("change", () => load(0)));
    await load(0);

    onAction(ctx.main, { new: el => openTransferForm(ctx, el.dataset.type, locations) });
}

function lastStep(t) {
    if (t.received_at) return html`Received ${formatDateTime(t.received_at)}<br><small>${t.received_by_name || ""}</small>`;
    if (t.dispatched_at) return html`Dispatched ${formatDateTime(t.dispatched_at)}<br><small>${t.dispatched_by_name || ""}</small>`;
    if (t.approved_at) return html`${t.status === "REJECTED" ? "Rejected" : "Approved"} ${formatDateTime(t.approved_at)}<br><small>${t.approved_by_name || ""}</small>`;
    return "—";
}

async function openTransferForm(ctx, requestType, locations) {
    const medicines = (await api("/medicines", { params: { sort: "name", active: true } }));
    const active = locations.filter(l => l.is_active);
    const own = ctx.user.location_id;
    const requisition = requestType === "REQUISITION";
    const store = active.find(l => l.location_type === "CENTRAL_STORE") || active[0];
    // Requisition: store -> my ward. Transfer: my location -> somewhere else.
    const fromId = requisition ? store?.id : (own || active[0]?.id);
    const toId = requisition && own && own !== fromId ? own : active.find(l => l.id !== fromId)?.id;
    const body = openModal(requisition ? "New requisition" : "New transfer", html`
        <form class="form-grid" id="transfer-form" novalidate>
            <p class="form-intro full">${requisition
                ? "Request stock for your ward / department from the store. An approver reviews it; the store dispatches FEFO."
                : "Send stock from one location to another. After approval it is dispatched FEFO from the source."}</p>
            <div class="field"><label for="t-from">From (source) <span class="req">*</span></label>
                <select id="t-from" name="from">${active.map(l => html`<option value="${l.id}"
                    ${l.id === fromId ? html`selected` : ""}>${l.name}</option>`)}</select></div>
            <div class="field"><label for="t-to">To (destination) <span class="req">*</span></label>
                <select id="t-to" name="to">${active.map(l => html`<option value="${l.id}"
                    ${l.id === toId ? html`selected` : ""}>${l.name}</option>`)}</select></div>
            <div class="field"><label for="t-priority">Priority</label>
                <select id="t-priority" name="priority"><option value="ROUTINE">Routine</option><option value="URGENT">Urgent</option></select></div>
            <div class="field full"><label>Items <span class="req">*</span></label>
                <div id="t-lines"></div>
                <button type="button" class="view-btn" id="t-add-line">+ Add item</button></div>
            <div class="field full"><label for="t-notes">Notes</label><textarea id="t-notes" name="notes" rows="2" maxlength="2000"></textarea></div>
            <div class="form-error full" role="alert" hidden></div>
            <div class="form-actions full">
                <button type="button" class="view-btn" data-cancel="1">Cancel</button>
                <button type="submit" class="refresh-btn primary">Submit request</button>
            </div>
        </form>`, { wide: true });

    const form = body.querySelector("form");
    const lines = form.querySelector("#t-lines");
    const errorBox = form.querySelector(".form-error");
    const addLine = () => {
        const row = document.createElement("div");
        row.className = "line-row";
        mount(row, html`
            <select aria-label="Medicine" class="t-med"><option value="">Select medicine…</option>
                ${medicines.map(m => html`<option value="${m.id}">${m.name} ${m.strength || ""} ${m.dosage_form || ""}</option>`)}</select>
            <input type="number" class="t-qty" min="1" step="1" placeholder="Quantity" aria-label="Quantity">
            <button type="button" class="icon-btn" aria-label="Remove item">×</button>`);
        row.querySelector("button").addEventListener("click", () => row.remove());
        lines.appendChild(row);
    };
    addLine();
    form.querySelector("#t-add-line").addEventListener("click", addLine);
    form.querySelector("[data-cancel]").addEventListener("click", closeModal);
    form.addEventListener("submit", async event => {
        event.preventDefault();
        errorBox.hidden = true;
        const items = [...lines.querySelectorAll(".line-row")].map(r => ({
            medicine_id: Number(r.querySelector(".t-med").value), quantity: Number(r.querySelector(".t-qty").value),
        })).filter(i => i.medicine_id || i.quantity);
        const problem = !items.length ? "Add at least one item."
            : items.some(i => !i.medicine_id || !Number.isInteger(i.quantity) || i.quantity < 1) ? "Each item needs a medicine and a whole quantity of 1 or more."
            : form.from.value === form.to.value ? "Source and destination must be different." : null;
        if (problem) { errorBox.textContent = problem; errorBox.hidden = false; return; }
        const button = form.querySelector("button[type=submit]");
        button.disabled = true;
        try {
            const created = await api("/transfers", { method: "POST", body: {
                request_type: requestType, from_location_id: Number(form.from.value), to_location_id: Number(form.to.value),
                priority: form.priority.value, notes: form.notes.value.trim() || null, items,
            } });
            closeModal();
            toast(`${created.transfer.transfer_number} submitted for approval.`);
            ctx.navigate(`transfers/${created.transfer.id}`);
        } catch (error) {
            errorBox.textContent = error.message;
            errorBox.hidden = false;
        } finally {
            button.disabled = false;
        }
    });
}

export async function renderDetail(ctx) {
    const data = await api(`/transfers/${encodeURIComponent(ctx.params.id)}`);
    if (!ctx.isCurrent()) return;
    const t = data.transfer;
    const mine = t.requested_by === ctx.user.id;

    const actions = [];
    if (t.status === "REQUESTED" && ctx.can("transfers.approve")) {
        actions.push(html`<button type="button" class="refresh-btn primary" data-action="approve">Approve</button>
            <button type="button" class="refresh-btn danger" data-action="reject">Reject</button>`);
    }
    if (t.status === "APPROVED" && ctx.can("transfers.dispatch")) {
        actions.push(html`<button type="button" class="refresh-btn primary" data-action="dispatch">Dispatch (FEFO)</button>`);
    }
    if (t.status === "DISPATCHED" && ctx.can("transfers.receive")) {
        actions.push(html`<button type="button" class="refresh-btn primary" data-action="receive">Receive at ${t.to_location}</button>`);
    }
    if (["REQUESTED", "APPROVED"].includes(t.status) && (mine || ctx.can("transfers.approve")) && ctx.can("transfers.request")) {
        actions.push(html`<button type="button" class="view-btn danger" data-action="cancel">Cancel request</button>`);
    }

    mount(ctx.main, html`
        ${pageHeader(`${t.transfer_number}`, `${t.request_type.toLowerCase()} · ${t.from_location} → ${t.to_location}`, html`
            <a class="view-btn" href="#/transfers">← All transfers</a>${actions}`)}
        <section class="cards">
            <div class="card"><div><span>Status</span><strong>${badge(t.status)}</strong>
                <small class="card-hint">${t.priority === "URGENT" ? "Urgent" : "Routine"} priority</small></div></div>
            <div class="card"><div><span>Requested</span><strong>${formatDate(t.requested_at)}</strong>
                <small class="card-hint">${t.requested_by_name || "—"}</small></div></div>
            <div class="card"><div><span>${t.status === "REJECTED" ? "Rejected" : "Approved"}</span>
                <strong>${t.approved_at ? formatDate(t.approved_at) : "—"}</strong><small class="card-hint">${t.approved_by_name || ""}</small></div></div>
            <div class="card"><div><span>Dispatched / received</span>
                <strong>${t.dispatched_at ? formatDate(t.dispatched_at) : "—"} / ${t.received_at ? formatDate(t.received_at) : "—"}</strong>
                <small class="card-hint">${[t.dispatched_by_name, t.received_by_name].filter(Boolean).join(" → ")}</small></div></div>
        </section>
        ${t.notes || t.approval_note || t.closed_reason ? html`<section class="section"><dl class="summary">
            ${t.notes ? html`<div><dt>Request notes</dt><dd>${t.notes}</dd></div>` : ""}
            ${t.approval_note ? html`<div><dt>Approval note</dt><dd>${t.approval_note}</dd></div>` : ""}
            ${t.closed_reason ? html`<div><dt>${t.status === "REJECTED" ? "Rejection" : "Cancellation"} reason</dt><dd>${t.closed_reason}</dd></div>` : ""}
        </dl></section>` : ""}
        <section class="section">
            <div class="section-header"><div><h3>Items</h3><p>Batches are chosen FEFO at dispatch and keep their number and expiry at the destination</p></div></div>
            ${table("transfer-items", [
                { label: "Medicine", render: i => html`<a href="#/medicines/${i.medicine_id}"><strong>${i.medicine}</strong></a> <small>${i.strength || ""} ${i.dosage_form || ""}</small>` },
                { label: "Requested", render: i => number(i.quantity_requested), className: "num" },
                { label: "Approved", render: i => i.quantity_approved === null ? "—" : number(i.quantity_approved), className: "num" },
                { label: "Dispatched", render: i => number(i.quantity_dispatched), className: "num" },
                { label: "Received", render: i => number(i.quantity_received), className: "num" },
                { label: "Available at source", render: i => number(i.available_at_source), className: "num" },
                { label: "Batches", render: i => i.batches.length ? i.batches.map(b => html`<div>
                    <a href="#/batches/${b.source_batch_id}">${b.batch_number}</a> × ${number(b.quantity)} <small>exp ${formatDate(b.expiry_date)}</small>
                    ${b.destination_batch_id ? html` → <a href="#/batches/${b.destination_batch_id}">destination batch</a>` : ""}</div>`) : "—" },
            ], data.items)}
        </section>`);

    const act = async (path, body, message) => {
        await api(`/transfers/${t.id}/${path}`, { method: "POST", body: body || {} });
        toast(message);
        ctx.reload();
    };
    onAction(ctx.main, {
        approve: () => formModal({
            title: `Approve ${t.transfer_number}`,
            intro: "Approve the requested quantities or reduce them. Set 0 to leave an item out.",
            fields: [
                ...data.items.map(i => ({ name: `q_${i.id}`, label: `${i.medicine} ${i.strength || ""} (requested ${i.quantity_requested}, ${i.available_at_source} available)`,
                    type: "number", min: 0, step: 1, required: true, value: Math.min(i.quantity_requested, i.available_at_source) || i.quantity_requested })),
                { name: "note", label: "Note (optional)", type: "textarea", full: true },
            ],
            submitLabel: "Approve",
            onSubmit: async v => {
                const quantities = Object.fromEntries(data.items.map(i => [i.id, v[`q_${i.id}`]]));
                await act("approve", { quantities, note: v.note }, "Approved.");
            },
        }),
        reject: () => formModal({
            title: `Reject ${t.transfer_number}`,
            fields: [{ name: "reason", label: "Reason", type: "textarea", required: true, full: true }],
            submitLabel: "Reject", onSubmit: v => act("reject", v, "Request rejected."),
        }),
        cancel: () => formModal({
            title: `Cancel ${t.transfer_number}`,
            fields: [{ name: "reason", label: "Reason", type: "textarea", required: true, full: true }],
            submitLabel: "Cancel request", onSubmit: v => act("cancel", v, "Request cancelled."),
        }),
        dispatch: async () => {
            try { await act("dispatch", {}, "Dispatched — stock is in transit."); } catch (error) { toast(error.message, "error"); }
        },
        receive: async () => {
            try { await act("receive", {}, "Received into the destination location."); } catch (error) { toast(error.message, "error"); }
        },
    });
}
