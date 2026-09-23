/* PharmaStock cleaned app.js — consolidated navigation and medicine management. */

const API_BASE = "";


/* =========================
   LOAD DASHBOARD
========================= */

async function loadDashboard() {

    // Do not update dashboard elements when another page is open
    if (!document.getElementById("totalMedicines")) {
        return;
    }

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

        const expiryTable = document.getElementById("expiryTable");

        if (expiryTable) {
            expiryTable.innerHTML = `
                <tr>
                    <td colspan="6" class="loading">
                        Unable to connect to PharmaStock API.
                    </td>
                </tr>
            `;
        }

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


    const totalMedicines =
        document.getElementById("totalMedicines");

    const totalStockElement =
        document.getElementById("totalStock");

    const expiredCount =
        document.getElementById("expiredCount");

    const urgentCount =
        document.getElementById("urgentCount");

    if (totalMedicines) {
        totalMedicines.textContent = medicines.size;
    }

    if (totalStockElement) {
        totalStockElement.textContent =
            totalStock.toLocaleString();
    }

    if (expiredCount) {
        expiredCount.textContent = expired;
    }

    if (urgentCount) {
        urgentCount.textContent = urgent;
    }

}


/* =========================
   EXPIRY TABLE
========================= */

function updateExpiryTable(inventory) {

    const table = document.getElementById("expiryTable");

    if (!table) return;

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

    if (!table) return;

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

    if (!container) return;

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
// DASHBOARD NAVIGATION
// ============================================================

function setActiveNav(label) {

    document.querySelectorAll(".nav-item").forEach(button => {
        button.classList.remove("active");
    });

    const buttons = Array.from(
        document.querySelectorAll(".nav-item")
    );

    const target = buttons.find(button =>
        button.textContent.trim().startsWith(label)
    );

    if (target) {
        target.classList.add("active");
    }
}


let originalDashboardHTML =
    document.querySelector(".main")?.innerHTML || null;


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
    setActiveNav("Dashboard");

    // Reload dashboard data
    loadDashboard();
}

/* =========================
   MEDICINES PAGE
========================= */

async function showMedicines() {

    try {

        const [medicineResponse, stockResponse] =
            await Promise.all([
                fetch("/medicines"),
                fetch("/stock-alerts")
            ]);

        if (!medicineResponse.ok) {
            throw new Error("Could not load medicines");
        }

        if (!stockResponse.ok) {
            throw new Error("Could not load stock information");
        }

        const medicines = await medicineResponse.json();
        const stock = await stockResponse.json();

        window.currentStock = stock;

        const main = document.querySelector(".main");

        if (!main) {
            console.error("Main content area not found");
            return;
        }

        setActiveNav("Medicines");

        if (originalDashboardHTML === null) {
            originalDashboardHTML = main.innerHTML;
        }

        main.innerHTML = `

            <header class="topbar">

                <div>
                    <h2>Medicines</h2>
                    <p>Manage medicines in PharmaStock</p>
                </div>

                <div class="top-actions">

                    <button class="refresh-btn"
                            onclick="showMedicines()">
                        ↻ Refresh
                    </button>

                </div>

            </header>


            <section class="section">

                <div class="section-header">

                    <div>
                        <h3>Medicine List</h3>
                        <p>Registered medicines</p>
                    </div>

                    <button class="view-btn"
                            onclick="showAddMedicineForm()">
                        + Add Medicine
                    </button>

                </div>


                <div class="medicine-toolbar">

                    <input
                        type="text"
                        id="medicineSearch"
                        placeholder="Search medicine..."
                        oninput="filterMedicines()"
                    >

                </div>


                <div class="table-container">

                    <table>

                        <thead>

                            <tr>
                                <th>Medicine</th>
<th>Strength</th>
<th>Dosage Form</th>
<th>Current Stock</th>
<th>Reorder Level</th>
<th>Status</th>
<th>Actions</th>
                            </tr>

                        </thead>

                        <tbody id="medicinesTable">

                        </tbody>

                    </table>

                </div>

            </section>
        `;

        window.currentMedicines = medicines;

        renderMedicines(medicines, stock);

    }

    catch (error) {

        console.error(error);

        const main = document.querySelector(".main");

        if (main) {

            main.innerHTML = `

                <header class="topbar">

                    <div>
                        <h2>Medicines</h2>
                        <p>Manage medicines in PharmaStock</p>
                    </div>

                </header>

                <section class="section">

                    <div class="loading">
                        Unable to load medicines.
                    </div>

                </section>

            `;

        }

    }

}


