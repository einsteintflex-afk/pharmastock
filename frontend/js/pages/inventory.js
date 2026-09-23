/* Inventory, expiry alerts, batch detail, batch and movement forms. */

import {
    api, badge, daysLabel, debounce, formatDate, formatDateTime, formModal, html, money, mount, number,
    onAction, pageHeader, plural, signedQuantity, sortableTable, statTile, table, toast, today,
} from "../core.js";

const STATUSES = ["EXPIRED", "CRITICAL", "URGENT", "APPROACHING EXPIRY", "NORMAL"];

async function lookups() {
    const [medicines, locations, suppliers] = await Promise.all([
        api("/medicines", { params: { sort: "name" } }), api("/locations"), api("/suppliers"),
    ]);
    return { medicines, locations: locations.filter(l => l.is_active), suppliers };
}

export async function openBatchForm(medicineId, onSaved) {
    const { medicines, locations, suppliers } = await lookups();
    const fields = [
        { name: "barcode_data", label: "Scan pack barcode (optional)", maxlength: 200, full: true, autocomplete: "off",
          help: "A GS1 DataMatrix scan fills in the medicine, batch number and expiry date." },
        { name: "medicine_id", label: "Medicine", type: "select", required: true, value: medicineId, placeholder: "Select…",
          options: medicines.filter(m => m.is_active !== false || m.id === medicineId)
              .map(m => ({ value: m.id, label: `${m.name} ${m.strength || ""} ${m.dosage_form || ""}` })) },
        { name: "batch_number", label: "Batch number", required: true, maxlength: 100 },
        { name: "quantity", label: "Quantity (units)", type: "number", min: 0, step: 1, required: true },
        { name: "expiry_date", label: "Expiry date", type: "date", required: true },
        { name: "unit_cost", label: "Unit cost", type: "number", min: 0, step: "0.01", help: "Acquisition cost per unit (for valuation)." },
        { name: "location_id", label: "Location", type: "select", value: locations[0]?.id,
          options: locations.map(l => ({ value: l.id, label: l.name })) },
        { name: "supplier_id", label: "Supplier", type: "select", placeholder: "Unknown / none",
          options: suppliers.map(s => ({ value: s.id, label: s.name })) },
        { name: "received_date", label: "Received date", type: "date", value: today() },
        { name: "purchase_date", label: "Purchase date", type: "date", help: "When the stock was bought (optional)." },
    ];
    const form = formModal({
        title: "Register Batch / Opening Stock",
        intro: "Use this for stock not received through a purchase order. Deliveries against an order are received from the order.",
        fields,
        validate: v => (!Number.isInteger(v.quantity) || v.quantity < 0 ? "Quantity must be a whole number of 0 or more." : null),
        onSubmit: async values => {
            ["medicine_id", "location_id", "supplier_id"].forEach(k => { if (values[k] !== null) values[k] = Number(values[k]); });
            await api("/batches", { method: "POST", body: values });
            toast("Batch registered.");
            onSaved?.();
        },
    });
    const { wireScanInput } = await import("./scan.js");
    wireScanInput(form.elements.barcode_data, result => {
        if (result.medicine) form.elements.medicine_id.value = String(result.medicine.id);
        if (result.parsed.batch_number) form.elements.batch_number.value = result.parsed.batch_number;
        if (result.parsed.expiry_date) form.elements.expiry_date.value = result.parsed.expiry_date;
    });
}

const MOVEMENT_HELP = {
    RECEIVED: "Units added to this batch (outside a purchase order).",
    DISPENSED: "Units dispensed from this batch. FEFO is enforced: an earlier-expiring batch must be used first unless overridden.",
    RETURNED: "Units returned to stock (e.g. patient or ward return).",
    DAMAGED: "Units written off as damaged. A reason is required.",
    EXPIRED: "Expired units written off / sent for disposal (only for expired batches).",
    ADJUSTMENT: "Enter the PHYSICALLY COUNTED quantity; the difference is recorded. A reason is required.",
};

