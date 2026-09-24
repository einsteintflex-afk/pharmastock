/* =========================================================
   PharmaStock application shell
   One router (URL hash), one navigation, one session state.
========================================================= */

import {
    api, closeModal, debounce, errorBlock, formModal, html, loadingBlock, mount, onStepUp, onUnauthorized, setCurrency,
    store, toast,
} from "./core.js";

import * as dashboard from "./pages/dashboard.js";
import * as medicines from "./pages/medicines.js";
import * as inventory from "./pages/inventory.js";
import * as stock from "./pages/stock.js";
import * as dispensing from "./pages/dispensing.js";
import * as purchasing from "./pages/purchasing.js";
import * as suppliers from "./pages/suppliers.js";
import * as analytics from "./pages/analytics.js";
import * as reports from "./pages/reports.js";
import * as notifications from "./pages/notifications.js";
import * as assistant from "./pages/assistant.js";
import * as admin from "./pages/admin.js";
import * as transfers from "./pages/transfers.js";
import * as scan from "./pages/scan.js";
import * as organization from "./pages/organization.js";
import * as stockcontrol from "./pages/stockcontrol.js";
import * as saas from "./pages/saas.js";
import * as platform from "./pages/platform.js";

/* ---------- Routes (single source of truth for navigation) ---------- */

// section: sidebar group. Routes without a label are sub-pages (detail views).
const ROUTES = [
    { path: "dashboard", label: "Command Center", icon: "▣", permission: "analytics.read", page: dashboard.render, section: "Overview" },
    { path: "scan", label: "Scan Center", icon: "⌗", permission: "inventory.read", page: scan.render, section: "Overview" },
    { path: "assistant", label: "AI Assistant", icon: "✦", permission: "assistant.use", feature: "ai_assistant", page: assistant.render, section: "Overview" },
    { path: "notifications", label: "Notifications", icon: "🔔", permission: "notifications.read", page: notifications.render, section: "Overview" },

    { path: "dispense", label: "Dispensing Counter", icon: "🧾", permission: "stock.dispense", page: dispensing.renderCounter, section: "Sales" },
    { path: "dispensations", label: "Sales History", icon: "🗂", permission: "inventory.read", page: dispensing.renderHistory, section: "Sales" },
    { path: "dispensations/:id", parent: "dispensations", permission: "inventory.read", page: dispensing.renderDetail },

    { path: "medicines", label: "Medicines", icon: "💊", permission: "inventory.read", page: medicines.renderList, section: "Inventory" },
    { path: "medicines/:id", parent: "medicines", permission: "inventory.read", page: medicines.renderDetail },
    { path: "inventory", label: "Stock & Batches", icon: "📦", permission: "inventory.read", page: inventory.renderInventory, section: "Inventory" },
    { path: "batches/:id", parent: "inventory", permission: "inventory.read", page: inventory.renderBatch },
    { path: "expiry", label: "Expiry Alerts", icon: "⚠", permission: "inventory.read", page: inventory.renderExpiry, section: "Inventory" },
    { path: "stock-counts", label: "Stock Counts", icon: "🔢", permission: "inventory.read", feature: "stock_count", page: stockcontrol.renderCounts, section: "Inventory" },
    { path: "stock-counts/:id", parent: "stock-counts", permission: "inventory.read", feature: "stock_count", page: stockcontrol.renderCount },
    { path: "adjustments", label: "Adjustments", icon: "±", permission: "inventory.read", feature: "stock_count", page: stockcontrol.renderAdjustments, section: "Inventory" },
    { path: "transfers", label: "Transfers", icon: "⇄", permission: "inventory.read", feature: "multi_location", page: transfers.renderList, section: "Inventory" },
    { path: "transfers/:id", parent: "transfers", permission: "inventory.read", feature: "multi_location", page: transfers.renderDetail },
    { path: "movements", label: "Stock Movements", icon: "↔", permission: "inventory.read", page: stock.renderMovements, section: "Inventory" },
    { path: "reconciliation", label: "Reconciliation", icon: "⚖", permission: "inventory.read", page: inventory.renderReconciliation, section: "Inventory" },

    { path: "reorder", label: "Reorder", icon: "🛒", permission: "analytics.read", page: purchasing.renderReorder, section: "Purchasing" },
    { path: "purchasing", label: "Purchase Orders", icon: "📋", permission: "inventory.read", page: purchasing.renderList, section: "Purchasing" },
    { path: "purchasing/:id", parent: "purchasing", permission: "inventory.read", page: purchasing.renderDetail },
    { path: "suppliers", label: "Suppliers", icon: "🚚", permission: "inventory.read", page: suppliers.renderList, section: "Purchasing" },
    { path: "suppliers/:id", parent: "suppliers", permission: "inventory.read", page: suppliers.renderDetail },

    { path: "analytics", label: "Analytics", icon: "📊", permission: "analytics.read", page: analytics.render, section: "Insights" },
    { path: "reports", label: "Reports", icon: "📄", permission: "analytics.read", page: reports.render, section: "Insights" },

    { path: "onboarding", label: "Set-up Guide", icon: "🚀", permission: "settings.manage", page: saas.renderOnboarding, section: "Administration", onlyIncomplete: true },
    { path: "users", label: "Users & Roles", icon: "👥", permission: "users.manage", page: admin.renderUsers, section: "Administration" },
    { path: "settings", label: "Settings & Locations", icon: "⚙", permission: "inventory.read", page: admin.renderSettings, section: "Administration" },
    { path: "messaging", label: "Messaging", icon: "💬", permission: "communications.manage", page: saas.renderMessaging, section: "Administration" },
    { path: "delivery", label: "E-mail & Schedules", icon: "✉", permission: "settings.manage", page: organization.renderDelivery, section: "Administration" },
    { path: "billing", label: "Plan & Billing", icon: "💳", permission: "billing.manage", page: saas.renderBilling, section: "Administration" },
    { path: "organization", label: "Organization", icon: "🏥", permission: "inventory.read", page: organization.renderOrganization, section: "Administration" },
    { path: "audit", label: "Audit Trail", icon: "🧾", permission: "audit.read", page: admin.renderAudit, section: "Administration" },

    { path: "platform", label: "MedCart Console", icon: "🌐", permission: null, platform: true, page: platform.render, section: "MedCart Tech" },
    { path: "account", parent: "account", permission: null, page: admin.renderAccount },
];


