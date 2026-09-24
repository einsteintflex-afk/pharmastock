/* Barcode scanning: GS1 DataMatrix / GS1-128 / EAN.
   Keyboard-wedge scanners type the code into a focused input and press
   Enter, so every scan field is a normal text input. Where the browser
   supports the BarcodeDetector API (Chrome / Android), the camera can be
   used instead. Scanners that send the GS separator as a control character
   work; "<GS>" / "^]" text forms are understood by the server as well. */

import {
    api, badge, busy, daysLabel, formatDate, html, idempotencyKey, money, mount, number, pageHeader, table, toast,
} from "../core.js";

/** Look a scanned code up. Returns the server result or throws ApiError. */
export function lookup(code, locationId) {
    return api("/barcode/lookup", { method: "POST", body: { code, location_id: locationId || null } });
}

/**
 * Wire a scan input inside a form: on Enter / change, the code is parsed
 * and onResult(result) fills the other fields. The raw code stays in the
 * input (it is saved with the batch as barcode_data).
 */
export function wireScanInput(input, onResult) {
    let last = "";
    const run = async () => {
        const code = input.value.trim();
        if (!code || code === last) return;
        last = code;
        try {
            const result = await lookup(code);
            onResult(result);
            result.warnings.forEach(w => toast(w, "warning"));
        } catch (error) {
            toast(error.message, "error");
        }
    };
    input.addEventListener("keydown", event => {
        if (event.key === "Enter") { event.preventDefault(); run(); }
    });
    input.addEventListener("change", run);
}

/* ---------- Camera (optional) ---------- */

export function cameraSupported() {
    return "BarcodeDetector" in window && Boolean(navigator.mediaDevices?.getUserMedia);
}

/** Scan one code with the camera into `container`; resolves the raw value. */
export async function scanWithCamera(container) {
    const formats = ["data_matrix", "ean_13", "ean_8", "code_128", "upc_a", "qr_code"];
    const supported = await window.BarcodeDetector.getSupportedFormats();
    const detector = new window.BarcodeDetector({ formats: formats.filter(f => supported.includes(f)) });
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    const video = document.createElement("video");
    video.setAttribute("playsinline", "");
    video.className = "camera-view";
    video.srcObject = stream;
    container.replaceChildren(video);
    await video.play();
    try {
        for (let i = 0; i < 600; i++) {  // about 60 seconds
            const codes = await detector.detect(video);
            if (codes.length) return codes[0].rawValue;
            await new Promise(r => setTimeout(r, 100));
        }
        throw new Error("No barcode found. Hold the pack steady, closer to the camera.");
    } finally {
        stream.getTracks().forEach(t => t.stop());
        container.replaceChildren();
    }
}

/* ---------- Scan Center ---------- */

const MODES = [
    { key: "lookup", label: "Look up", icon: "🔍", hint: "Identify a pack: medicine, batch, FEFO order, open orders", permission: "inventory.read" },
    { key: "receive", label: "Receive", icon: "📥", hint: "Receive a delivery against an open purchase order, or register opening stock", permission: "purchasing.receive" },
    { key: "count", label: "Count", icon: "🔢", hint: "Count the shelves in an open stock count", permission: "stock.count", feature: "stock_count" },
    { key: "dispense", label: "Sell", icon: "🧾", hint: "Add the medicine to a sale at the counter", permission: "stock.dispense" },
    { key: "adjust", label: "Adjust", icon: "±", hint: "Record damage, breakage or a count difference for the scanned batch", permission: "stock.adjust", feature: "stock_count" },
    { key: "transfer", label: "Transfer", icon: "⇄", hint: "Request stock from another location", permission: "transfers.request", feature: "multi_location" },
];

