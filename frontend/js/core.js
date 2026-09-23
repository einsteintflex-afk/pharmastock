/* =========================================================
   PharmaStock frontend core
   - html``: templates that escape every interpolated value
   - api():   one fetch wrapper (session cookie, JSON errors)
   - UI helpers: tables, badges, modal forms, toasts
   No inline event handlers: pages attach listeners after
   rendering (the Content-Security-Policy forbids inline JS).
========================================================= */

/* ---------- Safe HTML ---------- */

export class SafeHtml {
    constructor(value) { this.value = value; }
    toString() { return this.value; }
}

export function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function renderValue(value) {
    if (value instanceof SafeHtml) return value.value;
    if (Array.isArray(value)) return value.map(renderValue).join("");
    if (value === null || value === undefined || value === false) return "";
    return escapeHtml(value);
}

export function html(strings, ...values) {
    let out = strings[0];
    values.forEach((value, index) => { out += renderValue(value) + strings[index + 1]; });
    return new SafeHtml(out);
}

export function mount(element, content) {
    element.innerHTML = renderValue(content);
    return element;
}

/* ---------- Formatting ---------- */

let currencySymbol = "₵";
export function setCurrency(symbol) { if (symbol) currencySymbol = symbol; }

export function money(value) {
    if (value === null || value === undefined || value === "") return "—";
    return currencySymbol + Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function number(value, digits = 0) {
    if (value === null || value === undefined || value === "") return "—";
    return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function formatDate(value) {
    if (!value) return "—";
    const date = new Date(String(value).length === 10 ? value + "T00:00:00" : String(value).replace(" ", "T"));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function formatDateTime(value) {
    if (!value) return "—";
    const date = new Date(String(value).replace(" ", "T"));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function daysLabel(days) {
    if (days === null || days === undefined) return "—";
    if (days < 0) return `${Math.abs(days)} days overdue`;
    if (days === 0) return "Expires today";
    return `${days} days`;
}

export function today(offsetDays = 0) {
    const d = new Date();
    d.setDate(d.getDate() + offsetDays);
    return d.toISOString().slice(0, 10);
}

/* ---------- Status badges (colour + text, never colour alone) ---------- */

const BADGE_CLASS = {
    "EXPIRED": "expired", "CRITICAL": "critical", "URGENT": "urgent", "APPROACHING EXPIRY": "approaching",
    "NORMAL": "normal", "LOW STOCK": "low", "OUT OF STOCK": "expired", "NO MOVEMENT": "muted",
    "SLOW-MOVING": "approaching", "ACTIVE": "normal", "HIGH": "expired", "MEDIUM": "urgent", "LOW": "normal",
    "DRAFT": "muted", "ORDERED": "info", "PARTIALLY_RECEIVED": "urgent", "RECEIVED": "normal",
    "CANCELLED": "muted", "INCREASING": "info", "DECREASING": "approaching", "STABLE": "normal",
    "NEW DEMAND": "info", "CRITICAL_SEV": "expired", "WARNING": "urgent", "INFO": "info",
    "DISPENSED": "urgent", "RETURNED": "info", "DAMAGED": "expired", "ADJUSTMENT": "muted",
    "Active": "normal", "Inactive": "muted",
};

export function badge(value, override) {
    if (value === null || value === undefined) return "";
    const cls = BADGE_CLASS[override || value] || "muted";
    return html`<span class="status ${cls}">${String(value).replaceAll("_", " ")}</span>`;
}

/* ---------- API ---------- */

export class ApiError extends Error {
    constructor(status, detail) {
        super(ApiError.message(detail) || `Request failed (${status})`);
        this.status = status;
        this.detail = detail;
    }

    static message(detail) {
        if (!detail) return "";
        if (typeof detail === "string") return detail;
        if (detail.message) return detail.message + (detail.hint ? ` ${detail.hint}` : "");
        return JSON.stringify(detail);
    }
}

let unauthorizedHandler = () => {};
export function onUnauthorized(handler) { unauthorizedHandler = handler; }

export async function api(path, { method = "GET", body, params, raw = false } = {}) {
    let url = path;
    if (params) {
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== "") query.append(key, value);
        });
        const text = query.toString();
        if (text) url += (url.includes("?") ? "&" : "?") + text;
    }

    let response;
    try {
        response = await fetch(url, {
            method,
            credentials: "same-origin",
            headers: body !== undefined ? { "Content-Type": "application/json" } : {},
            body: body !== undefined ? JSON.stringify(body) : undefined,
        });
    } catch {
        throw new ApiError(0, "Unable to connect to the PharmaStock server.");
    }

    if (response.status === 401 && path !== "/auth/login") {
        unauthorizedHandler();
        throw new ApiError(401, "Your session has ended. Please sign in again.");
    }

    if (!response.ok) {
        let detail = null;
        try {
            const data = await response.json();
            detail = data.errors
                ? data.errors.map(e => `${e.field || "value"}: ${e.message}`).join("; ")
                : data.detail;
        } catch { /* not JSON */ }
        throw new ApiError(response.status, detail);
    }

    if (raw) return response;
    if (response.status === 204) return null;
    return response.json();
}

export async function download(path, params) {
    const response = await api(path, { params, raw: true });
    const disposition = response.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = match ? match[1] : "pharmastock-export";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

/* ---------- Toasts ---------- */

export function toast(message, type = "success") {
    const container = document.getElementById("toasts");
    if (!container) return;
    const item = document.createElement("div");
    item.className = `toast ${type}`;
    item.setAttribute("role", type === "error" ? "alert" : "status");
    item.textContent = message;
    container.appendChild(item);
    while (container.children.length > 3) container.firstElementChild.remove();
    setTimeout(() => item.remove(), type === "error" ? 7000 : 3500);
}

/* ---------- Page scaffolding ---------- */

export function pageHeader(title, subtitle, actions = "") {
    return html`
        <header class="topbar">
            <div>
                <h2>${title}</h2>
                <p>${subtitle}</p>
            </div>
            <div class="top-actions">${actions}</div>
        </header>`;
}

export function loadingBlock(text = "Loading…") {
    return html`<div class="loading">${text}</div>`;
}

export function errorBlock(error) {
    return html`<div class="loading error-text" role="alert">${error.message || String(error)}</div>`;
}

export function statTile(label, value, tone = "blue", hint = "", icon = "") {
    return html`
        <div class="card">
            ${icon ? html`<div class="card-icon ${tone}" aria-hidden="true">${icon}</div>` : ""}
            <div>
                <span>${label}</span>
                <strong>${value}</strong>
                ${hint ? html`<small class="card-hint">${hint}</small>` : ""}
            </div>
        </div>`;
}

export function plural(count, singular, pluralForm = `${singular}s`) {
    return `${Number(count).toLocaleString()} ${Number(count) === 1 ? singular : pluralForm}`;
}

/* ---------- Tables ---------- */

/**
 * columns: [{ key, label, render?(row) -> string|SafeHtml, sort?(row) -> value, className? }]
 * Returns SafeHtml. Use sortableTable() for headers that sort on click.
 */
export function table(id, columns, rows, { empty = "No records found.", rowAttrs } = {}) {
    const header = columns.map((c, i) =>
        html`<th scope="col" class="${c.className || ""}"><button type="button" class="th-sort" data-sort="${i}">${c.label}</button></th>`);
    const body = rows.length
        ? rows.map(row => html`<tr ${rowAttrs ? rowAttrs(row) : ""}>${columns.map(c =>
            html`<td class="${c.className || ""}">${c.render ? c.render(row) : row[c.key]}</td>`)}</tr>`)
        : html`<tr><td colspan="${columns.length}" class="loading">${empty}</td></tr>`;
    return html`<div class="table-container"><table id="${id}"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function sortRows(rows, column, direction) {
    const key = column.sort || (row => row[column.key]);
    return [...rows].sort((a, b) => {
        const x = key(a), y = key(b);
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * direction;
    });
}

/**
 * Render a table whose headers sort client-side. The chosen sort is kept on
 * the container, so re-rendering with filtered rows preserves it.
 */
export function sortableTable(container, id, columns, rows, options = {}) {
    const sortKey = container.dataset.sortKey;
    const direction = container.dataset.sortDir === "desc" ? -1 : 1;
    const data = sortKey !== undefined && columns[Number(sortKey)] ? sortRows(rows, columns[Number(sortKey)], direction) : rows;

    mount(container, table(id, columns, data, options));
    if (sortKey !== undefined) {
        container.querySelector(`[data-sort="${sortKey}"]`)?.classList.add(direction === 1 ? "asc" : "desc");
    }
    container.querySelectorAll(`#${id} [data-sort]`).forEach(button => {
        button.addEventListener("click", () => {
            const same = container.dataset.sortKey === button.dataset.sort;
            container.dataset.sortDir = same && container.dataset.sortDir === "asc" ? "desc" : "asc";
            container.dataset.sortKey = button.dataset.sort;
            sortableTable(container, id, columns, rows, options);
        });
    });
    options.afterRender?.(container);
}

