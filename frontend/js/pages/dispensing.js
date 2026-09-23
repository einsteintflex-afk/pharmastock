/* Dispensing counter (community pharmacy), history, receipts and voids. */

import {
    api, badge, debounce, formModal, formatDate, formatDateTime, html, money, mount, number, onAction, openModal,
    pageHeader, plural, sortableTable, statTile, toast, today,
} from "../core.js";

const PAYMENT_LABELS = {
    CASH: "Cash", MOBILE_MONEY: "Mobile money", CARD: "Card", NHIS: "NHIS", INSURANCE: "Private insurance",
    CREDIT: "Credit / account", NO_CHARGE: "No charge",
};

let receiptHeader = { name: "PharmaStock Pharmacy", address: "", phone: "" };

async function loadReceiptHeader() {
    try {
        const settings = await api("/settings");
        const value = key => settings.find(s => s.key === key)?.value || "";
        receiptHeader = { name: value("pharmacy.name"), address: value("pharmacy.address"), phone: value("pharmacy.phone") };
    } catch { /* keep default */ }
}

/* ---------- Receipt ---------- */

function receiptHtml(d) {
    const priced = d.items.some(i => i.unit_price !== null);
    return html`
        <div class="receipt" id="receipt">
            <div class="receipt-head">
                <strong>${receiptHeader.name}</strong>
                ${receiptHeader.address ? html`<div>${receiptHeader.address}</div>` : ""}
                ${receiptHeader.phone ? html`<div>Tel: ${receiptHeader.phone}</div>` : ""}
            </div>
            ${d.status === "VOIDED" ? html`<div class="receipt-void">VOIDED — ${d.void_reason}</div>` : ""}
            <dl class="receipt-meta">
                <div><dt>No.</dt><dd>${d.dispensation_number}</dd></div>
                <div><dt>Date</dt><dd>${formatDateTime(d.dispensed_at)}</dd></div>
                <div><dt>Type</dt><dd>${d.dispense_type === "PRESCRIPTION" ? "Prescription" : "Over the counter"}</dd></div>
                ${d.patient_name ? html`<div><dt>Patient</dt><dd>${d.patient_name}</dd></div>` : ""}
                ${d.prescriber ? html`<div><dt>Prescriber</dt><dd>${d.prescriber}</dd></div>` : ""}
                ${d.prescription_number ? html`<div><dt>Rx no.</dt><dd>${d.prescription_number}</dd></div>` : ""}
            </dl>
            <table class="receipt-lines">
                <thead><tr><th>Item</th><th class="num">Qty</th>${priced ? html`<th class="num">Price</th><th class="num">Amount</th>` : ""}</tr></thead>
                <tbody>${d.items.map(i => html`
                    <tr>
                        <td><strong>${i.medicine} ${i.strength || ""}</strong> ${i.dosage_form || ""}
                            ${i.directions ? html`<div class="directions">${i.directions}</div>` : ""}
                            <div class="batch-note">Batch ${i.batches.map(b => `${b.batch_number} (exp ${formatDate(b.expiry_date)})`).join(", ")}</div></td>
                        <td class="num">${number(i.quantity)}</td>
                        ${priced ? html`<td class="num">${money(i.unit_price)}</td><td class="num">${money(i.line_total)}</td>` : ""}
                    </tr>`)}</tbody>
            </table>
            <div class="receipt-total">
                <span>Total</span><strong>${money(d.total_amount)}</strong>
            </div>
            <div class="receipt-foot">
                Paid by: ${PAYMENT_LABELS[d.payment_method] || d.payment_method} · Served by: ${d.dispensed_by}
            </div>
        </div>`;
}

function printReceipt() {
    document.body.classList.add("printing-receipt");
    const done = () => { document.body.classList.remove("printing-receipt"); window.removeEventListener("afterprint", done); };
    window.addEventListener("afterprint", done);
    window.print();
    setTimeout(done, 1000);
}

