"""
Curator AI — Python API for the Next.js frontend.

Run (from C:\\Testing):
  pip install -r requirements.txt
  uvicorn api_server:app --host 127.0.0.1 --port 8787 --reload

Set NEXT_PUBLIC_PYTHON_API_URL=http://127.0.0.1:8787 in curator-ai/.env.local
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import (Depends, FastAPI, File, Form, Header, HTTPException, Request,
                     UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import auth_service  # noqa: E402  — after dotenv so DATABASE_URL loads
import db_service  # noqa: E402  — after dotenv so DATABASE_URL loads
import search_options  # noqa: E402
import verification_pipeline as testing  # noqa: E402  — after dotenv so keys load

_jobs_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}

# One stop-flag per job. The pipeline polls it between units of work, so a stop
# drains queued rows/platforms without killing requests that are already in
# flight — partial results are still assembled and saved.
_cancel_events: Dict[str, threading.Event] = {}


def _cancel_event(job_id: str) -> threading.Event:
    with _jobs_lock:
        event = _cancel_events.get(job_id)
        if event is None:
            event = threading.Event()
            _cancel_events[job_id] = event
        return event


def _persist(job_id: str, rows: Optional[List[dict]] = None,
             snapshot: Optional[Dict[str, Any]] = None) -> None:
    """
    Mirror a job's lifecycle state to Postgres. Progress ticks are NOT persisted.

    Pass ``snapshot`` when the caller already holds the state. Re-reading
    ``_jobs`` here would silently write nothing if the entry were evicted between
    the status change and this call — the terminal write is exactly the one that
    must not be lost.
    """
    job = snapshot if snapshot is not None else None
    if job is None:
        with _jobs_lock:
            job = dict(_jobs.get(job_id) or {})
    if job:
        db_service.save_job(job_id, job, rows)


def _finalize_job(job_id: str, out_path: str, serper_path: Optional[str],
                  rows: Optional[List[dict]] = None) -> None:
    """Mark a finished run completed — or cancelled when a stop was requested."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        was_cancelled = job_id in _cancel_events and _cancel_events[job_id].is_set()
        job["status"] = "cancelled" if was_cancelled else "completed"
        job["output_path"] = out_path
        job["serper_output_path"] = serper_path
        for entry in job["names"]:
            entry["status"] = "done"
            entry["current_platform"] = None
            entry["completed_platforms"] = list(testing.PLATFORMS.keys())
        snapshot = dict(job)          # taken under the lock, immune to eviction
    _persist(job_id, rows, snapshot=snapshot)

UPLOAD_DIR = ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Generated workbooks live in exports/ rather than the repo root, so a run never
# litters the source tree. Override with EXPORT_DIR when deploying.
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "")).resolve() if os.getenv("EXPORT_DIR") else ROOT / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@asynccontextmanager
async def _lifespan(app: FastAPI):
    paths = sorted(
        {getattr(r, "path", "") for r in app.routes if getattr(r, "path", "").startswith("/api")}
    )
    # Refuse to serve a production deployment that cannot authenticate anyone.
    # Discovering this from a log line after the fact is how an internal tool
    # ends up briefly public; a container that will not start is noticed at once.
    problems = auth_service.startup_report()
    if problems:
        for problem in problems:
            print(f"[api_server] FATAL CONFIG: {problem}")
        raise RuntimeError(
            "Refusing to start: the production configuration above would leave this "
            "API unauthenticated or unreachable. Fix the environment and redeploy."
        )

    purged = auth_service.purge_expired_sessions()
    if purged:
        print(f"[api_server] Purged {purged} expired session(s).")
    if auth_service.enforced():
        print(f"[api_server] Authentication ENFORCED ({auth_service.user_count()} account(s)).")
    elif auth_service.IS_PRODUCTION:
        print("[api_server] Authentication unavailable — requests will be REFUSED (503), "
              "never served anonymously.")
    else:
        print("[api_server] Authentication is OFF — set DATABASE_URL and create a user to enable it.")
    reaped = db_service.reap_orphaned_jobs()
    if reaped:
        print(f"[api_server] Marked {reaped} job(s) failed — orphaned by a previous restart.")
    print(f"[api_server] Registered API paths: {paths}")
    print("[api_server] Tip: use --reload so route changes apply without manual restarts.")
    yield


app = FastAPI(title="Curator AI", version="1.0.0", lifespan=_lifespan)