/* =========================
   RENDER MEDICINES
========================= */

function renderMedicines(
    medicines,
    stock = window.currentStock || []
) {

    const table =
        document.getElementById("medicinesTable");

    if (!table) return;


    if (medicines.length === 0) {

        table.innerHTML = `
            <tr>
                <td colspan="7" class="loading">
                    No medicines found.
                </td>
            </tr>
        `;

        return;
    }


    const stockByMedicineId =
        Object.fromEntries(
            stock.map(item => [
                item.medicine_id,
                item
            ])
        );


    table.innerHTML = medicines.map(medicine => {

        const stockItem =
            stockByMedicineId[medicine.id];


        const currentStock =
            stockItem
                ? Number(stockItem.current_stock)
                : 0;


        const stockStatus =
            stockItem
                ? stockItem.status
                : "NO STOCK";


        const statusClass =
            stockStatus === "LOW STOCK"
                ? "low"
                : "normal";


        return `

            <tr>

                <td>
                    <strong>
                        ${medicine.name}
                    </strong>
                </td>


                <td>
                    ${medicine.strength || "—"}
                </td>


                <td>
                    ${medicine.dosage_form || "—"}
                </td>


                <td>
                    ${currentStock.toLocaleString()}
                </td>


                <td>
                    ${Number(
            medicine.reorder_level
        ).toLocaleString()}
                </td>


                <td>

                    <span
                        class="status ${statusClass}"
                    >
                        ${stockStatus}
                    </span>

                </td>


                <td>

                    <button
                        class="view-btn"
                        onclick="editMedicine(${medicine.id})"
                    >
                        Edit
                    </button>

                </td>

            </tr>

        `;

    }).join("");

}


/* =========================
   SEARCH MEDICINES
========================= */

function filterMedicines() {

    const searchInput =
        document.getElementById("medicineSearch");

    if (!searchInput) return;

    const search =
        searchInput.value.toLowerCase().trim();

    const filtered =
        (window.currentMedicines || []).filter(medicine => {

            return (

                medicine.name.toLowerCase().includes(search) ||

                (medicine.strength || "")
                    .toLowerCase()
                    .includes(search) ||

                (medicine.dosage_form || "")
                    .toLowerCase()
                    .includes(search)

            );

        });

    renderMedicines(filtered, window.currentStock);

}


/* =========================
   ADD MEDICINE
========================= */

function showAddMedicineForm() {

    const main = document.querySelector(".main");

    if (!main) return;

    setActiveNav("Medicines");

    main.innerHTML = `

        <header class="topbar">

            <div>
                <h2>Add Medicine</h2>
                <p>Register a new medicine</p>
            </div>

        </header>


        <section class="section">

            <div class="medicine-form">

                <label>
                    Medicine Name
                </label>

                <input
                    id="newMedicineName"
                    type="text"
                    placeholder="e.g. Paracetamol"
                >


                <label>
                    Strength
                </label>

                <input
                    id="newMedicineStrength"
                    type="text"
                    placeholder="e.g. 500 mg"
                >


                <label>
                    Dosage Form
                </label>

                <input
                    id="newMedicineForm"
                    type="text"
                    placeholder="e.g. Tablet"
                >


                <label>
                    Reorder Level
                </label>

                <input
                    id="newMedicineReorder"
                    type="number"
                    min="0"
                    value="20"
                >


                <div class="form-actions">

                    <button
                        class="view-btn"
                        onclick="showMedicines()">
                        Cancel
                    </button>

                    <button
                        class="refresh-btn"
                        onclick="createMedicine()">
                        Save Medicine
                    </button>

                </div>

            </div>

        </section>
    `;

}


