# ============================================================
# ROLES AND PERMISSIONS
# ============================================================
# Permissions are enforced on the API (see security.require). The frontend
# receives the same list only to hide controls the user cannot use.

ROLES = (
    "OWNER",
    "ADMINISTRATOR",
    "MANAGER",
    "PHARMACIST",
    "PHARMACY_TECHNICIAN",
    "STOREKEEPER",
    "VIEWER",
    "INVENTORY_OFFICER",
    "PURCHASING_OFFICER",
    "CASHIER",
    "AUDITOR",
)

ROLE_LABELS = {
    "OWNER": "Organization Owner",
    "ADMINISTRATOR": "Administrator",
    "MANAGER": "Manager",
    "PHARMACIST": "Pharmacist",
    "PHARMACY_TECHNICIAN": "Pharmacy Technician",
    "STOREKEEPER": "Storekeeper",
    "VIEWER": "Viewer",
    "INVENTORY_OFFICER": "Inventory Officer",
    "PURCHASING_OFFICER": "Purchasing Officer",
    "CASHIER": "Cashier",
    "AUDITOR": "Auditor",
}

# Roles that manage the organization; MFA can be required for them.
ADMIN_ROLES = {"OWNER", "ADMINISTRATOR"}

# Every permission the API checks.
PERMISSIONS = {
    "inventory.read": "View medicines, batches, inventory, suppliers and purchasing",
    "analytics.read": "View dashboard, alerts, analytics and reports",
    "reports.export": "Export reports (CSV / Excel / PDF)",
    "notifications.read": "Use the notification centre",
    "assistant.use": "Use the AI inventory assistant",
    "medicines.write": "Create and edit medicines",
    "suppliers.write": "Create, edit, activate and deactivate suppliers",
    "batches.write": "Create batches / opening stock and edit batch details",
    "stock.dispense": "Dispense stock (FEFO)",
    "stock.fefo_override": "Dispense from a batch other than the FEFO batch, with a reason",
    "dispensing.void": "Void a completed dispensation (stock is returned to its batches)",
    "stock.adjust": "Record returns, damage, expiry write-offs and stock-count adjustments",
    "stock.count": "Create and count physical stock counts",
    "stock.approve": "Approve stock adjustments above the threshold and post stock counts",
    "purchasing.write": "Create, edit and cancel purchase orders",
    "purchasing.receive": "Receive purchase order deliveries",
    "purchasing.approve": "Approve submitted purchase orders",
    "transfers.request": "Request stock transfers and ward / department requisitions",
    "transfers.approve": "Approve or reject transfers and requisitions",
    "transfers.dispatch": "Dispatch approved transfers from the source location (FEFO)",
    "transfers.receive": "Receive transfers at the destination location",
    "locations.manage": "Create and edit stock locations",
    "settings.manage": "Change organisation settings such as expiry thresholds",
    "audit.read": "View the audit trail",
    "users.manage": "Create users, change roles, reset passwords",
    "communications.manage": "Configure WhatsApp / SMS / e-mail messaging and templates",
    "billing.manage": "View and manage the subscription, payments and messaging credits",
}

_READ = {"inventory.read", "analytics.read", "notifications.read", "assistant.use"}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "VIEWER": _READ,
    "STOREKEEPER": _READ | {
        "reports.export", "batches.write", "stock.adjust", "stock.count", "purchasing.receive",
        "transfers.request", "transfers.dispatch", "transfers.receive",
    },
    "PHARMACY_TECHNICIAN": _READ | {
        "reports.export", "stock.dispense", "purchasing.receive",
        "transfers.request", "transfers.receive",
    },
    "PHARMACIST": _READ | {
        "reports.export", "medicines.write", "suppliers.write", "batches.write",
        "stock.dispense", "stock.fefo_override", "stock.adjust", "dispensing.void",
        "purchasing.write", "purchasing.receive", "stock.count",
        "transfers.request", "transfers.approve", "transfers.dispatch", "transfers.receive",
    },
    "MANAGER": _READ | {
        "reports.export", "medicines.write", "suppliers.write", "batches.write",
        "stock.dispense", "stock.fefo_override", "stock.adjust", "dispensing.void",
        "purchasing.write", "purchasing.receive",
        "transfers.request", "transfers.approve", "transfers.dispatch", "transfers.receive",
        "locations.manage", "settings.manage", "audit.read",
        "stock.count", "stock.approve", "purchasing.approve", "communications.manage",
    },
    "INVENTORY_OFFICER": _READ | {
        "reports.export", "medicines.write", "batches.write", "stock.adjust", "stock.count",
        "purchasing.receive", "transfers.request", "transfers.dispatch", "transfers.receive",
    },
    "PURCHASING_OFFICER": _READ | {
        "reports.export", "suppliers.write", "purchasing.write", "purchasing.receive",
    },
    "CASHIER": {"inventory.read", "notifications.read", "stock.dispense"},
    "AUDITOR": _READ - {"assistant.use"} | {"reports.export", "audit.read"},
    # The administrator manages everything except billing, which is the owner's.
    "ADMINISTRATOR": set(PERMISSIONS) - {"billing.manage"},
    "OWNER": set(PERMISSIONS),
}

assert set(ROLE_PERMISSIONS) == set(ROLES)
assert all(perms <= set(PERMISSIONS) for perms in ROLE_PERMISSIONS.values())


def permissions_for(role: str) -> set[str]:
    return ROLE_PERMISSIONS.get(role, set())
