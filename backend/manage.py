# ============================================================
# ADMINISTRATION COMMANDS
# ============================================================
#   python -m backend.manage create-admin      create an administrator (prompts)
#   python -m backend.manage reset-password    reset any user's password (prompts)
#   python -m backend.manage refresh-notifications
#   python -m backend.manage reconcile         check batches against the ledger

import getpass
import json
import sys

from fastapi import HTTPException

from .database import connect
from .security import hash_password, validate_password_strength
from .services import notifications, stock


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
    username = input("Username: ").strip()
    full_name = input("Full name: ").strip() or "Administrator"
    password = _password(username)
    with connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE lower(username) = lower(%s)", (username,)).fetchone():
            sys.exit("That username already exists.")
        row = conn.execute(
            """
            INSERT INTO users (username, full_name, role, password_hash)
            VALUES (%s, %s, 'ADMINISTRATOR', %s) RETURNING id
            """,
            (username, full_name, hash_password(password)),
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
            WHERE lower(username) = lower(%s) RETURNING id
            """,
            (hash_password(password), username),
        ).fetchone()
        if row is None:
            sys.exit("No such user.")
        conn.execute("UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP WHERE user_id = %s AND revoked_at IS NULL",
                     (row["id"],))
        conn.execute(
            "INSERT INTO audit_log (username, action, entity_type, entity_id) "
            "VALUES ('system', 'PASSWORD_RESET_CLI', 'user', %s)",
            (str(row["id"]),),
        )
        conn.commit()
    print("Password reset; existing sessions signed out.")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "create-admin":
        create_admin()
    elif command == "reset-password":
        reset_password()
    elif command == "refresh-notifications":
        with connect() as conn:
            print(notifications.refresh(conn))
            conn.commit()
    elif command == "reconcile":
        with connect() as conn:
            rows = stock.reconciliation(conn)
        print("Stock ledger reconciled." if not rows else rows)
    else:
        print(__doc__ or "", "Commands: create-admin, reset-password, refresh-notifications, reconcile")


if __name__ == "__main__":
    main()