export async function openMovementForm(batch, ctx, onSaved) {
    const types = await api("/stock-movements/types");
    const allowed = types.filter(t => t.allowed).map(t => t.movement_type);
    if (!allowed.length) { toast("Your role cannot record stock movements.", "error"); return; }

    const fields = [
        { name: "movement_type", label: "Movement type", type: "select", required: true, value: allowed[0],
          options: allowed.map(t => ({ value: t, label: t })) },
        { name: "quantity", label: "Quantity", type: "number", min: 0, step: 1, required: true,
          help: Object.entries(MOVEMENT_HELP).map(([k, v]) => `${k}: ${v}`).join(" ") },
        { name: "reason", label: "Reason / reference", type: "textarea", full: true },
    ];
    if (ctx.can("stock.fefo_override")) {
        fields.push({ name: "fefo_override_reason", label: "FEFO override reason (only if not dispensing the earliest batch)",
            type: "textarea", full: true });
    }
    formModal({
        title: `Stock movement — ${batch.medicine} batch ${batch.batch_number} (${number(batch.quantity)} in stock)`,
        fields,
        submitLabel: "Record Movement",
        validate: v => (!Number.isInteger(v.quantity) || v.quantity < 0 ? "Quantity must be a whole number." : null),
        onSubmit: async values => {
            const result = await api("/stock-movements", { method: "POST", body: { batch_id: batch.batch_id, ...values } });
            toast(`${result.movement_type} recorded. Batch now holds ${number(result.new_batch_quantity)}.`);
            onSaved?.();
        },
    });
}

/* ---------- Inventory page ---------- */

