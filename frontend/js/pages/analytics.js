/* Analytics: consumption, reorder, expiry risk, forecast, turnover,
   purchasing patterns. Tabs share one page; data loads per tab. */

import { api, badge, daysLabel, formatDate, html, money, mount, number, pageHeader, sortableTable, statTile } from "../core.js";

const TABS = [
    ["reorder", "Reorder"],
    ["risk", "Expiry risk"],
    ["consumption", "Consumption"],
    ["forecast", "Forecast"],
    ["turnover", "Turnover"],
    ["valuation", "Valuation"],
    ["purchasing", "Purchasing"],
];

/* Single-series inline bar: the number is always printed beside it. */
function bar(value, max) {
    const pct = max > 0 && value > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
    return html`<span class="bar" aria-hidden="true"><span class="bar-fill" data-pct="${pct}"></span></span>`;
}

function applyBars(root) {
    root.querySelectorAll(".bar-fill[data-pct]").forEach(el => { el.style.width = `${el.dataset.pct}%`; });
}

const RENDERERS = {
    async reorder(el) {
        const rows = await api("/analytics/reorder");
        const needed = rows.filter(r => r.reorder_recommended);
        mount(el, html`
            <p class="method">A reorder is recommended when usable stock is at or below the reorder level, or will run out within the supplier lead time. The suggested quantity covers lead time + cover days at the current consumption rate, minus stock already on order.</p>
            <div class="cards compact">${statTile("Reorders recommended", number(needed.length), "orange")}
                ${statTile("Units to order", number(needed.reduce((s, r) => s + r.recommended_quantity, 0)), "blue")}</div>
            <div id="t"></div>`);
        sortableTable(el.querySelector("#t"), "reorder-table", [
            { label: "Medicine", key: "medicine", render: r => html`<a href="#/medicines/${r.medicine_id}"><strong>${r.medicine}</strong></a> <small>${r.strength}</small>` },
            { label: "Usable", key: "usable_stock", render: r => number(r.usable_stock), className: "num" },
            { label: "Reorder lvl", key: "reorder_level", className: "num" },
            { label: "Daily use", key: "average_daily_consumption", render: r => number(r.average_daily_consumption, 2), className: "num" },
            { label: "Days of stock", key: "days_of_stock", render: r => r.days_of_stock ?? "—", className: "num" },
            { label: "On order", key: "on_order", className: "num" },
            { label: "Suggested", key: "recommended_quantity", render: r => r.reorder_recommended ? html`<strong>${number(r.recommended_quantity)}</strong>` : "—", className: "num" },
            { label: "Status", key: "stock_status", render: r => badge(r.stock_status) },
            { label: "Why", key: "reason", render: r => r.reason || "—" },
        ], rows);
    },

    async risk(el) {
        const data = await api("/analytics/expiry-risk");
        mount(el, html`
            <p class="method">Batches are consumed earliest-expiry-first at each medicine's current daily consumption. Units still on the shelf at a batch's expiry date are "at risk". HIGH: units at risk inside the urgent window, or at least half the batch at risk inside the approaching window. MEDIUM: at risk further out (likely dead stock unless demand changes).</p>
            <div class="cards compact">
                ${statTile("Batches at risk", number(data.summary.batches_at_risk), "red")}
                ${statTile("Units at risk", number(data.summary.units_at_risk), "orange")}
                ${statTile("Value at risk", money(data.summary.value_at_risk), "red",
                    data.summary.batches_without_cost ? `${data.summary.batches_without_cost} batches without cost` : "")}
            </div>
            <div id="t"></div>`);
        const max = Math.max(0, ...data.batches.map(r => r.projected_units_at_risk));
        sortableTable(el.querySelector("#t"), "risk-table", [
            { label: "Risk", key: "risk_level", render: r => badge(r.risk_level), sort: r => ["EXPIRED", "HIGH", "MEDIUM", "LOW"].indexOf(r.risk_level) },
            { label: "Medicine", key: "medicine", render: r => html`<a href="#/medicines/${r.medicine_id}">${r.medicine}</a> <small>${r.strength}</small>` },
            { label: "Batch", key: "batch_number", render: r => html`<a href="#/batches/${r.batch_id}">${r.batch_number}</a>` },
            { label: "Expiry", key: "expiry_date", render: r => `${formatDate(r.expiry_date)} (${daysLabel(r.days_until_expiry)})` },
            { label: "Qty", key: "quantity", className: "num" },
            { label: "Daily use", key: "average_daily_consumption", render: r => number(r.average_daily_consumption, 2), className: "num" },
            { label: "Units at risk", key: "projected_units_at_risk", render: r => html`${bar(r.projected_units_at_risk, max)} ${number(r.projected_units_at_risk)}` },
            { label: "Value at risk", key: "value_at_risk", render: r => money(r.value_at_risk), className: "num" },
        ], data.batches, { afterRender: applyBars });
    },

    async consumption(el) {
        const data = await api("/analytics/consumption");
        mount(el, html`
            <p class="method">Units dispensed. Daily average uses the last 30 days (or 90 days when nothing was dispensed recently). Trend compares the last 30 days with the 30 days before.</p>
            <div id="t"></div>`);
        const max = Math.max(0, ...data.medicines.map(r => r.units_dispensed_last_90_days));
        sortableTable(el.querySelector("#t"), "consumption-table", [
            { label: "Medicine", key: "medicine", render: r => html`<a href="#/medicines/${r.medicine_id}">${r.medicine}</a> <small>${r.strength}</small>` },
            { label: "30 days", key: "units_dispensed_last_30_days", className: "num" },
            { label: "90 days", key: "units_dispensed_last_90_days", render: r => html`${bar(r.units_dispensed_last_90_days, max)} ${number(r.units_dispensed_last_90_days)}` },
            { label: "Daily avg", key: "average_daily_consumption", render: r => number(r.average_daily_consumption, 2), className: "num" },
            { label: "Weekly avg", key: "average_weekly_consumption", render: r => number(r.average_weekly_consumption, 1), className: "num" },
            { label: "Trend", key: "trend", render: r => html`${badge(r.trend)} ${r.trend_change_percent !== null ? `${r.trend_change_percent > 0 ? "+" : ""}${r.trend_change_percent}%` : ""}` },
            { label: "Days of stock", key: "days_of_stock", render: r => r.days_of_stock ?? "—", className: "num" },
            { label: "Class", key: "movement_class", render: r => badge(r.movement_class) },
        ], data.medicines.sort((a, b) => b.units_dispensed_last_90_days - a.units_dispensed_last_90_days), { afterRender: applyBars });
    },

    async forecast(el) {
        const rows = await api("/analytics/forecast", { params: { horizon_days: 30 } });
        mount(el, html`
            <p class="method">${rows[0]?.method || ""}. Confidence reflects how many of the last 12 weeks had dispensing activity. Forecasts support, not replace, purchasing judgement.</p>
            <div id="t"></div>`);
        sortableTable(el.querySelector("#t"), "forecast-table", [
            { label: "Medicine", key: "medicine", render: r => html`<a href="#/medicines/${r.medicine_id}">${r.medicine}</a> <small>${r.strength}</small>` },
            { label: "Weekly demand", key: "forecast_weekly_demand", render: r => number(r.forecast_weekly_demand, 1), className: "num" },
            { label: "30-day demand", key: "forecast_demand", render: r => number(r.forecast_demand, 1), className: "num" },
            { label: "Usable stock", key: "usable_stock", className: "num" },
            { label: "Stock after 30 d", key: "projected_stock_at_horizon", render: r => number(r.projected_stock_at_horizon, 1), className: "num" },
            { label: "Covered", key: "covers_horizon", render: r => r.covers_horizon ? badge("Covered", "NORMAL") : badge("Shortfall", "EXPIRED") },
            { label: "Confidence", key: "confidence", render: r => badge(r.confidence, r.confidence === "HIGH" ? "NORMAL" : r.confidence === "MEDIUM" ? "URGENT" : "NO MOVEMENT") },
        ], rows);
    },

    async turnover(el) {
        const rows = await api("/analytics/turnover");
        mount(el, html`
            <p class="method">Turnover = units dispensed in 90 days ÷ average stock (mean of today's stock and stock 90 days ago, reconstructed from the movement ledger).</p>
            <div id="t"></div>`);
        sortableTable(el.querySelector("#t"), "turnover-table", [
            { label: "Medicine", key: "medicine", render: r => html`${r.medicine} <small>${r.strength}</small>` },
            { label: "Current stock", key: "current_stock", className: "num" },
            { label: "Avg stock", key: "average_stock", className: "num" },
            { label: "Dispensed 90 d", key: "dispensed_90d", className: "num" },
            { label: "Turnover (90 d)", key: "turnover_90d", render: r => r.turnover_90d ?? "—", className: "num" },
            { label: "Annualised", key: "annualised_turnover", render: r => r.annualised_turnover ?? "—", className: "num" },
            { label: "Days of inventory", key: "days_of_inventory", render: r => r.days_of_inventory ?? "—", className: "num" },
        ], rows);
    },

    async valuation(el) {
        const v = await api("/analytics/valuation");
        const statuses = Object.entries(v.by_expiry_status);
        mount(el, html`
            <p class="method">Stock valued at batch acquisition cost. Units with no recorded cost are counted but not valued (never estimated).</p>
            <div class="cards compact">
                ${statTile("Total value", money(v.total.value), "blue", `${number(v.total.units)} units`)}
                ${statTile("Usable value", money(v.usable_value), "green")}
                ${statTile("Expired value", money(v.expired.value), "red")}
                ${statTile("Units without cost", number(v.total.units_without_cost), "orange")}
            </div>
            <div class="lower-grid">
                <div id="t1"></div><div id="t2"></div>
            </div>`);
        sortableTable(el.querySelector("#t1"), "val-status", [
            { label: "Expiry status", render: ([s]) => badge(s) },
            { label: "Batches", render: ([, x]) => number(x.batches), className: "num" },
            { label: "Units", render: ([, x]) => number(x.units), className: "num" },
            { label: "Value", render: ([, x]) => money(x.value), className: "num" },
        ], statuses);
        sortableTable(el.querySelector("#t2"), "val-location", [
            { label: "Location", key: "location" },
            { label: "Batches", key: "batches", className: "num" },
            { label: "Units", key: "units", render: r => number(r.units), className: "num" },
            { label: "Value", key: "value", render: r => money(r.value), className: "num" },
        ], v.by_location);
    },

    async purchasing(el) {
        const p = await api("/analytics/purchasing");
        mount(el, html`<div class="lower-grid"><div id="t1"></div><div id="t2"></div></div>`);
        sortableTable(el.querySelector("#t1"), "pur-supplier", [
            { label: "Supplier", key: "supplier" },
            { label: "Orders", key: "orders", className: "num" },
            { label: "Ordered value", key: "ordered_value", render: r => money(r.ordered_value), className: "num" },
            { label: "Fill rate", key: "fill_rate_percent", render: r => r.fill_rate_percent === null ? "—" : `${r.fill_rate_percent}%`, className: "num" },
            { label: "Avg lead time", key: "average_lead_time_days", render: r => r.average_lead_time_days === null ? "—" : `${r.average_lead_time_days} d`, className: "num" },
        ], p.by_supplier);
        sortableTable(el.querySelector("#t2"), "pur-month", [
            { label: "Month", key: "month" },
            { label: "Orders", key: "orders", className: "num" },
            { label: "Value", key: "ordered_value", render: r => money(r.ordered_value), className: "num" },
        ], p.by_month, { empty: "No orders." });
    },
};

export async function render(ctx) {
    const active = TABS.some(([key]) => key === ctx.params.query?.tab) ? ctx.params.query.tab : "reorder";
    mount(ctx.main, html`
        ${pageHeader("Analytics", "Consumption, reorder, expiry risk, forecasting and turnover — computed from live data")}
        <nav class="tabs" aria-label="Analytics views">
            ${TABS.map(([key, label]) => html`<a href="#/analytics?tab=${key}" class="tab ${key === active ? "active" : ""}"
                ${key === active ? html`aria-current="page"` : ""}>${label}</a>`)}
        </nav>
        <section class="section" id="tab-body"><div class="loading">Loading…</div></section>`);
    await RENDERERS[active](ctx.main.querySelector("#tab-body"));
}
