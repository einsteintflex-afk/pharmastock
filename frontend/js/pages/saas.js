/* Organization self-service: set-up guide (onboarding), plan & billing,
   messaging (WhatsApp / SMS / e-mail) settings and customer consent. */

import {
    api, badge, busy, emptyState, formatDate, formatDateTime, formModal, html, money, mount, number, onAction,
    pageHeader, table, toast,
} from "../core.js";
import { onboardingCompleted } from "../app.js";

/* Bar widths are set from data-pct: the Content Security Policy does not
   allow inline style attributes. */
function applyWidths(root) {
    root.querySelectorAll("[data-pct]").forEach(el => { el.style.width = `${el.dataset.pct}%`; });
}

/* ---------- Set-up guide ---------- */

export async function renderOnboarding(ctx) {
    const status = await api("/onboarding");
    if (!ctx.isCurrent()) return;
    const percent = Math.round((status.done / status.total) * 100);
    mount(ctx.main, html`
        ${pageHeader("Welcome to PharmaStock", "Nine steps to a pharmacy that runs itself. Each step is checked against your real records.")}
        <section class="section">
            <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${status.total}" aria-valuenow="${status.done}"
                 aria-label="Set-up progress"><span data-pct="${percent}"></span></div>
            <p>${status.done} of ${status.total} done${status.completed_at ? html` · set-up completed ${formatDate(status.completed_at)}` : ""}</p>
            <ol class="onboarding-steps">
                ${status.steps.map((step, i) => html`<li class="${step.done ? "done" : ""}">
                    <span class="step-number" aria-hidden="true">${step.done ? "✓" : i + 1}</span>
                    <div><strong>${step.title}</strong>${step.optional ? html` <small>(optional)</small>` : ""}<p>${step.description}</p></div>
                    <a class="${step.done ? "view-btn" : "refresh-btn primary"}" href="${step.link}">${step.done ? "Review" : "Start →"}</a>
                </li>`)}
            </ol>
            ${status.completed_at ? "" : html`<div class="form-actions">
                <button type="button" class="refresh-btn primary" data-action="finish" ${status.required_done ? "" : html`disabled`}>
                    ${status.required_done ? "✓ Finish set-up" : "Finish the required steps first"}</button></div>`}
        </section>`);
    applyWidths(ctx.main);
    onAction(ctx.main, {
        finish: button => busy(button, async () => {
            await api("/onboarding/complete", { method: "POST" });
            onboardingCompleted();
            toast("Set-up complete. Welcome aboard!");
            ctx.navigate("dashboard");
        }),
    });
}

/* ---------- Plan & billing ---------- */