export async function renderInventory(ctx) {
    const [rows, locations, value] = await Promise.all([
        api("/inventory", { params: { include_empty: false } }), api("/locations"), api("/analytics/valuation"),
    ]);
    if (!ctx.isCurrent()) return;

    mount(ctx.main, html`
        ${pageHeader("Inventory", "Every batch in stock, earliest expiry first", html`
            ${ctx.can("batches.write") ? html`<button type="button" class="refresh-btn primary" data-action="add-batch">+ Register Batch</button>` : ""}
            <button type="button" class="refresh-btn" data-action="refresh">↻ Refresh</button>`)}
        <section class="cards">
            ${statTile("Batches in stock", number(value.total.batches), "blue")}
            ${statTile("Units", number(value.total.units), "green")}
            ${statTile("Stock value", money(value.total.value), "blue",
                value.total.units_without_cost ? `${number(value.total.units_without_cost)} units without cost` : "")}
            ${statTile("Expired value", money(value.expired.value), "red", `${number(value.expired.units)} units`)}
        </section>
        <section class="section">
            <div class="toolbar">
                <input type="search" id="inv-search" placeholder="Search medicine or batch…" aria-label="Search inventory">
                <select id="inv-status" aria-label="Expiry status"><option value="">All expiry statuses</option>
                    ${STATUSES.map(s => html`<option>${s}</option>`)}</select>
                <select id="inv-location" aria-label="Location"><option value="">All locations</option>
                    ${locations.map(l => html`<option value="${l.id}">${l.name}</option>`)}</select>
                <select id="inv-hold" aria-label="Hold status"><option value="">Any hold status</option>
                    <option value="ACTIVE">Active</option><option value="QUARANTINED">Quarantined</option><option value="RECALLED">Recalled</option></select>
                <label class="inline-check"><input type="checkbox" id="inv-empty"> Show empty batches</label>
                <span class="toolbar-count" id="inv-count"></span>
            </div>
            <div id="inv-table"></div>
        </section>`);

    const columns = [
        { label: "Medicine", key: "medicine", render: b => html`<a href="#/medicines/${b.medicine_id}"><strong>${b.medicine}</strong></a><br><small>${b.strength} ${b.dosage_form}</small>` },
        { label: "Batch", key: "batch_number", render: b => html`<a href="#/batches/${b.batch_id}">${b.batch_number}</a>` },
        { label: "Qty", key: "quantity", render: b => number(b.quantity), className: "num" },
        { label: "Expiry", key: "expiry_date", render: b => formatDate(b.expiry_date) },
        { label: "Days left", key: "days_until_expiry", render: b => daysLabel(b.days_until_expiry) },
        { label: "Status", key: "status", render: b => html`${badge(b.status)} ${b.batch_status !== "ACTIVE" ? badge(b.batch_status) : ""}`,
          sort: b => STATUSES.indexOf(b.status) },
        { label: "Location", key: "location" },
        { label: "Supplier", key: "supplier", render: b => b.supplier || "—" },
        { label: "Unit cost", key: "unit_cost", render: b => money(b.unit_cost), className: "num", sort: b => Number(b.unit_cost ?? -1) },
        { label: "Value", key: "stock_value", render: b => money(b.stock_value), className: "num", sort: b => Number(b.stock_value ?? -1) },
        { label: "", render: b => html`<button type="button" class="view-btn" data-action="move" data-id="${b.batch_id}">Movement</button>` },
    ];

    let data = rows;
    const search = ctx.main.querySelector("#inv-search");
    const status = ctx.main.querySelector("#inv-status");
    const location = ctx.main.querySelector("#inv-location");
    const empty = ctx.main.querySelector("#inv-empty");
    const hold = ctx.main.querySelector("#inv-hold");
    const draw = () => {
        const term = search.value.trim().toLowerCase();
        const filtered = data.filter(b =>
            (!term || [b.medicine, b.batch_number, b.strength].some(v => (v || "").toLowerCase().includes(term)))
            && (!status.value || b.status === status.value)
            && (!hold.value || b.batch_status === hold.value)
            && (!location.value || String(b.location_id) === location.value));
        ctx.main.querySelector("#inv-count").textContent = `${filtered.length} batches`;
        sortableTable(ctx.main.querySelector("#inv-table"), "inventory-table", columns, filtered, { empty: "No batches match." });
    };
    search.addEventListener("input", debounce(draw, 150));
    status.addEventListener("change", draw);
    location.addEventListener("change", draw);
    hold.addEventListener("change", draw);
    empty.addEventListener("change", async () => {
        data = await api("/inventory", { params: { include_empty: empty.checked } });
        draw();
    });
    draw();

    onAction(ctx.main, {
        refresh: () => ctx.reload(),
        "add-batch": () => openBatchForm(null, () => ctx.reload()),
        move: el => {
            const batch = data.find(b => b.batch_id === Number(el.dataset.id));
            openMovementForm(batch, ctx, () => ctx.reload());
        },
    });
}

/* ---------- Expiry alerts ---------- */

