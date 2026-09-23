const API_BASE = "";


/* =========================
   LOAD DASHBOARD
========================= */

async function loadDashboard() {

    try {

        const [
            inventoryResponse,
            stockResponse,
            movementsResponse
        ] = await Promise.all([
            fetch(`${API_BASE}/inventory`),
            fetch(`${API_BASE}/stock-alerts`),
            fetch(`${API_BASE}/stock-movements`)
        ]);


        if (!inventoryResponse.ok) {
            throw new Error("Could not load inventory");
        }

        if (!stockResponse.ok) {
            throw new Error("Could not load stock alerts");
        }

        if (!movementsResponse.ok) {
            throw new Error("Could not load stock movements");
        }


        const inventory = await inventoryResponse.json();
        const stock = await stockResponse.json();
        const movements = await movementsResponse.json();


        updateCards(inventory, stock);

        updateExpiryTable(inventory);

        updateStockTable(stock);

        updateMovementList(movements);

    }

    catch (error) {

        console.error(error);

        document.getElementById("expiryTable").innerHTML = `
            <tr>
                <td colspan="6" class="loading">
                    Unable to connect to PharmaStock API.
                </td>
            </tr>
        `;

    }

}


/* =========================
   DASHBOARD CARDS
========================= */

function updateCards(inventory, stock) {

    const medicines = new Set();

    let totalStock = 0;

    let expired = 0;

    let urgent = 0;


    inventory.forEach(item => {

        medicines.add(
            `${item.medicine}|${item.strength}|${item.dosage_form}`
        );

        totalStock += Number(item.quantity);


        if (item.status === "EXPIRED") {
            expired++;
        }


        if (item.status === "URGENT") {
            urgent++;
        }

    });


    document.getElementById("totalMedicines").textContent =
        medicines.size;


    document.getElementById("totalStock").textContent =
        totalStock.toLocaleString();


    document.getElementById("expiredCount").textContent =
        expired;


    document.getElementById("urgentCount").textContent =
        urgent;

}


/* =========================
   EXPIRY TABLE
========================= */

function updateExpiryTable(inventory) {

    const table = document.getElementById("expiryTable");


    if (!inventory.length) {

        table.innerHTML = `
            <tr>
                <td colspan="6" class="loading">
                    No inventory found.
                </td>
            </tr>
        `;

        return;
    }


    table.innerHTML = inventory.map(item => {

        let statusClass = "normal";


        if (item.status === "EXPIRED") {
            statusClass = "expired";
        }

        else if (item.status === "URGENT") {
            statusClass = "urgent";
        }

        else if (item.status === "APPROACHING EXPIRY") {
            statusClass = "approaching";
        }


        return `
            <tr>

                <td>
                    <strong>${escapeHtml(item.medicine)}</strong>
                    <br>
                    <small>
                        ${escapeHtml(item.strength)}
                        ${escapeHtml(item.dosage_form)}
                    </small>
                </td>

                <td>
                    ${escapeHtml(item.batch_number)}
                </td>

                <td>
                    ${Number(item.quantity).toLocaleString()}
                </td>

                <td>
                    ${formatDate(item.expiry_date)}
                </td>

                <td>
                    ${item.days_until_expiry < 0
                        ? `${Math.abs(item.days_until_expiry)} days overdue`
                        : `${item.days_until_expiry} days`
                    }
                </td>

                <td>
                    <span class="status ${statusClass}">
                        ${escapeHtml(item.status)}
                    </span>
                </td>

            </tr>
        `;

    }).join("");

}


/* =========================
   STOCK TABLE
========================= */

function updateStockTable(stock) {

    const table = document.getElementById("stockTable");


    if (!stock.length) {

        table.innerHTML = `
            <tr>
                <td colspan="4" class="loading">
                    No stock information found.
                </td>
            </tr>
        `;

        return;
    }


    table.innerHTML = stock.map(item => {

        const low =
            item.status === "LOW STOCK";


        return `
            <tr>

                <td>
                    <strong>${escapeHtml(item.medicine)}</strong>
                    <br>
                    <small>
                        ${escapeHtml(item.strength)}
                        ${escapeHtml(item.dosage_form)}
                    </small>
                </td>

                <td>
                    ${Number(item.current_stock).toLocaleString()}
                </td>

                <td>
                    ${Number(item.reorder_level).toLocaleString()}
                </td>

                <td>

                    <span class="status ${low ? "low" : "normal"}">
                        ${escapeHtml(item.status)}
                    </span>

                </td>

            </tr>
        `;

    }).join("");

}


