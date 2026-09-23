/* FEFO dispensing and stock movement history. */

import {
    api, badge, daysLabel, debounce, download, formatDate, formatDateTime, html, mount, number, onAction, pageHeader,
    sortableTable, table, toast, today,
} from "../core.js";
import { signedQuantity } from "./dashboard.js";

const TYPES = ["RECEIVED", "DISPENSED", "RETURNED", "DAMAGED", "EXPIRED", "ADJUSTMENT"];

export async function renderDispense(ctx) {
    const [stock, locations] = await Promise.all([api("/stock-alerts"), api("/locations")]);
    if (!ctx.isCurrent()) return;
    const available = stock.filter(m => m.current_stock > 0);
    const preselected = ctx.params.query?.medicine || "";

    mount(ctx.main, html`
        ${pageHeader("Dispense (FEFO)", "Stock is taken from the earliest-expiring usable batches first")}
        <section class="section">
            <form id="dispense-form" class="form-grid" novalidate>
                <div class="field">
                    <label for="d-medicine">Medicine <span class="req" aria-hidden="true">*</span></label>
                    <select id="d-medicine" required>
                        <option value="">Select a medicine…</option>
                        ${available.map(m => html`<option value="${m.medicine_id}" ${String(m.medicine_id) === preselected ? html`selected` : ""}>
                            ${m.medicine} ${m.strength || ""} ${m.dosage_form || ""} — ${number(m.current_stock)} usable</option>`)}
                    </select>
                </div>
                <div class="field">
                    <label for="d-quantity">Quantity <span class="req" aria-hidden="true">*</span></label>
                    <input id="d-quantity" type="number" min="1" step="1" required>
                </div>
                <div class="field">
                    <label for="d-location">Location</label>
                    <select id="d-location"><option value="">Any location</option>
                        ${locations.filter(l => l.is_active).map(l => html`<option value="${l.id}">${l.name}</option>`)}</select>
                </div>
                <div class="field full">
                    <label for="d-reason">Reference (prescription / ward / note)</label>
                    <input id="d-reason" maxlength="500">
                </div>
                <div class="full" id="fefo-preview"></div>
                <div class="form-error full" role="alert" hidden></div>
                <div class="form-actions full">
                    <button type="submit" class="refresh-btn primary">Dispense</button>
                </div>
            </form>
        </section>
        <section class="section" id="dispense-result" hidden></section>`);

    const form = ctx.main.querySelector("#dispense-form");
    const medicine = form.querySelector("#d-medicine");
    const quantity = form.querySelector("#d-quantity");
    const location = form.querySelector("#d-location");
    const preview = form.querySelector("#fefo-preview");
    const errorBox = form.querySelector(".form-error");

    const updatePreview = async () => {
        errorBox.hidden = true;
        const qty = Number(quantity.value);
        if (!medicine.value) { mount(preview, ""); return; }
        try {
            const plan = await api(`/fefo/${medicine.value}`, {
                params: { quantity: Number.isInteger(qty) && qty > 0 ? qty : 1, location_id: location.value },
            });
            const allocating = Number.isInteger(qty) && qty > 0;
            mount(preview, html`
                <h4>FEFO plan ${allocating ? html`for ${number(qty)} units` : html`(usable batches in order)`}</h4>
                ${table("fefo-plan", [
                    { label: "Order", render: b => plan.allocations.indexOf(b) + 1 },
                    { label: "Batch", key: "batch_number" },
                    { label: "Expiry", render: b => `${formatDate(b.expiry_date)} (${daysLabel(b.days_until_expiry)})` },
                    { label: "In batch", render: b => number(b.quantity), className: "num" },
                    { label: "Take", render: b => html`<strong>${allocating ? number(b.allocate) : "—"}</strong>`, className: "num" },
                    { label: "Location", key: "location" },
                ], allocating ? plan.allocations : plan.allocations.slice(0, 1), { empty: "No usable stock." })}
                ${plan.shortfall > 0 && allocating ? html`<p class="warning-text" role="alert">Only ${number(plan.available_usable_stock)} usable units available — short by ${number(plan.shortfall)}.</p>` : ""}`);
        } catch (error) {
            mount(preview, html`<p class="error-text">${error.message}</p>`);
        }
    };
    medicine.addEventListener("change", updatePreview);
    location.addEventListener("change", updatePreview);
    quantity.addEventListener("input", debounce(updatePreview, 250));
    if (preselected) updatePreview();

    form.addEventListener("submit", async event => {
        event.preventDefault();
        errorBox.hidden = true;
        const qty = Number(quantity.value);
        if (!medicine.value || !Number.isInteger(qty) || qty <= 0) {
            errorBox.textContent = "Choose a medicine and a whole-number quantity greater than zero.";
            errorBox.hidden = false;
            return;
        }
        const button = form.querySelector("button[type=submit]");
        button.disabled = true;
        try {
            const result = await api("/dispense", { method: "POST", body: {
                medicine_id: Number(medicine.value), quantity: qty,
                location_id: location.value ? Number(location.value) : null,
                reason: form.querySelector("#d-reason").value.trim() || null,
            } });
            toast(`Dispensed ${number(result.quantity_dispensed)} × ${result.medicine}.`);
            const box = ctx.main.querySelector("#dispense-result");
            box.hidden = false;
            mount(box, html`
                <div class="section-header"><div><h3>Dispensed ${number(result.quantity_dispensed)} × ${result.medicine} ${result.strength || ""}</h3>
                <p>Movements recorded (FEFO)</p></div></div>
                ${table("dispense-done", [
                    { label: "Batch", key: "batch_number" },
                    { label: "Expiry", render: m => formatDate(m.expiry_date) },
                    { label: "Quantity", render: m => number(m.quantity), className: "num" },
                    { label: "Left in batch", render: m => number(m.batch_quantity_after), className: "num" },
                ], result.movements)}`);
            quantity.value = "";
            updatePreview();
        } catch (error) {
            errorBox.textContent = error.message;
            errorBox.hidden = false;
        } finally {
            button.disabled = false;
        }
    });
}

