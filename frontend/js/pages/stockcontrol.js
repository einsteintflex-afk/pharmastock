/* Stock control: adjustments (reason codes, approval) and stock counts
   (scan or type, variance by quantity and value, submit, approve, post). */

import {
    api, badge, busy, emptyState, formatDate, formatDateTime, formModal, html, idempotencyKey, money, mount, number,
    onAction, pageHeader, table, toast,
} from "../core.js";
import { cameraSupported, lookup, scanWithCamera } from "./scan.js";

/* ---------- Adjustments ---------- */

let reasonCodes = null;
async function reasons() {
    reasonCodes = reasonCodes || await api("/adjustments/reason-codes");
    return reasonCodes;
}

/** Pick a batch: medicine first, then one of its batches with stock. */
async function batchOptions(medicineId, locationId) {
    const batches = await api("/inventory", { params: { medicine_id: medicineId, location_id: locationId } });
    return batches.map(b => ({ value: b.batch_id, label: `${b.batch_number} · ${b.location || ""} · qty ${b.quantity} · exp ${formatDate(b.expiry_date)}` }));
}

export async function openAdjustmentForm(ctx, { batchId, medicineId, onSaved } = {}) {
    const codes = await reasons();
    let batchChoices = [];
    let medicines = [];
    if (!batchId) {
        medicines = (await api("/medicines")).filter(m => m.is_active);
        if (medicineId) batchChoices = await batchOptions(medicineId);
    }
    const fields = [
        ...(batchId ? [] : [
            { name: "medicine_id", label: "Medicine", type: "select", required: true, value: medicineId ?? "",
              placeholder: "— choose —", options: medicines.map(m => ({ value: m.id, label: `${m.name} ${m.strength || ""}` })) },
            { name: "batch_id", label: "Batch", type: "select", required: true, placeholder: "— choose the medicine first —",
              options: batchChoices },
        ]),
        { name: "reason_code", label: "Reason", type: "select", required: true, placeholder: "— choose —",
          options: codes.map(c => ({ value: c.code, label: c.label })) },
        { name: "mode", label: "I know", type: "select", value: "counted",
          options: [{ value: "counted", label: "the counted quantity" }, { value: "change", label: "the change (+ / −)" }] },
        { name: "quantity", label: "Quantity", type: "number", required: true, step: 1,
          help: "Counted quantity on the shelf, or the change: −3 removes three units." },
        { name: "notes", label: "Notes", type: "textarea", full: true, help: "Required for theft / loss and other." },
    ];
    const key = idempotencyKey();
    const form = formModal({
        title: "Stock adjustment", fields, submitLabel: "Record adjustment",
        intro: "Large adjustments wait for approval by a manager (Settings → approval thresholds).",
        onSubmit: async values => {
            const body = { batch_id: Number(batchId || values.batch_id), reason_code: values.reason_code, notes: values.notes };
            if (values.mode === "counted") body.counted_quantity = values.quantity;
            else body.change = values.quantity;
            const result = await api("/adjustments", { method: "POST", body, headers: { "Idempotency-Key": key } });
            toast(result.status === "POSTED"
                ? `Adjustment ${result.adjustment_number} posted: ${result.previous_quantity} → ${result.new_quantity}`
                : `Adjustment ${result.adjustment_number} is waiting for approval`, result.status === "POSTED" ? "success" : "warning");
            onSaved?.(result);
        },
    });
    const medicineSelect = form.elements.medicine_id;
    medicineSelect?.addEventListener("change", async () => {
        const options = medicineSelect.value ? await batchOptions(medicineSelect.value) : [];
        mount(form.elements.batch_id, html`<option value="">${options.length ? "— choose —" : "No batches"}</option>
            ${options.map(o => html`<option value="${o.value}">${o.label}</option>`)}`);
    });
}

