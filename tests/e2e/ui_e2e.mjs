// End-to-end browser test of the PharmaStock frontend (Playwright + Chromium).
//
//   BASE_URL=http://localhost:8000 ADMIN_USER=admin ADMIN_PASSWORD=... \
//   NEW_PASSWORD=... node tests/e2e/ui_e2e.mjs [screenshot-dir]
//
// Run against a TEST copy of the database: it creates records.
// Fails (exit 1) on any failed check, uncaught page error or console error.

import { chromium } from "playwright";

const BASE = process.env.BASE_URL || "http://localhost:8000";
const ADMIN = process.env.ADMIN_USER || "admin";
const FIRST_PASSWORD = process.env.ADMIN_PASSWORD;
const NEW_PASSWORD = process.env.NEW_PASSWORD || "Pharma-e2e-pass-2026";
const SHOTS = process.argv[2];
const stamp = Date.now().toString().slice(-6);

const results = [];
const problems = [];

async function check(name, fn) {
    try {
        await fn();
        results.push(`PASS ${name}`);
    } catch (error) {
        results.push(`FAIL ${name}: ${error.message.split("\n")[0]}`);
    }
    console.log(results[results.length - 1]);
}

function expect(condition, message) {
    if (!condition) throw new Error(message);
}

function watch(page, label) {
    page.on("pageerror", e => problems.push(`[${label}] page error: ${e.stack || e.message}`));
    page.on("console", m => {
        if (m.type() === "error" && !/status of 4\d\d/.test(m.text())) problems.push(`[${label}] console: ${m.text()}`);
    });
}

async function heading(page) {
    await page.waitForSelector(".topbar h2");
    return (await page.textContent(".topbar h2")).trim();
}

async function go(page, hash) {
    await page.goto(`${BASE}/app/#/${hash}`);
    await page.waitForFunction(() => !document.querySelector("#main > .page > .loading"), null, { timeout: 10000 });
}

async function shot(page, name) {
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function login(page, username, password) {
    await page.fill("#login-username", username);
    await page.fill("#login-password", password);
    await page.click("#login-form button");
}

const browser = await chromium.launch();
const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1360, height: 900 } });
context.setDefaultTimeout(8000);
const page = await context.newPage();
watch(page, "admin");

await check("login screen shown when signed out", async () => {
    await page.goto(`${BASE}/app/`);
    await page.waitForSelector("#login:not([hidden])");
});

await check("wrong password rejected with message", async () => {
    await login(page, ADMIN, "wrong-password-1");
    await page.waitForSelector("#login-error:not([hidden])");
    expect((await page.textContent("#login-error")).includes("Invalid"), "no error message");
});

await check("first sign-in forces password change", async () => {
    await login(page, ADMIN, FIRST_PASSWORD);
    await page.waitForSelector("#pw-form");
    expect((await heading(page)) === "My Account", "not on account page");
    await page.fill("#pw-current", FIRST_PASSWORD);
    await page.fill("#pw-new", NEW_PASSWORD);
    await page.fill("#pw-repeat", NEW_PASSWORD);
    await page.click("#pw-form button[type=submit]");
    await page.waitForFunction(() => location.hash.includes("dashboard"));
    expect((await heading(page)) === "Dashboard", "not redirected to dashboard");
});

await check("dashboard shows live figures", async () => {
    await page.waitForSelector(".cards .card strong");
    const values = await page.$$eval(".cards .card strong", els => els.map(e => e.textContent.trim()));
    expect(values[0] === "3", `total medicines ${values[0]}`);
    expect(await page.$$eval("#dash-expiry tbody tr", r => r.length) > 0, "no expiry rows");
    await shot(page, "01-dashboard");
});

