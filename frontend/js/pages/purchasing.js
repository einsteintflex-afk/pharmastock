/* Purchasing: orders, lines, receiving (partial / full), cancellation. */

import {
    api, badge, busy, confirmModal, emptyState, formModal, formatDate, formatDateTime, html, idempotencyKey, money,
    mount, number, onAction, pageHeader, sortableTable, statTile, table, toast, today,
} from "../core.js";

const STATUSES = ["DRAFT", "SUBMITTED", "APPROVED", "ORDERED", "PARTIALLY_RECEIVED", "RECEIVED", "CANCELLED"];

export async function renderList(ctx) {
    const [orders, suppliers] = await Promise.all([api("/purchase-orders"), api("/suppliers")]);
    if (!ctx.isCurrent()) return;

    const open = orders.filter(o => ["DRAFT", "SUBMITTED", "APPROVED", "ORDERED", "PARTIALLY_RECEIVED"].includes(o.status));
    mount(ctx.main, html`
        ${pageHeader("Purchasing", "Purchase orders and deliveries", ctx.can("purchasing.write")
            ? html`<a class="refresh-btn" href="#/reorder">🛒 From reorder list</a>
                <button type="button" class="refresh-btn primary" data-action="new">+ New Purchase Order</button>` : "")}
        <section class="cards">
            ${statTile("Open orders", number(open.length), "blue")}
            ${statTile("Awaiting delivery (units)", number(open.reduce((s, o) => s + o.units_ordered - o.units_received, 0)), "orange")}
            ${statTile("Open order value", money(open.reduce((s, o) => s + Number(o.order_value), 0)), "green")}
            ${statTile("Overdue deliveries", number(orders.filter(o => o.overdue).length), "red", "past the expected date")}
        </section>
        <section class="section">
            <div class="toolbar">
                <select id="po-status" aria-label="Status"><option value="">All statuses</option>
                    ${STATUSES.map(s => html`<option value="${s}">${s.replaceAll("_", " ")}</option>`)}</select>
                <select id="po-supplier" aria-label="Supplier"><option value="">All suppliers</option>
                    ${suppliers.map(s => html`<option value="${s.id}">${s.name}</option>`)}</select>
                <label class="inline-check"><input type="checkbox" id="po-overdue"> Overdue only</label>
            </div>
            <div id="po-table"></div>
        </section>`);

    const columns = [
        { label: "Order", key: "order_number", render: o => html`<a href="#/purchasing/${o.id}"><strong>${o.order_number}</strong></a>` },
        { label: "Date", key: "order_date", render: o => formatDate(o.order_date) },
        { label: "Supplier", key: "supplier" },
        { label: "Lines", key: "items", className: "num" },
        { label: "Received", render: o => `${number(o.units_received)} / ${number(o.units_ordered)}`, sort: o => o.units_received / (o.units_ordered || 1) },
        { label: "Value", key: "order_value", render: o => money(o.order_value), className: "num", sort: o => Number(o.order_value) },
        { label: "Expected", key: "expected_delivery_date", render: o => o.expected_delivery_date
            ? html`${formatDate(o.expected_delivery_date)} ${o.overdue ? badge("OVERDUE") : ""}` : "—" },
        { label: "Status", key: "status", render: o => badge(o.status) },
        { label: "Created by", key: "created_by_name", render: o => o.created_by_name || "—" },
    ];
    const status = ctx.main.querySelector("#po-status");
    const supplier = ctx.main.querySelector("#po-supplier");
    const overdue = ctx.main.querySelector("#po-overdue");
    status.value = ctx.params.query.status || "";
    overdue.checked = ctx.params.query.overdue === "true";
    const draw = () => sortableTable(ctx.main.querySelector("#po-table"), "po-table-el", columns,
        orders.filter(o => (!status.value || o.status === status.value) && (!supplier.value || String(o.supplier_id) === supplier.value)
            && (!overdue.checked || o.overdue)),
        { empty: "No purchase orders match." });
    status.addEventListener("change", draw);
    supplier.addEventListener("change", draw);
    overdue.addEventListener("change", draw);
    draw();

    onAction(ctx.main, {
        new: async () => {
            const { order_number } = await api("/purchase-orders/next-number");
            formModal({
                title: "New Purchase Order",
                fields: [
                    { name: "supplier_id", label: "Supplier", type: "select", required: true, placeholder: "Select…",
                      options: suppliers.filter(s => s.is_active).map(s => ({ value: s.id, label: s.name })) },
                    { name: "order_number", label: "Order number", required: true, value: order_number, maxlength: 50 },
                    { name: "expected_delivery_date", label: "Expected delivery", type: "date", min: today() },
                    { name: "notes", label: "Notes", type: "textarea", full: true },
                ],
                submitLabel: "Create Order",
                onSubmit: async v => {
                    const order = await api("/purchase-orders", { method: "POST", body: { ...v, supplier_id: Number(v.supplier_id) } });
                    toast(`Order ${order.order_number} created. Add the medicines to order.`);
                    ctx.navigate(`purchasing/${order.id}`);
                },
            });
        },
    });
}