export function showReceipt(dispensation) {
    const body = openModal(`Receipt ${dispensation.dispensation_number}`, html`
        ${receiptHtml(dispensation)}
        <div class="form-actions receipt-actions">
            <a class="view-btn" href="#/dispensations/${dispensation.id}">Open record</a>
            <button type="button" class="refresh-btn primary" data-print="1">Print receipt</button>
        </div>`);
    body.querySelector("[data-print]").addEventListener("click", printReceipt);
}

/* ---------- Counter ---------- */

export async function renderCounter(ctx) {
    const [stock, locations, summary] = await Promise.all([
        api("/stock-alerts"), api("/locations"), api("/dispensations/summary"), loadReceiptHeader(),
    ]);
    if (!ctx.isCurrent()) return;

    const activeLocations = locations.filter(l => l.is_active);
    const cart = [];   // { medicine, quantity, unit_price, directions, plan }
    const preselected = Number(ctx.params.query?.medicine || 0);

    mount(ctx.main, html`
        ${pageHeader("Dispensing Counter", "Prescriptions and over-the-counter sales · stock is taken earliest-expiry-first",
            html`<a class="view-btn" href="#/dispensations">Dispensing history</a>`)}
        <section class="cards">
            ${statTile("Dispensed today", plural(summary.dispensations, "transaction"), "blue", `${summary.prescriptions} prescriptions`, "🧾")}
            ${statTile("Units today", number(summary.units), "green", "", "💊")}
            ${statTile("Sales today", money(summary.sales_total), "blue",
                summary.voided ? `${plural(summary.voided, "void")}` : "", "₵")}
        </section>

        <div class="counter-grid">
            <section class="section">
                <div class="section-header"><div><h3>1. Find medicine</h3><p>Only medicines with usable (non-expired) stock are listed</p></div></div>
                <div class="toolbar"><input type="search" id="c-search" placeholder="Type a medicine name or scan a barcode…" aria-label="Search medicines or scan a barcode" autocomplete="off"></div>
                <ul class="pick-list" id="c-results"></ul>
            </section>

            <section class="section">
                <div class="section-header"><div><h3>2. Items</h3><p>FEFO batches are chosen automatically</p></div></div>
                <div id="c-cart"></div>
                <div class="cart-total"><span>Total</span><strong id="c-total">${money(0)}</strong></div>
            </section>
        </div>

        <section class="section">
            <div class="section-header"><div><h3>3. Details and payment</h3></div></div>
            <form id="c-form" class="form-grid" novalidate>
                <fieldset class="field full type-toggle">
                    <legend>Dispensing type</legend>
                    <label><input type="radio" name="dispense_type" value="OTC" checked> Over the counter</label>
                    <label><input type="radio" name="dispense_type" value="PRESCRIPTION"> Prescription</label>
                </fieldset>
                <div class="field rx-only" hidden><label for="c-prescriber">Prescriber</label><input id="c-prescriber" name="prescriber" maxlength="150" placeholder="e.g. Dr. A. Mensah, Korle Bu"></div>
                <div class="field rx-only" hidden><label for="c-rx">Prescription number</label><input id="c-rx" name="prescription_number" maxlength="100"></div>
                <div class="field"><label for="c-patient">Patient / customer name</label><input id="c-patient" name="patient_name" maxlength="150" placeholder="Optional"></div>
                <div class="field"><label for="c-phone">Phone</label><input id="c-phone" name="patient_phone" type="tel" maxlength="50" placeholder="Optional"></div>
                <div class="field"><label for="c-payment">Payment method <span class="req" aria-hidden="true">*</span></label>
                    <select id="c-payment" name="payment_method">${Object.entries(PAYMENT_LABELS).map(([v, l]) => html`<option value="${v}">${l}</option>`)}</select></div>
                ${activeLocations.length > 1 ? html`<div class="field"><label for="c-location">Dispense from</label>
                    <select id="c-location" name="location_id"><option value="">Any location</option>
                    ${activeLocations.map(l => html`<option value="${l.id}">${l.name}</option>`)}</select></div>` : ""}
                <div class="field full"><label for="c-notes">Notes</label><input id="c-notes" name="notes" maxlength="500" placeholder="Optional"></div>
                <div class="form-error full" role="alert" hidden></div>
                <div class="form-actions full">
                    <button type="button" class="view-btn" data-action="clear">Clear</button>
                    <button type="submit" class="refresh-btn primary big">Complete dispensing</button>
                </div>
            </form>
        </section>`);

    const form = ctx.main.querySelector("#c-form");
    const errorBox = form.querySelector(".form-error");
    const search = ctx.main.querySelector("#c-search");
    const results = ctx.main.querySelector("#c-results");
    const cartBox = ctx.main.querySelector("#c-cart");
    const locationValue = () => form.elements.location_id?.value || null;

    const drawResults = () => {
        const term = search.value.trim().toLowerCase();
        const available = stock.filter(m => m.current_stock > 0 && !cart.some(c => c.medicine.medicine_id === m.medicine_id)
            && (!term || [m.medicine, m.strength, m.dosage_form].some(v => (v || "").toLowerCase().includes(term))));
        mount(results, available.length ? available.slice(0, 12).map(m => html`
            <li>
                <button type="button" class="pick" data-action="add" data-id="${m.medicine_id}">
                    <span><strong>${m.medicine}</strong> ${m.strength || ""} <small>${m.dosage_form || ""}</small></span>
                    <span class="pick-meta">${number(m.current_stock)} in stock · ${m.selling_price !== null ? money(m.selling_price) : "no price"}</span>
                </button>
            </li>`) : html`<li class="loading">${term ? "No medicine with usable stock matches." : "All available medicines are in the list."}</li>`);
    };

    const planText = line => (line.plan && line.plan.allocations.length
        ? `FEFO: ${line.plan.allocations.map(a => `${a.batch_number} ×${a.allocate}`).join(", ")}` : "");

    const updateTotal = () => {
        ctx.main.querySelector("#c-total").textContent = money(cart.reduce((sum, l) => sum + lineTotal(l), 0));
    };

    /* Update one row in place (keeps the cursor in the field being edited). */
    const updateRow = line => {
        const row = cartBox.querySelector(`tr[data-row="${cart.indexOf(line)}"]`);
        if (!row) return;
        const short = line.quantity > line.medicine.current_stock;
        row.classList.toggle("short", short);
        row.querySelector(".line-warning").hidden = !short;
        row.querySelector(".batch-note").textContent = planText(line);
        row.querySelector(".line-amount").textContent = money(lineTotal(line));
        updateTotal();
    };

    const lineTotal = line => (line.unit_price === null || Number.isNaN(line.unit_price) ? 0 : line.unit_price * line.quantity);

    const drawCart = () => {
        mount(cartBox, cart.length ? html`
            <table class="cart"><thead><tr><th>Medicine</th><th class="num">Qty</th><th class="num">Unit price</th><th>Directions</th><th class="num">Amount</th><th></th></tr></thead>
            <tbody>${cart.map((line, index) => {
                const short = line.quantity > line.medicine.current_stock;
                return html`
                <tr class="${short ? "short" : ""}" data-row="${index}">
                    <td><strong>${line.medicine.medicine}</strong> ${line.medicine.strength || ""}
                        <div class="batch-note">${planText(line)}</div>
                        <div class="error-text line-warning" ${short ? "" : html`hidden`}>Only ${number(line.medicine.current_stock)} usable in stock</div></td>
                    <td class="num"><input type="number" class="qty" min="1" step="1" value="${line.quantity}" data-index="${index}" data-field="quantity" aria-label="Quantity"></td>
                    <td class="num"><input type="number" class="price" min="0" step="0.01" value="${line.unit_price ?? ""}" placeholder="—" data-index="${index}" data-field="unit_price" aria-label="Unit price"></td>
                    <td><input class="directions-input" maxlength="255" value="${line.directions}" placeholder="e.g. 1 tab 3× daily" data-index="${index}" data-field="directions" aria-label="Directions"></td>
                    <td class="num line-amount">${money(lineTotal(line))}</td>
                    <td><button type="button" class="icon-btn" data-action="remove" data-index="${index}" aria-label="Remove ${line.medicine.medicine}">×</button></td>
                </tr>`;
            })}</tbody></table>` : html`<div class="loading">No items yet — search and add medicines.</div>`);
        updateTotal();
    };

    const refreshPlan = async line => {
        if (!Number.isInteger(line.quantity) || line.quantity < 1) { line.plan = null; return; }
        try {
            line.plan = await api(`/fefo/${line.medicine.medicine_id}`, {
                params: { quantity: line.quantity, location_id: locationValue() },
            });
        } catch { line.plan = null; }
    };

    const addLine = async medicineId => {
        const medicine = stock.find(m => m.medicine_id === medicineId);
        if (!medicine || cart.some(c => c.medicine.medicine_id === medicineId)) return;
        const line = { medicine, quantity: 1, unit_price: medicine.selling_price !== null ? Number(medicine.selling_price) : null, directions: "", plan: null };
        cart.push(line);
        search.value = "";
        drawResults();
        drawCart();
        await refreshPlan(line);
        updateRow(line);
        cartBox.querySelector(`input.qty[data-index="${cart.indexOf(line)}"]`)?.select();
    };

    const replan = debounce(async line => { await refreshPlan(line); updateRow(line); }, 300);
    cartBox.addEventListener("input", event => {
        const input = event.target;
        const line = cart[Number(input.dataset.index)];
        if (!line) return;
        if (input.dataset.field === "quantity") { line.quantity = Number(input.value); replan(line); }
        if (input.dataset.field === "unit_price") line.unit_price = input.value === "" ? null : Number(input.value);
        if (input.dataset.field === "directions") line.directions = input.value;
        updateRow(line);
    });

    form.querySelectorAll('input[name="dispense_type"]').forEach(radio => radio.addEventListener("change", () => {
        const rx = form.elements.dispense_type.value === "PRESCRIPTION";
        form.querySelectorAll(".rx-only").forEach(el => { el.hidden = !rx; });
    }));
    form.elements.location_id?.addEventListener("change", async () => { await Promise.all(cart.map(refreshPlan)); cart.forEach(updateRow); });
    search.addEventListener("input", debounce(drawResults, 120));
    // A scanner types the code and presses Enter: GS1 codes and EAN/GTIN
    // numbers are looked up by barcode instead of by name.
    const looksLikeBarcode = text => /^\d{8,14}$/.test(text) || /^\(01\)/.test(text) || /^(\]d2)?01\d{14}/.test(text) || text.includes("\x1d");
    search.addEventListener("keydown", async event => {
        if (event.key === "Enter" && looksLikeBarcode(search.value.trim())) {
            event.preventDefault();
            const code = search.value.trim();
            try {
                const { lookup } = await import("./scan.js");
                const result = await lookup(code, locationValue() ? Number(locationValue()) : null);
                result.warnings.forEach(w => toast(w, "warning"));
                if (result.medicine) {
                    if (result.matched_batch && result.matched_batch.batch_status !== "ACTIVE") return;
                    await addLine(result.medicine.id);
                }
                search.value = "";
            } catch (error) {
                toast(error.message, "error");
            }
            return;
        }
        if (event.key === "Enter") {
            event.preventDefault();
            drawResults();  // apply the current text now, not after the debounce
            const first = results.querySelector("[data-action=add]");
            if (first) addLine(Number(first.dataset.id));
        }
    });

    const reset = () => {
        cart.length = 0;
        form.reset();
        form.querySelectorAll(".rx-only").forEach(el => { el.hidden = true; });
        errorBox.hidden = true;
        drawResults();
        drawCart();
        search.focus();
    };

    form.addEventListener("submit", async event => {
        event.preventDefault();
        errorBox.hidden = true;
        const fail = message => { errorBox.textContent = message; errorBox.hidden = false; };
        if (!cart.length) return fail("Add at least one medicine.");
        const bad = cart.find(l => !Number.isInteger(l.quantity) || l.quantity < 1);
        if (bad) return fail(`Enter a whole-number quantity for ${bad.medicine.medicine}.`);
        const short = cart.find(l => l.quantity > l.medicine.current_stock);
        if (short) return fail(`Not enough usable stock of ${short.medicine.medicine}.`);
        const f = form.elements;
        const type = f.dispense_type.value;
        if (type === "PRESCRIPTION" && !f.prescriber.value.trim() && !f.prescription_number.value.trim()) {
            return fail("Enter the prescriber or the prescription number.");
        }
        const button = form.querySelector("button[type=submit]");
        button.disabled = true;
        try {
            const result = await api("/dispensations", { method: "POST", body: {
                dispense_type: type,
                payment_method: f.payment_method.value,
                patient_name: f.patient_name.value.trim() || null,
                patient_phone: f.patient_phone.value.trim() || null,
                prescriber: type === "PRESCRIPTION" ? f.prescriber.value.trim() || null : null,
                prescription_number: type === "PRESCRIPTION" ? f.prescription_number.value.trim() || null : null,
                location_id: locationValue() ? Number(locationValue()) : null,
                notes: f.notes.value.trim() || null,
                items: cart.map(l => ({
                    medicine_id: l.medicine.medicine_id, quantity: l.quantity,
                    unit_price: l.unit_price === null || Number.isNaN(l.unit_price) ? null : l.unit_price,
                    directions: l.directions.trim() || null,
                })),
            } });
            toast(`Dispensed ${result.dispensation_number} · ${money(result.total_amount)}`);
            // Update local stock figures so the next customer sees current stock.
            result.items.forEach(item => {
                const m = stock.find(s => s.medicine_id === item.medicine_id);
                if (m) m.current_stock -= item.quantity;
            });
            reset();
            showReceipt(result);
            api("/dispensations/summary").then(s => {
                const tiles = ctx.main.querySelectorAll(".cards .card strong");
                if (tiles.length >= 3) {
                    tiles[0].textContent = plural(s.dispensations, "transaction");
                    tiles[1].textContent = number(s.units);
                    tiles[2].textContent = money(s.sales_total);
                }
            }).catch(() => {});
        } catch (error) {
            fail(error.message);
        } finally {
            button.disabled = false;
        }
    });

    onAction(ctx.main, {
        add: el => addLine(Number(el.dataset.id)),
        remove: el => { cart.splice(Number(el.dataset.index), 1); drawResults(); drawCart(); },
        clear: reset,
    });

    drawResults();
    drawCart();
    if (preselected) await addLine(preselected);
    search.focus();
}