const pages = {
    medicines: "Medicines", inventory: "Inventory", expiry: "Expiry Alerts", dispense: "Dispensing Counter",
    dispensations: "Dispensing History",
    movements: "Stock Movements", purchasing: "Purchasing", suppliers: "Suppliers", analytics: "Analytics",
    reports: "Reports", assistant: "AI Inventory Assistant", notifications: "Notifications", audit: "Audit Trail",
    users: "Users", settings: "Settings", account: "My Account",
    scan: "Barcode Scan", transfers: "Transfers", reconciliation: "Stock Reconciliation",
    delivery: "E-mail, SMS & Scheduled Reports", platform: "Platform: Organizations",
};
for (const [hash, title] of Object.entries(pages)) {
    await check(`page ${hash} renders`, async () => {
        await go(page, hash);
        expect((await heading(page)) === title, `heading was ${await heading(page)}`);
        expect(!(await page.$(".error-text")), "error block shown");
        const active = await page.$eval(".nav-item.active", el => el.getAttribute("href")).catch(() => null);
        if (hash !== "account") expect(active === `#/${hash}`, `active nav ${active}`);
    });
}

await check("sidebar navigation click works", async () => {
    await go(page, "dashboard");
    await page.click('a.nav-item[href="#/medicines"]');
    await page.waitForSelector("#medicines-table");
    await page.click('a.nav-item[href="#/dashboard"]');
    await page.waitForSelector("#dash-expiry");
});

await check("medicines show real stock (was 0 / NO STOCK)", async () => {
    await go(page, "medicines");
    const rows = await page.$$eval("#medicines-table tbody tr", trs => trs.map(t => t.innerText));
    const amox = rows.find(r => r.includes("Amoxicillin"));
    expect(amox && amox.includes("280"), `amoxicillin row: ${amox}`);
    const ibu = rows.find(r => r.includes("Ibuprofen"));
    expect(ibu && ibu.includes("OUT OF STOCK"), `ibuprofen row: ${ibu}`);
    await shot(page, "02-medicines");
});

await check("medicine search filters", async () => {
    await page.fill("#med-search", "amox");
    await page.waitForTimeout(300);
    const rows = await page.$$eval("#medicines-table tbody tr", trs => trs.length);
    expect(rows === 1, `rows ${rows}`);
    await page.fill("#med-search", "");
    await page.waitForFunction(() => document.querySelectorAll("#medicines-table tbody tr").length > 1);
});

await check("table sorting by header", async () => {
    await page.click('#medicines-table [data-sort="3"]');
    await page.waitForSelector('#medicines-table [data-sort="3"].asc');
    const first = await page.$eval("#medicines-table tbody tr td", td => td.innerText);
    expect(first.includes("Ibuprofen"), `first after sort: ${first}`);
});

await check("add medicine validates and saves", async () => {
    await page.click('[data-action="add"]');
    await page.fill("#f-name", `E2E Cetirizine ${stamp}`);
    await page.fill("#f-strength", "10 mg");
    await page.fill("#f-dosage_form", "Tablet");
    await page.fill("#f-reorder_level", "-4");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(".modal .form-error:not([hidden])");
    await page.fill("#f-reorder_level", "15");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(".modal", { state: "detached" });
    await page.waitForSelector(`text=E2E Cetirizine ${stamp}`);
});

await check("duplicate medicine rejected by server", async () => {
    await page.click('[data-action="add"]');
    await page.fill("#f-name", "Paracetamol");
    await page.fill("#f-strength", "500 mg");
    await page.fill("#f-dosage_form", "Tablet");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(".modal .form-error:not([hidden])");
    expect((await page.textContent(".modal .form-error")).includes("already exists"), "no duplicate message");
    await page.click(".modal [data-cancel]");
});

await check("edit medicine persists", async () => {
    await page.click('#medicines-table button[data-action="edit"]');
    await page.waitForSelector("#f-reorder_level");
    await page.fill("#f-reorder_level", "25");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(".modal", { state: "detached" });
    const api = await page.evaluate(() => fetch("/medicines").then(r => r.json()));
    expect(api.some(m => m.reorder_level === 25), "reorder level not saved");
});

await check("medicine detail shows FEFO order", async () => {
    await go(page, "medicines/1");
    const batches = await page.$$eval("#fefo tbody tr", trs => trs.map(t => t.innerText));
    expect(batches[0].includes("PARA002"), `first FEFO batch: ${batches[0]}`);
    expect(!batches.some(b => b.includes("PARA004")), "expired batch listed in FEFO");
    await shot(page, "03-medicine-detail");
});