export async function render(ctx) {
    const locations = (await api("/locations")).filter(l => l.is_active);
    if (!ctx.isCurrent()) return;
    const modes = MODES.filter(m => ctx.can(m.permission) && ctx.hasFeature(m.feature));
    const mode = modes.find(m => m.key === ctx.params.query.mode) || modes[0];

    mount(ctx.main, html`
        ${pageHeader("Scan Center", "Use the phone camera or a barcode scanner. Nothing is saved until you confirm.", "")}
        <nav class="mode-tabs" aria-label="Scan mode">
            ${modes.map(m => html`<a class="mode-tab ${m.key === mode.key ? "active" : ""}" href="#/scan?mode=${m.key}"
                ${m.key === mode.key ? html`aria-current="page"` : ""}><span aria-hidden="true">${m.icon}</span> ${m.label}</a>`)}
        </nav>
        <section class="section scan-panel">
            <p class="form-intro">${mode.hint}</p>
            <form class="toolbar scan-form" id="scan-form">
                <input id="scan-code" type="text" autocomplete="off" inputmode="text" placeholder="Scan or type a barcode, then press Enter"
                       aria-label="Barcode" required>
                <select id="scan-location" aria-label="Location">
                    <option value="">All locations</option>
                    ${locations.map(l => html`<option value="${l.id}" ${ctx.user.location_id === l.id ? html`selected` : ""}>${l.name}</option>`)}
                </select>
                <button type="submit" class="refresh-btn primary">Look up</button>
                ${cameraSupported() ? html`<button type="button" class="refresh-btn" id="scan-camera">📷 Camera</button>` : ""}
            </form>
            <div id="camera-box"></div>
            ${cameraSupported() ? "" : html`<p class="method">Camera scanning needs Chrome / Edge on Android or a desktop browser with
                barcode support; a USB or Bluetooth scanner works everywhere.</p>`}
            <p class="method">GS1 DataMatrix / GS1-128 (GTIN, batch, expiry, serial) and plain EAN / GTIN product barcodes.</p>
        </section>
        <div id="scan-result"></div>`);

    const input = ctx.main.querySelector("#scan-code");
    const location = ctx.main.querySelector("#scan-location");
    const out = ctx.main.querySelector("#scan-result");
    input.focus();

    const show = async code => {
        try {
            const r = await lookup(code, location.value ? Number(location.value) : null);
            if (!ctx.isCurrent()) return;
            if (!r.medicine) {
                mount(out, notFoundHtml(r, ctx));
            } else {
                mount(out, html`${modePanel(mode.key, r, ctx)}${resultHtml(r, ctx)}`);
                wireMode(mode.key, r, ctx, out, code, () => show(code));
            }
        } catch (error) {
            mount(out, html`<section class="section"><div class="loading error-text" role="alert">${error.message}</div></section>`);
        }
        input.select();
    };
    ctx.main.querySelector("#scan-form").addEventListener("submit", event => {
        event.preventDefault();
        if (input.value.trim()) show(input.value.trim());
    });
    ctx.main.querySelector("#scan-camera")?.addEventListener("click", async () => {
        try {
            const code = await scanWithCamera(ctx.main.querySelector("#camera-box"));
            input.value = code;
            show(code);
        } catch (error) {
            toast(error.message || "Camera unavailable", "error");
        }
    });
    if (ctx.params.query.code) { input.value = ctx.params.query.code; show(ctx.params.query.code); }
}

function notFoundHtml(r, ctx) {
    const gtin = r.parsed.gtin || r.parsed.content_gtin || "";
    return html`<section class="section not-found">
        <div class="empty-state">
            <span aria-hidden="true">?</span>
            <h3>PRODUCT NOT FOUND</h3>
            <p>No medicine is registered with barcode <strong>${gtin || "—"}</strong>.
               ${r.parsed.batch_number ? html`The pack shows batch <strong>${r.parsed.batch_number}</strong>${r.parsed.expiry_date ? html`, expiry <strong>${formatDate(r.parsed.expiry_date)}</strong>` : ""}.` : ""}</p>
            ${ctx.can("medicines.write") && gtin ? html`<a class="refresh-btn primary" href="#/medicines?new=1&gtin=${encodeURIComponent(gtin)}">＋ Create new medicine</a>
                <p class="method">You review every detail before it is saved. PharmaStock does not copy product data from outside databases.</p>`
                : html`<p class="method">Ask a pharmacist or manager to register this product.</p>`}
        </div>
    </section>`;
}

