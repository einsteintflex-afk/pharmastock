# ============================================================
# ADMINISTRATION COMMANDS
# ============================================================
#   python -m backend.manage create-admin          create an administrator (prompts)
#   python -m backend.manage create-organization   new tenant + first administrator (prompts)
#   python -m backend.manage reset-password        reset any user's password (prompts)
#   python -m backend.manage refresh-notifications (all organizations)
#   python -m backend.manage reconcile             batches vs ledger (all organizations)

import getpass
import json
import sys

from fastapi import HTTPException

from .database import active_organization_ids, connect, set_organization
from .security import hash_password, validate_password_strength
from .services import notifications, organizations, stock


def _password(username: str) -> str:
    while True:
        first = getpass.getpass("Password: ")
        if first != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            continue
        try:
            validate_password_strength(first, username)
            return first
        except HTTPException as error:
            print(error.detail)


def create_admin() -> None:
    organization_id = int(input("Organization id [1]: ").strip() or "1")
    username = input("Username: ").strip()
    full_name = input("Full name: ").strip() or "Administrator"
    password = _password(username)
    with connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE lower(username) = lower(%s)", (username,)).fetchone():
            sys.exit("That username already exists.")
        if conn.execute("SELECT 1 FROM organizations WHERE id = %s", (organization_id,)).fetchone() is None:
            sys.exit("No such organization.")
        set_organization(conn, organization_id)
        row = conn.execute(
            """
            INSERT INTO users (username, full_name, role, password_hash, organization_id, is_platform_admin)
            VALUES (%s, %s, 'ADMINISTRATOR', %s, %s, %s) RETURNING id
            """,
            (username, full_name, hash_password(password), organization_id, organization_id == 1),
        ).fetchone()
        conn.execute(
            "INSERT INTO audit_log (username, action, entity_type, entity_id, new_value) "
            "VALUES ('system', 'CREATE_ADMIN_CLI', 'user', %s, %s)",
            (str(row["id"]), json.dumps({"username": username})),
        )
        conn.commit()
    print(f"Administrator '{username}' created.")


def reset_password() -> None:
    username = input("Username: ").strip()
    password = _password(username)
    with connect() as conn:
        row = conn.execute(
            """
            UPDATE users SET password_hash = %s, failed_login_count = 0, locked_until = NULL,
                   must_change_password = false, password_changed_at = CURRENT_TIMESTAMP
            WHERE lower(username) = lower(%s) RETURNING id, organization_id
            """,
            (hash_password(password), username),
        ).fetchone()
        if row is None:
            sys.exit("No such user.")
        set_organization(conn, row["organization_id"])
        conn.execute("UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP WHERE user_id = %s AND revoked_at IS NULL",
                     (row["id"],))
        conn.execute(
            "INSERT INTO audit_log (username, action, entity_type, entity_id) "
            "VALUES ('system', 'PASSWORD_RESET_CLI', 'user', %s)",
            (str(row["id"]),),
        )
        conn.commit()
    print("Password reset; existing sessions signed out.")


def _organizations() -> list[int]:
    with connect() as conn:
        return active_organization_ids(conn)


def create_organization() -> None:
    name = input("Organization name: ").strip()
    org_type = (input("Type [COMMUNITY_PHARMACY/PHARMACY_CHAIN/HOSPITAL/WHOLESALE]: ").strip().upper()
                or "COMMUNITY_PHARMACY")
    plan = input("Plan [BASIC/PROFESSIONAL/ENTERPRISE]: ").strip().upper() or "BASIC"
    username = input("First administrator username: ").strip()
    full_name = input("First administrator full name: ").strip() or "Administrator"
    password = _password(username)
    with connect() as conn:
        result = organizations.create(
            conn, name=name, org_type=org_type, plan=plan, status="ACTIVE", admin_username=username,
            admin_full_name=full_name, admin_password_hash=hash_password(password), return_to_organization=None,
        )
        conn.commit()
    print(f"Organization {result['organization']['id']} created; administrator '{username}' must change the "
          "password at first sign-in.")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "create-admin":
        create_admin()
    elif command == "reset-password":
        reset_password()
    elif command == "create-organization":
        create_organization()
    elif command == "refresh-notifications":
        for organization_id in _organizations():
            with connect(organization_id) as conn:
                print(organization_id, notifications.refresh(conn))
                conn.commit()
    elif command == "reconcile":
        for organization_id in _organizations():
            with connect(organization_id) as conn:
                rows = stock.reconciliation(conn)
            print(f"Organization {organization_id}:", "stock ledger reconciled." if not rows else rows)
    else:
        print("Commands: create-admin, create-organization, reset-password, refresh-notifications, reconcile")


if __name__ == "__main__":
    main()