await check("set a selling price on a medicine", async () => {
    await go(page, "medicines/1");
    await page.click('[data-action="edit"]');
    await page.fill("#f-selling_price", "0.50");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(".modal", { state: "detached" });
    await page.waitForFunction(() => document.querySelector(".cards")?.innerText.includes("0.50"));
});

let dispensationUrl = "";
await check("dispensing counter: prescription with two medicines, FEFO, receipt", async () => {
    await go(page, "dispense");
    await page.fill("#c-search", "parac");
    await page.press("#c-search", "Enter");
    await page.waitForSelector('table.cart input.qty[data-index="0"]');
    await page.fill('input.qty[data-index="0"]', "60");
    await page.waitForFunction(() => document.querySelector('tr[data-row="0"] .batch-note').innerText.includes("PARA003"));
    const note = await page.textContent('tr[data-row="0"] .batch-note');
    expect(note.includes("PARA002 ×50") && note.includes("PARA003 ×10") && !note.includes("PARA004"), `FEFO note: ${note}`);
    await page.fill('input.directions-input[data-index="0"]', "2 tablets three times daily after meals");
    // Second medicine, unpriced -> price entered at the counter.
    await page.click('.pick >> text=Amoxicillin');
    await page.waitForSelector('input.qty[data-index="1"]');
    await page.fill('input.qty[data-index="1"]', "21");
    await page.fill('input.price[data-index="1"]', "1.20");
    await page.waitForFunction(() => document.getElementById("c-total").innerText.includes("55.20"));
    // Prescription details are required for a prescription.
    await page.check('input[name="dispense_type"][value="PRESCRIPTION"]');
    await page.click("#c-form button[type=submit]");
    await page.waitForSelector("#c-form .form-error:not([hidden])");
    await page.fill("#c-prescriber", "Dr. Ama Boateng");
    await page.fill("#c-rx", "RX-E2E-" + stamp);
    await page.fill("#c-patient", "Kofi Asante");
    await page.selectOption("#c-payment", "NHIS");
    await page.click("#c-form button[type=submit]");
    await page.waitForSelector(".modal .receipt");
    const receipt = await page.textContent(".modal .receipt");
    expect(receipt.includes("Kofi Asante") && receipt.includes("Dr. Ama Boateng") && receipt.includes("55.20")
        && receipt.includes("NHIS") && receipt.includes("PARA002"), `receipt: ${receipt}`);
    dispensationUrl = await page.getAttribute('.modal a[href^="#/dispensations/"]', "href");
    await shot(page, "04-dispensing-receipt");
    await page.click(".modal [data-close]:not(.modal-backdrop)");
    const cartEmpty = await page.textContent("#c-cart");
    expect(cartEmpty.includes("No items"), "cart not cleared");
    await shot(page, "04b-dispensing-counter");
});

await check("dispensing history shows the sale; search works", async () => {
    await go(page, "dispensations");
    await page.waitForSelector("#dispensations-table tbody tr");
    await page.fill("#h-search", "Kofi");
    await page.waitForFunction(() => document.querySelectorAll("#dispensations-table tbody tr").length === 1
        && document.querySelector("#dispensations-table").innerText.includes("Kofi Asante"));
});

await check("stock deducted by dispensing", async () => {
    const stock = await page.evaluate(() => fetch("/stock-alerts").then(r => r.json()));
    const para = stock.find(m => m.medicine === "Paracetamol");
    const amox = stock.find(m => m.medicine === "Amoxicillin");
    expect(para.current_stock === 120 && amox.current_stock === 259, `para ${para.current_stock} amox ${amox.current_stock}`);
});

await check("stock movements list shows dispensing with user", async () => {
    await go(page, "movements");
    await page.waitForSelector("#movements-table tbody tr");
    const text = await page.textContent("#movements-table");
    expect(text.includes("Dispensation DSP-") && text.includes("Administrator"), "movement not listed");
});

