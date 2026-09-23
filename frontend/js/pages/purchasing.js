/* Purchasing: orders, lines, receiving (partial / full), cancellation. */

import {
    api, badge, confirmModal, formModal, formatDate, formatDateTime, html, money, mount, number, onAction,
    pageHeader, sortableTable, statTile, table, toast,
} from "../core.js";

const STATUSES = ["DRAFT", "ORDERED", "PARTIALLY_RECEIVED", "RECEIVED", "CANCELLED"];

export async function renderList(ctx) {
    const [orders, suppliers] = await Promise.all([api("/purchase-orders"), api("/suppliers")]);
    if (!ctx.isCurrent()) return;

    const open = orders.filter(o => ["DRAFT", "ORDERED", "PARTIALLY_RECEIVED"].includes(o.status));
    mount(ctx.main, html`
        ${pageHeader("Purchasing", "Purchase orders and deliveries", ctx.can("purchasing.write")
            ? html`<button type="button" class="refresh-btn primary" data-action="new">+ New Purchase Order</button>` : "")}
        <section class="cards">
            ${statTile("Open orders", number(open.length), "blue")}
            ${statTile("Awaiting delivery (units)", number(open.reduce((s, o) => s + o.units_ordered - o.units_received, 0)), "orange")}
            ${statTile("Open order value", money(open.reduce((s, o) => s + Number(o.order_value), 0)), "green")}
        </section>
        <section class="section">
            <div class="toolbar">
                <select id="po-status" aria-label="Status"><option value="">All statuses</option>
                    ${STATUSES.map(s => html`<option value="${s}">${s.replaceAll("_", " ")}</option>`)}</select>
                <select id="po-supplier" aria-label="Supplier"><option value="">All suppliers</option>
                    ${suppliers.map(s => html`<option value="${s.id}">${s.name}</option>`)}</select>
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
        { label: "Status", key: "status", render: o => badge(o.status) },
        { label: "Created by", key: "created_by_name", render: o => o.created_by_name || "—" },
    ];
    const status = ctx.main.querySelector("#po-status");
    const supplier = ctx.main.querySelector("#po-supplier");
    const draw = () => sortableTable(ctx.main.querySelector("#po-table"), "po-table-el", columns,
        orders.filter(o => (!status.value || o.status === status.value) && (!supplier.value || String(o.supplier_id) === supplier.value)),
        { empty: "No purchase orders." });
    status.addEventListener("change", draw);
    supplier.addEventListener("change", draw);
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
    const editable = !["RECEIVED", "CANCELLED"].includes(o.status);
    const canWrite = ctx.can("purchasing.write") && editable;
    const canReceive = ctx.can("purchasing.receive") && o.status !== "CANCELLED";

    mount(ctx.main, html`
        ${pageHeader(`Purchase Order ${o.order_number}`, `${o.supplier} · ordered ${formatDate(o.order_date)}${o.created_by_name ? ` by ${o.created_by_name}` : ""}`, html`
            <a class="view-btn" href="#/purchasing">← Orders</a>
            ${canWrite ? html`<button type="button" class="refresh-btn" data-action="notes">Edit notes</button>
                <button type="button" class="refresh-btn danger" data-action="cancel">Cancel order</button>` : ""}`)}
        <section class="cards">
            <div class="card"><div><span>Status</span><strong>${badge(o.status)}</strong></div></div>
            ${statTile("Order value", money(data.totals.order_value), "blue")}
            ${statTile("Received value", money(data.totals.received_value), "green")}
            ${statTile("Units received", `${number(data.totals.units_received)} / ${number(data.totals.units_ordered)}`, "orange")}
        </section>
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
                    const result = await api("/purchase-receipts", { method: "POST", body: {
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
        notes: () => formModal({
            title: "Order notes",
            fields: [{ name: "notes", label: "Notes", type: "textarea", value: o.notes, full: true }],
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