# CORS:
# - Local dev defaults to localhost/127.0.0.1 on any port.
# - Production should set CORS_ORIGINS to a comma-separated list, e.g.
#   https://your-app.vercel.app,https://your-custom-domain.com
_cors_origins_env = os.getenv("CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]

# Localhost is trusted ONLY outside production. Leaving the regex on in a
# deployed environment means any page a user happens to be running locally can
# call the production API with their session — an origin we never intended to
# allow and cannot audit.
_LOCALHOST_RE = r"https?://(127\.0\.0\.1|localhost)(:\d+)?"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=None if auth_service.IS_PRODUCTION else _LOCALHOST_RE,
    # Bearer tokens travel in the Authorization header, not cookies, so the API
    # never needs credentialed cross-origin requests. Off by default keeps a
    # misconfigured origin from becoming a session-riding hole.
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


class StartJobBody(BaseModel):
    names: List[str] = Field(..., min_length=1)
    # "wikipedia" (default) or "custom". See search_options for what changes.
    search_mode: str = ""
    # Custom mode: the free-text SerpApi query prompt + whether to include the
    # file's profession in the query "<name> [<profession>] <prompt>".
    prompt: str = ""
    include_profession: bool = True


def _search_options(mode: str, prompt: str,
                    include_profession: bool = True) -> search_options.SearchOptions:
    """
    Build run options from request input. A bad mode/prompt is rejected here,
    before a single row is searched.
    """
    bad_mode = search_options.validate_mode(mode)
    if bad_mode:
        raise HTTPException(status_code=400, detail=bad_mode)
    opts = search_options.from_request(mode, prompt, include_profession)
    if opts.is_custom:
        problem = search_options.validate_prompt(opts.prompt)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
    return opts


def _reset_row_platform_state(entry: Dict[str, Any]) -> None:
    entry["current_platform"] = None
    entry["completed_platforms"] = []


def _apply_row_status(job: Dict[str, Any], row_index: int, status: str) -> None:
    """Set a single row's status independently (rows run concurrently now)."""
    if row_index < 0 or row_index >= len(job["names"]):
        return
    entry = job["names"][row_index]
    entry["status"] = status
    if status == "processing":
        _reset_row_platform_state(entry)
    elif status == "done":
        entry["current_platform"] = None
        entry["completed_platforms"] = list(testing.PLATFORMS.keys())


def _apply_platform_progress(
    job: Dict[str, Any],
    row_index: int,
    platform: str,
    phase: str,
) -> None:
    if row_index < 0 or row_index >= len(job["names"]):
        return
    entry = job["names"][row_index]
    if phase == "start":
        entry["current_platform"] = platform
        return
    if phase == "done":
        completed = entry.setdefault("completed_platforms", [])
        if platform not in completed:
            completed.append(platform)


def _persist_outputs(final_df: Any) -> tuple[str, Optional[str]]:
    """Save the final workbook plus the companion Serper-only (Phase A) workbook.

    Returns (final_output_path, serper_output_path). The Serper-only frame is
    stashed on ``final_df.attrs['serper_df']`` by the pipeline.
    """
    out_path = testing.save_output(final_df, output_dir=EXPORT_DIR)
    serper_path: Optional[str] = None
    serper_df = getattr(final_df, "attrs", {}).get("serper_df")
    if serper_df is not None:
        try:
            serper_path = testing.save_output(
                serper_df, output_dir=EXPORT_DIR, filename_prefix="Talent_Social_Serper"
            )
        except Exception as exc:  # noqa: BLE001 — companion is best-effort
            print(f"[api_server] Serper companion save failed: {exc}")
    return out_path, serper_path


def _frame_to_rows(df: Any) -> List[dict]:
    """Result frame -> JSON-safe rows, so results survive without the .xlsx."""
    try:
        export = df.reindex(columns=testing.excel_service.ordered_columns())
        return [{str(c): _cell_json(row[c]) for c in export.columns}
                for _, row in export.iterrows()]
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        print(f"[api_server] could not serialise rows: {exc.__class__.__name__}")
        return []


def _run_job(job_id: str, names: List[str],
             options: search_options.SearchOptions = search_options.DEFAULT) -> None:
    def row_status(row_index: int, status: str) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                return
            _apply_row_status(job, row_index, status)

    def platform_progress(row_index: int, platform: str, phase: str) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                return
            _apply_platform_progress(job, row_index, platform, phase)

    try:
        with _jobs_lock:
            _jobs[job_id]["status"] = "running"
        _persist(job_id)

        final_df = testing.run_pipeline_for_names(
            names,
            row_status=row_status,
            platform_progress=platform_progress,
            should_cancel=_cancel_event(job_id).is_set,
            options=options,
        )

        out_path, serper_path = _persist_outputs(final_df)
        _finalize_job(job_id, out_path, serper_path, _frame_to_rows(final_df))
    except Exception as exc:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(exc)
        _persist(job_id)


def _run_job_from_file(job_id: str, path: Path,
                       options: search_options.SearchOptions = search_options.DEFAULT) -> None:
    def row_status(row_index: int, status: str) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                return
            _apply_row_status(job, row_index, status)

    def platform_progress(row_index: int, platform: str, phase: str) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                return
            _apply_platform_progress(job, row_index, platform, phase)

    try:
        with _jobs_lock:
            _jobs[job_id]["status"] = "running"
        _persist(job_id)

        df = testing.load_talent_table_from_path(path)
        final_df = testing.run_pipeline_on_dataframe(
            df,
            row_status=row_status,
            platform_progress=platform_progress,
            should_cancel=_cancel_event(job_id).is_set,
            options=options,
        )

        out_path, serper_path = _persist_outputs(final_df)
        _finalize_job(job_id, out_path, serper_path, _frame_to_rows(final_df))
    except Exception as exc:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(exc)
        _persist(job_id)
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# ── authentication ──────────────────────────────────────────────────────────
# A rejected upload costs nothing; an unbounded one can burn the whole API
# budget in a single request. Both ceilings are env-tunable.
MAX_ROWS_PER_JOB = max(1, int(os.getenv("MAX_ROWS_PER_JOB", "5000")))


def _bearer(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    parts = authorization.split(None, 1)
    return parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""


def current_user(authorization: Optional[str] = Header(default=None)) -> Optional[Dict[str, Any]]:
    """
    Resolve the caller. Returns None only when auth is genuinely not enforced.

    In production, "we cannot check credentials right now" must never mean "come
    in". If the account store is unreachable the request is refused, not served.
    """
    if auth_service.fail_closed():
        raise HTTPException(
            status_code=503,
            detail="Authentication is temporarily unavailable. Please retry shortly.",
        )
    if not auth_service.enforced():
        return None
    user = auth_service.user_for_token(_bearer(authorization))
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


def _uid(user: Optional[Dict[str, Any]]) -> Optional[int]:
    return user.get("id") if user else None


class LoginBody(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)


class GoogleLoginBody(BaseModel):
    credential: str = Field(..., min_length=10)   # Google ID token (JWT)


@app.get("/api/auth/status")
def auth_status() -> dict[str, Any]:
    """Lets the UI decide what sign-in options to show — no auth required."""
    return {
        "auth_available": auth_service.is_available(),
        "auth_required": auth_service.enforced(),
        "has_accounts": auth_service.user_count() > 0,
        "google_enabled": auth_service.google_enabled(),
        "google_client_id": auth_service.GOOGLE_CLIENT_ID,   # public by design
    }


@app.post("/api/auth/google")
def auth_google(body: GoogleLoginBody, request: Request) -> dict[str, Any]:
    """
    Sign in with a Google ID token.

    The token is verified against Google's public keys server-side; the email a
    client claims is never trusted on its own.
    """
    if not auth_service.google_enabled():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured.")
    user, reason = auth_service.google_sign_in(body.credential)
    if not user:
        raise HTTPException(status_code=401, detail=reason)
    session = auth_service.start_session(user["id"], request.headers.get("user-agent", ""))
    return {"user": user, **session}


@app.post("/api/auth/login")
def auth_login(body: LoginBody, request: Request) -> dict[str, Any]:
    if not auth_service.is_available():
        raise HTTPException(status_code=503,
                            detail="Accounts are unavailable — DATABASE_URL is not configured.")
    user = auth_service.authenticate(body.email, body.password)
    if not user:
        # Deliberately identical for unknown email and wrong password.
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    session = auth_service.start_session(user["id"], request.headers.get("user-agent", ""))
    return {"user": user, **session}


@app.post("/api/auth/logout")
def auth_logout(authorization: Optional[str] = Header(default=None)) -> dict[str, str]:
    auth_service.end_session(_bearer(authorization))
    return {"status": "signed out"}


@app.get("/api/auth/me")
def auth_me(user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    return {"user": user}


# ── history ─────────────────────────────────────────────────────────────────

@app.get("/api/history/uploads")
def history_uploads(limit: int = 100,
                    user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    return {"uploads": db_service.list_uploads(min(limit, 500))}


@app.get("/api/history/runs")
def history_runs(limit: int = 100,
                 user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    return {"runs": db_service.list_runs(min(limit, 500))}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "curator-python-api"}


@app.post("/api/jobs")
def start_job(body: StartJobBody,
               user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, str]:
    names = [n.strip() for n in body.names if n and str(n).strip()]
    if not names:
        raise HTTPException(status_code=400, detail="Provide at least one non-empty name.")
    options = _search_options(body.search_mode, body.prompt, body.include_profession)
    if len(names) > MAX_ROWS_PER_JOB:
        raise HTTPException(
            status_code=413,
            detail=f"{len(names)} names exceeds the {MAX_ROWS_PER_JOB}-row limit for a single run. "
                   f"Split the list, or raise MAX_ROWS_PER_JOB.")

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "queued",
            "names": [
                {
                    "name": n,
                    "status": "queued",
                    "current_platform": None,
                    "completed_platforms": [],
                }
                for n in names
            ],
            "output_path": None,
            "error": None,
            "started_by": _uid(user),
            "search_mode": options.mode,
            "prompt": options.prompt,
            "include_profession": options.include_profession,
        }
    _persist(job_id)

    thread = threading.Thread(target=_run_job, args=(job_id, names, options), daemon=True)
    thread.start()
    return {"job_id": job_id}


@app.post("/api/upload")
@app.post("/api/jobs/upload")
async def start_job_from_upload(file: UploadFile = File(...),
                                search_mode: str = Form(""),
                                prompt: str = Form(""),
                                include_profession: str = Form("true"),
                                user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    """
    Upload an .xlsx / .xls / .csv from the UI. Rows are parsed like Demo_Social.xlsx
    (Talent Name + optional category columns). No need to copy the file into the repo folder.

    Custom (non-Wikipedia) mode form fields:
      * ``search_mode`` — "wikipedia" (default) or "custom".
      * ``prompt`` — free-text SerpApi query suffix ("<name> [<profession>] <prompt>").
      * ``include_profession`` — "true"/"false"; include the file's profession in the query.

    Use POST /api/upload or POST /api/jobs/upload (both work). The /api/jobs/upload path is
    registered as POST-only before GET /api/jobs/{job_id}, so it does not collide with the
    dynamic route.
    """
    include_prof = str(include_profession).strip().lower() not in ("false", "0", "no", "")
    options = _search_options(search_mode, prompt, include_prof)
    raw_name = (file.filename or "upload").strip()
    lower = raw_name.lower()
    if not (lower.endswith(".xlsx") or lower.endswith(".xls") or lower.endswith(".csv")):
        raise HTTPException(
            status_code=400,
            detail="Upload a .xlsx, .xls, or .csv file.",
        )

    job_id = str(uuid.uuid4())
    suffix = Path(raw_name).suffix or ".xlsx"
    dest = UPLOAD_DIR / f"{job_id}{suffix}"

    body = await file.read()
    if len(body) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB).")

    dest.write_bytes(body)

    try:
        df = testing.load_talent_table_from_path(dest)
    except ValueError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    names = [str(x).strip() for x in df["Talent Name"].tolist()]
    if not names:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="No talent names found in file.")
    if len(names) > MAX_ROWS_PER_JOB:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=f"{len(names)} rows exceeds the {MAX_ROWS_PER_JOB}-row limit for a single run. "
                   f"Split the file, or raise MAX_ROWS_PER_JOB.")

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "queued",
            "names": [
                {
                    "name": n,
                    "status": "queued",
                    "current_platform": None,
                    "completed_platforms": [],
                }
                for n in names
            ],
            "output_path": None,
            "error": None,
            "source_filename": raw_name,
            "started_by": _uid(user),
            "search_mode": options.mode,
            "prompt": options.prompt,
            "include_profession": options.include_profession,
        }
    _persist(job_id)
    db_service.record_upload(job_id, raw_name, len(body), len(names), _uid(user))

    thread = threading.Thread(target=_run_job_from_file, args=(job_id, dest, options), daemon=True)
    thread.start()

    return {
        "job_id": job_id,
        "names": names,
        "row_count": len(names),
        "source_filename": raw_name,
        "search_mode": options.mode,
    }


