/* Medicines: list, search, filter, sort, add, edit, detail. */

import {
    api, badge, daysLabel, debounce, formatDate, formatDateTime, formModal, html, money, mount, number,
    onAction, pageHeader, signedQuantity, sortableTable, table, toast,
} from "../core.js";

const MEDICINE_FIELDS = (medicine) => [
    { name: "name", label: "Medicine name", required: true, maxlength: 150, value: medicine.name, placeholder: "e.g. Paracetamol" },
    { name: "strength", label: "Strength", maxlength: 50, value: medicine.strength, placeholder: "e.g. 500 mg" },
    { name: "dosage_form", label: "Dosage form", maxlength: 50, value: medicine.dosage_form, placeholder: "e.g. Tablet" },
    { name: "reorder_level", label: "Reorder level (units)", type: "number", min: 0, step: 1, required: true,
      value: medicine.reorder_level ?? 20, help: "Low-stock alert when usable stock falls to this level." },
    { name: "selling_price", label: "Selling price per unit", type: "number", min: 0, step: "0.01",
      value: medicine.selling_price, help: "Default price at the dispensing counter (optional; can be changed per sale)." },
    { name: "generic_name", label: "Generic name (INN)", maxlength: 150, value: medicine.generic_name, placeholder: "e.g. Amoxicillin" },
    { name: "brand_name", label: "Brand name", maxlength: 150, value: medicine.brand_name, placeholder: "e.g. Amoxil" },
    { name: "route", label: "Route", type: "select", placeholder: "—", value: medicine.route,
      options: ["Oral", "Topical", "Intravenous", "Intramuscular", "Subcutaneous", "Inhalation", "Rectal", "Vaginal",
                "Ophthalmic", "Otic", "Nasal", "Sublingual", "Transdermal", "Other"].map(r => ({ value: r, label: r })) },
    { name: "manufacturer", label: "Manufacturer", maxlength: 150, value: medicine.manufacturer },
    { name: "gtin", label: "Barcode / GTIN", maxlength: 20, value: medicine.gtin, autocomplete: "off",
      help: "Scan or type the pack barcode (EAN-13 / GTIN-14). Used by barcode scanning." },
    { name: "is_active", label: "Active (can be ordered and stocked)", type: "checkbox", value: medicine.is_active ?? true },
];

function validateMedicine(values) {
    if (!values.name) return "Medicine name is required.";
    if (!Number.isInteger(values.reorder_level) || values.reorder_level < 0) return "Reorder level must be a whole number of 0 or more.";
    if (values.selling_price !== null && (Number.isNaN(values.selling_price) || values.selling_price < 0)) return "Selling price cannot be negative.";
    return null;
}

