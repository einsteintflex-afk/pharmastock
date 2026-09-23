/* Medicines: list, search, filter, sort, add, edit, detail. */

import {
    api, badge, daysLabel, debounce, formModal, formatDate, formatDateTime, html, money, mount, number, onAction,
    pageHeader, sortableTable, table, toast,
} from "../core.js";
import { signedQuantity } from "./dashboard.js";

const MEDICINE_FIELDS = (medicine) => [
    { name: "name", label: "Medicine name", required: true, maxlength: 150, value: medicine.name, placeholder: "e.g. Paracetamol" },
    { name: "strength", label: "Strength", maxlength: 50, value: medicine.strength, placeholder: "e.g. 500 mg" },
    { name: "dosage_form", label: "Dosage form", maxlength: 50, value: medicine.dosage_form, placeholder: "e.g. Tablet" },
    { name: "reorder_level", label: "Reorder level (units)", type: "number", min: 0, step: 1, required: true,
      value: medicine.reorder_level ?? 20, help: "Low-stock alert when usable stock falls to this level." },
];

function validateMedicine(values) {
    if (!values.name) return "Medicine name is required.";
    if (!Number.isInteger(values.reorder_level) || values.reorder_level < 0) return "Reorder level must be a whole number of 0 or more.";
    return null;
}

export function openMedicineForm(medicine, onSaved) {
    const fields = MEDICINE_FIELDS(medicine || {});
    formModal({
        title: medicine ? "Edit Medicine" : "Add Medicine",
        fields,
        submitLabel: medicine ? "Save Changes" : "Save Medicine",
        validate: validateMedicine,
        onSubmit: async values => {
            const saved = await api(medicine ? `/medicines/${medicine.id}` : "/medicines", {
                method: medicine ? "PUT" : "POST", body: values,
            });
            toast(medicine ? "Medicine updated." : "Medicine created.");
            onSaved?.(saved);
        },
    });
}

export async function renderList(ctx) {
    const stock = await api("/stock-alerts");
    if (!ctx.isCurrent()) return;

    const canWrite = ctx.can("medicines.write");
    mount(ctx.main, html`
        ${pageHeader("Medicines", "Registered medicines, usable stock and stock status", html`
            <button type="button" class="refresh-btn" data-action="refresh">↻ Refresh</button>
            ${canWrite ? html`<button type="button" class="refresh-btn primary" data-action="add">+ Add Medicine</button>` : ""}`)}
        <section class="section">
            <div class="toolbar">
                <input type="search" id="med-search" placeholder="Search name, strength or form…" aria-label="Search medicines">
                <select id="med-status" aria-label="Filter by stock status">
                    <option value="">All stock statuses</option>
                    <option>NORMAL</option><option>LOW STOCK</option><option>OUT OF STOCK</option>
                </select>
                <span class="toolbar-count" id="med-count"></span>
            </div>
            <div id="med-table"></div>
        </section>`);

    const columns = [
        { label: "Medicine", key: "medicine", render: m => html`<a href="#/medicines/${m.medicine_id}"><strong>${m.medicine}</strong></a>` },
        { label: "Strength", key: "strength", render: m => m.strength || "—" },
        { label: "Dosage Form", key: "dosage_form", render: m => m.dosage_form || "—" },
        { label: "Usable Stock", key: "current_stock", render: m => number(m.current_stock), className: "num" },
        { label: "Expired", key: "expired_stock", render: m => m.expired_stock ? number(m.expired_stock) : "—", className: "num" },
        { label: "Reorder Level", key: "reorder_level", render: m => number(m.reorder_level), className: "num" },
        { label: "Next Expiry", key: "next_expiry", render: m => formatDate(m.next_expiry) },
        { label: "Status", key: "status", render: m => badge(m.status) },
        { label: "Actions", render: m => html`<a class="view-btn" href="#/medicines/${m.medicine_id}">View</a>
            ${canWrite ? html`<button type="button" class="view-btn" data-action="edit" data-id="${m.medicine_id}">Edit</button>` : ""}` },
    ];

    const container = ctx.main.querySelector("#med-table");
    const search = ctx.main.querySelector("#med-search");
    const status = ctx.main.querySelector("#med-status");

    const draw = () => {
        const term = search.value.trim().toLowerCase();
        const rows = stock.filter(m =>
            (!term || [m.medicine, m.strength, m.dosage_form].some(v => (v || "").toLowerCase().includes(term)))
            && (!status.value || m.status === status.value));
        ctx.main.querySelector("#med-count").textContent = `${rows.length} of ${stock.length}`;
        sortableTable(container, "medicines-table", columns, rows, { empty: "No medicines found." });
    };
    search.addEventListener("input", debounce(draw, 150));
    status.addEventListener("change", draw);
    draw();

    onAction(ctx.main, {
        refresh: () => ctx.reload(),
        add: () => openMedicineForm(null, () => ctx.reload()),
        edit: async el => {
            const medicines = await api("/medicines");
            const medicine = medicines.find(m => m.id === Number(el.dataset.id));
            if (medicine) openMedicineForm(medicine, () => ctx.reload());
        },
    });
}