export async function renderExpiry(ctx) {
    const [alerts, settings] = await Promise.all([api("/expiry-alerts"), api("/settings")]);
    if (!ctx.isCurrent()) return;
    const setting = key => settings.find(s => s.key === key)?.value;
    const s = alerts.summary;

    mount(ctx.main, html`
        ${pageHeader("Expiry Alerts", `Critical ≤ ${setting("expiry.critical_days")} days · Urgent ≤ ${setting("expiry.urgent_days")} days · Approaching ≤ ${setting("expiry.approaching_days")} days`,
            html`<a class="view-btn" href="#/reports">Reports</a>`)}
        <section class="cards">
            ${statTile("Expired", plural(s.EXPIRED.batches, "batch", "batches"), "red", `${number(s.EXPIRED.units)} units · ${money(s.EXPIRED.value)}`)}
            ${statTile("Critical", plural(s.CRITICAL.batches, "batch", "batches"), "red", `${number(s.CRITICAL.units)} units · ${money(s.CRITICAL.value)}`)}
            ${statTile("Urgent", plural(s.URGENT.batches, "batch", "batches"), "orange", `${number(s.URGENT.units)} units · ${money(s.URGENT.value)}`)}
            ${statTile("Approaching", plural(s["APPROACHING EXPIRY"].batches, "batch", "batches"), "blue",
                `${number(s["APPROACHING EXPIRY"].units)} units · ${money(s["APPROACHING EXPIRY"].value)}`)}
        </section>
        <section class="section">
            <div class="toolbar">
                <select id="exp-status" aria-label="Status"><option value="">All alert levels</option>
                    ${STATUSES.slice(0, 4).map(x => html`<option>${x}</option>`)}</select>
            </div>
            <div id="exp-table"></div>
        </section>`);

    const canWriteOff = ctx.can("stock.adjust");
    const columns = [
        { label: "Status", key: "status", render: b => badge(b.status), sort: b => STATUSES.indexOf(b.status) },
        { label: "Medicine", key: "medicine", render: b => html`<a href="#/medicines/${b.medicine_id}"><strong>${b.medicine}</strong></a> <small>${b.strength}</small>` },
        { label: "Batch", key: "batch_number", render: b => html`<a href="#/batches/${b.batch_id}">${b.batch_number}</a>` },
        { label: "Qty", key: "quantity", render: b => number(b.quantity), className: "num" },
        { label: "Expiry", key: "expiry_date", render: b => formatDate(b.expiry_date) },
        { label: "Days", key: "days_until_expiry", render: b => daysLabel(b.days_until_expiry) },
        { label: "Location", key: "location" },
        { label: "Value", key: "stock_value", render: b => money(b.stock_value), className: "num" },
        { label: "", render: b => b.status === "EXPIRED" && canWriteOff
            ? html`<button type="button" class="view-btn danger" data-action="writeoff" data-id="${b.batch_id}">Write off</button>` : "" },
    ];
    const select = ctx.main.querySelector("#exp-status");
    const draw = () => sortableTable(ctx.main.querySelector("#exp-table"), "expiry-table", columns,
        alerts.batches.filter(b => !select.value || b.status === select.value), { empty: "No batches in this category." });
    select.addEventListener("change", draw);
    draw();

    onAction(ctx.main, {
        writeoff: el => {
            const batch = alerts.batches.find(b => b.batch_id === Number(el.dataset.id));
            formModal({
                title: `Write off expired batch ${batch.batch_number}`,
                intro: `${number(batch.quantity)} units of ${batch.medicine} expired on ${formatDate(batch.expiry_date)}. This records an EXPIRED movement and removes the units from stock.`,
                fields: [
                    { name: "quantity", label: "Units to write off", type: "number", min: 1, max: batch.quantity, value: batch.quantity, required: true },
                    { name: "reason", label: "Disposal reference / reason", type: "textarea", required: true, full: true },
                ],
                submitLabel: "Write Off",
                onSubmit: async v => {
                    await api("/stock-movements", { method: "POST", body: { batch_id: batch.batch_id, movement_type: "EXPIRED", ...v } });
                    toast("Expired stock written off.");
                    ctx.reload();
                },
            });
        },
    });
}

/* ---------- Batch detail ---------- */