/** prefill: values for a NEW medicine (e.g. the GTIN from a scanned pack). */
export function openMedicineForm(medicine, onSaved, prefill = {}) {
    const fields = MEDICINE_FIELDS(medicine || prefill);
    formModal({
        title: medicine ? "Edit Medicine" : "Add Medicine",
        intro: !medicine && prefill.gtin
            ? `Barcode ${prefill.gtin} is not registered yet. Check the pack and fill in the details; nothing is looked up from outside sources.`
            : "",
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
    const [levels, master] = await Promise.all([api("/stock-alerts"), api("/medicines")]);
    if (!ctx.isCurrent()) return;
    const byId = new Map(master.map(m => [m.id, m]));
    const stock = levels.map(level => ({ ...byId.get(level.medicine_id), ...level }));

    const canWrite = ctx.can("medicines.write");
    mount(ctx.main, html`
        ${pageHeader("Medicines", "Registered medicines, usable stock and stock status", html`
            <button type="button" class="refresh-btn" data-action="refresh">↻ Refresh</button>
            ${canWrite ? html`<button type="button" class="refresh-btn primary" data-action="add">+ Add Medicine</button>` : ""}`)}
        <section class="section">
            <div class="toolbar">
                <input type="search" id="med-search" placeholder="Search name, brand, generic, strength, form or barcode…" aria-label="Search medicines">
                <select id="med-status" aria-label="Filter by stock status">
                    <option value="">All stock statuses</option>
                    <option>NORMAL</option><option>LOW STOCK</option><option>OUT OF STOCK</option>
                </select>
                <select id="med-active" aria-label="Filter by active status">
                    <option value="active">Active medicines</option>
                    <option value="inactive">Inactive (discontinued)</option>
                    <option value="">All medicines</option>
                </select>
                <span class="toolbar-count" id="med-count"></span>
            </div>
            <div id="med-table"></div>
        </section>`);

    const columns = [
        { label: "Medicine", key: "medicine", render: m => html`<a href="#/medicines/${m.medicine_id}"><strong>${m.medicine}</strong></a>
            ${m.brand_name || (m.generic_name && m.generic_name !== m.medicine) ? html`<br><small>${[m.brand_name, m.generic_name !== m.medicine ? m.generic_name : ""].filter(Boolean).join(" · ")}</small>` : ""}
            ${m.is_active === false ? html` ${badge("Inactive")}` : ""}` },
        { label: "Strength", key: "strength", render: m => m.strength || "—" },
        { label: "Dosage Form", key: "dosage_form", render: m => m.dosage_form || "—" },
        { label: "Usable Stock", key: "current_stock", render: m => number(m.current_stock), className: "num" },
        { label: "Expired", key: "expired_stock", render: m => m.expired_stock ? number(m.expired_stock) : "—", className: "num" },
        { label: "Reorder Level", key: "reorder_level", render: m => number(m.reorder_level), className: "num" },
        { label: "Price", key: "selling_price", render: m => money(m.selling_price), className: "num", sort: m => Number(m.selling_price ?? -1) },
        { label: "Next Expiry", key: "next_expiry", render: m => formatDate(m.next_expiry) },
        { label: "Status", key: "status", render: m => badge(m.status) },
        { label: "Actions", render: m => html`<a class="view-btn" href="#/medicines/${m.medicine_id}">View</a>
            ${canWrite ? html`<button type="button" class="view-btn" data-action="edit" data-id="${m.medicine_id}">Edit</button>` : ""}` },
    ];

    const container = ctx.main.querySelector("#med-table");
    const search = ctx.main.querySelector("#med-search");
    const status = ctx.main.querySelector("#med-status");
    const active = ctx.main.querySelector("#med-active");

    const draw = () => {
        const term = search.value.trim().toLowerCase();
        const digits = term.replace(/\D/g, "");
        const rows = stock.filter(m =>
            (!term || [m.medicine, m.strength, m.dosage_form, m.brand_name, m.generic_name]
                .some(v => (v || "").toLowerCase().includes(term))
                || (digits.length >= 8 && m.gtin && m.gtin.endsWith(digits.replace(/^0+/, ""))))
            && (!status.value || m.status === status.value)
            && (!active.value || (active.value === "active") === (m.is_active !== false)));
        ctx.main.querySelector("#med-count").textContent = `${rows.length} of ${stock.length}`;
        sortableTable(container, "medicines-table", columns, rows, { empty: "No medicines found." });
    };
    search.addEventListener("input", debounce(draw, 150));
    status.addEventListener("change", draw);
    active.addEventListener("change", draw);
    draw();

    onAction(ctx.main, {
        refresh: () => ctx.reload(),
        add: () => openMedicineForm(null, () => ctx.reload()),
        edit: el => {
            const medicine = byId.get(Number(el.dataset.id));
            if (medicine) openMedicineForm(medicine, () => ctx.reload());
        },
    });
    // #/medicines?new=1[&gtin=…] (quick action, Scan Center "product not found").
    if (ctx.params.query.new && canWrite) {
        openMedicineForm(null, saved => ctx.navigate(`medicines/${saved.id}`), { gtin: ctx.params.query.gtin || "" });
    }
}

export async function renderDetail(ctx) {
    const data = await api(`/medicines/${encodeURIComponent(ctx.params.id)}`);
    if (!ctx.isCurrent()) return;
    const m = data.medicine;
    const use = data.consumption || {};
    const reorder = data.reorder;

    mount(ctx.main, html`
        ${pageHeader(`${m.medicine} ${m.strength || ""}`,
            [m.dosage_form || "—", m.brand_name, m.generic_name && m.generic_name !== m.medicine ? `generic: ${m.generic_name}` : "",
             m.route, m.gtin ? `GTIN ${m.gtin}` : "", m.is_active === false ? "INACTIVE (discontinued)" : ""]
                .filter(Boolean).join(" · "), html`
            <a class="view-btn" href="#/medicines">← Medicines</a>
            ${ctx.can("medicines.write") ? html`<button type="button" class="refresh-btn" data-action="edit">Edit</button>` : ""}
            ${ctx.can("batches.write") ? html`<button type="button" class="refresh-btn" data-action="add-batch">+ Batch / Opening Stock</button>` : ""}
            ${ctx.can("stock.dispense") && m.usable_stock > 0 ? html`<a class="refresh-btn primary" href="#/dispense?medicine=${m.medicine_id}">Dispense</a>` : ""}`)}

        <section class="cards">
            <div class="card"><div><span>Usable stock</span><strong>${number(m.usable_stock)}</strong><small class="card-hint">${badge(m.stock_status)}</small></div></div>
            <div class="card"><div><span>Expired on shelf</span><strong>${number(m.expired_stock)}</strong>
                ${m.held_stock ? html`<small class="card-hint">${number(m.held_stock)} units quarantined / recalled</small>` : ""}</div></div>
            <div class="card"><div><span>Selling price</span><strong>${money(m.selling_price)}</strong></div></div>
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
                { label: "Status", render: b => html`${badge(b.status)} ${b.batch_status !== "ACTIVE" ? badge(b.batch_status) : ""}` },
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
        edit: async () => {
            const medicine = (await api("/medicines")).find(x => x.id === m.medicine_id);
            if (medicine) openMedicineForm(medicine, () => ctx.reload());
        },
        "add-batch": () => openBatchForm(m.medicine_id, () => ctx.reload()),
    });
}
