/* Dashboard: headline figures, expiry, low stock, risk, reorder, movements. */

import {
    api, badge, daysLabel, formatDate, formatDateTime, html, money, mount, movementDirection, number, pageHeader, plural,
    signedQuantity, statTile, table,
} from "../core.js";

export async function render(ctx) {
    const [data, alerts] = await Promise.all([api("/dashboard"), api("/expiry-alerts")]);
    if (!ctx.isCurrent()) return;

    const t = data.thresholds;
    const expiring = alerts.batches.filter(b => b.status !== "EXPIRED").slice(0, 8);

    mount(ctx.main, html`
        ${pageHeader("Dashboard", `Pharmacy inventory overview · ${formatDate(data.as_of)}`,
            html`<button type="button" class="refresh-btn" id="dash-refresh">↻ Refresh</button>`)}

        <section class="cards" aria-label="Key figures">
            ${statTile("Total Medicines", number(data.total_medicines), "blue", `${data.medicines_in_stock} in stock`, "💊")}
            ${statTile("Usable Stock (units)", number(data.usable_units), "green", `${number(data.total_units)} incl. expired`, "📦")}
            ${statTile("Stock Value", money(data.stock_value), "blue",
                data.units_without_cost ? `${number(data.units_without_cost)} units have no recorded cost` : "at acquisition cost", "₵")}
            ${statTile("Expired", plural(data.expired.batches, "batch", "batches"), "red",
                `${number(data.expired.units)} units · ${money(data.expired.value)}`, "⚠")}
            ${statTile("Critical / Urgent", `${data.critical.batches} / ${data.urgent.batches}`, "orange",
                `batches ≤${t.critical_days} / ≤${t.urgent_days} days · ${money(data.critical.value + data.urgent.value)}`, "⏱")}
            ${statTile("Low / Out of Stock", `${data.low_stock_count} / ${data.out_of_stock_count}`, "orange",
                plural(data.reorder_count, "reorder") + " recommended", "↓")}
            ${statTile("Expiry Risk", money(data.expiry_risk.value_at_risk), "red",
                `${plural(data.expiry_risk.units_at_risk, "unit")} in ${plural(data.expiry_risk.batches, "batch", "batches")}`, "!")}
            ${statTile("Slow-moving", plural(data.slow_moving_count, "medicine"), "blue", "little or no dispensing in 90 days", "🐢")}
        </section>

        <section class="section today-strip">
            <div><h3>Dispensing today</h3>
                <p>${plural(data.dispensing_today.dispensations, "transaction")} · ${plural(data.dispensing_today.prescriptions, "prescription")} ·
                ${number(data.dispensing_today.units)} units · ${money(data.dispensing_today.sales_total)} sales</p></div>
            <div class="top-actions">
                <a class="view-btn" href="#/dispensations">History</a>
                ${ctx.can("stock.dispense") ? html`<a class="refresh-btn primary" href="#/dispense">Open dispensing counter</a>` : ""}
            </div>
        </section>

        <section class="section">
            <div class="section-header">
                <div><h3>Expiry Alerts</h3><p>Batches in stock approaching expiry (FEFO order)</p></div>
                <a class="view-btn" href="#/expiry">View all</a>
            </div>
            ${table("dash-expiry", [
                { label: "Medicine", render: b => html`<strong>${b.medicine}</strong><br><small>${b.strength} ${b.dosage_form}</small>` },
                { label: "Batch", render: b => html`<a href="#/batches/${b.batch_id}">${b.batch_number}</a>` },
                { label: "Quantity", render: b => number(b.quantity) },
                { label: "Expiry Date", render: b => formatDate(b.expiry_date) },
                { label: "Days Remaining", render: b => daysLabel(b.days_until_expiry) },
                { label: "Status", render: b => badge(b.status) },
            ], expiring, { empty: "No batches are approaching expiry." })}
        </section>

        <div class="lower-grid">
            <section class="section">
                <div class="section-header">
                    <div><h3>Expiry Risk</h3><p>Stock projected to expire unused at current consumption</p></div>
                    <a class="view-btn" href="#/analytics">Analyse</a>
                </div>
                ${table("dash-risk", [
                    { label: "Medicine / Batch", render: r => html`<strong>${r.medicine}</strong><br><small>${r.batch_number}</small>` },
                    { label: "At risk", render: r => `${number(r.projected_units_at_risk)} of ${number(r.quantity)}` },
                    { label: "Value", render: r => money(r.value_at_risk) },
                    { label: "Risk", render: r => badge(r.risk_level) },
                ], data.expiry_risk.top, { empty: "No expiry risk projected." })}
            </section>

            <section class="section">
                <div class="section-header">
                    <div><h3>Reorder Recommendations</h3><p>Usable stock vs reorder level and consumption</p></div>
                    <a class="view-btn" href="#/analytics">Details</a>
                </div>
                ${table("dash-reorder", [
                    { label: "Medicine", render: r => html`<a href="#/medicines/${r.medicine_id}"><strong>${r.medicine}</strong></a><br><small>${r.strength}</small>` },
                    { label: "Usable", render: r => number(r.usable_stock) },
                    { label: "Days left", render: r => r.days_of_stock ?? "—" },
                    { label: "Order", render: r => html`<strong>${number(r.recommended_quantity)}</strong>` },
                ], data.reorder, { empty: "Nothing needs reordering." })}
            </section>
        </div>

        <div class="lower-grid">
            <section class="section">
                <div class="section-header">
                    <div><h3>Stock Overview</h3><p>Usable stock against reorder level</p></div>
                    <a class="view-btn" href="#/medicines">Medicines</a>
                </div>
                ${table("dash-stock", [
                    { label: "Medicine", render: s => html`<strong>${s.medicine}</strong><br><small>${s.strength} ${s.dosage_form}</small>` },
                    { label: "Usable", render: s => number(s.usable_stock) },
                    { label: "Reorder Level", render: s => number(s.reorder_level) },
                    { label: "Status", render: s => badge(s.stock_status) },
                ], data.stock_levels)}
            </section>

            <section class="section">
                <div class="section-header">
                    <div><h3>Recent Movements</h3><p>Latest stock activity</p></div>
                    <a class="view-btn" href="#/movements">View all</a>
                </div>
                <div class="movement-list">
                    ${data.recent_movements.length ? data.recent_movements.map(m => html`
                        <div class="movement">
                            <div class="movement-icon" aria-hidden="true">${movementDirection(m) === "received" ? "↓" : "↑"}</div>
                            <div class="movement-info">
                                <strong>${m.medicine}</strong>
                                <small>${m.batch_number} · ${m.movement_type} · ${formatDateTime(m.movement_date)}${m.user_name ? ` · ${m.user_name}` : ""}</small>
                            </div>
                            <div class="movement-quantity ${movementDirection(m)}">${signedQuantity(m)}</div>
                        </div>`) : html`<div class="loading">No stock movements found.</div>`}
                </div>
            </section>
        </div>

        <div class="lower-grid">
            <section class="section">
                <div class="section-header">
                    <div><h3>Recent Purchases</h3><p>Latest purchase orders</p></div>
                    <a class="view-btn" href="#/purchasing">Purchasing</a>
                </div>
                ${table("dash-purchases", [
                    { label: "Order", render: p => html`<a href="#/purchasing/${p.id}">${p.order_number}</a><br><small>${formatDate(p.order_date)}</small>` },
                    { label: "Supplier", key: "supplier" },
                    { label: "Value", render: p => money(p.order_value), className: "num" },
                    { label: "Status", render: p => badge(p.status) },
                ], data.recent_purchases, { empty: "No purchase orders yet." })}
            </section>
            <section class="section">
                <div class="section-header">
                    <div><h3>Supplier Activity</h3><p>Latest deliveries received</p></div>
                    <a class="view-btn" href="#/suppliers">Suppliers</a>
                </div>
                ${table("dash-supplier-activity", [
                    { label: "Received", render: r => formatDateTime(r.received_date) },
                    { label: "Supplier", key: "supplier" },
                    { label: "Medicine", key: "medicine" },
                    { label: "Qty", render: r => number(r.quantity_received), className: "num" },
                    { label: "Order", render: r => html`<a href="#/purchasing/${r.purchase_order_id}">${r.order_number}</a>` },
                ], data.supplier_activity, { empty: "No deliveries received yet." })}
            </section>
        </div>

        ${data.open_transfers && (data.open_transfers.awaiting_approval || data.open_transfers.awaiting_dispatch
            || data.open_transfers.in_transit || data.held_batches.batches) ? html`
        <section class="section today-strip">
            <div><h3>Transfers & held stock</h3>
                <p>${plural(data.open_transfers.awaiting_approval, "request")} awaiting approval ·
                ${number(data.open_transfers.awaiting_dispatch)} awaiting dispatch · ${number(data.open_transfers.in_transit)} in transit ·
                ${plural(data.held_batches.batches, "batch", "batches")} quarantined / recalled (${number(data.held_batches.units)} units)</p></div>
            ${ctx.can("inventory.read") && ctx.hasFeature("multi_location")
                ? html`<a class="view-btn" href="#/transfers">Transfers</a>` : ""}
        </section>` : ""}

        ${data.slow_moving.length ? html`
        <section class="section">
            <div class="section-header"><div><h3>Slow-moving Stock</h3><p>Medicines holding stock with little or no dispensing in 90 days</p></div></div>
            ${table("dash-slow", [
                { label: "Medicine", render: s => html`<strong>${s.medicine}</strong> <small>${s.strength}</small>` },
                { label: "Dispensed (90 d)", render: s => number(s.units_dispensed_last_90_days) },
                { label: "Usable stock", render: s => number(s.usable_stock) },
                { label: "Class", render: s => badge(s.movement_class) },
            ], data.slow_moving)}
        </section>` : ""}
    `);

    ctx.main.querySelector("#dash-refresh").addEventListener("click", () => ctx.reload());
}


