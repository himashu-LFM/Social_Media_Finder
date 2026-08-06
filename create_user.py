"""
create_user.py  —  Create or reset a Curator AI account.

    python create_user.py

Prompts for the details interactively. The password is read with getpass, so it
is not echoed to the terminal and never lands in your shell history; it is
hashed with Argon2id before it reaches the database and is never stored,
logged, or printed anywhere.

Run sql/003_create_auth_and_history.sql first.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import auth_service  # noqa: E402  — after dotenv so DATABASE_URL loads
import db_service  # noqa: E402


def main() -> int:
    if not db_service.is_configured():
        print("DATABASE_URL is not set. Add it to .env first.")
        return 1

    health = db_service.ping()
    if not health.get("connected"):
        print(f"Cannot reach the database: {health.get('detail')}")
        return 1

    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.app_user')")
            if cur.fetchone()[0] is None:
                print("Table app_user is missing. Run sql/003_create_auth_and_history.sql "
                      "in pgAdmin, then try again.")
                return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Schema check failed: {exc}")
        return 1

    print("Create or reset a Curator AI account.")
    print(f"Existing active accounts: {auth_service.user_count()}\n")

    email = input("Email        : ").strip()
    name = input("Display name : ").strip()
    role = (input("Role [analyst/admin] (analyst): ").strip() or "analyst").lower()
    if role not in ("analyst", "admin"):
        print("Role must be 'analyst' or 'admin'.")
        return 1

    # getpass keeps the password off the screen and out of shell history.
    password = getpass.getpass("Password (min 10 chars): ")
    if password != getpass.getpass("Confirm password       : "):
        print("Passwords do not match.")
        return 1

    try:
        user = auth_service.create_user(email, password, name, role)
    except ValueError as exc:
        print(f"\n{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not create the account: {exc.__class__.__name__}: {exc}")
        return 1
    finally:
        del password  # drop the plaintext as soon as it is no longer needed

    print(f"\nAccount ready: {user['email']}  (id={user['id']}, role={user['role']})")
    print("Sign in at /login in the web app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