export async function renderBilling(ctx) {
    const [billing, plans] = await Promise.all([api("/billing"), api("/plans")]);
    if (!ctx.isCurrent()) return;
    const org = billing.organization;
    const reference = ctx.params.query.reference;
    const planCards = Object.entries(plans.plans);
    mount(ctx.main, html`
        ${pageHeader("Plan & Billing", `${org.name} · ${org.plan_label} plan`)}
        ${reference ? html`<section class="section"><p class="form-intro">Payment ${reference}: your plan or credits change as soon as the
            payment provider confirms the payment (this page does not activate anything by itself). Refresh in a minute.</p></section>` : ""}
        <section class="cards">
            <div class="card"><div><span>Plan</span><strong>${badge(org.plan)} ${org.plan_label}</strong>
                <small class="card-hint">${org.status === "TRIAL" ? `Trial${org.trial_ends_at ? ` until ${formatDate(org.trial_ends_at)}` : ""}` : org.status}</small></div></div>
            <div class="card"><div><span>Paid until</span><strong>${org.current_period_end ? formatDate(org.current_period_end) : "—"}</strong>
                <small class="card-hint">${billing.subscription_status ? badge(billing.subscription_status) : ""}</small></div></div>
            <div class="card"><div><span>Messaging credits</span><strong>${number(billing.messaging.credit_balance)}</strong>
                <small class="card-hint">1 credit = 1 WhatsApp or SMS message sent through MedCart Tech</small></div></div>
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Usage</h3><p>Against your plan's limits</p></div></div>
            <dl class="summary">
                ${[["Active users", org.usage.active_users, org.limits.max_users], ["Active locations", org.usage.active_locations, org.limits.max_locations],
                   ["Medicines", org.usage.medicines, org.limits.max_medicines]].map(([label, used, max]) => html`
                    <div><dt>${label}</dt><dd>${number(used)} / ${max === null ? "unlimited" : number(max)}
                        ${max ? html`<div class="meter"><span data-pct="${Math.min(100, Math.round(used / max * 100))}"></span></div>` : ""}</dd></div>`)}
            </dl>
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Plans</h3><p>${plans.note}</p></div></div>
            <div class="plan-cards">
                ${planCards.map(([code, p]) => html`<div class="plan-card ${code === org.plan ? "current" : ""}">
                    <h4>${p.label} ${code === org.plan ? badge("CURRENT", "ACTIVE") : ""}</h4>
                    <p>${p.description || ""}</p>
                    <p class="price">${p.price_monthly !== null ? html`${p.currency} ${number(p.price_monthly, 2)} / month` : html`<small>Price on request</small>`}
                        ${p.price_annual !== null ? html`<br><small>${p.currency} ${number(p.price_annual, 2)} / year</small>` : ""}</p>
                    <ul>${p.features.map(f => html`<li>${plans.feature_labels[f] || f}</li>`)}</ul>
                    <p><small>${Object.entries(p.limits).map(([k, v]) => `${plans.limit_labels[k] || k}: ${v === null ? "unlimited" : v}`).join(" · ")}</small></p>
                    ${p.price_monthly !== null ? html`<div class="top-actions">
                        <button type="button" class="refresh-btn ${code === org.plan ? "" : "primary"}" data-action="subscribe" data-plan="${code}" data-cycle="MONTHLY">Pay monthly</button>
                        ${p.price_annual !== null ? html`<button type="button" class="refresh-btn" data-action="subscribe" data-plan="${code}" data-cycle="ANNUAL">Pay yearly</button>` : ""}
                    </div>` : html`<a class="view-btn" href="mailto:?subject=PharmaStock%20${encodeURIComponent(p.label)}%20plan">Contact MedCart Tech</a>`}
                </div>`)}
            </div>
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Messaging credits</h3><p>For WhatsApp / SMS receipts sent through MedCart Tech</p></div>
                <a class="view-btn" href="#/messaging">Messaging settings</a></div>
            ${billing.messaging.packages.length ? html`<div class="plan-cards">${billing.messaging.packages.map(p => html`
                <div class="plan-card"><h4>${p.label}</h4><p class="price">${p.currency} ${number(p.price, 2)}</p><p>${number(p.credits)} credits</p>
                    <button type="button" class="refresh-btn primary" data-action="credits" data-package="${p.code}">Buy</button></div>`)}</div>`
                : emptyState("No credit packages are on sale yet. MedCart Tech can add credits to your account on request.")}
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Payments</h3></div></div>
            ${table("payments", [
                { label: "Date", render: p => formatDateTime(p.created_at) },
                { label: "For", render: p => p.purpose === "SUBSCRIPTION" ? `${p.plan_code} (${(p.billing_cycle || "").toLowerCase()})` : `Credits: ${p.package_code}` },
                { label: "Amount", className: "num", render: p => `${p.currency} ${number(p.amount, 2)}` },
                { label: "Reference", key: "reference" },
                { label: "Status", render: p => badge(p.status) },
            ], billing.payments, { empty: "No payments yet." })}
        </section>`);

    applyWidths(ctx.main);
    const checkout = async body => {
        const result = await api("/billing/checkout", { method: "POST", body });
        if (result.checkout_url) {
            window.location.href = result.checkout_url;
        } else {
            formModal({ title: "Payment instructions", fields: [], submitLabel: "Done", intro: result.instructions, onSubmit: async () => ctx.reload() });
        }
    };
    onAction(ctx.main, {
        subscribe: button => busy(button, () => checkout({ purpose: "SUBSCRIPTION", plan_code: button.dataset.plan, billing_cycle: button.dataset.cycle })),
        credits: button => busy(button, () => checkout({ purpose: "MESSAGING_CREDITS", package_code: button.dataset.package })),
    });
}

