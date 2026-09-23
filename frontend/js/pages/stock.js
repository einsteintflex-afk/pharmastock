/* Stock movement history. (Dispensing lives in dispensing.js.) */

import {
    api, apiPage, badge, bindPager, download, formatDateTime, html, mount, onAction, pageHeader, pager, signedQuantity,
    sortableTable, toast, today,
} from "../core.js";

const TYPES = ["RECEIVED", "DISPENSED", "RETURNED", "DAMAGED", "EXPIRED", "ADJUSTMENT", "TRANSFER_OUT", "TRANSFER_IN"];
const PAGE_SIZE = 200;

export async function renderMovements(ctx) {
    const medicines = await api("/medicines", { params: { sort: "name" } });
    if (!ctx.isCurrent()) return;

    mount(ctx.main, html`
        ${pageHeader("Stock Movements", "Complete transaction history of every batch", html`
            ${ctx.can("reports.export") ? html`<button type="button" class="refresh-btn" data-action="export">Export CSV</button>` : ""}`)}
        <section class="section">
            <div class="toolbar">
                <select id="mv-type" aria-label="Movement type"><option value="">All types</option>${TYPES.map(t => html`<option value="${t}">${t.replace("_", " ").toLowerCase()}</option>`)}</select>
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
    const load = async (page = 0) => {
        const { rows, total } = await apiPage("/stock-movements", filters(), page, PAGE_SIZE);
        if (!ctx.isCurrent()) return;
        ctx.main.querySelector("#mv-count").textContent = `${total.toLocaleString()} movements`;
        sortableTable(ctx.main.querySelector("#mv-table"), "movements-table", columns, rows, {
            empty: "No movements in this period.",
            afterRender: c => {
                c.insertAdjacentHTML("beforeend", String(pager("mv-pager", total, page, PAGE_SIZE)));
                bindPager(c, "mv-pager", load);
            },
        });
    };
    ctx.main.querySelectorAll(".toolbar select, .toolbar input").forEach(el => el.addEventListener("change", () => load(0)));
    await load(0);

    onAction(ctx.main, {
        export: () => {
            const f = filters();
            download("/reports/stock-movements", { format: "csv", movement_type: f.movement_type, date_from: f.date_from, date_to: f.date_to })
                .catch(error => toast(error.message, "error"));
        },
    });
}