/* =========================
   CREATE MEDICINE
========================= */

async function createMedicine() {

    const name =
        document.getElementById("newMedicineName").value.trim();

    const strength =
        document.getElementById("newMedicineStrength").value.trim();

    const dosageForm =
        document.getElementById("newMedicineForm").value.trim();

    const reorderLevel =
        Number(document.getElementById("newMedicineReorder").value);


    if (!name) {

        alert("Medicine name is required.");

        return;

    }


    if (reorderLevel < 0) {

        alert("Reorder level cannot be negative.");

        return;

    }


    try {

        const response = await fetch("/medicines", {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({

                name: name,

                strength: strength,

                dosage_form: dosageForm,

                reorder_level: reorderLevel

            })

        });


        if (!response.ok) {

            const error =
                await response.text();

            console.error(error);

            alert("Could not create medicine.");

            return;

        }


        alert("Medicine created successfully.");

        showMedicines();

    }

    catch (error) {

        console.error(error);

        alert("Unable to connect to PharmaStock API.");

    }

}


/* =========================
   EDIT MEDICINE
========================= */

function editMedicine(id) {

    const medicine =
        (window.currentMedicines || [])
            .find(item => item.id === id);

    if (!medicine) return;

    const main = document.querySelector(".main");

    if (!main) return;

    setActiveNav("Medicines");

    main.innerHTML = `

        <header class="topbar">

            <div>
                <h2>Edit Medicine</h2>
                <p>Update medicine information</p>
            </div>

        </header>


        <section class="section">

            <div class="medicine-form">

                <label>
                    Medicine Name
                </label>

                <input
                    id="editMedicineName"
                    type="text"
                    value="${medicine.name}"
                >


                <label>
                    Strength
                </label>

                <input
                    id="editMedicineStrength"
                    type="text"
                    value="${medicine.strength || ""}"
                >


                <label>
                    Dosage Form
                </label>

                <input
                    id="editMedicineForm"
                    type="text"
                    value="${medicine.dosage_form || ""}"
                >


                <label>
                    Reorder Level
                </label>

                <input
                    id="editMedicineReorder"
                    type="number"
                    min="0"
                    value="${medicine.reorder_level}"
                >


                <div class="form-actions">

                    <button
                        class="view-btn"
                        onclick="showMedicines()">
                        Cancel
                    </button>

                    <button
                        class="refresh-btn"
                        onclick="updateMedicine(${medicine.id})">
                        Save Changes
                    </button>

                </div>

            </div>

        </section>

    `;

}


/* =========================
   UPDATE MEDICINE
========================= */

async function updateMedicine(id) {

    const name =
        document.getElementById("editMedicineName").value.trim();

    const strength =
        document.getElementById("editMedicineStrength").value.trim();

    const dosageForm =
        document.getElementById("editMedicineForm").value.trim();

    const reorderLevel =
        Number(document.getElementById("editMedicineReorder").value);


    if (!name) {

        alert("Medicine name is required.");

        return;

    }


    if (reorderLevel < 0) {

        alert("Reorder level cannot be negative.");

        return;

    }


    try {

        const response = await fetch(`/medicines/${id}`, {

            method: "PUT",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({

                name: name,

                strength: strength,

                dosage_form: dosageForm,

                reorder_level: reorderLevel

            })

        });


        if (!response.ok) {

            console.error(await response.text());

            alert("Could not update medicine.");

            return;

        }


        alert("Medicine updated successfully.");

        showMedicines();

    }

    catch (error) {

        console.error(error);

        alert("Unable to connect to PharmaStock API.");

    }

}