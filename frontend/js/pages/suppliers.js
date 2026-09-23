/* Suppliers: list, search, add, edit, activate/deactivate, detail. */

import {
    api, badge, debounce, formModal, formatDate, html, money, mount, number, onAction, pageHeader, sortableTable,
    statTile, table, toast,
} from "../core.js";

function supplierFields(s = {}) {
    return [
        { name: "name", label: "Supplier name", required: true, maxlength: 150, value: s.name },
        { name: "contact_person", label: "Contact person", maxlength: 150, value: s.contact_person },
        { name: "phone", label: "Phone", maxlength: 50, value: s.phone, type: "tel" },
        { name: "email", label: "Email", maxlength: 150, value: s.email, type: "email" },
        { name: "address", label: "Address", type: "textarea", full: true, value: s.address },
    ];
}

function openSupplierForm(supplier, onSaved) {
    formModal({
        title: supplier ? "Edit Supplier" : "Add Supplier",
        fields: supplierFields(supplier || {}),
        validate: v => (v.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v.email) ? "Enter a valid email address." : null),
        onSubmit: async values => {
            await api(supplier ? `/suppliers/${supplier.id}` : "/suppliers", { method: supplier ? "PUT" : "POST", body: values });
            toast(supplier ? "Supplier updated." : "Supplier added.");
            onSaved();
        },
    });
}

async function toggleActive(supplier, onDone) {
    try {
        await api(`/suppliers/${supplier.id}/${supplier.is_active ? "deactivate" : "activate"}`, { method: "POST" });
        toast(supplier.is_active ? "Supplier deactivated." : "Supplier activated.");
        onDone();
    } catch (error) { toast(error.message, "error"); }
}

export async function renderList(ctx) {
    const suppliers = await api("/suppliers");
    if (!ctx.isCurrent()) return;
    const canWrite = ctx.can("suppliers.write");

    mount(ctx.main, html`
        ${pageHeader("Suppliers", "Supplier directory and purchasing history", canWrite
            ? html`<button type="button" class="refresh-btn primary" data-action="add">+ Add Supplier</button>` : "")}
        <section class="section">
            <div class="toolbar">
                <input type="search" id="sup-search" placeholder="Search name, contact, phone, email…" aria-label="Search suppliers">
                <select id="sup-active" aria-label="Status"><option value="">Active and inactive</option>
                    <option value="true">Active</option><option value="false">Inactive</option></select>
            </div>
            <div id="sup-table"></div>
        </section>`);

    const columns = [
        { label: "Supplier", key: "name", render: s => html`<a href="#/suppliers/${s.id}"><strong>${s.name}</strong></a>` },
        { label: "Contact", key: "contact_person", render: s => s.contact_person || "—" },
        { label: "Phone", key: "phone", render: s => s.phone || "—" },
        { label: "Email", key: "email", render: s => s.email || "—" },
        { label: "Status", key: "is_active", render: s => badge(s.is_active ? "Active" : "Inactive") },
        { label: "", render: s => html`<a class="view-btn" href="#/suppliers/${s.id}">View</a>
            ${canWrite ? html`<button type="button" class="view-btn" data-action="edit" data-id="${s.id}">Edit</button>
            <button type="button" class="view-btn" data-action="toggle" data-id="${s.id}">${s.is_active ? "Deactivate" : "Activate"}</button>` : ""}` },
    ];
    const search = ctx.main.querySelector("#sup-search");
    const active = ctx.main.querySelector("#sup-active");
    const draw = () => {
        const term = search.value.trim().toLowerCase();
        sortableTable(ctx.main.querySelector("#sup-table"), "suppliers-table", columns, suppliers.filter(s =>
            (!term || [s.name, s.contact_person, s.phone, s.email].some(v => (v || "").toLowerCase().includes(term)))
            && (!active.value || String(s.is_active) === active.value)), { empty: "No suppliers found." });
    };
    search.addEventListener("input", debounce(draw, 150));
    active.addEventListener("change", draw);
    draw();

    const find = el => suppliers.find(s => s.id === Number(el.dataset.id));
    onAction(ctx.main, {
        add: () => openSupplierForm(null, () => ctx.reload()),
        edit: el => openSupplierForm(find(el), () => ctx.reload()),
        toggle: el => toggleActive(find(el), () => ctx.reload()),
    });
}

export async function renderDetail(ctx) {
    const data = await api(`/suppliers/${encodeURIComponent(ctx.params.id)}`);
    if (!ctx.isCurrent()) return;
    const s = data.supplier;
    const canWrite = ctx.can("suppliers.write");

    mount(ctx.main, html`
        ${pageHeader(s.name, [s.contact_person, s.phone, s.email].filter(Boolean).join(" · ") || "Supplier", html`
            <a class="view-btn" href="#/suppliers">← Suppliers</a>
            ${canWrite ? html`<button type="button" class="refresh-btn" data-action="edit">Edit</button>
                <button type="button" class="refresh-btn" data-action="toggle">${s.is_active ? "Deactivate" : "Activate"}</button>` : ""}`)}
        <section class="cards">
            <div class="card"><div><span>Status</span><strong>${badge(s.is_active ? "Active" : "Inactive")}</strong></div></div>
            ${statTile("Orders", `${number(data.summary.orders)} (${number(data.summary.open_orders)} open)`, "blue")}
            ${statTile("Ordered value", money(data.summary.total_ordered_value), "blue", "excluding cancelled")}
            ${statTile("Received value", money(data.summary.total_received_value), "green")}
        </section>
        ${s.address ? html`<section class="section"><p class="notes">${s.address}</p></section>` : ""}
        <div class="lower-grid">
            <section class="section">
                <div class="section-header"><div><h3>Purchase history</h3></div></div>
                ${table("sup-orders", [
                    { label: "Order", render: o => html`<a href="#/purchasing/${o.id}">${o.order_number}</a>` },
                    { label: "Date", render: o => formatDate(o.order_date) },
                    { label: "Lines", render: o => number(o.items) },
                    { label: "Value", render: o => money(o.order_value) },
                    { label: "Status", render: o => badge(o.status) },
                ], data.purchase_history, { empty: "No orders yet." })}
            </section>
            <section class="section">
                <div class="section-header"><div><h3>Products supplied</h3></div></div>
                ${table("sup-products", [
                    { label: "Medicine", render: p => html`<a href="#/medicines/${p.medicine_id}">${p.medicine}</a> <small>${p.strength}</small>` },
                    { label: "Received / ordered", render: p => `${number(p.units_received)} / ${number(p.units_ordered)}` },
                    { label: "Avg cost", render: p => money(p.average_unit_cost) },
                    { label: "Last ordered", render: p => formatDate(p.last_ordered) },
                ], data.products_supplied, { empty: "No products ordered yet." })}
            </section>
        </div>`);

    onAction(ctx.main, {
        edit: () => openSupplierForm(s, () => ctx.reload()),
        toggle: () => toggleActive(s, () => ctx.reload()),
    });
}