/* ---------- History ---------- */

export async function renderHistory(ctx) {
    mount(ctx.main, html`
        ${pageHeader("Dispensing History", "Every dispensation, with receipts and voids", html`
            ${ctx.can("stock.dispense") ? html`<a class="refresh-btn primary" href="#/dispense">New dispensing</a>` : ""}`)}
        <section class="cards" id="h-cards"></section>
        <section class="section">
            <div class="toolbar">
                <input type="search" id="h-search" placeholder="Number, patient, Rx number or medicine…" aria-label="Search dispensations">
                <label>From <input type="date" id="h-from" value="${today()}"></label>
                <label>To <input type="date" id="h-to" value="${today()}"></label>
                <select id="h-status" aria-label="Status"><option value="">All</option><option value="COMPLETED">Completed</option><option value="VOIDED">Voided</option></select>
                <span class="toolbar-count" id="h-count"></span>
            </div>
            <div id="h-table"></div>
        </section>`);

    const columns = [
        { label: "Number", key: "dispensation_number", render: d => html`<a href="#/dispensations/${d.id}"><strong>${d.dispensation_number}</strong></a>` },
        { label: "Time", key: "dispensed_at", render: d => formatDateTime(d.dispensed_at) },
        { label: "Type", key: "dispense_type", render: d => d.dispense_type === "PRESCRIPTION" ? "Prescription" : "OTC" },
        { label: "Patient", key: "patient_name", render: d => d.patient_name || "—" },
        { label: "Medicines", key: "medicines", render: d => html`<small>${d.medicines}</small>` },
        { label: "Units", key: "units", className: "num" },
        { label: "Payment", key: "payment_method", render: d => PAYMENT_LABELS[d.payment_method] || d.payment_method },
        { label: "Total", key: "total_amount", render: d => money(d.total_amount), className: "num", sort: d => Number(d.total_amount) },
        { label: "Status", key: "status", render: d => badge(d.status === "VOIDED" ? "VOIDED" : "COMPLETED", d.status === "VOIDED" ? "CANCELLED" : "NORMAL") },
        { label: "By", key: "dispensed_by" },
    ];

    const load = async () => {
        const params = {
            search: ctx.main.querySelector("#h-search").value.trim(),
            date_from: ctx.main.querySelector("#h-from").value, date_to: ctx.main.querySelector("#h-to").value,
            status: ctx.main.querySelector("#h-status").value,
        };
        const rows = await api("/dispensations", { params });
        const completed = rows.filter(r => r.status === "COMPLETED");
        mount(ctx.main.querySelector("#h-cards"), html`
            ${statTile("Dispensations", number(completed.length), "blue", `${rows.length - completed.length} voided`)}
            ${statTile("Units", number(completed.reduce((s, r) => s + r.units, 0)), "green")}
            ${statTile("Sales", money(completed.reduce((s, r) => s + Number(r.total_amount), 0)), "blue")}`);
        ctx.main.querySelector("#h-count").textContent = plural(rows.length, "record");
        sortableTable(ctx.main.querySelector("#h-table"), "dispensations-table", columns, rows, { empty: "No dispensations in this period." });
    };
    ctx.main.querySelectorAll(".toolbar select, .toolbar input[type=date]").forEach(el => el.addEventListener("change", load));
    ctx.main.querySelector("#h-search").addEventListener("input", debounce(load, 300));
    await load();
}