/* ---------- Event helpers ---------- */

export function onAction(root, handlers) {
    root.addEventListener("click", event => {
        const target = event.target.closest("[data-action]");
        if (!target || !root.contains(target)) return;
        const handler = handlers[target.dataset.action];
        if (handler) {
            event.preventDefault();
            handler(target, event);
        }
    });
}

export function debounce(fn, wait = 250) {
    let timer;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); };
}

/* ---------- Modal forms ---------- */

let lastFocus = null;

export function closeModal() {
    const root = document.getElementById("modal-root");
    if (root) root.innerHTML = "";
    document.body.classList.remove("modal-open");
    lastFocus?.focus?.();
}

export function openModal(title, content, { wide = false } = {}) {
    const root = document.getElementById("modal-root");
    lastFocus = document.activeElement;
    mount(root, html`
        <div class="modal-backdrop" data-close="1">
            <div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-labelledby="modal-title">
                <div class="modal-header">
                    <h3 id="modal-title">${title}</h3>
                    <button type="button" class="icon-btn" data-close="1" aria-label="Close">×</button>
                </div>
                <div class="modal-body">${content}</div>
            </div>
        </div>`);
    document.body.classList.add("modal-open");
    root.querySelectorAll("[data-close]").forEach(el => el.addEventListener("click", event => {
        if (event.target === el) closeModal();
    }));
    root.querySelector("input, select, textarea, button:not(.icon-btn)")?.focus();
    return root.querySelector(".modal-body");
}