def _latest_lookup_paths() -> List[Path]:
    paths = sorted(
        EXPORT_DIR.glob("Talent_Social_Lookup_*.xlsx"),
        key=lambda p: p.name,
        reverse=True,
    )
    return [p for p in paths if not p.name.startswith(".~")]


def _cell_json(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return float(v)
    if isinstance(v, bool):
        return v
    return str(v).strip()


def _latest_serper_paths() -> List[Path]:
    paths = sorted(
        EXPORT_DIR.glob("Talent_Social_Serper_*.xlsx"),
        key=lambda p: p.name,
        reverse=True,
    )
    return [p for p in paths if not p.name.startswith(".~")]


def _resolve_result_paths(job_id: Optional[str], output_key: str, fallback):
    """
    Decide which workbook(s) to read.

    If ``job_id`` is given, serve THAT job's specific output (so viewing results
    right after a new upload can never show a previous run's file). Returns
    ``(paths, pending)`` — ``pending`` is True when the job exists but its output
    isn't written yet (so the UI shows "processing", not stale data). With no
    ``job_id`` (or an unknown one), fall back to the newest file on disk.
    """
    if job_id:
        with _jobs_lock:
            job = _jobs.get(job_id)
            path_str = job.get(output_key) if job else None
        if path_str:
            p = Path(path_str)
            return ([p] if p.is_file() else []), False
        if job is not None:
            return [], True  # job exists but this output isn't ready yet
    return fallback(), False


def _read_rows_response(paths: List[Path]) -> dict[str, Any]:
    """Read the first readable workbook in ``paths`` to JSON rows (with retries)."""
    if not paths:
        return {"rows": [], "filename": None, "warning": None, "error": None}

    skipped: List[str] = []
    last_err: Optional[str] = None
    for p in paths:
        for _attempt in range(8):
            try:
                df = pd.read_excel(p)
                records: List[Dict[str, Any]] = []
                for _, row in df.iterrows():
                    rec = {str(c): _cell_json(row[c]) for c in df.columns}
                    records.append(rec)
                warning = (
                    f"Newer file(s) were busy; showing data from {p.name}."
                    if skipped
                    else None
                )
                return {"rows": records, "filename": p.name, "warning": warning, "error": None}
            except Exception as exc:
                last_err = str(exc)
                time.sleep(0.35)
        skipped.append(p.name)

    return {
        "rows": [],
        "filename": paths[0].name if paths else None,
        "warning": None,
        "error": last_err or "Could not read any workbook.",
    }


@app.get("/api/results/latest")
def api_results_latest(job_id: Optional[str] = None,
                       user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    """
    Final results as JSON. When ``job_id`` is supplied, serves that job's own
    output (never an older run); otherwise the newest Talent_Social_Lookup_*.xlsx.
    """
    stored = db_service.load_job_rows(job_id) if job_id else None
    if stored:
        return {"rows": stored, "filename": None, "warning": None,
                "error": None, "source": "database"}
    paths, pending = _resolve_result_paths(job_id, "output_path", _latest_lookup_paths)
    if pending:
        return {"rows": [], "filename": None, "warning": None, "error": None, "pending": True}
    return _read_rows_response(paths)


@app.get("/api/results/serper/latest")
def api_results_serper_latest(job_id: Optional[str] = None,
                              user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    """
    Serper-only (Phase A) results — what Serper + LLM produced BEFORE the Apify
    backup / cross-platform corroboration. Same schema as /api/results/latest.
    """
    paths, pending = _resolve_result_paths(job_id, "serper_output_path", _latest_serper_paths)
    if pending:
        return {"rows": [], "filename": None, "warning": None, "error": None, "pending": True}
    return _read_rows_response(paths)


def _rebuild_export_from_db(job_id: Optional[str]) -> Optional[Path]:
    """
    Regenerate a run's workbook from the rows persisted in Postgres.

    Local disk cannot be the system of record once this runs on more than one
    container: uploads and exports written by instance A simply do not exist on
    instance B, and both vanish on the next deploy. The job row already carries
    every result cell as JSONB, so a download is always reproducible.
    """
    if not job_id:
        return None
    try:
        rows = db_service.load_job_rows(job_id)
        if not rows:
            return None
        frame = pd.DataFrame(rows)
        return testing.excel_service.save_results(frame, output_dir=EXPORT_DIR)
    except Exception as exc:  # noqa: BLE001 — a failed rebuild is a 404, not a 500
        print(f"[api_server] export rebuild failed for {job_id}: "
              f"{exc.__class__.__name__}: {exc}")
        return None


@app.get("/api/export/latest")
def api_export_latest(job_id: Optional[str] = None,
                      user: Optional[Dict[str, Any]] = Depends(current_user)) -> FileResponse:
    """Download the newest export file (for Open in browser / save as)."""
    paths, pending = _resolve_result_paths(job_id, "output_path", _latest_lookup_paths)
    if pending:
        raise HTTPException(status_code=409, detail="That run is still processing.")
    if not paths:
        # Nothing on this container's disk. On AWS that is routine, not an error:
        # the filesystem is ephemeral and the instance that ran the job is often
        # not the one serving the download. Postgres holds the rows, so rebuild
        # the workbook rather than telling the analyst their results are gone.
        rebuilt = _rebuild_export_from_db(job_id)
        if rebuilt:
            return FileResponse(
                path=str(rebuilt), filename=rebuilt.name,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        raise HTTPException(status_code=404, detail="No export available for that request.")
    last_err: Optional[str] = None
    for p in paths:
        for _attempt in range(5):
            try:
                if not p.is_file():
                    break
                return FileResponse(
                    path=str(p),
                    filename=p.name,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception as exc:
                last_err = str(exc)
                time.sleep(0.3)
    raise HTTPException(
        status_code=503,
        detail=last_err or "Export file is locked or unreadable. Close it in Excel and try again.",
    )


class DecisionBody(BaseModel):
    """One analyst decision about one profile link."""
    title: str = Field(..., min_length=1)
    platform: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    title_category: str = ""
    title_subcategory: str = ""


class DecisionLookupBody(BaseModel):
    titles: List[str] = Field(default_factory=list)


@app.get("/api/db/health")
def db_health(user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    """
    Is the decision database reachable and are both tables present?

    Authenticated: the response names our tables and reports connection errors,
    which is useful to an analyst and equally useful to someone probing the
    deployment. /api/health stays open for the load balancer.
    """
    return {"configured": db_service.is_configured(), **db_service.ping()}


def _record(save, body: DecisionBody, kind: str) -> dict[str, Any]:
    if not db_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL is not set. Add it to .env and restart the API.",
        )
    if body.platform not in db_service.PLATFORM_COLUMNS:
        raise HTTPException(status_code=400, detail=f"Unknown platform '{body.platform}'.")
    try:
        row = save(body.title, body.title_category, body.title_subcategory,
                   body.platform, body.url)
    except Exception as exc:  # noqa: BLE001 — surface the cause, don't 500 blindly
        raise HTTPException(status_code=502, detail=f"{exc.__class__.__name__}: {exc}") from exc
    return {"ok": True, "kind": kind, "row": row}


@app.post("/api/decisions/verify")
def decision_verify(body: DecisionBody,
                    user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    """Save a confirmed profile URL onto the title's single verified_url row."""
    return _record(db_service.save_verified, body, "verified")


@app.post("/api/decisions/reject")
def decision_reject(body: DecisionBody,
                    user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    """Save a rejected profile URL onto the title's single rejected_url row."""
    return _record(db_service.save_rejected, body, "rejected")


@app.post("/api/decisions/lookup")
def decision_lookup(body: DecisionLookupBody,
                    user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    """Existing decisions for these titles, so the UI can show saved state."""
    return {"decisions": db_service.fetch_decisions(body.titles)}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str,
               user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    """
    Ask a running job to stop.

    Cooperative, not a kill: queued rows/platforms are skipped, in-flight API
    calls are allowed to finish, and whatever was already verified is assembled
    and saved. The job then reports status "cancelled" with a usable workbook,
    so a stopped run is never a wasted run.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown job_id.")
        terminal = job["status"] in ("completed", "failed", "cancelled")
        if not terminal:
            _cancel_events.setdefault(job_id, threading.Event()).set()
            job["status"] = "cancelling"
        status = job["status"]
    return {"job_id": job_id, "status": status, "cancel_requested": not terminal}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str,
            user: Optional[Dict[str, Any]] = Depends(current_user)) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        # Not in memory: either this process restarted, or another instance owns
        # it. Postgres is the durable record, so try there before 404-ing.
        job = db_service.load_job(job_id)
        if job:
            return {"job_id": job_id, "restored_from_db": True, **job}
        raise HTTPException(status_code=404, detail="Unknown job_id.")
    return {"job_id": job_id, **job}