export async function renderAdjustments(ctx) {
    const status = ctx.params.query.status || "";
    const [rows, codes] = await Promise.all([api("/adjustments", { params: { status, limit: 200 } }), reasons()]);
    if (!ctx.isCurrent()) return;
    const label = Object.fromEntries(codes.map(c => [c.code, c.label]));
    mount(ctx.main, html`
        ${pageHeader("Stock Adjustments", "Every manual stock change with its reason, before / after quantity and approver",
            ctx.can("stock.adjust") ? html`<button type="button" class="refresh-btn primary" data-action="new">＋ New adjustment</button>` : "")}
        <section class="section">
            <div class="toolbar">
                ${["", "PENDING_APPROVAL", "POSTED", "REJECTED", "CANCELLED"].map(s => html`
                    <a class="chip ${s === status ? "active" : ""}" href="#/adjustments${s ? `?status=${s}` : ""}">${s ? s.replace("_", " ").toLowerCase() : "all"}</a>`)}
            </div>
            ${rows.length ? table("adjustments", [
                { label: "Adjustment", render: a => html`<strong>${a.adjustment_number}</strong><br><small>${formatDateTime(a.requested_at)}</small>` },
                { label: "Medicine / batch", render: a => html`${a.medicine} ${a.strength || ""}<br><small>${a.batch_number} · ${a.location}</small>` },
                { label: "Before → after", render: a => html`${number(a.previous_quantity)} → ${a.new_quantity === null ? "…" : number(a.new_quantity)}` },
                { label: "Change", className: "num", render: a => html`<strong class="${a.adjustment_quantity < 0 ? "neg" : "pos"}">${a.adjustment_quantity > 0 ? "+" : ""}${a.adjustment_quantity}</strong>` },
                { label: "Value", className: "num", render: a => a.value === null ? html`<small>no cost</small>` : money(a.value) },
                { label: "Reason", render: a => html`${label[a.reason_code] || a.reason_code}${a.notes ? html`<br><small>${a.notes}</small>` : ""}` },
                { label: "By", render: a => html`${a.requested_by_name}${a.decided_by_name ? html`<br><small>✓ ${a.decided_by_name}</small>` : ""}${a.client_name ? html`<br><small>📱 ${a.client_name}</small>` : ""}` },
                { label: "Status", render: a => badge(a.status) },
                { label: "", render: a => a.status !== "PENDING_APPROVAL" ? "" : html`<div class="row-actions">
                    ${ctx.can("stock.approve") && a.requested_by !== ctx.user.id ? html`
                        <button type="button" class="refresh-btn primary small" data-action="approve" data-id="${a.id}">✓ Approve</button>
                        <button type="button" class="view-btn small" data-action="reject" data-id="${a.id}">✕ Reject</button>` : ""}
                    ${a.requested_by === ctx.user.id ? html`<button type="button" class="view-btn small" data-action="cancel" data-id="${a.id}">Cancel</button>` : ""}
                </div>` },
            ], rows) : emptyState(status ? "No adjustments with this status." : "No stock adjustments yet.",
                ctx.can("stock.adjust") ? html`<button type="button" class="refresh-btn primary" data-action="new">＋ Record the first adjustment</button>` : "")}
        </section>`);
    onAction(ctx.main, {
        new: () => openAdjustmentForm(ctx, { onSaved: () => ctx.reload() }),
        approve: button => busy(button, async () => {
            await api(`/adjustments/${button.dataset.id}/decision`, { method: "POST", body: { approve: true } });
            toast("Approved and posted");
            ctx.reload();
        }),
        reject: button => formModal({
            title: "Reject adjustment", submitLabel: "Reject",
            fields: [{ name: "note", label: "Reason", type: "textarea", required: true, full: true }],
            onSubmit: async v => {
                await api(`/adjustments/${button.dataset.id}/decision`, { method: "POST", body: { approve: false, note: v.note } });
                toast("Rejected", "warning");
                ctx.reload();
            },
        }),
        cancel: button => busy(button, async () => {
            await api(`/adjustments/${button.dataset.id}/cancel`, { method: "POST" });
            ctx.reload();
        }),
    });
}

/* ---------- Stock counts ---------- */

export async function renderCounts(ctx) {
    const [counts, locations] = await Promise.all([api("/stock-counts"), api("/locations")]);
    if (!ctx.isCurrent()) return;
    const active = locations.filter(l => l.is_active);
    mount(ctx.main, html`
        ${pageHeader("Stock Counts", "Count the shelves by scanning; differences become adjustments when posted",
            ctx.can("stock.count") ? html`<button type="button" class="refresh-btn primary" data-action="new">＋ New count</button>` : "")}
        <section class="section">
            ${counts.length ? table("counts", [
                { label: "Count", render: c => html`<a href="#/stock-counts/${c.id}"><strong>${c.count_number}</strong></a><br><small>${c.name || ""}</small>` },
                { label: "Location", key: "location" },
                { label: "Started", render: c => html`${formatDateTime(c.created_at)}<br><small>${c.created_by_name}</small>` },
                { label: "Lines", className: "num", render: c => number(c.lines) },
                { label: "With variance", className: "num", render: c => number(c.lines_with_variance) },
                { label: "Status", render: c => badge(c.status) },
            ], counts) : emptyState("No stock counts yet. A count is the quickest way to record opening stock or check the shelves.",
                ctx.can("stock.count") ? html`<button type="button" class="refresh-btn primary" data-action="new">＋ Start a count</button>` : "")}
        </section>`);
    onAction(ctx.main, {
        new: () => formModal({
            title: "New stock count", submitLabel: "Start counting",
            fields: [
                { name: "location_id", label: "Location", type: "select", required: true,
                  value: ctx.user.location_id ?? (active.length === 1 ? active[0].id : ""),
                  placeholder: "— choose —", options: active.map(l => ({ value: l.id, label: l.name })) },
                { name: "name", label: "Name", placeholder: "e.g. Monthly count, shelf A" },
                { name: "notes", label: "Notes", type: "textarea", full: true },
            ],
            onSubmit: async v => {
                const count = await api("/stock-counts", { method: "POST", body: { ...v, location_id: Number(v.location_id) } });
                ctx.navigate(`stock-counts/${count.id}`);
            },
        }),
    });
}