/* ---------- Detail ---------- */

export async function renderDetail(ctx) {
    const [d] = await Promise.all([api(`/dispensations/${encodeURIComponent(ctx.params.id)}`), loadReceiptHeader()]);
    if (!ctx.isCurrent()) return;
    const canVoid = ctx.can("dispensing.void") && d.status === "COMPLETED";

    mount(ctx.main, html`
        ${pageHeader(`Dispensation ${d.dispensation_number}`, `${formatDateTime(d.dispensed_at)} · ${d.dispensed_by}`, html`
            <a class="view-btn" href="#/dispensations">← History</a>
            <button type="button" class="refresh-btn" data-action="print">Print receipt</button>
            ${canVoid ? html`<button type="button" class="refresh-btn danger" data-action="void">Void</button>` : ""}`)}
        <section class="section receipt-page">
            ${d.status === "VOIDED" ? html`<p class="warning-text">Voided ${formatDateTime(d.voided_at)} by ${d.voided_by_name}: ${d.void_reason}. The stock was returned to its batches.</p>` : ""}
            ${receiptHtml(d)}
            ${d.notes ? html`<p class="notes">Notes: ${d.notes}</p>` : ""}
        </section>`);

    onAction(ctx.main, {
        print: printReceipt,
        void: () => formModal({
            title: `Void ${d.dispensation_number}`,
            intro: "All items are returned to the exact batches they were taken from, and the sale is removed from today's totals. This cannot be undone.",
            fields: [{ name: "reason", label: "Reason", type: "textarea", required: true, full: true }],
            submitLabel: "Void dispensation",
            onSubmit: async v => {
                await api(`/dispensations/${d.id}/void`, { method: "POST", body: v });
                toast("Dispensation voided; stock returned.", "warning");
                ctx.reload();
            },
        }),
    });
}
