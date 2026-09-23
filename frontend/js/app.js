/* =========================================================
   PharmaStock application shell
   One router (URL hash), one navigation, one session state.
========================================================= */

import {
    api, closeModal, errorBlock, html, loadingBlock, mount, onUnauthorized, setCurrency, toast,
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

/* ---------- Routes (single source of truth for navigation) ---------- */

const ROUTES = [
    { path: "dashboard", label: "Dashboard", icon: "▣", permission: "analytics.read", page: dashboard.render },
    { path: "medicines", label: "Medicines", icon: "💊", permission: "inventory.read", page: medicines.renderList },
    { path: "medicines/:id", parent: "medicines", permission: "inventory.read", page: medicines.renderDetail },
    { path: "inventory", label: "Inventory", icon: "📦", permission: "inventory.read", page: inventory.renderInventory },
    { path: "batches/:id", parent: "inventory", permission: "inventory.read", page: inventory.renderBatch },
    { path: "expiry", label: "Expiry Alerts", icon: "⚠", permission: "inventory.read", page: inventory.renderExpiry },
    { path: "dispense", label: "Dispensing Counter", icon: "➜", permission: "stock.dispense", page: dispensing.renderCounter },
    { path: "dispensations", label: "Dispensing History", icon: "🧾", permission: "inventory.read", page: dispensing.renderHistory },
    { path: "dispensations/:id", parent: "dispensations", permission: "inventory.read", page: dispensing.renderDetail },
    { path: "movements", label: "Stock Movements", icon: "↔", permission: "inventory.read", page: stock.renderMovements },
    { path: "purchasing", label: "Purchasing", icon: "🛒", permission: "inventory.read", page: purchasing.renderList },
    { path: "purchasing/:id", parent: "purchasing", permission: "inventory.read", page: purchasing.renderDetail },
    { path: "suppliers", label: "Suppliers", icon: "🚚", permission: "inventory.read", page: suppliers.renderList },
    { path: "suppliers/:id", parent: "suppliers", permission: "inventory.read", page: suppliers.renderDetail },
    { path: "analytics", label: "Analytics", icon: "📊", permission: "analytics.read", page: analytics.render },
    { path: "reports", label: "Reports", icon: "📄", permission: "analytics.read", page: reports.render },
    { path: "assistant", label: "AI Assistant", icon: "✦", permission: "assistant.use", page: assistant.render },
    { path: "notifications", label: "Notifications", icon: "🔔", permission: "notifications.read", page: notifications.render },
    { path: "audit", label: "Audit Trail", icon: "🧾", permission: "audit.read", page: admin.renderAudit, group: "admin" },
    { path: "users", label: "Users", icon: "👥", permission: "users.manage", page: admin.renderUsers, group: "admin" },
    { path: "settings", label: "Settings", icon: "⚙", permission: "inventory.read", page: admin.renderSettings, group: "admin" },
    { path: "account", parent: "account", permission: null, page: admin.renderAccount },
];

const state = { user: null, renderSeq: 0 };

export function can(permission) {
    return !permission || Boolean(state.user?.permissions.includes(permission));
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
    return can("analytics.read") ? "dashboard" : "medicines";
}

/* ---------- Layout ---------- */

function renderNav(activePath) {
    const main = ROUTES.filter(r => r.label && !r.group && can(r.permission));
    const adminRoutes = ROUTES.filter(r => r.label && r.group === "admin" && can(r.permission));
    const item = r => html`
        <a class="nav-item ${r.path === activePath ? "active" : ""}" href="#/${r.path}"
           ${r.path === activePath ? html`aria-current="page"` : ""}>
            <span aria-hidden="true">${r.icon}</span>${r.label}
            ${r.path === "notifications" ? html`<em class="nav-count" id="nav-unread" hidden></em>` : ""}
        </a>`;
    mount(document.getElementById("nav-main"), main.map(item));
    mount(document.getElementById("nav-admin"), adminRoutes.map(item));
}

function renderUserBox() {
    const user = state.user;
    mount(document.getElementById("user-box"), html`
        <a class="user" href="#/account" title="My account">
            <div class="avatar" aria-hidden="true">${(user.full_name || user.username).slice(0, 1).toUpperCase()}</div>
            <div><strong>${user.full_name}</strong><small>${user.role_label}</small></div>
        </a>
        <button type="button" class="view-btn" id="logout-btn">Sign out</button>`);
    document.getElementById("logout-btn").addEventListener("click", logout);
}

async function refreshUnread() {
    if (!state.user || !can("notifications.read")) return;
    try {
        const { unread } = await api("/notifications/unread-count");
        const badge = document.getElementById("nav-unread");
        if (badge) {
            badge.hidden = unread === 0;
            badge.textContent = unread > 99 ? "99+" : String(unread);
        }
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
    if (!state.user) return;
    const main = document.getElementById("main");
    const found = matchRoute(location.hash);

    if (!found) { navigate(defaultPath()); return; }
    const { route: current, params } = found;

    if (state.user.must_change_password && current.path !== "account") { navigate("account"); return; }

    renderNav(current.parent || current.path);
    refreshUnread();
    closeModal();

    if (!can(current.permission)) {
        mount(main, html`<section class="section"><div class="loading">You do not have access to this page.</div></section>`);
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

function showLogin(message = "") {
    state.user = null;
    document.getElementById("app").hidden = true;
    const login = document.getElementById("login");
    login.hidden = false;
    const error = document.getElementById("login-error");
    error.textContent = message;
    error.hidden = !message;
    document.getElementById("login-username").focus();
}

async function startApp(user) {
    state.user = user;
    document.getElementById("login").hidden = true;
    document.getElementById("app").hidden = false;
    try {
        const settings = await api("/settings");
        setCurrency(settings.find(s => s.key === "currency.symbol")?.value);
    } catch { /* default currency */ }
    renderUserBox();
    checkHealth();
    refreshUnread();
    if (!location.hash || location.hash === "#/" || location.hash === "#") location.hash = `#/${defaultPath()}`;
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
            body: { username: form.username.value.trim(), password: form.password.value },
        });
        form.password.value = "";
        await startApp(result.user);
        if (result.user.must_change_password) toast("Please choose a new password before continuing.", "warning");
    } catch (error) {
        showLogin(error.message);
    } finally {
        button.disabled = false;
    }
});

onUnauthorized(() => showLogin("Your session has ended. Please sign in again."));
window.addEventListener("hashchange", route);

export function userUpdated(user) {
    state.user = user;
    renderUserBox();
}

// Periodic refresh of the notification badge and system status.
setInterval(() => { if (state.user) { refreshUnread(); checkHealth(); } }, 60000);

(async function init() {
    try {
        const user = await fetch("/auth/me", { credentials: "same-origin" });
        if (user.ok) { await startApp(await user.json()); return; }
    } catch { /* offline */ }
    showLogin();
})();

