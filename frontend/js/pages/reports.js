/* Reports: run on screen, export CSV / Excel / PDF. */

import { api, download, formatDate, formatDateTime, html, money, mount, number, pageHeader, table, toast, today } from "../core.js";

function cell(value, type) {
    if (value === null || value === undefined || value === "") return "—";
    if (type === "money") return money(value);
    if (type === "int") return number(value);
    if (type === "number") return number(value, 2);
    if (type === "date") return formatDate(value);
    if (type === "datetime") return formatDateTime(value);
    return String(value);
}

export async function render(ctx) {
    const [reports, locations, suppliers] = await Promise.all([api("/reports"), api("/locations"), api("/suppliers")]);
    if (!ctx.isCurrent()) return;
    const canExport = ctx.can("reports.export");

    mount(ctx.main, html`
        ${pageHeader("Reports", "Run a report on screen or export it")}
        <section class="section">
            <form id="report-form" class="toolbar wrap" novalidate>
                <label>Report
                    <select id="r-key">${reports.map(r => html`<option value="${r.key}">${r.name}</option>`)}</select></label>
                <label data-filter="date_from">From <input type="date" id="r-from" value="${today(-30)}"></label>
                <label data-filter="date_to">To <input type="date" id="r-to" value="${today()}"></label>
                <label data-filter="location_id">Location <select id="r-location"><option value="">All</option>
                    ${locations.map(l => html`<option value="${l.id}">${l.name}</option>`)}</select></label>
                <label data-filter="supplier_id">Supplier <select id="r-supplier"><option value="">All</option>
                    ${suppliers.map(s => html`<option value="${s.id}">${s.name}</option>`)}</select></label>
                <label data-filter="status">Status <select id="r-status"><option value="">All</option>
                    ${["EXPIRED", "CRITICAL", "URGENT", "APPROACHING EXPIRY", "NORMAL"].map(s => html`<option>${s}</option>`)}</select></label>
                <label data-filter="movement_type">Type <select id="r-type"><option value="">All</option>
                    ${["RECEIVED", "DISPENSED", "RETURNED", "DAMAGED", "EXPIRED", "ADJUSTMENT"].map(s => html`<option>${s}</option>`)}</select></label>
                <button type="submit" class="refresh-btn primary">Run report</button>
                ${canExport ? html`
                    <button type="button" class="refresh-btn" data-format="csv">CSV</button>
                    <button type="button" class="refresh-btn" data-format="xlsx">Excel</button>
                    <button type="button" class="refresh-btn" data-format="pdf">PDF</button>` : ""}
            </form>
        </section>
        <section class="section" id="report-output"><div class="loading">Choose a report and select Run.</div></section>`);

    const form = ctx.main.querySelector("#report-form");
    const key = form.querySelector("#r-key");
    const params = () => {
        const report = reports.find(r => r.key === key.value);
        const all = {
            date_from: form.querySelector("#r-from").value, date_to: form.querySelector("#r-to").value,
            location_id: form.querySelector("#r-location").value, supplier_id: form.querySelector("#r-supplier").value,
            status: form.querySelector("#r-status").value, movement_type: form.querySelector("#r-type").value,
        };
        return Object.fromEntries(Object.entries(all).filter(([k]) => report.filters.includes(k)));
    };
    const syncFilters = () => {
        const report = reports.find(r => r.key === key.value);
        form.querySelectorAll("[data-filter]").forEach(el => { el.hidden = !report.filters.includes(el.dataset.filter); });
    };
    key.addEventListener("change", syncFilters);
    syncFilters();

    const output = ctx.main.querySelector("#report-output");
    form.addEventListener("submit", async event => {
        event.preventDefault();
        mount(output, html`<div class="loading">Running…</div>`);
        try {
            const report = await api(`/reports/${key.value}`, { params: params() });
            mount(output, html`
                <div class="section-header"><div><h3>${report.title}</h3><p>${report.subtitle} · generated ${formatDateTime(report.generated_at)}</p></div></div>
                ${report.summary.length ? html`<dl class="summary">${report.summary.map(s => html`<div><dt>${s.label}</dt><dd>${s.value}</dd></div>`)}</dl>` : ""}
                ${table("report-table", report.columns.map(c => ({
                    label: c.label, render: row => cell(row[c.key], c.type),
                    className: ["int", "money", "number"].includes(c.type) ? "num" : "",
                })), report.rows, { empty: "No records for this report." })}`);
        } catch (error) {
            mount(output, html`<div class="loading error-text">${error.message}</div>`);
        }
    });

    form.querySelectorAll("[data-format]").forEach(button => button.addEventListener("click", async () => {
        button.disabled = true;
        try {
            await download(`/reports/${key.value}`, { ...params(), format: button.dataset.format });
        } catch (error) {
            toast(error.message, "error");
        } finally {
            button.disabled = false;
        }
    }));
}