function fieldHtml(field) {
    const id = `f-${field.name}`;
    const required = field.required ? html` <span class="req" aria-hidden="true">*</span>` : "";
    const common = { id, name: field.name };
    let control;
    if (field.type === "select") {
        control = html`<select id="${common.id}" name="${common.name}" ${field.required ? html`required` : ""}>
            ${field.placeholder !== undefined ? html`<option value="">${field.placeholder}</option>` : ""}
            ${field.options.map(o => html`<option value="${o.value}" ${String(o.value) === String(field.value ?? "") ? html`selected` : ""}>${o.label}</option>`)}
        </select>`;
    } else if (field.type === "textarea") {
        control = html`<textarea id="${common.id}" name="${common.name}" rows="3" maxlength="${field.maxlength || 2000}" ${field.required ? html`required` : ""}>${field.value ?? ""}</textarea>`;
    } else if (field.type === "checkbox") {
        control = html`<input id="${common.id}" name="${common.name}" type="checkbox" ${field.value ? html`checked` : ""}>`;
    } else {
        control = html`<input id="${common.id}" name="${common.name}" type="${field.type || "text"}"
            value="${field.value ?? ""}" ${field.required ? html`required` : ""}
            ${field.min !== undefined ? html`min="${field.min}"` : ""} ${field.max !== undefined ? html`max="${field.max}"` : ""}
            ${field.step !== undefined ? html`step="${field.step}"` : ""} ${field.maxlength ? html`maxlength="${field.maxlength}"` : ""}
            ${field.placeholder ? html`placeholder="${field.placeholder}"` : ""} ${field.autocomplete ? html`autocomplete="${field.autocomplete}"` : ""}>`;
    }
    return html`<div class="field ${field.type === "checkbox" ? "checkbox" : ""} ${field.full ? "full" : ""}">
        <label for="${id}">${field.label}${required}</label>
        ${control}
        ${field.help ? html`<small class="help">${field.help}</small>` : ""}
    </div>`;
}