/* =========================
   MOVEMENT LIST
========================= */

function updateMovementList(movements) {

    const container =
        document.getElementById("movementList");


    if (!movements.length) {

        container.innerHTML = `
            <div class="loading">
                No stock movements found.
            </div>
        `;

        return;
    }


    container.innerHTML =
        movements.slice(0, 6).map(movement => {

            const received =
                movement.movement_type === "RECEIVED";


            const movementClass =
                received ? "received" : "dispensed";


            const icon =
                received ? "↓" : "↑";


            return `
                <div class="movement">

                    <div class="movement-icon">
                        ${icon}
                    </div>

                    <div class="movement-info">

                        <strong>
                            ${escapeHtml(movement.medicine)}
                        </strong>

                        <small>
                            ${escapeHtml(movement.batch_number)}
                            ·
                            ${escapeHtml(movement.movement_type)}
                        </small>

                    </div>

                    <div class="movement-quantity ${movementClass}">
                        ${received ? "+" : "-"}${movement.quantity}
                    </div>

                </div>
            `;

        }).join("");

}


/* =========================
   DATE
========================= */

function formatDate(dateString) {

    if (!dateString) {
        return "—";
    }


    const date = new Date(dateString);


    if (Number.isNaN(date.getTime())) {
        return dateString;
    }


    return date.toLocaleDateString(
        "en-GB",
        {
            day: "2-digit",
            month: "short",
            year: "numeric"
        }
    );

}


/* =========================
   SECURITY
========================= */

function escapeHtml(value) {

    if (value === null || value === undefined) {
        return "";
    }


    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");

}


/* =========================
   START
========================= */

loadDashboard();


/*
Refresh dashboard every 60 seconds.
*/

setInterval(
    loadDashboard,
    60000
);

// ============================================================
// MEDICINES MODULE
// ============================================================

async function showMedicines() {
    try {
        const response = await fetch("/medicines");

        if (!response.ok) {
            throw new Error("Failed to load medicines");
        }

        const medicines = await response.json();

        console.log("Medicines:", medicines);

        const mainContent = document.querySelector(".main");

        if (!mainContent) {
            console.error("Main content area not found");
            return;
        }

        if (originalDashboardHTML === null) {
    originalDashboardHTML = mainContent.innerHTML;
}

        mainContent.innerHTML = `
            <div class="page-header">
                <div>
                    <h2>Medicines</h2>
                    <p>Manage medicines registered in PharmaStock.</p>
                </div>

                <button class="primary-button" onclick="showAddMedicineForm()">
                    + Add Medicine
                </button>
            </div>

            <div class="card">
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Medicine</th>
                                <th>Strength</th>
                                <th>Dosage Form</th>
                                <th>Reorder Level</th>
                            </tr>
                        </thead>

                        <tbody>
                            ${
                                medicines.length === 0
                                    ? `
                                        <tr>
                                            <td colspan="5">
                                                No medicines found.
                                            </td>
                                        </tr>
                                    `
                                    : medicines.map(medicine => `
                                        <tr>
                                            <td>${medicine.id}</td>
                                            <td>${medicine.name}</td>
                                            <td>${medicine.strength}</td>
                                            <td>${medicine.dosage_form}</td>
                                            <td>${medicine.reorder_level}</td>
                                        </tr>
                                    `).join("")
                            }
                        </tbody>
                    </table>
                </div>
            </div>
        `;

    } catch (error) {
        console.error("Error loading medicines:", error);

        alert("Unable to load medicines. Please check that the PharmaStock server is running.");
    }
}

// ============================================================
// DASHBOARD NAVIGATION
// ============================================================

let originalDashboardHTML = null;


// ============================================================
// SHOW DASHBOARD
// ============================================================

function showDashboard() {

    const mainContent = document.querySelector(".main");

    if (!mainContent) {
        console.error("Main content area not found");
        return;
    }

    // Restore the original dashboard
    if (originalDashboardHTML !== null) {
        mainContent.innerHTML = originalDashboardHTML;
    }

    // Update active navigation button
    document.querySelectorAll(".nav-item").forEach(button => {
        button.classList.remove("active");
    });

    const dashboardButton = document.querySelector(
        '.nav-item[onclick="showDashboard()"]'
    );

    if (dashboardButton) {
        dashboardButton.classList.add("active");
    }

    // Reload dashboard data
    loadDashboard();
}