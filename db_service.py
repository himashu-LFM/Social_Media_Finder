"""
db_service.py  —  Analyst decision storage (verified_url / rejected_url)

Two tables with an identical, deliberately flat schema:

    title | title_category | title_subcategory |
    instagram_url | facebook_url | tiktok_url | twitter_url | youtube_url

ONE ROW PER TITLE. Saving Instagram now and TikTok later updates the same row —
the upsert writes only the column for the platform being saved and COALESCEs
every other column back to its stored value, so nothing is scattered across
duplicate rows and nothing already saved is wiped.

Configure with a single environment variable:

    DATABASE_URL=postgresql://user:password@host:5432/dbname

Everything degrades gracefully: if DATABASE_URL is unset the module reports
``is_configured() == False`` and the API returns a clear message instead of
failing. The pipeline itself never depends on this module.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore
    dict_row = None  # type: ignore

try:  # Optional but recommended: the API is multi-threaded.
    from psycopg_pool import ConnectionPool
    _DRIVER = "psycopg3 + pool"
except ImportError:
    ConnectionPool = None  # type: ignore
    _DRIVER = "psycopg3 (no pool)" if psycopg else "none"

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# UI platform key -> database column. The single source of truth for the
# mapping; both the API and the upsert build their SQL from this.
PLATFORM_COLUMNS: Dict[str, str] = {
    "Instagram": "instagram_url",
    "Facebook": "facebook_url",
    "TikTok": "tiktok_url",
    "X": "twitter_url",
    "YouTube": "youtube_url",
}

_ALL_URL_COLUMNS = list(PLATFORM_COLUMNS.values())
_TABLES = ("verified_url", "rejected_url")

_pool: Optional["ConnectionPool"] = None


def is_configured() -> bool:
    return bool(DATABASE_URL)


@contextmanager
def _connection():
    """
    Yield a live connection.

    Uses a pool when ``psycopg_pool`` is installed (recommended — the API is
    multi-threaded), and falls back to a short-lived per-call connection when it
    isn't, so the feature works out of the box on a plain ``psycopg`` install.
    """
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set — cannot reach the database.")
    if ConnectionPool is not None:
        if _pool is None:
            _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=True,
                                   kwargs={"autocommit": True})
        with _pool.connection() as conn:
            yield conn
        return
    if psycopg is None:
        raise RuntimeError(
            "psycopg is not installed. Run: python -m pip install \"psycopg[binary,pool]\""
        )
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def ping() -> Dict[str, Any]:
    """Health check used by /api/db/health — never raises."""
    if not is_configured():
        return {"connected": False, "detail": "DATABASE_URL is not set."}
    try:
        with _connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            missing = []
            for table in _TABLES:
                cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if cur.fetchone()[0] is None:
                    missing.append(table)
        if missing:
            return {"connected": True, "detail":
                    f"Connected, but missing table(s): {', '.join(missing)}. "
                    f"Run sql/001_create_url_tables.sql in pgAdmin."}
        return {"connected": True, "detail": "Connected. Both tables present."}
    except Exception as exc:  # noqa: BLE001 — health check must not raise
        return {"connected": False, "detail": f"{exc.__class__.__name__}: {exc}"}


def _upsert(table: str, title: str, category: str, subcategory: str,
            platform: str, url: str) -> Dict[str, Any]:
    """
    Write one platform URL onto the title's single row, creating it if needed.

    Only the target platform's column is supplied; every other column COALESCEs
    to its existing value, which is what keeps one row per title accumulating
    instead of one row per saved link.
    """
    if table not in _TABLES:
        raise ValueError(f"Unknown table: {table}")
    column = PLATFORM_COLUMNS.get(platform)
    if not column:
        raise ValueError(f"Unknown platform: {platform}")

    # Positional values: the target column carries the URL, the rest are NULL.
    url_values = [url if c == column else None for c in _ALL_URL_COLUMNS]
    set_clause = ",\n        ".join(
        f"{c} = COALESCE(EXCLUDED.{c}, {table}.{c})"
        for c in ["title_category", "title_subcategory"] + _ALL_URL_COLUMNS
    )
    sql = f"""
        INSERT INTO {table}
            (title, title_category, title_subcategory, {", ".join(_ALL_URL_COLUMNS)})
        VALUES (%s, %s, %s, {", ".join(["%s"] * len(_ALL_URL_COLUMNS))})
        ON CONFLICT (lower(title)) DO UPDATE SET
        {set_clause}
        RETURNING id, title, title_category, title_subcategory,
                  {", ".join(_ALL_URL_COLUMNS)}, updated_at
    """
    params = [title.strip(), (category or "").strip() or None,
              (subcategory or "").strip() or None, *url_values]
    with _connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    row["updated_at"] = row["updated_at"].isoformat() if row.get("updated_at") else None
    return row


def save_verified(title: str, category: str, subcategory: str,
                  platform: str, url: str) -> Dict[str, Any]:
    """Confirm a profile. Also clears it from rejected_url if it was there."""
    row = _upsert("verified_url", title, category, subcategory, platform, url)
    _clear_from(table="rejected_url", title=title, platform=platform, url=url)
    return row


def save_rejected(title: str, category: str, subcategory: str,
                  platform: str, url: str) -> Dict[str, Any]:
    """Reject a profile. Also clears it from verified_url if it was there."""
    row = _upsert("rejected_url", title, category, subcategory, platform, url)
    _clear_from(table="verified_url", title=title, platform=platform, url=url)
    return row


def _clear_from(table: str, title: str, platform: str, url: str) -> None:
    """
    Remove this exact URL from the opposite table so a title can't be both
    verified and rejected for the same link. Only clears when the stored URL
    matches — a different URL on that platform is a separate decision.
    """
    column = PLATFORM_COLUMNS.get(platform)
    if not column:
        return
    try:
        with _connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET {column} = NULL "
                f"WHERE lower(title) = lower(%s) AND {column} = %s",
                (title.strip(), url),
            )
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
        print(f"  [DB] could not clear {platform} from {table}: {exc.__class__.__name__}")


def fetch_decisions(titles: List[str]) -> Dict[str, Dict[str, Dict[str, str]]]:
    """
    Load existing decisions so the Results page can show saved state on load.

    Returns ``{lower(title): {"verified": {platform: url}, "rejected": {...}}}``.
    Returns an empty dict when unconfigured — the page still renders.
    """
    clean = [t.strip() for t in titles if t and t.strip()]
    if not clean or not is_configured():
        return {}
    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    try:
        with _connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            for table, key in (("verified_url", "verified"), ("rejected_url", "rejected")):
                cur.execute(
                    f"SELECT title, {', '.join(_ALL_URL_COLUMNS)} FROM {table} "
                    f"WHERE lower(title) = ANY(%s)",
                    ([t.lower() for t in clean],),
                )
                for row in cur.fetchall():
                    slot = out.setdefault(row["title"].lower(), {"verified": {}, "rejected": {}})
                    for platform, column in PLATFORM_COLUMNS.items():
                        if row.get(column):
                            slot[key][platform] = row[column]
    except Exception as exc:  # noqa: BLE001 — never block the page on the DB
        print(f"  [DB] fetch_decisions failed: {exc.__class__.__name__}: {exc}")
        return {}
    return out