export function formHtml(fields, submitLabel = "Save", intro = "") {
    return html`
        <form class="form-grid" novalidate>
            ${intro ? html`<p class="form-intro full">${intro}</p>` : ""}
            ${fields.map(fieldHtml)}
            <div class="form-error full" role="alert" hidden></div>
            <div class="form-actions full">
                <button type="button" class="view-btn" data-cancel="1">Cancel</button>
                <button type="submit" class="refresh-btn primary">${submitLabel}</button>
            </div>
        </form>`;
}

export function readForm(form, fields) {
    const values = {};
    for (const field of fields) {
        const element = form.elements[field.name];
        if (!element) continue;
        if (field.type === "checkbox") { values[field.name] = element.checked; continue; }
        const raw = element.value.trim();
        if (raw === "") { values[field.name] = field.emptyValue !== undefined ? field.emptyValue : null; continue; }
        values[field.name] = field.type === "number" ? Number(raw) : raw;
    }
    return values;
}

/**
 * Open a modal form. onSubmit(values) may throw ApiError; the message is
 * shown inside the form and the form stays open.
 */
export function formModal({ title, fields, submitLabel = "Save", intro = "", validate, onSubmit, wide = false }) {
    const body = openModal(title, formHtml(fields, submitLabel, intro), { wide });
    const form = body.querySelector("form");
    const errorBox = form.querySelector(".form-error");
    form.querySelector("[data-cancel]").addEventListener("click", closeModal);
    form.addEventListener("submit", async event => {
        event.preventDefault();
        errorBox.hidden = true;
        for (const field of fields) {
            const element = form.elements[field.name];
            if (field.required && element && field.type !== "checkbox" && !element.value.trim()) {
                errorBox.textContent = `${field.label} is required.`;
                errorBox.hidden = false;
                element.focus();
                return;
            }
        }
        const values = readForm(form, fields);
        const problem = validate?.(values);
        if (problem) { errorBox.textContent = problem; errorBox.hidden = false; return; }
        const button = form.querySelector("button[type=submit]");
        button.disabled = true;
        try {
            await onSubmit(values);
            closeModal();
        } catch (error) {
            errorBox.textContent = error.message || String(error);
            errorBox.hidden = false;
        } finally {
            button.disabled = false;
        }
    });
    return form;
}

export function confirmModal(title, message, confirmLabel = "Confirm") {
    return new Promise(resolve => {
        const body = openModal(title, html`
            <p>${message}</p>
            <div class="form-actions">
                <button type="button" class="view-btn" data-answer="no">Cancel</button>
                <button type="button" class="refresh-btn primary" data-answer="yes">${confirmLabel}</button>
            </div>`);
        body.querySelectorAll("[data-answer]").forEach(button => button.addEventListener("click", () => {
            closeModal();
            resolve(button.dataset.answer === "yes");
        }));
    });
}

document.addEventListener("keydown", event => {
    if (event.key === "Escape" && document.body.classList.contains("modal-open")) closeModal();
});