const state = { user: null, renderSeq: 0, challenge: null, onboardingComplete: true };

export function can(permission) {
    return !permission || Boolean(state.user?.permissions.includes(permission));
}

/** Plan feature of the organization (the API enforces it too). */
export function hasFeature(feature) {
    return !feature || Boolean(state.user?.organization?.features?.includes(feature));
}

function allowed(route) {
    return can(route.permission) && hasFeature(route.feature) && (!route.platform || state.user?.is_platform_admin);
}

function matchRoute(hash) {
    const [path, queryString = ""] = hash.replace(/^#\/?/, "").split("?");
    const query = Object.fromEntries(new URLSearchParams(queryString));
    const parts = path.split("/").filter(Boolean);
    for (const route of ROUTES) {
        const pattern = route.path.split("/");
        if (pattern.length !== parts.length) continue;
        const params = {};
        const ok = pattern.every((segment, i) => {
            if (segment.startsWith(":")) { params[segment.slice(1)] = decodeURIComponent(parts[i]); return true; }
            return segment === parts[i];
        });
        if (ok) return { route, params: { ...params, query } };
    }
    return null;
}

export function navigate(path) {
    if (location.hash === `#/${path}`) route();
    else location.hash = `#/${path}`;
}

function defaultPath() {
    if (can("analytics.read")) return "dashboard";
    if (can("stock.dispense")) return "dispense";
    return "medicines";
}

/* ---------- Layout ---------- */

function renderNav(activePath) {
    const visible = ROUTES.filter(r => r.label && allowed(r)
        && !(r.onlyIncomplete && state.onboardingComplete));
    // Hospitals call ward stock requests "requisitions".
    const labelOf = r => (r.path === "transfers" && state.user?.organization?.type === "HOSPITAL" ? "Requisitions" : r.label);
    const item = r => html`
        <a class="nav-item ${r.path === activePath ? "active" : ""}" href="#/${r.path}" title="${labelOf(r)}"
           ${r.path === activePath ? html`aria-current="page"` : ""}>
            <span aria-hidden="true">${r.icon}</span><b>${labelOf(r)}</b>
            ${r.path === "notifications" ? html`<em class="nav-count" id="nav-unread" hidden></em>` : ""}
        </a>`;
    const sections = [...new Set(visible.map(r => r.section))];
    const collapsed = store.get("nav-collapsed-sections", []);
    mount(document.getElementById("nav-main"), sections.map(section => html`
        <div class="nav-section ${collapsed.includes(section) && !visible.some(r => r.section === section && r.path === activePath) ? "collapsed" : ""}">
            <button type="button" class="nav-section-title" data-section="${section}" aria-expanded="${collapsed.includes(section) ? "false" : "true"}">${section}</button>
            <div class="nav-section-items">${visible.filter(r => r.section === section).map(item)}</div>
        </div>`));
    mount(document.getElementById("nav-admin"), "");
    document.querySelectorAll(".nav-section-title").forEach(button => button.addEventListener("click", () => {
        const list = new Set(store.get("nav-collapsed-sections", []));
        const section = button.dataset.section;
        if (list.has(section)) list.delete(section); else list.add(section);
        store.set("nav-collapsed-sections", [...list]);
        button.parentElement.classList.toggle("collapsed");
        button.setAttribute("aria-expanded", String(!list.has(section)));
    }));
}

function renderUserBox() {
    const user = state.user;
    mount(document.getElementById("user-box"), html`
        <span class="org-name">${user.organization?.name || ""}</span>
        <a class="user" href="#/account" title="My account">
            <div class="avatar" aria-hidden="true">${(user.full_name || user.username).slice(0, 1).toUpperCase()}</div>
            <div><strong>${user.full_name}</strong><small>${user.role_label}</small></div>
        </a>
        <button type="button" class="view-btn" id="logout-btn">Sign out</button>`);
    document.getElementById("logout-btn").addEventListener("click", logout);
    document.getElementById("brand-org").textContent = user.organization?.name || "Inventory Intelligence";
}

/* ---------- Responsive navigation ---------- */

function setDrawer(open) {
    document.body.classList.toggle("drawer-open", open);
    document.getElementById("drawer-backdrop").hidden = !open;
    document.getElementById("menu-open").setAttribute("aria-expanded", String(open));
}

function setCollapsed(collapsed) {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    const button = document.getElementById("sidebar-collapse");
    button.textContent = collapsed ? "»" : "«";
    button.setAttribute("aria-label", collapsed ? "Expand menu" : "Collapse menu");
    button.title = button.getAttribute("aria-label");
    store.set("sidebar-collapsed", collapsed);
}

document.getElementById("menu-open").addEventListener("click", () => setDrawer(!document.body.classList.contains("drawer-open")));
document.getElementById("drawer-backdrop").addEventListener("click", () => setDrawer(false));
document.getElementById("sidebar-collapse").addEventListener("click", () => setCollapsed(!document.body.classList.contains("sidebar-collapsed")));
document.getElementById("sidebar").addEventListener("click", event => { if (event.target.closest("a")) setDrawer(false); });
document.addEventListener("keydown", event => { if (event.key === "Escape") setDrawer(false); });
setCollapsed(Boolean(store.get("sidebar-collapsed", false)));

async function refreshUnread() {
    if (!state.user || !can("notifications.read")) return;
    try {
        const { unread } = await api("/notifications/unread-count");
        ["nav-unread", "mobile-unread"].forEach(id => {
            const badge = document.getElementById(id);
            if (badge) {
                badge.hidden = unread === 0;
                badge.textContent = unread > 99 ? "99+" : String(unread);
            }
        });
    } catch { /* shown elsewhere */ }
}

async function checkHealth() {
    const box = document.getElementById("system-status");
    try {
        const response = await fetch("/health");
        const ok = response.ok;
        box.classList.toggle("offline", !ok);
        box.querySelector("small").textContent = ok ? "Database connected" : "Database unavailable";
    } catch {
        box.classList.add("offline");
        box.querySelector("small").textContent = "Server unreachable";
    }
}

/* ---------- Routing ---------- */

async function route() {
    if (location.hash.startsWith("#/reset-password")) { showReset(); return; }
    if (!state.user) return;
    const main = document.getElementById("main");
    const found = matchRoute(location.hash);

    if (!found) { navigate(defaultPath()); return; }
    const { route: current, params } = found;

    if ((state.user.must_change_password || state.user.mfa_setup_required) && current.path !== "account") {
        navigate("account");
        return;
    }

    renderNav(current.parent || current.path);
    refreshUnread();
    closeModal();

    if (!allowed(current)) {
        const reason = !hasFeature(current.feature)
            ? "This feature is not included in your organization's plan. See Plan & Billing."
            : "You do not have access to this page.";
        mount(main, html`<section class="section"><div class="loading">${reason}</div></section>`);
        return;
    }

    // Each render gets a fresh page element, so event listeners attached by
    // previously visited pages are discarded with their element.
    const seq = ++state.renderSeq;
    const pageElement = document.createElement("div");
    pageElement.className = "page";
    mount(pageElement, loadingBlock());
    main.replaceChildren(pageElement);
    const ctx = {
        main: pageElement,
        params,
        user: state.user,
        can,
        hasFeature,
        navigate,
        isCurrent: () => seq === state.renderSeq,
        refreshUnread,
        reload: () => route(),
    };
    try {
        await current.page(ctx);
    } catch (error) {
        if (seq === state.renderSeq && error.status !== 401) mount(pageElement, errorBlock(error));
    }
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
}

/* ---------- Session ---------- */

function showPanel(id) {
    ["login-form", "mfa-form", "forgot-form", "reset-form"].forEach(form => { document.getElementById(form).hidden = form !== id; });
}

function showLogin(message = "") {
    state.user = null;
    document.getElementById("app").hidden = true;
    const login = document.getElementById("login");
    login.hidden = false;
    showPanel("login-form");
    const error = document.getElementById("login-error");
    error.textContent = message;
    error.hidden = !message;
    document.getElementById("login-username").focus();
}

function resetToken() {
    const query = location.hash.split("?")[1] || "";
    return new URLSearchParams(query).get("token");
}

function showReset() {
    document.getElementById("app").hidden = true;
    document.getElementById("login").hidden = false;
    showPanel("reset-form");
    document.getElementById("reset-password").focus();
}

document.getElementById("show-forgot").addEventListener("click", () => {
    showPanel("forgot-form");
    document.getElementById("forgot-message").hidden = true;
    document.getElementById("forgot-username").value = document.getElementById("login-username").value;
    document.getElementById("forgot-username").focus();
});
document.querySelectorAll("[data-back-to-login]").forEach(button => button.addEventListener("click", () => {
    if (location.hash.startsWith("#/reset-password")) location.hash = "";
    showLogin();
}));
document.getElementById("forgot-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.target;
    const message = document.getElementById("forgot-message");
    try {
        const result = await api("/auth/forgot-password", { method: "POST", body: { username: form.username.value.trim() } });
        message.textContent = result.message;
    } catch (error) {
        message.textContent = error.message;
    }
    message.hidden = false;
});
document.getElementById("reset-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.target;
    const error = document.getElementById("reset-error");
    error.hidden = true;
    if (form.password.value !== form.repeat.value) {
        error.textContent = "The passwords do not match.";
        error.hidden = false;
        return;
    }
    try {
        const result = await api("/auth/reset-password", { method: "POST", body: { token: resetToken() || "", new_password: form.password.value } });
        form.reset();
        location.hash = "";
        showLogin(result.message);
    } catch (problem) {
        error.textContent = problem.message;
        error.hidden = false;
    }
});