export async function renderBatch(ctx) {
    const data = await api(`/batches/${encodeURIComponent(ctx.params.id)}`);
    if (!ctx.isCurrent()) return;
    const b = data.batch;

    mount(ctx.main, html`
        ${pageHeader(`Batch ${b.batch_number}`, `${b.medicine} ${b.strength || ""} ${b.dosage_form || ""}`, html`
            <a class="view-btn" href="#/medicines/${b.medicine_id}">← ${b.medicine}</a>
            ${ctx.can("batches.write") ? html`<button type="button" class="refresh-btn" data-action="edit">Edit details</button>` : ""}
            ${ctx.can("stock.adjust") && b.batch_status === "ACTIVE" ? html`
                <button type="button" class="refresh-btn" data-action="hold" data-status="QUARANTINED">Quarantine</button>
                <button type="button" class="refresh-btn danger" data-action="hold" data-status="RECALLED">Recall</button>` : ""}
            ${ctx.can("stock.adjust") && b.batch_status !== "ACTIVE" ? html`
                <button type="button" class="refresh-btn" data-action="hold" data-status="ACTIVE">Release to stock</button>` : ""}
            <button type="button" class="refresh-btn primary" data-action="move">Record movement</button>`)}
        ${b.batch_status !== "ACTIVE" ? html`<p class="warning-text">⚠ This batch is <strong>${b.batch_status.toLowerCase()}</strong>:
            it is excluded from FEFO and cannot be dispensed. Reason: ${b.status_reason || "—"}</p>` : ""}
        <section class="cards">
            <div class="card"><div><span>Quantity</span><strong>${number(b.quantity)}</strong>
                <small class="card-hint">${data.reconciled ? "✓ Matches movement history" : "⚠ Does not match movement history"}</small></div></div>
            <div class="card"><div><span>Expiry</span><strong>${formatDate(b.expiry_date)}</strong><small class="card-hint">${badge(b.status)} ${daysLabel(b.days_until_expiry)}</small></div></div>
            <div class="card"><div><span>Unit cost / value</span><strong>${money(b.unit_cost)}</strong><small class="card-hint">Value ${money(b.stock_value)}</small></div></div>
            <div class="card"><div><span>Location · Supplier</span><strong>${b.location}</strong><small class="card-hint">${b.supplier || "Supplier unknown"} · received ${formatDate(b.received_date)}${b.purchase_date ? ` · purchased ${formatDate(b.purchase_date)}` : ""}</small></div></div>
            ${b.barcode_data ? html`<div class="card"><div><span>Scanned barcode</span><strong class="mono">${b.barcode_data}</strong></div></div>` : ""}
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Batch history</h3><p>Every movement with the running balance</p></div></div>
            ${table("batch-history", [
                { label: "Date", render: m => formatDateTime(m.movement_date) },
                { label: "Type", render: m => badge(m.movement_type) },
                { label: "Change", render: m => signedQuantity(m), className: "num" },
                { label: "Balance", render: m => number(m.balance_after), className: "num" },
                { label: "Reason", render: m => m.reason || "—" },
                { label: "By", render: m => m.user_name || "—" },
            ], data.history, { empty: "No movements recorded." })}
        </section>
        ${data.receipts.length ? html`<section class="section">
            <div class="section-header"><div><h3>Purchase receipts</h3></div></div>
            ${table("batch-receipts", [
                { label: "Received", render: r => formatDateTime(r.received_date) },
                { label: "Order", render: r => html`<a href="#/purchasing/${r.purchase_order_id}">${r.order_number}</a>` },
                { label: "Qty", render: r => number(r.quantity_received) },
                { label: "Unit cost", render: r => money(r.unit_cost) },
                { label: "Received by", render: r => r.received_by || "—" },
            ], data.receipts)}</section>` : ""}`);

    onAction(ctx.main, {
        move: () => openMovementForm(b, ctx, () => ctx.reload()),
        hold: el => {
            const status = el.dataset.status;
            const verb = { QUARANTINED: "Quarantine", RECALLED: "Recall", ACTIVE: "Release" }[status];
            formModal({
                title: `${verb} batch ${b.batch_number}`,
                intro: status === "ACTIVE"
                    ? "The batch returns to usable stock and FEFO. Releasing a recalled batch needs a manager."
                    : "The batch stays on record but is excluded from FEFO and dispensing until released.",
                fields: [{ name: "reason", label: "Reason / reference", type: "textarea", required: true, full: true }],
                submitLabel: verb,
                onSubmit: async v => {
                    await api(`/batches/${b.batch_id}/status`, { method: "POST", body: { batch_status: status, reason: v.reason } });
                    toast(`Batch ${verb.toLowerCase()}d.`);
                    ctx.reload();
                },
            });
        },
        edit: async () => {
            const suppliers = await api("/suppliers");
            formModal({
                title: `Edit batch ${b.batch_number}`,
                intro: "Correct batch details. Quantity changes only through stock movements.",
                fields: [
                    { name: "batch_number", label: "Batch number", required: true, value: b.batch_number, maxlength: 100 },
                    { name: "expiry_date", label: "Expiry date", type: "date", required: true, value: b.expiry_date },
                    { name: "unit_cost", label: "Unit cost", type: "number", min: 0, step: "0.01", value: b.unit_cost },
                    { name: "supplier_id", label: "Supplier", type: "select", placeholder: "Unknown / none", value: b.supplier_id,
                      options: suppliers.map(s => ({ value: s.id, label: s.name })) },
                    { name: "received_date", label: "Received date", type: "date", value: b.received_date },
                    { name: "reason", label: "Reason for change", type: "textarea", required: true, full: true },
                ],
                onSubmit: async v => {
                    if (v.supplier_id !== null) v.supplier_id = Number(v.supplier_id);
                    await api(`/batches/${b.batch_id}`, { method: "PUT", body: v });
                    toast("Batch updated.");
                    ctx.reload();
                },
            });
        },
    });
}