export async function renderDetail(ctx) {
    const [data, medicines, locations] = await Promise.all([
        api(`/purchase-orders/${encodeURIComponent(ctx.params.id)}`),
        api("/medicines", { params: { sort: "name" } }),
        api("/locations"),
    ]);
    if (!ctx.isCurrent()) return;
    const o = data.order;
    const workflow = data.approval_required;
    const editable = workflow ? o.status === "DRAFT" : !["RECEIVED", "CANCELLED", "SUBMITTED", "APPROVED"].includes(o.status);
    const canWrite = ctx.can("purchasing.write") && editable;
    const canCancel = ctx.can("purchasing.write") && !["RECEIVED", "CANCELLED"].includes(o.status);
    const canReceive = ctx.can("purchasing.receive") && !["CANCELLED", "SUBMITTED"].includes(o.status)
        && !(workflow && o.status === "DRAFT");
    const flow = [];
    if (workflow && o.status === "DRAFT" && ctx.can("purchasing.write") && data.items.length) {
        flow.push(html`<button type="button" class="refresh-btn primary" data-action="submit">Submit for approval →</button>`);
    }
    if (o.status === "SUBMITTED" && ctx.can("purchasing.approve") && o.submitted_by !== ctx.user.id) {
        flow.push(html`<button type="button" class="refresh-btn primary" data-action="approve">✓ Approve</button>
            <button type="button" class="refresh-btn danger" data-action="reject">✕ Reject to draft</button>`);
    }
    if (o.status === "APPROVED" && ctx.can("purchasing.write")) {
        flow.push(html`<button type="button" class="refresh-btn primary" data-action="ordered">📨 Mark as sent to supplier</button>`);
    }

    mount(ctx.main, html`
        ${pageHeader(`Purchase Order ${o.order_number}`, `${o.supplier} · ordered ${formatDate(o.order_date)}${o.created_by_name ? ` by ${o.created_by_name}` : ""}`, html`
            <a class="view-btn" href="#/purchasing">← Orders</a>
            ${flow}
            ${ctx.can("purchasing.write") && !["RECEIVED", "CANCELLED"].includes(o.status) ? html`<button type="button" class="refresh-btn" data-action="notes">Edit notes / date</button>` : ""}
            ${canCancel ? html`<button type="button" class="refresh-btn danger" data-action="cancel">Cancel order</button>` : ""}`)}
        <section class="cards">
            <div class="card"><div><span>Status</span><strong>${badge(o.status)}</strong></div></div>
            ${statTile("Order value", money(data.totals.order_value), "blue")}
            ${statTile("Received value", money(data.totals.received_value), "green")}
            ${statTile("Units received", `${number(data.totals.units_received)} / ${number(data.totals.units_ordered)}`, "orange")}
            ${statTile("Expected delivery", o.expected_delivery_date ? formatDate(o.expected_delivery_date) : "—", o.overdue ? "red" : "blue",
                o.overdue ? "OVERDUE" : "")}
        </section>
        ${workflow || o.submitted_at ? html`<section class="section approval-trail">
            <ol class="steps">
                <li class="${o.submitted_at ? "done" : ""}">Submitted${o.submitted_at ? html`<small>${formatDateTime(o.submitted_at)} · ${o.submitted_by_name}</small>` : ""}</li>
                <li class="${o.approved_at ? "done" : ""}">Approved${o.approved_at ? html`<small>${formatDateTime(o.approved_at)} · ${o.approved_by_name}</small>` : ""}</li>
                <li class="${o.ordered_at || ["ORDERED", "PARTIALLY_RECEIVED", "RECEIVED"].includes(o.status) ? "done" : ""}">Sent to supplier${o.ordered_at ? html`<small>${formatDateTime(o.ordered_at)}</small>` : ""}</li>
                <li class="${o.status === "RECEIVED" ? "done" : ""}">Received</li>
            </ol></section>` : ""}
        ${o.notes ? html`<section class="section"><p class="notes">${o.notes}</p></section>` : ""}
        <section class="section">
            <div class="section-header"><div><h3>Order lines</h3><p>Receive each line as deliveries arrive (partial deliveries allowed)</p></div>
                ${canWrite ? html`<button type="button" class="refresh-btn primary" data-action="add-line">+ Add medicine</button>` : ""}</div>
            <div id="lines"></div>
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Receipts</h3><p>Deliveries recorded against this order</p></div></div>
            ${table("po-receipts", [
                { label: "Received", render: r => formatDateTime(r.received_date) },
                { label: "Medicine", key: "medicine" },
                { label: "Batch", key: "batch_number" },
                { label: "Expiry", render: r => formatDate(r.expiry_date) },
                { label: "Location", key: "location" },
                { label: "Qty", render: r => number(r.quantity_received), className: "num" },
                { label: "Received by", render: r => r.received_by || "—" },
            ], data.receipts, { empty: "Nothing received yet." })}
        </section>`);

    sortableTable(ctx.main.querySelector("#lines"), "po-lines", [
        { label: "Medicine", key: "medicine", render: i => html`<a href="#/medicines/${i.medicine_id}"><strong>${i.medicine}</strong></a> <small>${i.strength} ${i.dosage_form}</small>` },
        { label: "Ordered", key: "quantity_ordered", render: i => number(i.quantity_ordered), className: "num" },
        { label: "Received", key: "quantity_received", render: i => number(i.quantity_received), className: "num" },
        { label: "Outstanding", key: "quantity_remaining", render: i => number(i.quantity_remaining), className: "num" },
        { label: "Unit cost", key: "unit_cost", render: i => money(i.unit_cost), className: "num" },
        { label: "Line value", render: i => money(i.quantity_ordered * i.unit_cost), className: "num", sort: i => i.quantity_ordered * i.unit_cost },
        { label: "", render: i => html`
            ${canReceive && i.quantity_remaining > 0 ? html`<button type="button" class="view-btn" data-action="receive" data-id="${i.id}">Receive</button>` : ""}
            ${canWrite ? html`<button type="button" class="view-btn" data-action="edit-line" data-id="${i.id}">Edit</button>` : ""}
            ${canWrite && i.quantity_received === 0 ? html`<button type="button" class="view-btn danger" data-action="remove-line" data-id="${i.id}">Remove</button>` : ""}` },
    ], data.items, { empty: "No lines yet. Add the medicines to order." });

    const item = el => data.items.find(i => i.id === Number(el.dataset.id));
    onAction(ctx.main, {
        "add-line": () => formModal({
            title: "Add medicine to order",
            fields: [
                { name: "medicine_id", label: "Medicine", type: "select", required: true, placeholder: "Select…",
                  options: medicines.filter(m => !data.items.some(i => i.medicine_id === m.id))
                      .map(m => ({ value: m.id, label: `${m.name} ${m.strength || ""} ${m.dosage_form || ""}` })) },
                { name: "quantity_ordered", label: "Quantity", type: "number", min: 1, step: 1, required: true },
                { name: "unit_cost", label: "Unit cost", type: "number", min: 0, step: "0.01", required: true },
            ],
            submitLabel: "Add Line",
            onSubmit: async v => {
                await api(`/purchase-orders/${o.id}/items`, { method: "POST", body: { ...v, medicine_id: Number(v.medicine_id) } });
                toast("Line added.");
                ctx.reload();
            },
        }),
        "edit-line": el => {
            const line = item(el);
            formModal({
                title: `Edit line — ${line.medicine}`,
                intro: line.quantity_received ? "Stock has been received on this line: the unit cost is fixed and the quantity cannot go below what was received." : "",
                fields: [
                    { name: "quantity_ordered", label: "Quantity", type: "number", min: Math.max(1, line.quantity_received), step: 1, required: true, value: line.quantity_ordered },
                    { name: "unit_cost", label: "Unit cost", type: "number", min: 0, step: "0.01", required: true, value: line.unit_cost },
                ],
                onSubmit: async v => {
                    await api(`/purchase-orders/${o.id}/items/${line.id}`, { method: "PUT", body: v });
                    toast("Line updated.");
                    ctx.reload();
                },
            });
        },
        "remove-line": async el => {
            const line = item(el);
            if (!(await confirmModal("Remove line", `Remove ${line.medicine} from this order?`, "Remove"))) return;
            try {
                await api(`/purchase-orders/${o.id}/items/${line.id}`, { method: "DELETE" });
                toast("Line removed.");
                ctx.reload();
            } catch (error) { toast(error.message, "error"); }
        },
        receive: async el => {
            const line = item(el);
            const receiveKey = idempotencyKey();
            const form = formModal({
                title: `Receive ${line.medicine} ${line.strength || ""}`,
                intro: `${number(line.quantity_remaining)} of ${number(line.quantity_ordered)} units outstanding at ${money(line.unit_cost)} each. Receiving into an existing batch number adds to that batch.`,
                fields: [
                    { name: "barcode_data", label: "Scan pack barcode (optional)", maxlength: 200, full: true, autocomplete: "off",
                      help: "A GS1 DataMatrix scan fills in the batch number and expiry date and checks the product." },
                    { name: "batch_number", label: "Batch number", required: true, maxlength: 100 },
                    { name: "expiry_date", label: "Expiry date", type: "date", required: true },
                    { name: "quantity_received", label: "Quantity received", type: "number", min: 1, max: line.quantity_remaining,
                      step: 1, required: true, value: line.quantity_remaining },
                    { name: "location_id", label: "Receive into", type: "select",
                      options: locations.filter(l => l.is_active).map(l => ({ value: l.id, label: l.name })) },
                    { name: "received_by", label: "Received by (name on delivery note)", value: ctx.user.full_name, maxlength: 150 },
                    { name: "notes", label: "Notes (delivery note, condition)", type: "textarea", full: true },
                ],
                submitLabel: "Receive Stock",
                validate: v => (v.quantity_received > line.quantity_remaining
                    ? `Only ${line.quantity_remaining} units are outstanding.` : null),
                onSubmit: async v => {
                    const result = await api("/purchase-receipts", { method: "POST", headers: { "Idempotency-Key": receiveKey }, body: {
                        ...v, purchase_order_item_id: line.id, location_id: v.location_id ? Number(v.location_id) : null,
                    } });
                    toast(`Received. Batch now holds ${number(result.new_batch_quantity)}; order ${result.purchase_order_status.replaceAll("_", " ").toLowerCase()}.`);
                    ctx.reload();
                },
            });
            const { wireScanInput } = await import("./scan.js");
            wireScanInput(form.elements.barcode_data, result => {
                if (result.medicine && result.medicine.id !== line.medicine_id) {
                    toast(`This pack is ${result.medicine.name}, not ${line.medicine}.`, "error");
                    return;
                }
                if (result.parsed.batch_number) form.elements.batch_number.value = result.parsed.batch_number;
                if (result.parsed.expiry_date) form.elements.expiry_date.value = result.parsed.expiry_date;
            });
        },
        submit: button => busy(button, async () => {
            await api(`/purchase-orders/${o.id}/submit`, { method: "POST" });
            toast("Submitted for approval");
            ctx.reload();
        }),
        approve: button => busy(button, async () => {
            await api(`/purchase-orders/${o.id}/approve`, { method: "POST", body: {} });
            toast("Order approved");
            ctx.reload();
        }),
        reject: () => formModal({
            title: `Reject ${o.order_number}`, submitLabel: "Reject to draft",
            fields: [{ name: "reason", label: "Reason", type: "textarea", required: true, full: true }],
            onSubmit: async v => { await api(`/purchase-orders/${o.id}/reject`, { method: "POST", body: v }); ctx.reload(); },
        }),
        ordered: button => busy(button, async () => {
            await api(`/purchase-orders/${o.id}/mark-ordered`, { method: "POST" });
            toast("Marked as sent to the supplier");
            ctx.reload();
        }),
        notes: () => formModal({
            title: "Order notes and expected delivery",
            fields: [{ name: "expected_delivery_date", label: "Expected delivery", type: "date", value: o.expected_delivery_date },
                     { name: "notes", label: "Notes", type: "textarea", value: o.notes, full: true }],
            onSubmit: async v => {
                await api(`/purchase-orders/${o.id}`, { method: "PUT", body: v });
                ctx.reload();
            },
        }),
        cancel: () => formModal({
            title: `Cancel ${o.order_number}`,
            intro: "Stock already received stays in inventory. The outstanding balance will no longer be received.",
            fields: [{ name: "reason", label: "Reason", type: "textarea", required: true, full: true }],
            submitLabel: "Cancel Order",
            onSubmit: async v => {
                await api(`/purchase-orders/${o.id}/cancel`, { method: "POST", body: v });
                toast("Order cancelled.", "warning");
                ctx.reload();
            },
        }),
    });
}