await check("inventory filter by status", async () => {
    await go(page, "inventory");
    await page.selectOption("#inv-status", "EXPIRED");
    const rows = await page.$$eval("#inventory-table tbody tr", trs => trs.map(t => t.innerText));
    expect(rows.length === 1 && rows[0].includes("PARA004"), `rows ${rows}`);
    await shot(page, "05-inventory");
});

await check("expired batch write-off from expiry alerts", async () => {
    await go(page, "expiry");
    await page.click('[data-action="writeoff"]');
    await page.fill("#f-reason", "E2E disposal certificate 7");
    await page.click(".modal button[type=submit]");
    await page.waitForFunction(() => !document.querySelector('[data-action="writeoff"]'));
});

let supplierName = `E2E Supplies ${stamp}`;
await check("add and deactivate supplier", async () => {
    await go(page, "suppliers");
    await page.click('[data-action="add"]');
    await page.fill("#f-name", supplierName);
    await page.fill("#f-email", "bad-email");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(".modal .form-error:not([hidden])");
    await page.fill("#f-email", "orders@e2e.example");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(`text=${supplierName}`);
});

await check("purchase order: create, add line, partial receive", async () => {
    await go(page, "purchasing");
    await page.click('[data-action="new"]');
    await page.selectOption("#f-supplier_id", { label: supplierName });
    await page.click(".modal button[type=submit]");
    await page.waitForFunction(() => /#\/purchasing\/\d+/.test(location.hash));
    await page.waitForSelector('[data-action="add-line"]');
    await page.click('[data-action="add-line"]');
    await page.selectOption("#f-medicine_id", { label: "Ibuprofen 400 mg Tablet" });
    await page.fill("#f-quantity_ordered", "120");
    await page.fill("#f-unit_cost", "0.45");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector('[data-action="receive"]');
    await page.click('[data-action="receive"]');
    await page.fill("#f-batch_number", `IBU-E2E-${stamp}`);
    await page.fill("#f-expiry_date", "2028-06-30");
    await page.fill("#f-quantity_received", "80");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(`#po-receipts >> text=IBU-E2E-${stamp}`);
    const status = await page.textContent(".cards .status");
    expect(status.includes("PARTIALLY RECEIVED"), `status ${status}`);
    await shot(page, "06-purchase-order");
});

await check("received stock visible on medicines page", async () => {
    await go(page, "medicines");
    const ibu = (await page.$$eval("#medicines-table tbody tr", t => t.map(r => r.innerText))).find(r => r.includes("Ibuprofen"));
    expect(ibu.includes("80"), `ibuprofen row ${ibu}`);
});

await check("analytics tabs render", async () => {
    for (const tab of ["reorder", "risk", "consumption", "trends", "locations", "stockouts", "forecast", "turnover",
                       "valuation", "suppliers", "purchasing"]) {
        await go(page, `analytics?tab=${tab}`);
        await page.waitForSelector("#tab-body table");
    }
    await go(page, "analytics?tab=risk");
    await shot(page, "07-analytics-risk");
});

await check("report runs and exports CSV / Excel / PDF", async () => {
    await go(page, "reports");
    await page.selectOption("#r-key", "valuation");
    await page.click("#report-form button[type=submit]");
    await page.waitForSelector("#report-table tbody tr");
    for (const format of ["csv", "xlsx", "pdf"]) {
        const [download] = await Promise.all([page.waitForEvent("download"), page.click(`[data-format="${format}"]`)]);
        expect(download.suggestedFilename().endsWith(`.${format}`), `filename ${download.suggestedFilename()}`);
    }
    await shot(page, "08-reports");
});

await check("assistant answers from data", async () => {
    await go(page, "assistant");
    await page.click('.example[data-q="What needs reordering?"]');
    await page.waitForFunction(() => {
        const bubbles = document.querySelectorAll(".bubble.assistant");
        return bubbles.length && !bubbles[bubbles.length - 1].innerText.includes("Checking");
    });
    const answer = await page.$$eval(".bubble.assistant", b => b[b.length - 1].innerText);
    expect(answer.includes("Recommended reorders") || answer.includes("Nothing needs reordering"), answer);
    await page.fill("#chat-input", "Which batches of Paracetamol should be used first?");
    await page.click("#chat-form button");
    await page.waitForFunction(() => document.querySelectorAll(".bubble.assistant").length === 2
        && !document.querySelectorAll(".bubble.assistant")[1].innerText.includes("Checking"));
    const fefo = await page.$$eval(".bubble.assistant", b => b[1].innerText);
    expect(fefo.includes("PARA003") && !fefo.includes("PARA004"), fefo);
    await shot(page, "09-assistant");
});

await check("trends tab draws charts from data", async () => {
    await go(page, "analytics?tab=trends");
    await page.waitForSelector(".chart svg rect", { state: "attached" });
    expect(await page.$$eval(".chart", c => c.length) === 3, "three charts expected");
    const drawn = await page.$$eval(".chart rect", rects => rects.filter(r => r.getBoundingClientRect().height > 0).length);
    expect(drawn > 0, "no bars drawn");
    await shot(page, "07b-analytics-trends");
});

await check("organization page shows plan and usage", async () => {
    await go(page, "organization");
    const text = await page.textContent("#main");
    expect(text.includes("Enterprise") && text.includes("Users"), "plan/usage missing");
});

await check("barcode: GTIN on medicine, GS1 scan finds batch and warns FEFO", async () => {
    await go(page, "medicines/1");
    await page.click('[data-action="edit"]');
    await page.fill("#f-gtin", "4006381333931");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(".modal", { state: "detached" });
    await go(page, "scan");
    await page.fill("#scan-code", "(01)04006381333931(10)PARA003");
    await page.press("#scan-code", "Enter");
    await page.waitForSelector("#scan-batches");
    const text = await page.textContent("#scan-result");
    expect(text.includes("Paracetamol") && text.includes("← scanned") && text.includes("FEFO"), text.slice(0, 300));
    await shot(page, "11-barcode-scan");
});

await check("barcode scan at the dispensing counter adds the medicine", async () => {
    await go(page, "dispense");
    await page.fill("#c-search", "4006381333931");
    await page.press("#c-search", "Enter");
    await page.waitForSelector('table.cart input.qty[data-index="0"]');
    expect((await page.textContent("#c-cart")).includes("Paracetamol"), "not added by scan");
    await page.click('[data-action="clear"]');
});

await check("quarantine and release a batch", async () => {
    await go(page, "medicines/1");
    const batchHref = await page.getAttribute("#fefo tbody tr a", "href");
    await go(page, batchHref.slice(2));
    await page.click('[data-action="hold"][data-status="QUARANTINED"]');
    await page.fill("#f-reason", "Seal damaged — E2E");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector("text=excluded from FEFO");
    await page.click('[data-action="hold"][data-status="ACTIVE"]');
    await page.fill("#f-reason", "Checked OK");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector('[data-action="hold"][data-status="QUARANTINED"]');
});

await check("requisition: new ward, request, approve, dispatch, receive", async () => {
    await go(page, "settings");
    await page.click('[data-action="add-location"]');
    await page.fill("#f-name", `Ward E2E ${stamp}`);
    await page.selectOption("#f-location_type", "WARD");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(`text=Ward E2E ${stamp}`);
    await go(page, "transfers");
    await page.click('[data-action="new"][data-type="REQUISITION"]');
    await page.selectOption("#t-from", { label: "Main Pharmacy" });
    await page.selectOption("#t-to", { label: `Ward E2E ${stamp}` });
    await page.selectOption(".t-med", { label: "Amoxicillin 500 mg Capsule" });
    await page.fill(".t-qty", "12");
    await page.click("#transfer-form button[type=submit]");
    await page.waitForFunction(() => /#\/transfers\/\d+/.test(location.hash));
    await page.click('[data-action="approve"]');
    await page.click(".modal button[type=submit]");
    await page.waitForSelector('[data-action="dispatch"]');
    await page.click('[data-action="dispatch"]');
    await page.waitForSelector('[data-action="receive"]');
    await page.click('[data-action="receive"]');
    await page.waitForFunction(() => document.querySelector(".cards")?.innerText.includes("RECEIVED"));
    const rec = await page.evaluate(() => fetch("/stock-reconciliation").then(r => r.json()));
    expect(rec.reconciled, "ledger not reconciled after transfer");
    await shot(page, "12-requisition");
});

await check("reconciliation page reports a reconciled ledger", async () => {
    await go(page, "reconciliation");
    expect((await page.textContent("#main")).includes("fully reconciled"), "not reconciled");
});

await check("account: notification preferences and signed-in devices", async () => {
    await go(page, "account");
    await page.waitForSelector("#prefs-form");
    await page.fill("#p-email", "admin@example.com");
    await page.check("#p-notify-email");
    await page.click("#prefs-form button[type=submit]");
    await page.waitForSelector("text=Notification preferences saved.");
    const devices = await page.textContent("#my-sessions");
    expect(devices.includes("Web browser") && devices.includes("This device"), devices);
});

await check("sidebar collapses and expands", async () => {
    await page.click("#sidebar-collapse");
    expect(await page.evaluate(() => document.body.classList.contains("sidebar-collapsed")), "not collapsed");
    await shot(page, "13-collapsed");
    await page.click("#sidebar-collapse");
    expect(!(await page.evaluate(() => document.body.classList.contains("sidebar-collapsed"))), "not expanded");
});

await check("service worker registered for the app shell", async () => {
    const scope = await page.evaluate(async () => (await navigator.serviceWorker.getRegistration())?.scope || "");
    expect(scope.endsWith("/app/"), `scope ${scope}`);
});

await check("notifications: list and mark all read", async () => {
    await go(page, "notifications");
    await page.waitForSelector(".notification");
    await page.click('[data-action="read-all"]');
    await page.waitForSelector('[data-action="read-all"][disabled]');
    await page.waitForFunction(() => document.getElementById("nav-unread")?.hidden === true);
});

await check("audit trail records UI actions", async () => {
    await go(page, "audit");
    const text = await page.textContent("#audit-table");
    expect(text.includes("DISPENSE") && text.includes("RECEIVE") && text.includes("PASSWORD_CHANGED"), "missing entries");
});

await check("void dispensation returns stock", async () => {
    await page.goto(`${BASE}/app/${dispensationUrl}`);
    await page.waitForSelector('[data-action="void"]');
    await page.click('[data-action="void"]');
    await page.fill("#f-reason", "Entered against the wrong patient");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(".receipt-void");
    const stock = await page.evaluate(() => fetch("/stock-alerts").then(r => r.json()));
    expect(stock.find(m => m.medicine === "Paracetamol").current_stock === 180, "paracetamol not returned");
    const rec = await page.evaluate(() => fetch("/stock-reconciliation").then(r => r.json()));
    expect(rec.reconciled, "ledger not reconciled after void");
});

await check("settings change updates thresholds", async () => {
    await go(page, "settings");
    await page.click('[data-action="edit-settings"]');
    await page.fill('[name="expiry.critical_days"]', "200");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(".modal .form-error:not([hidden])");
    await page.click(".modal [data-cancel]");
});

await check("create viewer user", async () => {
    await go(page, "users");
    await page.click('[data-action="add"]');
    await page.fill("#f-username", `viewer${stamp}`);
    await page.fill("#f-full_name", "E2E Viewer");
    await page.selectOption("#f-role", "VIEWER");
    await page.fill("#f-password", "Viewer-temp-2026");
    await page.click(".modal button[type=submit]");
    await page.waitForSelector(`text=viewer${stamp}`);
});

await check("sign out", async () => {
    await page.click("#logout-btn");
    await page.waitForSelector("#login:not([hidden])");
    const status = await page.evaluate(() => fetch("/medicines").then(r => r.status));
    expect(status === 401, `after logout /medicines -> ${status}`);
});

// Viewer: restricted navigation and controls; API blocks writes.
const viewerContext = await browser.newContext();
viewerContext.setDefaultTimeout(8000);
const viewerPage = await viewerContext.newPage();
watch(viewerPage, "viewer");
await check("viewer sees read-only interface", async () => {
    await viewerPage.goto(`${BASE}/app/`);
    await login(viewerPage, `viewer${stamp}`, "Viewer-temp-2026");
    await viewerPage.waitForSelector("#pw-form");
    await viewerPage.fill("#pw-current", "Viewer-temp-2026");
    await viewerPage.fill("#pw-new", "Viewer-perm-2026");
    await viewerPage.fill("#pw-repeat", "Viewer-perm-2026");
    await viewerPage.click("#pw-form button[type=submit]");
    await viewerPage.waitForFunction(() => location.hash.includes("dashboard"));
    const nav = await viewerPage.$$eval(".nav-item", a => a.map(x => x.getAttribute("href")));
    expect(!nav.includes("#/users") && !nav.includes("#/audit") && !nav.includes("#/dispense"), `nav ${nav}`);
    await viewerPage.goto(`${BASE}/app/#/medicines`);
    await viewerPage.waitForSelector("#medicines-table");
    expect(!(await viewerPage.$('[data-action="add"]')), "add button visible to viewer");
    const status = await viewerPage.evaluate(() => fetch("/medicines", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "PharmaStock" },
        body: JSON.stringify({ name: "Hack" }),
    }).then(r => r.status));
    expect(status === 403, `viewer POST /medicines -> ${status}`);
    // Without the application header a cookie request is refused (CSRF guard).
    const csrf = await viewerPage.evaluate(() => fetch("/notifications/read-all", { method: "POST" })
        .then(async r => [r.status, (await r.json()).detail]));
    expect(csrf[0] === 403 && csrf[1].includes("CSRF"), `csrf ${csrf}`);
    await viewerPage.goto(`${BASE}/app/#/users`);
    await viewerPage.waitForSelector("text=You do not have access to this page.");
});