async function startApp(user) {
    state.user = user;
    document.getElementById("login").hidden = true;
    document.getElementById("app").hidden = false;
    try {
        const settings = await api("/settings");
        setCurrency(settings.find(s => s.key === "currency.symbol")?.value);
    } catch { /* default currency */ }
    try {
        state.onboardingComplete = Boolean((await api("/onboarding")).completed_at);
    } catch { state.onboardingComplete = true; }
    renderUserBox();
    checkHealth();
    refreshUnread();
    const start = !state.onboardingComplete && can("settings.manage") ? "onboarding" : defaultPath();
    if (!location.hash || location.hash === "#/" || location.hash === "#") location.hash = `#/${start}`;
    else route();
}

async function logout() {
    try { await api("/auth/logout", { method: "POST" }); } catch { /* already signed out */ }
    location.hash = "";
    showLogin("You have signed out.");
}

document.getElementById("login-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector("button");
    button.disabled = true;
    try {
        const result = await api("/auth/login", {
            method: "POST",
            body: { username: form.username.value.trim(), password: form.password.value, client_name: "Web browser" },
        });
        form.password.value = "";
        if (result.mfa_required) {
            state.challenge = result.challenge_token;
            showPanel("mfa-form");
            document.getElementById("mfa-error").hidden = true;
            document.getElementById("mfa-login-code").value = "";
            document.getElementById("mfa-login-code").focus();
            return;
        }
        await signedIn(result.user);
    } catch (error) {
        showLogin(error.message);
    } finally {
        button.disabled = false;
    }
});

