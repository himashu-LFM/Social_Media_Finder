"""
run_sql.py  —  Apply a .sql file to the configured database.

    python scripts/run_sql.py sql/004_admin_users_and_password_reset.sql
    python scripts/run_sql.py sql/*.sql          # several, in the order given
    python scripts/run_sql.py --all              # every sql/*.sql, in order

Exists so nobody needs the `psql` client installed. psycopg is already a
dependency, the connection string is already in .env, and pgAdmin is awkward to
talk someone through over chat.

Each file is sent as ONE statement batch, so the BEGIN/COMMIT the migrations
already contain does what it says: a file either applies completely or not at
all. The migrations are written to be re-runnable (CREATE TABLE IF NOT EXISTS,
ADD COLUMN IF NOT EXISTS), so running one twice is harmless.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from app.persistence import db as db_service  # noqa: E402


def apply(path: Path) -> bool:
    sql = path.read_text(encoding="utf-8")
    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
    except Exception as exc:  # noqa: BLE001 — report, do not traceback at a user
        print(f"  FAILED  {path.name}: {exc.__class__.__name__}: {exc}")
        return False
    print(f"  applied {path.name}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply .sql files to DATABASE_URL.")
    parser.add_argument("files", nargs="*", help="Paths to .sql files, in order")
    parser.add_argument("--all", action="store_true",
                        help="Apply every sql/*.sql in filename order")
    args = parser.parse_args()

    if not db_service.is_configured():
        print("DATABASE_URL is not set. Add it to .env first.")
        return 1
    health = db_service.ping()
    if not health.get("connected"):
        print(f"Cannot reach the database: {health.get('detail')}")
        return 1

    paths = ([p for p in sorted((ROOT / "sql").glob("*.sql"))] if args.all
             else [Path(f) for f in args.files])
    if not paths:
        parser.print_help()
        return 1

    missing = [p for p in paths if not p.is_file()]
    if missing:
        print("Not found: " + ", ".join(str(p) for p in missing))
        return 1

    print(f"Applying {len(paths)} file(s) to the configured database:")
    for path in paths:
        if not apply(path):
            # Stop on the first failure: later migrations usually assume the
            # earlier ones landed, so carrying on just produces noise.
            return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