export async function renderCount(ctx) {
    const count = await api(`/stock-counts/${ctx.params.id}`);
    if (!ctx.isCurrent()) return;
    const open = count.status === "IN_PROGRESS";
    const s = count.summary;
    mount(ctx.main, html`
        ${pageHeader(`${count.count_number} ${count.name ? `· ${count.name}` : ""}`,
            `${count.location} · started ${formatDateTime(count.created_at)} by ${count.created_by_name}`,
            html`${badge(count.status)} <a class="view-btn" href="#/stock-counts">All counts</a>`)}
        <section class="cards">
            <div class="card"><div><span>Lines counted</span><strong>${number(s.lines)}</strong><small class="card-hint">${number(s.uncounted_batches)} batches not counted yet</small></div></div>
            <div class="card"><div><span>Differences</span><strong>${number(s.lines_with_variance)}</strong><small class="card-hint">+${number(s.units_over)} / −${number(s.units_short)} units</small></div></div>
            <div class="card"><div><span>Net value</span><strong>${money(s.net_variance_value)}</strong><small class="card-hint">${s.lines_without_cost ? `${s.lines_without_cost} line(s) without a cost` : "at recorded cost"}</small></div></div>
        </section>
        ${open && ctx.can("stock.count") ? html`
        <section class="section scan-panel">
            <h3>Count an item</h3>
            <form class="toolbar scan-form" id="count-form">
                <input id="count-code" autocomplete="off" placeholder="Scan the pack, or type a batch number" aria-label="Barcode or batch">
                <input id="count-qty" type="number" min="0" step="1" value="1" aria-label="Quantity" class="qty-input">
                <select id="count-mode" aria-label="Mode">
                    <option value="add">add to count</option><option value="set">set count to</option>
                </select>
                <button type="submit" class="refresh-btn primary">＋ Count</button>
                ${cameraSupported() ? html`<button type="button" class="refresh-btn" id="count-camera">📷 Camera</button>` : ""}
            </form>
            <div id="count-camera-box"></div>
            <p class="method">Scan each pack with the camera or a scanner ("add to count"), or type the total on the shelf ("set count to").
                The recorded quantity is taken at the moment you count, so sales during the count are handled correctly.</p>
        </section>` : ""}
        <section class="section">
            <div class="section-header"><div><h3>Counted</h3><p>Difference = counted − recorded at the time of counting</p></div></div>
            ${table("count-lines", [
                { label: "Medicine", render: l => html`<strong>${l.medicine}</strong> <small>${l.strength || ""}</small><br><small>${l.batch_number} · exp ${formatDate(l.expiry_date)}</small>` },
                { label: "Recorded", className: "num", render: l => number(l.system_quantity) },
                { label: "Counted", className: "num", render: l => html`<strong>${number(l.counted_quantity)}</strong>` },
                { label: "Difference", className: "num", render: l => html`<strong class="${l.variance < 0 ? "neg" : l.variance > 0 ? "pos" : ""}">${l.variance > 0 ? "+" : ""}${l.variance}</strong>` },
                { label: "Value", className: "num", render: l => l.variance_value === null ? (l.variance ? html`<small>no cost</small>` : "") : money(l.variance_value) },
                { label: "By", render: l => html`<small>${l.counted_by_name}<br>${formatDateTime(l.counted_at)}</small>` },
                { label: "", render: l => open && ctx.can("stock.count") ? html`<button type="button" class="icon-btn" data-action="remove" data-id="${l.id}" aria-label="Remove line">✕</button>`
                    : l.adjustment_id ? html`<a href="#/adjustments">adjusted</a>` : "" },
            ], count.lines, { empty: "Nothing counted yet — scan the first pack." })}
        </section>
        ${count.uncounted.length ? html`<section class="section">
            <div class="section-header"><div><h3>Not counted yet</h3><p>Recorded at this location with stock; not changed when the count is posted</p></div></div>
            ${table("count-uncounted", [
                { label: "Medicine", render: b => html`${b.medicine} <small>${b.strength || ""}</small>` },
                { label: "Batch", key: "batch_number" },
                { label: "Expiry", render: b => formatDate(b.expiry_date) },
                { label: "Recorded", className: "num", render: b => number(b.quantity) },
                { label: "", render: b => open && ctx.can("stock.count") ? html`<button type="button" class="view-btn small" data-action="zero" data-batch="${b.batch_id}">Count as 0</button>
                    <button type="button" class="view-btn small" data-action="asrecorded" data-batch="${b.batch_id}" data-qty="${b.quantity}">✓ As recorded</button>` : "" },
            ], count.uncounted)}
        </section>` : ""}
        <div class="form-actions sticky-actions">
            ${open && ctx.can("stock.count") ? html`<button type="button" class="view-btn" data-action="cancel">Cancel count</button>
                <button type="button" class="refresh-btn primary" data-action="submit">Submit for review →</button>` : ""}
            ${count.status === "SUBMITTED" && ctx.can("stock.approve") ? html`
                <button type="button" class="view-btn" data-action="reopen">↺ Send back for recount</button>
                <button type="button" class="refresh-btn primary" data-action="post">✓ Approve and post ${number(s.lines_with_variance)} adjustment(s)</button>` : ""}
        </div>`);

    const record = async (batchId, qty, mode) => {
        await api(`/stock-counts/${count.id}/lines`, { method: "POST", body: { batch_id: batchId, counted_quantity: qty, mode } });
        ctx.reload();
    };
    const form = ctx.main.querySelector("#count-form");
    if (form) {
        const input = form.querySelector("#count-code");
        input.focus();
        const countCode = async code => {
            const qty = Number(form.querySelector("#count-qty").value || 0);
            const mode = form.querySelector("#count-mode").value;
            let batch = count.lines.find(l => l.batch_number.toLowerCase() === code.toLowerCase())
                || count.uncounted.find(b => b.batch_number.toLowerCase() === code.toLowerCase());
            if (!batch) {
                const result = await lookup(code, count.location_id);
                const matched = result.matched_batch;
                if (matched && matched.location_id !== undefined && matched.location_id !== count.location_id) {
                    toast("That batch is recorded at another location.", "error");
                    return;
                }
                batch = matched || (result.batches.length === 1 ? result.batches[0] : null);
                if (!batch && result.medicine) {
                    toast(`${result.medicine.name}: several batches here — type or scan the batch number.`, "warning");
                    return;
                }
                if (!batch) { toast("Product not found at this location.", "error"); return; }
            }
            await record(batch.batch_id, qty, mode);
            toast(`Counted ${mode === "add" ? "+" : ""}${qty}`);
        };
        form.addEventListener("submit", event => {
            event.preventDefault();
            const code = input.value.trim();
            if (code) busy(form.querySelector("button[type=submit]"), () => countCode(code));
        });
        ctx.main.querySelector("#count-camera")?.addEventListener("click", async () => {
            try {
                const code = await scanWithCamera(ctx.main.querySelector("#count-camera-box"));
                input.value = code;
                await countCode(code);
            } catch (error) { toast(error.message || "Camera unavailable", "error"); }
        });
    }
    onAction(ctx.main, {
        remove: button => busy(button, async () => { await api(`/stock-counts/${count.id}/lines/${button.dataset.id}`, { method: "DELETE" }); ctx.reload(); }),
        zero: button => busy(button, () => record(Number(button.dataset.batch), 0, "set")),
        asrecorded: button => busy(button, () => record(Number(button.dataset.batch), Number(button.dataset.qty), "set")),
        submit: button => busy(button, async () => { await api(`/stock-counts/${count.id}/submit`, { method: "POST" }); toast("Submitted for review"); ctx.reload(); }),
        reopen: button => busy(button, async () => { await api(`/stock-counts/${count.id}/reopen`, { method: "POST" }); ctx.reload(); }),
        post: button => busy(button, async () => {
            await api(`/stock-counts/${count.id}/post`, { method: "POST", headers: { "Idempotency-Key": `count-post-${count.id}` } });
            toast("Count posted: stock updated");
            ctx.reload();
        }),
        cancel: () => formModal({
            title: "Cancel count", submitLabel: "Cancel count",
            fields: [{ name: "reason", label: "Reason", type: "textarea", required: true, full: true }],
            onSubmit: async v => { await api(`/stock-counts/${count.id}/cancel`, { method: "POST", body: v }); ctx.reload(); },
        }),
    });
}