async function signedIn(user) {
    await startApp(user);
    if (user.must_change_password) toast("Please choose a new password before continuing.", "warning");
    else if (user.mfa_setup_required) toast("Your organization requires two-step verification: set it up to continue.", "warning");
}

document.getElementById("mfa-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector("button[type=submit]");
    const error = document.getElementById("mfa-error");
    error.hidden = true;
    button.disabled = true;
    try {
        const result = await api("/auth/mfa/verify", { method: "POST",
            body: { challenge_token: state.challenge || "", code: form.code.value.trim() } });
        state.challenge = null;
        form.reset();
        await signedIn(result.user);
    } catch (problem) {
        error.textContent = problem.message;
        error.hidden = false;
        if (problem.status === 401 && /expired/i.test(problem.message)) setTimeout(() => showLogin(problem.message), 1500);
    } finally {
        button.disabled = false;
    }
});

/* ---------- Global search ---------- */

const SEARCH_GROUPS = {
    medicines: ["Medicines", r => [`#/medicines/${r.id}`, `${r.name} ${r.strength || ""}`, r.dosage_form || ""]],
    batches: ["Batches", r => [`#/batches/${r.id}`, r.batch_number, `${r.medicine} · ${r.location} · qty ${r.quantity}`]],
    suppliers: ["Suppliers", r => [`#/suppliers/${r.id}`, r.name, r.phone || ""]],
    purchase_orders: ["Purchase orders", r => [`#/purchasing/${r.id}`, r.order_number, `${r.supplier} · ${r.status}`]],
    dispensations: ["Sales", r => [`#/dispensations/${r.id}`, r.dispensation_number, r.patient_name || ""]],
    stock_counts: ["Stock counts", r => [`#/stock-counts/${r.id}`, r.count_number, r.name || r.status]],
    transfers: ["Transfers", r => [`#/transfers/${r.id}`, r.transfer_number, r.status]],
    users: ["Users", () => ["#/users", "", ""]],
};