/* ---------- Stock reconciliation ---------- */

export async function renderReconciliation(ctx) {
    const data = await api("/stock-reconciliation");
    if (!ctx.isCurrent()) return;
    const canFix = ctx.can("stock.adjust");
    mount(ctx.main, html`
        ${pageHeader("Stock Reconciliation", "Every batch quantity must equal the sum of its recorded movements", html`
            <button type="button" class="refresh-btn" data-action="refresh">↻ Re-check</button>`)}
        <section class="section">
            ${data.reconciled
                ? html`<div class="loading">✓ The stock ledger is fully reconciled: every batch matches its movement history.</div>`
                : html`
                <p class="method">A mismatch means a quantity was changed outside PharmaStock (for example directly in the
                    database). Choose <strong>Trust ledger</strong> to set the batch back to the sum of its movements, or
                    <strong>Trust count</strong> when a physical count confirms the recorded quantity — an ADJUSTMENT
                    movement then explains the difference. Both actions are audited.</p>
                ${table("reconcile", [
                    { label: "Medicine", key: "medicine" },
                    { label: "Batch", render: r => html`<a href="#/batches/${r.batch_id}">${r.batch_number}</a>` },
                    { label: "Recorded qty", render: r => number(r.recorded_quantity), className: "num" },
                    { label: "Ledger qty", render: r => number(r.ledger_quantity), className: "num" },
                    { label: "Difference", render: r => number(r.recorded_quantity - r.ledger_quantity), className: "num" },
                    { label: "", render: r => canFix ? html`
                        <button type="button" class="view-btn" data-action="fix" data-mode="TRUST_LEDGER" data-id="${r.batch_id}">Trust ledger</button>
                        <button type="button" class="view-btn" data-action="fix" data-mode="TRUST_COUNT" data-id="${r.batch_id}">Trust count</button>` : "" },
                ], data.mismatches)}`}
        </section>`);
    onAction(ctx.main, {
        refresh: () => ctx.reload(),
        fix: el => {
            const row = data.mismatches.find(r => r.batch_id === Number(el.dataset.id));
            const ledger = el.dataset.mode === "TRUST_LEDGER";
            formModal({
                title: `${ledger ? "Trust ledger" : "Trust count"} — batch ${row.batch_number}`,
                intro: ledger
                    ? `The batch quantity will be set to ${number(row.ledger_quantity)} (sum of movements).`
                    : `The recorded quantity ${number(row.recorded_quantity)} is kept and an ADJUSTMENT of ${number(row.recorded_quantity - row.ledger_quantity)} is recorded.`,
                fields: [{ name: "reason", label: "Reason / count reference", type: "textarea", required: true, full: true }],
                submitLabel: "Resolve",
                onSubmit: async v => {
                    await api(`/stock-reconciliation/${row.batch_id}/resolve`, { method: "POST", body: { action: el.dataset.mode, reason: v.reason } });
                    toast("Batch reconciled.");
                    ctx.reload();
                },
            });
        },
    });
}