/* ---------- Reorder list -> purchase order ---------- */

export async function renderReorder(ctx) {
    const [rows, suppliers] = await Promise.all([api("/analytics/reorder"), api("/suppliers")]);
    if (!ctx.isCurrent()) return;
    const needed = rows.filter(r => r.reorder_recommended);
    const canOrder = ctx.can("purchasing.write");
    mount(ctx.main, html`
        ${pageHeader("Reorder", "Safety stock, reorder point and suggested quantities from current consumption",
            html`<a class="view-btn" href="#/purchasing">Purchase orders</a>`)}
        <section class="section">
            <div class="section-header"><div><h3>${number(needed.length)} medicine(s) need reordering</h3>
                <p>Suggested quantity = target − usable − on order. Tick the lines to order and choose a supplier.</p></div>
                ${canOrder && needed.length ? html`<button type="button" class="refresh-btn primary" data-action="create">🛒 Create purchase order</button>` : ""}</div>
            ${needed.length ? table("reorder-table", [
                ...(canOrder ? [{ label: "", render: r => html`<input type="checkbox" class="reorder-pick" data-id="${r.medicine_id}" checked
                    aria-label="Order ${r.medicine}">` }] : []),
                { label: "Medicine", render: r => html`<a href="#/medicines/${r.medicine_id}"><strong>${r.medicine}</strong></a> <small>${r.strength || ""}</small><br><small>${r.reason}</small>` },
                { label: "Usable", className: "num", render: r => number(r.usable_stock) },
                { label: "Daily use", className: "num", render: r => number(r.average_daily_consumption, 2) },
                { label: "Safety stock", className: "num", render: r => number(r.safety_stock) },
                { label: "Reorder point", className: "num", render: r => r.reorder_point === null ? "—" : number(r.reorder_point) },
                { label: "On order", className: "num", render: r => number(r.on_order) },
                { label: "Order qty", className: "num", render: r => canOrder
                    ? html`<input type="number" class="qty-input" min="1" step="1" value="${r.recommended_quantity}" data-qty="${r.medicine_id}" aria-label="Quantity">`
                    : number(r.recommended_quantity) },
            ], needed) : emptyState("Nothing needs reordering right now.")}
            <details class="method"><summary>How is this calculated?</summary>
                <p>${needed[0]?.calculation || "Safety stock = daily use × safety days; reorder point = daily use × lead time + safety stock."}</p></details>
        </section>`);
    onAction(ctx.main, {
        create: () => {
            const picked = [...ctx.main.querySelectorAll(".reorder-pick:checked")].map(box => ({
                medicine_id: Number(box.dataset.id),
                quantity: Number(ctx.main.querySelector(`[data-qty="${box.dataset.id}"]`).value),
            })).filter(line => line.quantity > 0);
            if (!picked.length) { toast("Tick at least one medicine.", "warning"); return; }
            formModal({
                title: "Create purchase order", submitLabel: "Create draft order",
                intro: `${picked.length} line(s). Unit costs are taken from the last price paid; you can change them on the order.`,
                fields: [
                    { name: "supplier_id", label: "Supplier", type: "select", required: true, placeholder: "Select…",
                      options: suppliers.filter(s => s.is_active).map(s => ({ value: s.id, label: s.name })) },
                    { name: "expected_delivery_date", label: "Expected delivery", type: "date", min: today() },
                    { name: "notes", label: "Notes", type: "textarea", full: true },
                ],
                onSubmit: async v => {
                    const order = await api("/purchase-orders/from-reorder", { method: "POST",
                        body: { ...v, supplier_id: Number(v.supplier_id), items: picked } });
                    toast(`Draft order ${order.order.order_number} created`);
                    ctx.navigate(`purchasing/${order.order.id}`);
                },
            });
        },
    });
}
