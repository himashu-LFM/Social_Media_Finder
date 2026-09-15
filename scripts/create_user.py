"""
create_user.py  —  Create or reset a Curator AI account, from the command line.

    python scripts/create_user.py                 # prompts for everything
    python scripts/create_user.py --admin         # force the admin role
    python scripts/create_user.py --email a@b.com --name "Ada" --admin

This exists for exactly one job: **the first account**. After that, accounts are
created in the app itself (Users in the sidebar), which is nicer and leaves an
audit trail. But only an admin can open that page, so the very first admin has
to come from somewhere outside the app — here.

The password is read with getpass, so it is never echoed, never lands in your
shell history, and is hashed with Argon2id before it reaches the database. It is
not stored, logged, or printed anywhere. There is deliberately no --password
flag: a password on a command line is a password in `history` and in `ps`.

Run the sql/ migrations first (001 through 004).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from dotenv import load_dotenv

# Python puts the SCRIPT's directory on sys.path, not the repo root, so
# `python scripts/create_user.py` cannot see the `app` package without this.
# Running it as `python -m scripts.create_user` happens to work; both should.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from app.persistence import auth as auth_service  # noqa: E402  — after dotenv so DATABASE_URL loads
from app.persistence import db as db_service  # noqa: E402


def _check_schema() -> str:
    """Return a problem with the database schema, or "" if it is ready."""
    if not db_service.is_configured():
        return "DATABASE_URL is not set. Add it to .env first."

    health = db_service.ping()
    if not health.get("connected"):
        return f"Cannot reach the database: {health.get('detail')}"

    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.app_user')")
            if cur.fetchone()[0] is None:
                return ("Table app_user is missing. Run "
                        "sql/003_create_auth_and_history.sql, then try again.")
            # create_user writes must_change_password, which 004 adds. Without
            # it the insert fails with a column error that explains nothing.
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'app_user' AND column_name = 'must_change_password'
            """)
            if cur.fetchone() is None:
                return ("Column app_user.must_change_password is missing. Run "
                        "sql/004_admin_users_and_password_reset.sql, then try again.")
    except Exception as exc:  # noqa: BLE001
        return f"Schema check failed: {exc.__class__.__name__}: {exc}"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or reset a Curator AI account.")
    parser.add_argument("--email", default="", help="Account email address")
    parser.add_argument("--name", default="", help="Display name (optional)")
    parser.add_argument("--admin", action="store_true",
                        help="Create an admin (can manage accounts in the app)")
    parser.add_argument("--analyst", action="store_true",
                        help="Create an analyst, even if this is the first account")
    args = parser.parse_args()

    problem = _check_schema()
    if problem:
        print(problem)
        return 1

    existing_admins = auth_service.admin_count()
    total = auth_service.user_count()
    print("Create or reset a Curator AI account.")
    print(f"Existing active accounts: {total} ({existing_admins} admin)\n")

    email = args.email.strip() or input("Email        : ").strip()
    # Checked here, not by create_user() — otherwise a typo is only reported
    # after the password has been typed twice, and both have to be redone.
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        print(f"\n'{email}' is not a valid email address.")
        return 1
    name = args.name.strip() or input("Display name : ").strip()

    # With no admin yet, admin is the only useful answer: an analyst cannot
    # create anyone else, so the tool would have no way to grow.
    if args.admin:
        role = "admin"
    elif args.analyst:
        role = "analyst"
    elif existing_admins == 0:
        role = "admin"
        print("\nNo admin exists yet, so this account will be an ADMIN — otherwise")
        print("nobody could create further accounts from the app.")
    else:
        role = (input("Role [analyst/admin] (analyst): ").strip() or "analyst").lower()
        if role not in ("analyst", "admin"):
            print("Role must be 'analyst' or 'admin'.")
            return 1

    password = getpass.getpass(
        f"\nPassword (min {auth_service.MIN_PASSWORD_LENGTH} chars): ")
    if password != getpass.getpass("Confirm password           : "):
        print("Passwords do not match.")
        return 1

    try:
        user = auth_service.create_user(
            email, password, name, role,
            # Chosen by the person who will use it, so there is nothing to force
            # them to change. Only admin-issued temporary passwords set this.
            must_change_password=False,
        )
    except ValueError as exc:
        print(f"\n{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not create the account: {exc.__class__.__name__}: {exc}")
        return 1
    finally:
        del password  # drop the plaintext as soon as it is no longer needed

    print(f"\nAccount ready: {user['email']}  (id={user['id']}, role={user['role']})")
    if user["role"] == "admin":
        print("Sign in at /login, then create everyone else from Users in the sidebar.")
    else:
        print("Sign in at /login in the web app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