export async function renderDetail(ctx) {
    const data = await api(`/medicines/${encodeURIComponent(ctx.params.id)}`);
    if (!ctx.isCurrent()) return;
    const m = data.medicine;
    const use = data.consumption || {};
    const reorder = data.reorder;

    mount(ctx.main, html`
        ${pageHeader(`${m.medicine} ${m.strength || ""}`, `${m.dosage_form || "—"} · Medicine detail`, html`
            <a class="view-btn" href="#/medicines">← Medicines</a>
            ${ctx.can("medicines.write") ? html`<button type="button" class="refresh-btn" data-action="edit">Edit</button>` : ""}
            ${ctx.can("batches.write") ? html`<button type="button" class="refresh-btn" data-action="add-batch">+ Batch / Opening Stock</button>` : ""}
            ${ctx.can("stock.dispense") && m.usable_stock > 0 ? html`<a class="refresh-btn primary" href="#/dispense?medicine=${m.medicine_id}">Dispense</a>` : ""}`)}

        <section class="cards">
            <div class="card"><div><span>Usable stock</span><strong>${number(m.usable_stock)}</strong><small class="card-hint">${badge(m.stock_status)}</small></div></div>
            <div class="card"><div><span>Expired on shelf</span><strong>${number(m.expired_stock)}</strong></div></div>
            <div class="card"><div><span>Reorder level</span><strong>${number(m.reorder_level)}</strong>
                <small class="card-hint">${reorder?.reorder_recommended ? `Reorder ${number(reorder.recommended_quantity)} units` : "No reorder needed"}</small></div></div>
            <div class="card"><div><span>Stock value</span><strong>${money(m.stock_value)}</strong>
                ${m.units_without_cost ? html`<small class="card-hint">${number(m.units_without_cost)} units without cost</small>` : ""}</div></div>
            <div class="card"><div><span>Consumption</span><strong>${number(use.average_daily, 2)}/day</strong>
                <small class="card-hint">${number(use.dispensed_30d)} in 30 d · ${number(use.dispensed_90d)} in 90 d · ${use.trend || ""}</small></div></div>
            <div class="card"><div><span>Days of stock</span><strong>${reorder?.days_of_stock ?? "—"}</strong>
                <small class="card-hint">${reorder?.on_order ? `${number(reorder.on_order)} on order` : "Nothing on order"}</small></div></div>
        </section>

        <section class="section">
            <div class="section-header"><div><h3>FEFO order</h3><p>Usable batches, earliest expiry first — dispense from the top</p></div></div>
            ${table("fefo", [
                { label: "#", render: (b) => data.fefo_order.indexOf(b) + 1 },
                { label: "Batch", render: b => html`<a href="#/batches/${b.batch_id}">${b.batch_number}</a>` },
                { label: "Quantity", render: b => number(b.quantity), className: "num" },
                { label: "Expiry", render: b => formatDate(b.expiry_date) },
                { label: "Days left", render: b => daysLabel(b.days_until_expiry) },
                { label: "Location", key: "location" },
            ], data.fefo_order, { empty: "No usable stock." })}
        </section>

        <section class="section">
            <div class="section-header"><div><h3>All batches</h3><p>Including expired and empty batches</p></div></div>
            ${table("med-batches", [
                { label: "Batch", render: b => html`<a href="#/batches/${b.batch_id}">${b.batch_number}</a>` },
                { label: "Qty", render: b => number(b.quantity), className: "num" },
                { label: "Expiry", render: b => formatDate(b.expiry_date) },
                { label: "Status", render: b => badge(b.status) },
                { label: "Location", key: "location" },
                { label: "Supplier", render: b => b.supplier || "—" },
                { label: "Unit cost", render: b => money(b.unit_cost), className: "num" },
                { label: "Value", render: b => money(b.stock_value), className: "num" },
            ], data.batches)}
        </section>

        <div class="lower-grid">
            <section class="section">
                <div class="section-header"><div><h3>Recent movements</h3><p>Last 50</p></div></div>
                ${table("med-moves", [
                    { label: "Date", render: x => formatDateTime(x.movement_date) },
                    { label: "Batch", key: "batch_number" },
                    { label: "Type", render: x => badge(x.movement_type) },
                    { label: "Qty", render: x => signedQuantity(x), className: "num" },
                    { label: "By", render: x => x.user_name || "—" },
                ], data.movements)}
            </section>
            <section class="section">
                <div class="section-header"><div><h3>Purchase history</h3><p>Orders including this medicine</p></div></div>
                ${table("med-po", [
                    { label: "Order", render: p => html`<a href="#/purchasing/${p.purchase_order_id}">${p.order_number}</a>` },
                    { label: "Supplier", key: "supplier" },
                    { label: "Date", render: p => formatDate(p.order_date) },
                    { label: "Qty", render: p => `${number(p.quantity_received)} / ${number(p.quantity_ordered)}` },
                    { label: "Unit cost", render: p => money(p.unit_cost) },
                    { label: "Status", render: p => badge(p.status) },
                ], data.purchase_history, { empty: "Never ordered." })}
            </section>
        </div>`);

    const { openBatchForm } = await import("./inventory.js");
    onAction(ctx.main, {
        edit: () => openMedicineForm({ id: m.medicine_id, name: m.medicine, strength: m.strength,
            dosage_form: m.dosage_form, reorder_level: m.reorder_level }, () => ctx.reload()),
        "add-batch": () => openBatchForm(m.medicine_id, () => ctx.reload()),
    });
}