/* ---------- Messaging ---------- */

const CHANNEL_LABEL = { WHATSAPP: "WhatsApp Business", SMS: "SMS gateway", EMAIL: "E-mail (SMTP)" };
const FIELD_LABEL = {
    phone_number_id: "Phone number ID", access_token: "Permanent access token", app_secret: "App secret (webhook signature)",
    receipt_template: "Approved receipt template name", thank_you_template: "Approved thank-you template name (optional)",
    language: "Template language code (e.g. en)", url: "Gateway URL (https://…)", token: "Bearer token",
    host: "SMTP host", port: "Port", security: "Security (starttls or ssl)", username: "Username", password: "Password", from: "From address",
};

export async function renderMessaging(ctx) {
    const [data, consents] = await Promise.all([api("/messaging"), api("/messaging/consents", { params: { limit: 50 } })]);
    if (!ctx.isCurrent()) return;
    const modes = [
        ["DISABLED", "Off", "No customer messages are sent."],
        ["PLATFORM_CREDITS", "Through MedCart Tech", `Uses MedCart Tech's official WhatsApp / SMS accounts. 1 credit per message · balance ${number(data.credit_balance)}.`],
        ["OWN_PROVIDER", "Own accounts", "Your own WhatsApp Business (Cloud API), SMS gateway and e-mail server. Credentials are stored encrypted."],
    ];
    mount(ctx.main, html`
        ${pageHeader("Messaging", "Receipts and thank-you messages to customers who agreed to receive them")}
        <section class="section">
            <div class="section-header"><div><h3>How messages are sent</h3><p>Nothing is sent until you choose</p></div></div>
            <div class="mode-cards">${modes.map(([key, label, text]) => html`
                <label class="mode-card ${data.mode === key ? "active" : ""}">
                    <input type="radio" name="mode" value="${key}" ${data.mode === key ? html`checked` : ""}>
                    <strong>${label}</strong><small>${text}</small></label>`)}</div>
            <p class="method">Plan: WhatsApp ${data.features.WHATSAPP ? "included" : "not included"} · SMS ${data.features.SMS ? "included" : "not included"}
                · e-mail receipts ${data.features.EMAIL ? "included" : "not included"}. Which messages go out is chosen in Settings → Customer messages.</p>
        </section>
        ${data.mode === "OWN_PROVIDER" ? html`<section class="section">
            <div class="section-header"><div><h3>Your providers</h3><p>WhatsApp webhook URL for Meta: <code>${data.webhook_url || "/messaging/webhooks/whatsapp"}</code></p></div></div>
            <div class="plan-cards">${Object.keys(data.provider_fields).map(channel => {
                const p = data.providers[channel];
                return html`<div class="plan-card"><h4>${CHANNEL_LABEL[channel]} ${p ? badge("ACTIVE") : badge("DISABLED")}</h4>
                    ${p ? html`<dl class="summary">${Object.entries(p.config).map(([k, v]) => html`<div><dt>${FIELD_LABEL[k] || k}</dt><dd>${String(v)}</dd></div>`)}</dl>` : html`<p>Not set up.</p>`}
                    <div class="top-actions"><button type="button" class="refresh-btn" data-action="provider" data-channel="${channel}">${p ? "Edit" : "Set up"}</button>
                    ${p ? html`<button type="button" class="view-btn danger" data-action="remove" data-channel="${channel}">Remove</button>` : ""}</div></div>`;
            })}</div>
        </section>` : ""}
        <section class="section">
            <div class="section-header"><div><h3>This month</h3></div>
                ${data.mode !== "DISABLED" ? html`<button type="button" class="refresh-btn" data-action="test">✉ Send a test message</button>` : ""}</div>
            ${table("messaging-month", [
                { label: "Channel", key: "channel" }, { label: "Type", key: "message_type" },
                { label: "Sent", className: "num", render: r => number(r.sent) }, { label: "Delivered / read", className: "num", render: r => number(r.delivered) },
                { label: "Not sent", className: "num", render: r => number(r.skipped) }, { label: "Failed", className: "num", render: r => number(r.failed) },
            ], data.this_month, { empty: "No customer messages this month." })}
            <p class="method"><a href="#/delivery">See every message and why it was or was not sent →</a></p>
        </section>
        <section class="section">
            <div class="section-header"><div><h3>Customer consent</h3><p>Recorded at the counter; customers can reply STOP on WhatsApp</p></div>
                <button type="button" class="refresh-btn" data-action="consent">Record a choice</button></div>
            ${table("consents", [
                { label: "Phone", key: "phone" }, { label: "Channel", key: "channel" }, { label: "Status", render: c => badge(c.status) },
                { label: "Source", key: "source" }, { label: "When", render: c => formatDateTime(c.recorded_at) },
                { label: "By", render: c => c.recorded_by_name || "—" },
            ], consents, { empty: "No consents recorded yet." })}
        </section>`);

    ctx.main.querySelectorAll("input[name=mode]").forEach(radio => radio.addEventListener("change", async () => {
        try {
            await api("/messaging/mode", { method: "PUT", body: { mode: radio.value } });
            toast("Messaging mode saved.");
            ctx.reload();
        } catch (error) { toast(error.message, "error"); }
    }));
    onAction(ctx.main, {
        provider: button => {
            const channel = button.dataset.channel;
            const spec = data.provider_fields[channel];
            const current = data.providers[channel]?.config || {};
            formModal({
                title: CHANNEL_LABEL[channel], wide: true, submitLabel: "Save",
                intro: channel === "WHATSAPP" ? "From Meta Business Suite → WhatsApp Manager. Only pre-approved templates can be sent to customers."
                    : "Secrets are encrypted; leave a masked value unchanged to keep it.",
                fields: spec.fields.map(f => ({ name: f, label: FIELD_LABEL[f] || f, value: current[f] ?? "",
                    required: spec.required.includes(f), type: spec.secret.includes(f) ? "password" : "text", autocomplete: "off" })),
                onSubmit: async v => {
                    await api(`/messaging/providers/${channel}`, { method: "PUT", body: { config: v } });
                    toast("Provider saved.");
                    ctx.reload();
                },
            });
        },
        remove: button => busy(button, async () => {
            await api(`/messaging/providers/${button.dataset.channel}`, { method: "DELETE" });
            ctx.reload();
        }),
        test: () => formModal({
            title: "Send a test message", submitLabel: "Queue test",
            fields: [{ name: "channel", label: "Channel", type: "select", options: ["WHATSAPP", "SMS", "EMAIL"].map(c => ({ value: c, label: CHANNEL_LABEL[c] })) },
                     { name: "recipient", label: "Your own phone number or e-mail", required: true }],
            onSubmit: async v => { await api("/messaging/test", { method: "POST", body: v }); toast("Queued: check Notifications → E-mail & Schedules for the result."); },
        }),
        consent: () => formModal({
            title: "Record a customer's choice", submitLabel: "Save",
            fields: [{ name: "phone", label: "Phone", required: true },
                     { name: "channel", label: "Channel", type: "select", options: [{ value: "WHATSAPP", label: "WhatsApp" }, { value: "SMS", label: "SMS" }] },
                     { name: "status", label: "Choice", type: "select", options: [{ value: "OPTED_OUT", label: "Does not want messages" }, { value: "OPTED_IN", label: "Agrees to messages" }] }],
            onSubmit: async v => { await api("/messaging/consents", { method: "POST", body: v }); ctx.reload(); },
        }),
    });
}