function setupSearch() {
    const form = document.getElementById("global-search");
    const input = document.getElementById("global-search-input");
    const box = document.getElementById("global-search-results");
    let seq = 0;
    const close = () => { box.hidden = true; input.setAttribute("aria-expanded", "false"); };
    const run = debounce(async () => {
        const q = input.value.trim();
        if (q.length < 2) { close(); return; }
        const mine = ++seq;
        try {
            const data = await api("/search", { params: { q } });
            if (mine !== seq) return;
            const groups = Object.entries(data.results).filter(([, rows]) => rows.length);
            mount(box, groups.length ? groups.map(([key, rows]) => {
                const [title, fn] = SEARCH_GROUPS[key];
                return html`<div class="search-group"><h4>${title}</h4>${rows.map(r => {
                    const [href, main, sub] = key === "users" ? ["#/users", r.full_name, `${r.username} · ${r.role}`] : fn(r);
                    return html`<a href="${href}" class="search-hit"><strong>${main}</strong><small>${sub}</small></a>`;
                })}</div>`;
            }) : html`<p class="search-empty">No results for “${q}”.</p>`);
            box.hidden = false;
            input.setAttribute("aria-expanded", "true");
        } catch { close(); }
    }, 200);
    input.addEventListener("input", run);
    form.addEventListener("submit", event => { event.preventDefault(); box.querySelector("a")?.click(); });
    box.addEventListener("click", event => { if (event.target.closest("a")) { close(); input.value = ""; } });
    document.addEventListener("click", event => { if (!form.contains(event.target)) close(); });
    document.addEventListener("keydown", event => {
        if (event.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) { event.preventDefault(); input.focus(); }
        if (event.key === "Escape") close();
    });
}
setupSearch();

onUnauthorized(() => showLogin("Your session has ended. Please sign in again."));
onStepUp(() => new Promise(resolve => {
    let done = false;
    formModal({
        title: "Confirm it's you", submitLabel: "Confirm",
        intro: "This is a high-risk action. Enter the current code from your authenticator app.",
        fields: [{ name: "code", label: "Authenticator code", required: true, autocomplete: "one-time-code" }],
        onSubmit: async v => {
            await api("/auth/mfa/step-up", { method: "POST", body: { code: v.code }, noStepUp: true });
            done = true;
            resolve(true);
        },
    });
    // Resolve false when the dialog is closed without confirming.
    const observer = new MutationObserver(() => {
        if (!document.querySelector("#modal-root .modal")) { observer.disconnect(); if (!done) resolve(false); }
    });
    observer.observe(document.getElementById("modal-root"), { childList: true, subtree: true });
}));
window.addEventListener("hashchange", route);

export function userUpdated(user) {
    state.user = user;
    renderUserBox();
}

export function onboardingCompleted() {
    state.onboardingComplete = true;
}

// Periodic refresh of the notification badge and system status.
setInterval(() => { if (state.user) { refreshUnread(); checkHealth(); } }, 60000);

// Installable web app: cache the application shell for fast start-up.
// API responses are never cached (they are private and must be current).
if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => { /* optional */ });
}

(async function init() {
    if (location.hash.startsWith("#/reset-password")) { showReset(); return; }
    try {
        const user = await fetch("/auth/me", { credentials: "same-origin" });
        if (user.ok) { await startApp(await user.json()); return; }
    } catch { /* offline */ }
    showLogin();
})();

