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

import json
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


# ────────────────────────────────────────────────────────────────────────────
#  Job persistence
#
#  Progress ticks stay in memory (they fire per row per platform and would make
#  the DB the bottleneck). Postgres is written at lifecycle boundaries only —
#  created, running, finished — plus the final result rows. That is enough for a
#  job to survive a restart without turning every progress update into a network
#  round trip.
# ────────────────────────────────────────────────────────────────────────────

def save_job(job_id: str, job: Dict[str, Any], rows: Optional[List[dict]] = None) -> None:
    """Upsert a job's lifecycle state. Never raises — persistence is best-effort."""
    if not is_configured():
        return
    try:
        with _connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job (id, status, source_filename, names, rows,
                                 output_path, serper_output_path, error)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    status             = EXCLUDED.status,
                    source_filename    = COALESCE(EXCLUDED.source_filename, job.source_filename),
                    names              = EXCLUDED.names,
                    rows               = COALESCE(EXCLUDED.rows, job.rows),
                    output_path        = COALESCE(EXCLUDED.output_path, job.output_path),
                    serper_output_path = COALESCE(EXCLUDED.serper_output_path, job.serper_output_path),
                    error              = COALESCE(EXCLUDED.error, job.error)
                """,
                (job_id, job.get("status", "queued"), job.get("source_filename"),
                 json.dumps(job.get("names", [])),
                 json.dumps(rows) if rows is not None else None,
                 job.get("output_path"), job.get("serper_output_path"), job.get("error")),
            )
    except Exception as exc:  # noqa: BLE001
        print(f"  [DB] save_job({job_id[:8]}…) failed: {exc.__class__.__name__}: {exc}")


def load_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Rehydrate a job the in-memory store no longer has (e.g. after a restart)."""
    if not is_configured():
        return None
    try:
        with _connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT status, source_filename, names, output_path, "
                "serper_output_path, error FROM job WHERE id = %s", (job_id,))
            row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        print(f"  [DB] load_job failed: {exc.__class__.__name__}")
        return None


def load_job_rows(job_id: str) -> Optional[List[dict]]:
    """The stored result rows for a job — lets Results render with no .xlsx present."""
    if not is_configured():
        return None
    try:
        with _connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT rows FROM job WHERE id = %s", (job_id,))
            row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception as exc:  # noqa: BLE001
        print(f"  [DB] load_job_rows failed: {exc.__class__.__name__}")
        return None


def reap_orphaned_jobs() -> int:
    """
    Fail any job left mid-flight by a previous process. Called at startup: the
    thread that owned it is gone, so it will never progress, and leaving it
    'running' shows the UI a spinner that never resolves.
    """
    if not is_configured():
        return 0
    try:
        with _connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE job SET status = 'failed', "
                "error = COALESCE(error, 'Interrupted by a server restart.') "
                "WHERE status IN ('queued', 'running', 'cancelling')")
            return cur.rowcount or 0
    except Exception as exc:  # noqa: BLE001
        print(f"  [DB] reap_orphaned_jobs failed: {exc.__class__.__name__}")
        return 0


# ────────────────────────────────────────────────────────────────────────────
#  History — uploads and runs
# ────────────────────────────────────────────────────────────────────────────

def record_upload(job_id: str, filename: str, size_bytes: int,
                  row_count: int, user_id: Optional[int]) -> None:
    """Log an uploaded file. Best-effort: never block an upload on bookkeeping."""
    if not is_configured():
        return
    try:
        with _connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO upload_history (job_id, filename, size_bytes, row_count, uploaded_by) "
                "VALUES (%s, %s, %s, %s, %s)",
                (job_id, filename, size_bytes, row_count, user_id))
    except Exception as exc:  # noqa: BLE001
        print(f"  [DB] record_upload failed: {exc.__class__.__name__}")


def list_uploads(limit: int = 100) -> List[dict]:
    """Upload history, newest first, with the uploader's name resolved."""
    if not is_configured():
        return []
    try:
        with _connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT h.id, h.job_id, h.filename, h.size_bytes, h.row_count,
                       h.created_at, u.email AS uploaded_by_email, u.name AS uploaded_by_name,
                       j.status AS job_status
                FROM upload_history h
                LEFT JOIN app_user u ON u.id = h.uploaded_by
                LEFT JOIN job j      ON j.id = h.job_id
                ORDER BY h.created_at DESC LIMIT %s
                """, (limit,))
            return [_jsonable(dict(r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        print(f"  [DB] list_uploads failed: {exc.__class__.__name__}")
        return []


def list_runs(limit: int = 100) -> List[dict]:
    """
    Run history with a per-run status tally, computed in SQL.

    The counts come from the stored `rows` JSONB, so a run's summary survives
    even if its .xlsx has been deleted.
    """
    if not is_configured():
        return []
    try:
        with _connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT j.id, j.status, j.source_filename, j.created_at, j.updated_at,
                       j.output_path, j.error,
                       jsonb_array_length(COALESCE(j.names, '[]'::jsonb)) AS row_count,
                       CASE WHEN j.rows IS NULL THEN 0
                            ELSE jsonb_array_length(j.rows) END              AS result_rows,
                       u.email AS started_by_email, u.name AS started_by_name
                FROM job j
                LEFT JOIN app_user u ON u.id = j.started_by
                ORDER BY j.created_at DESC LIMIT %s
                """, (limit,))
            return [_jsonable(dict(r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        print(f"  [DB] list_runs failed: {exc.__class__.__name__}")
        return []


def _jsonable(row: dict) -> dict:
    """Timestamps -> ISO strings so FastAPI can serialise the row directly."""
    for k, v in list(row.items()):
        if hasattr(v, "isoformat"):
            row[k] = v.isoformat()
    return row


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
