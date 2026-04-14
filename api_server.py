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
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import testing  # noqa: E402  — after dotenv so keys load

_jobs_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}

UPLOAD_DIR = ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@asynccontextmanager
async def _lifespan(app: FastAPI):
    paths = sorted(
        {getattr(r, "path", "") for r in app.routes if getattr(r, "path", "").startswith("/api")}
    )
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # Keep localhost enabled even when production CORS_ORIGINS is set.
    allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartJobBody(BaseModel):
    names: List[str] = Field(..., min_length=1)


def _run_job(job_id: str, names: List[str]) -> None:
    def progress(i: int, total: int, talent_name: str) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                return
            for k, entry in enumerate(job["names"]):
                if k < i - 1:
                    entry["status"] = "done"
                elif k == i - 1:
                    entry["status"] = "processing"
                else:
                    entry["status"] = "queued"

    try:
        with _jobs_lock:
            _jobs[job_id]["status"] = "running"

        final_df = testing.run_pipeline_for_names(names, progress=progress)

        out_path = testing.save_output(final_df, output_dir=ROOT)

        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "completed"
                job["output_path"] = out_path
                for entry in job["names"]:
                    entry["status"] = "done"
    except Exception as exc:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(exc)


def _run_job_from_file(job_id: str, path: Path) -> None:
    def progress(i: int, total: int, talent_name: str) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                return
            for k, entry in enumerate(job["names"]):
                if k < i - 1:
                    entry["status"] = "done"
                elif k == i - 1:
                    entry["status"] = "processing"
                else:
                    entry["status"] = "queued"

    try:
        with _jobs_lock:
            _jobs[job_id]["status"] = "running"

        df = testing.load_talent_table_from_path(path)
        final_df = testing.run_pipeline_on_dataframe(df, progress=progress)

        out_path = testing.save_output(final_df, output_dir=ROOT)

        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "completed"
                job["output_path"] = out_path
                for entry in job["names"]:
                    entry["status"] = "done"
    except Exception as exc:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(exc)
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "curator-python-api"}


@app.post("/api/jobs")
def start_job(body: StartJobBody) -> dict[str, str]:
    names = [n.strip() for n in body.names if n and str(n).strip()]
    if not names:
        raise HTTPException(status_code=400, detail="Provide at least one non-empty name.")

    if not testing.SERPER_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="SERPER_API_KEY is not set in environment (.env).",
        )

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "queued",
            "names": [{"name": n, "status": "queued"} for n in names],
            "output_path": None,
            "error": None,
        }

    thread = threading.Thread(target=_run_job, args=(job_id, names), daemon=True)
    thread.start()
    return {"job_id": job_id}


@app.post("/api/upload")
@app.post("/api/jobs/upload")
async def start_job_from_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """
    Upload an .xlsx / .xls / .csv from the UI. Rows are parsed like Demo_Social.xlsx
    (Talent Name + optional category columns). No need to copy the file into the repo folder.

    Use POST /api/upload or POST /api/jobs/upload (both work). The /api/jobs/upload path is
    registered as POST-only before GET /api/jobs/{job_id}, so it does not collide with the
    dynamic route.
    """
    if not testing.SERPER_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="SERPER_API_KEY is not set in environment (.env).",
        )

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

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "queued",
            "names": [{"name": n, "status": "queued"} for n in names],
            "output_path": None,
            "error": None,
            "source_filename": raw_name,
        }

    thread = threading.Thread(target=_run_job_from_file, args=(job_id, dest), daemon=True)
    thread.start()

    return {
        "job_id": job_id,
        "names": names,
        "row_count": len(names),
        "source_filename": raw_name,
    }


def _latest_lookup_paths() -> List[Path]:
    paths = sorted(
        ROOT.glob("Talent_Social_Lookup_*.xlsx"),
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


@app.get("/api/results/latest")
def api_results_latest() -> dict[str, Any]:
    """
    Read the newest Talent_Social_Lookup_*.xlsx as JSON (retries + older files if locked).
    Next.js Results page calls this when NEXT_PUBLIC_PYTHON_API_URL is set.
    """
    paths = _latest_lookup_paths()
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
                return {
                    "rows": records,
                    "filename": p.name,
                    "warning": warning,
                    "error": None,
                }
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


@app.get("/api/export/latest")
def api_export_latest() -> FileResponse:
    """Download the newest export file (for Open in browser / save as)."""
    paths = _latest_lookup_paths()
    if not paths:
        raise HTTPException(status_code=404, detail="No Talent_Social_Lookup_*.xlsx in export folder.")
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


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job_id.")
    return {"job_id": job_id, **job}