export async function renderMovements(ctx) {
    const medicines = await api("/medicines", { params: { sort: "name" } });
    if (!ctx.isCurrent()) return;

    mount(ctx.main, html`
        ${pageHeader("Stock Movements", "Complete transaction history of every batch", html`
            ${ctx.can("reports.export") ? html`<button type="button" class="refresh-btn" data-action="export">Export CSV</button>` : ""}`)}
        <section class="section">
            <div class="toolbar">
                <select id="mv-type" aria-label="Movement type"><option value="">All types</option>${TYPES.map(t => html`<option>${t}</option>`)}</select>
                <select id="mv-medicine" aria-label="Medicine"><option value="">All medicines</option>
                    ${medicines.map(m => html`<option value="${m.id}">${m.name} ${m.strength || ""}</option>`)}</select>
                <label>From <input type="date" id="mv-from" value="${today(-90)}"></label>
                <label>To <input type="date" id="mv-to" value="${today()}"></label>
                <span class="toolbar-count" id="mv-count"></span>
            </div>
            <div id="mv-table"></div>
        </section>`);

    const filters = () => ({
        movement_type: ctx.main.querySelector("#mv-type").value,
        medicine_id: ctx.main.querySelector("#mv-medicine").value,
        date_from: ctx.main.querySelector("#mv-from").value,
        date_to: ctx.main.querySelector("#mv-to").value,
    });
    const columns = [
        { label: "Date", key: "movement_date", render: m => formatDateTime(m.movement_date) },
        { label: "Medicine", key: "medicine", render: m => html`<a href="#/medicines/${m.medicine_id}">${m.medicine}</a> <small>${m.strength}</small>` },
        { label: "Batch", key: "batch_number", render: m => html`<a href="#/batches/${m.batch_id}">${m.batch_number}</a>` },
        { label: "Location", key: "location" },
        { label: "Type", key: "movement_type", render: m => badge(m.movement_type) },
        { label: "Qty", key: "quantity", render: m => signedQuantity(m), className: "num" },
        { label: "Reason", key: "reason", render: m => m.reason || "—" },
        { label: "User", key: "user_name", render: m => m.user_name || "—" },
    ];
    const load = async () => {
        const rows = await api("/stock-movements", { params: { ...filters(), limit: 2000 } });
        ctx.main.querySelector("#mv-count").textContent = `${rows.length} movements`;
        sortableTable(ctx.main.querySelector("#mv-table"), "movements-table", columns, rows, { empty: "No movements in this period." });
    };
    ctx.main.querySelectorAll(".toolbar select, .toolbar input").forEach(el => el.addEventListener("change", load));
    await load();

    onAction(ctx.main, {
        export: () => {
            const f = filters();
            download("/reports/stock-movements", { format: "csv", movement_type: f.movement_type, date_from: f.date_from, date_to: f.date_to })
                .catch(error => toast(error.message, "error"));
        },
    });
}