function modePanel(mode, r, ctx) {
    const m = r.medicine;
    const parsed = r.parsed;
    if (mode === "receive") {
        return html`<section class="section mode-panel">
            <h3>Receive ${m.name} ${m.strength || ""}</h3>
            ${r.open_order_items.length ? html`
                <form class="form-grid" id="receive-form">
                    <div class="field full"><label for="rcv-item">Purchase order line</label>
                        <select id="rcv-item" required>${r.open_order_items.map(o => html`<option value="${o.purchase_order_item_id}">
                            ${o.order_number} · ${o.supplier} · ${o.outstanding} outstanding</option>`)}</select></div>
                    <div class="field"><label for="rcv-batch">Batch number</label><input id="rcv-batch" required value="${parsed.batch_number || ""}"></div>
                    <div class="field"><label for="rcv-expiry">Expiry date</label><input id="rcv-expiry" type="date" required value="${parsed.expiry_date || ""}"></div>
                    <div class="field"><label for="rcv-qty">Quantity received</label><input id="rcv-qty" type="number" min="1" step="1" required
                        value="${r.open_order_items[0].outstanding}"></div>
                    <div class="form-actions full"><button type="submit" class="refresh-btn primary">✓ Confirm receipt</button></div>
                </form>` : html`<p>No open purchase order for this medicine.</p>
                ${ctx.can("batches.write") ? html`<button type="button" class="refresh-btn primary" id="rcv-opening">＋ Register as opening stock</button>` : ""}`}
        </section>`;
    }
    if (mode === "count") {
        return html`<section class="section mode-panel"><h3>Count</h3><div id="count-choices" class="loading">Loading open counts…</div></section>`;
    }
    if (mode === "dispense") {
        return html`<section class="section mode-panel"><h3>Sell ${m.name}</h3>
            <a class="refresh-btn primary" href="#/dispense?medicine=${m.id}">🧾 Add to sale at the counter</a></section>`;
    }
    if (mode === "adjust") {
        return html`<section class="section mode-panel"><h3>Adjust stock</h3>
            ${r.matched_batch ? html`<button type="button" class="refresh-btn primary" id="adj-open">± Adjust batch ${r.matched_batch.batch_number}</button>`
                : html`<button type="button" class="refresh-btn primary" id="adj-open">± Choose the batch to adjust</button>`}</section>`;
    }
    if (mode === "transfer") {
        return html`<section class="section mode-panel"><h3>Transfer</h3>
            <a class="refresh-btn primary" href="#/transfers?new=1&medicine=${m.id}">⇄ Request ${m.name}</a></section>`;
    }
    return "";
}

function wireMode(mode, r, ctx, out, code, refresh) {
    if (mode === "receive") {
        const form = out.querySelector("#receive-form");
        const key = idempotencyKey();
        form?.addEventListener("submit", event => {
            event.preventDefault();
            busy(form.querySelector("button[type=submit]"), async () => {
                const result = await api("/purchase-receipts", { method: "POST", headers: { "Idempotency-Key": key }, body: {
                    purchase_order_item_id: Number(form.querySelector("#rcv-item").value),
                    batch_number: form.querySelector("#rcv-batch").value.trim(),
                    expiry_date: form.querySelector("#rcv-expiry").value,
                    quantity_received: Number(form.querySelector("#rcv-qty").value),
                    barcode_data: code,
                    location_id: Number(ctx.main.querySelector("#scan-location").value) || null,
                } });
                toast(`Received ${result.quantity_received} units · order is ${result.purchase_order_status.replace("_", " ").toLowerCase()}`);
                refresh();
            });
        });
        out.querySelector("#rcv-opening")?.addEventListener("click", async () => {
            const { openBatchForm } = await import("./inventory.js");
            openBatchForm(r.medicine.id, refresh, { batch_number: r.parsed.batch_number, expiry_date: r.parsed.expiry_date, barcode_data: code });
        });
    }
    if (mode === "count") {
        api("/stock-counts", { params: { status: "IN_PROGRESS" } }).then(counts => {
            const box = out.querySelector("#count-choices");
            if (!box) return;
            mount(box, counts.length ? html`<p>Continue counting in:</p>${counts.map(c => html`
                <a class="refresh-btn" href="#/stock-counts/${c.id}">${c.count_number} · ${c.location}</a> `)}`
                : html`<p>No count is open.</p><a class="refresh-btn primary" href="#/stock-counts">＋ Start a stock count</a>`);
        });
    }
    if (mode === "adjust") {
        out.querySelector("#adj-open")?.addEventListener("click", async () => {
            const { openAdjustmentForm } = await import("./stockcontrol.js");
            openAdjustmentForm(ctx, r.matched_batch ? { batchId: r.matched_batch.batch_id, onSaved: refresh }
                : { medicineId: r.medicine.id, onSaved: refresh });
        });
    }
}

