/* Barcode scanning: GS1 DataMatrix / GS1-128 / EAN.
   Keyboard-wedge scanners type the code into a focused input and press
   Enter, so every scan field is a normal text input. Where the browser
   supports the BarcodeDetector API (Chrome / Android), the camera can be
   used instead. Scanners that send the GS separator as a control character
   work; "<GS>" / "^]" text forms are understood by the server as well. */

import { api, badge, daysLabel, formatDate, html, money, mount, number, pageHeader, table, toast } from "../core.js";

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

/* ---------- Scan page ---------- */

export async function render(ctx) {
    const locations = (await api("/locations")).filter(l => l.is_active);
    if (!ctx.isCurrent()) return;

    mount(ctx.main, html`
        ${pageHeader("Barcode Scan", "Scan a pack to see the medicine, the batch, FEFO order and open orders", "")}
        <section class="section">
            <form class="toolbar scan-form" id="scan-form">
                <input id="scan-code" type="text" autocomplete="off" placeholder="Scan or type a barcode, then press Enter"
                       aria-label="Barcode" required>
                <select id="scan-location" aria-label="Location">
                    <option value="">All locations</option>
                    ${locations.map(l => html`<option value="${l.id}">${l.name}</option>`)}
                </select>
                <button type="submit" class="refresh-btn primary">Look up</button>
                ${cameraSupported() ? html`<button type="button" class="refresh-btn" id="scan-camera">📷 Camera</button>` : ""}
            </form>
            <div id="camera-box"></div>
            <p class="method">Supports GS1 DataMatrix / GS1-128 codes (GTIN, batch, expiry, serial) and plain EAN / GTIN
                product barcodes. Codes without batch or expiry still identify the medicine.</p>
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
            mount(out, resultHtml(r, ctx));
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