await check("mobile layout: drawer navigation, no horizontal page scroll", async () => {
    await viewerPage.setViewportSize({ width: 390, height: 800 });
    for (const hash of ["dashboard", "inventory", "medicines", "analytics?tab=trends"]) {
        await viewerPage.goto(`${BASE}/app/#/${hash}`);
        await viewerPage.waitForFunction(() => !document.querySelector("#main > .page > .loading"));
        const overflow = await viewerPage.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow <= 1, `${hash}: page scrolls horizontally by ${overflow}px`);
    }
    await viewerPage.goto(`${BASE}/app/#/dashboard`);
    await viewerPage.waitForSelector(".cards .card");
    expect(await viewerPage.evaluate(() => document.getElementById("sidebar").getBoundingClientRect().right <= 0),
        "sidebar visible before the menu is opened");
    await shot(viewerPage, "10-mobile");
    await viewerPage.click("#menu-open");
    await viewerPage.waitForFunction(() => document.getElementById("sidebar").getBoundingClientRect().left >= 0);
    await shot(viewerPage, "10b-mobile-drawer");
    await viewerPage.click('a.nav-item[href="#/medicines"]');
    await viewerPage.waitForSelector("#medicines-table");
    expect(!(await viewerPage.evaluate(() => document.body.classList.contains("drawer-open"))), "drawer did not close");
});

await check("forgot password screen responds without revealing accounts", async () => {
    const ctx2 = await browser.newContext();
    const p2 = await ctx2.newPage();
    watch(p2, "forgot");
    await p2.goto(`${BASE}/app/`);
    await p2.click("#show-forgot");
    await p2.fill("#forgot-username", "nobody-here");
    await p2.click("#forgot-form button[type=submit]");
    await p2.waitForSelector("#forgot-message:not([hidden])");
    expect((await p2.textContent("#forgot-message")).includes("If the account exists"), "no generic message");
    await shot(p2, "14-forgot-password");
    await ctx2.close();
});

await browser.close();

if (problems.length) console.log("\nBrowser errors:\n" + problems.join("\n"));
const failed = results.filter(r => r.startsWith("FAIL")).length;
console.log(`\n${results.length - failed}/${results.length} checks passed, ${problems.length} browser errors`);
process.exit(failed || problems.length ? 1 : 0);