function resultHtml(r, ctx) {
    const p = r.parsed;
    const m = r.medicine;
    return html`
        <section class="section">
            <div class="section-header"><div><h3>Decoded barcode</h3><p>${p.format === "GS1" ? "GS1 code" : "Product barcode"}</p></div></div>
            <dl class="summary">
                <div><dt>GTIN</dt><dd>${p.gtin || p.content_gtin || "—"}</dd></div>
                <div><dt>Batch / lot</dt><dd>${p.batch_number || "not in code"}</dd></div>
                <div><dt>Expiry</dt><dd>${p.expiry_date ? formatDate(p.expiry_date) : "not in code"}</dd></div>
                <div><dt>Serial</dt><dd>${p.serial_number || "—"}</dd></div>
            </dl>
            ${r.warnings.map(w => html`<p class="warning-text scan-warning">⚠ ${w}</p>`)}
        </section>
        ${m ? html`
        <section class="section">
            <div class="section-header">
                <div><h3><a href="#/medicines/${m.id}">${m.name} ${m.strength || ""}</a></h3>
                    <p>${[m.dosage_form, m.brand_name, m.generic_name].filter(Boolean).join(" · ")} · price ${money(m.selling_price)}</p></div>
                <div class="top-actions">
                    ${ctx.can("stock.dispense") ? html`<a class="refresh-btn primary" href="#/dispense?medicine=${m.id}">Dispense</a>` : ""}
                    ${r.matched_batch ? html`<a class="view-btn" href="#/batches/${r.matched_batch.batch_id}">Batch record</a>` : ""}
                </div>
            </div>
            ${table("scan-batches", [
                { label: "Batch", render: b => html`<a href="#/batches/${b.batch_id}">${b.batch_number}</a>
                    ${r.matched_batch?.batch_id === b.batch_id ? html` <strong>← scanned</strong>` : ""}
                    ${r.fefo_batch?.batch_id === b.batch_id ? html` ${badge("FEFO first", "ACTIVE")}` : ""}` },
                { label: "Qty", render: b => number(b.quantity), className: "num" },
                { label: "Expiry", render: b => formatDate(b.expiry_date) },
                { label: "Days", render: b => daysLabel(b.days_until_expiry) },
                { label: "Status", render: b => html`${badge(b.status)} ${b.batch_status !== "ACTIVE" ? badge(b.batch_status) : ""}` },
                { label: "Location", key: "location" },
            ], r.batches, { empty: "No stock of this medicine here." })}
        </section>
        ${r.open_order_items.length ? html`<section class="section">
            <div class="section-header"><div><h3>Open purchase orders</h3><p>Receive this delivery against an order</p></div></div>
            ${table("scan-orders", [
                { label: "Order", render: o => html`<a href="#/purchasing/${o.purchase_order_id}">${o.order_number}</a>` },
                { label: "Supplier", key: "supplier" },
                { label: "Outstanding", render: o => `${number(o.outstanding)} of ${number(o.quantity_ordered)}`, className: "num" },
            ], r.open_order_items)}</section>` : ""}` : ""}`;
}
