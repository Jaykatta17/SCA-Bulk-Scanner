# SPDX-License-Identifier: Apache-2.0
"""FastAPI app: scan submission API, history API, and static frontend."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from . import config, db
from .models import HealthResponse, ScanCreateRequest, ScanListResponse, ScanRecord, ScanStatus
from .scanner import ScanError, ScanInputs, run_scan

logger = logging.getLogger("sca_scanner")

_queue: "asyncio.Queue[ScanInputs]" = asyncio.Queue()
_workers: list[asyncio.Task] = []


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_record(doc: dict) -> ScanRecord:
    doc = dict(doc)
    doc.pop("_id", None)
    doc.pop("git_token", None)  # defense in depth: never stored, but never echoed either
    doc["report_available"] = bool(doc.get("report_filename"))
    return ScanRecord(**doc)


def _update_scan(scan_id: str, **fields) -> None:
    try:
        db.get_scans_collection().update_one({"scan_id": scan_id}, {"$set": fields})
    except PyMongoError:
        logger.exception("Failed to update scan %s", scan_id)


def _execute_scan(inputs: ScanInputs) -> None:
    """Runs on a worker thread: blocking clone/scan pipeline + status updates."""
    started_at = _now()
    _update_scan(inputs.scan_id, status=ScanStatus.CLONING.value, started_at=started_at)

    def on_status(status: str) -> None:
        _update_scan(inputs.scan_id, status=status)

    try:
        outcome = run_scan(inputs, on_status)
    except ScanError as exc:
        completed_at = _now()
        _update_scan(
            inputs.scan_id,
            status=ScanStatus.FAILED.value,
            error_message=str(exc),
            completed_at=completed_at,
            duration_seconds=(completed_at - started_at).total_seconds(),
        )
        return
    except Exception:
        logger.exception("Unexpected error scanning %s", inputs.scan_id)
        completed_at = _now()
        _update_scan(
            inputs.scan_id,
            status=ScanStatus.FAILED.value,
            error_message="Internal error during scan; check server logs.",
            completed_at=completed_at,
            duration_seconds=(completed_at - started_at).total_seconds(),
        )
        return

    completed_at = _now()
    _update_scan(
        inputs.scan_id,
        status=ScanStatus.COMPLETED.value,
        commit_resolved=outcome.commit_resolved,
        report_filename=outcome.report_filename,
        severity_counts=outcome.severity_counts,
        completed_at=completed_at,
        duration_seconds=(completed_at - started_at).total_seconds(),
    )


async def _worker_loop() -> None:
    while True:
        inputs = await _queue.get()
        try:
            await asyncio.to_thread(_execute_scan, inputs)
        finally:
            _queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(db.ensure_indexes)
    for _ in range(max(1, config.MAX_CONCURRENT_SCANS)):
        _workers.append(asyncio.create_task(_worker_loop()))
    yield
    for task in _workers:
        task.cancel()


app = FastAPI(title="SCA Bulk Scanner", lifespan=lifespan)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    try:
        await asyncio.to_thread(db.get_client().admin.command, "ping")
        mongo_ok = True
    except PyMongoError:
        mongo_ok = False
    return HealthResponse(status="ok" if mongo_ok else "degraded", mongo=mongo_ok)


@app.post("/api/scans", response_model=ScanRecord, status_code=201)
async def create_scan(payload: ScanCreateRequest) -> ScanRecord:
    scan_id = uuid.uuid4().hex
    doc = {
        "scan_id": scan_id,
        "project_name": payload.project_name,
        "repo_url": payload.repo_url,
        "branch": payload.branch,
        "author": payload.author,
        "assessment_type": payload.assessment_type or "Initial",
        "commit_id_requested": payload.commit_id,
        "commit_resolved": None,
        "status": ScanStatus.QUEUED.value,
        "error_message": None,
        "severity_counts": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
        "report_filename": None,
        "used_git_token": bool(payload.git_token),
        "created_at": _now(),
        "started_at": None,
        "completed_at": None,
        "duration_seconds": None,
    }
    try:
        await asyncio.to_thread(db.get_scans_collection().insert_one, doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=500, detail="scan_id collision, please retry") from exc
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    inputs = ScanInputs(
        scan_id=scan_id,
        project_name=payload.project_name,
        repo_url=payload.repo_url,
        branch=payload.branch,
        commit_id=payload.commit_id,
        git_token=payload.git_token,
    )
    await _queue.put(inputs)
    return _to_record(doc)


@app.get("/api/scans", response_model=ScanListResponse)
async def list_scans(
    limit: int = Query(default=25, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
    status: Optional[ScanStatus] = Query(default=None),
    q: Optional[str] = Query(default=None, max_length=200),
) -> ScanListResponse:
    filt: dict = {}
    if status:
        filt["status"] = status.value
    if q:
        filt["project_name"] = {"$regex": re.escape(q), "$options": "i"}

    def _query():
        coll = db.get_scans_collection()
        total = coll.count_documents(filt)
        items = list(coll.find(filt).sort("created_at", DESCENDING).skip(skip).limit(limit))
        return total, items

    total, items = await asyncio.to_thread(_query)
    return ScanListResponse(total=total, items=[_to_record(i) for i in items])


@app.get("/api/scans/{scan_id}", response_model=ScanRecord)
async def get_scan(scan_id: str) -> ScanRecord:
    doc = await asyncio.to_thread(db.get_scans_collection().find_one, {"scan_id": scan_id})
    if not doc:
        raise HTTPException(status_code=404, detail="scan not found")
    return _to_record(doc)


@app.get("/api/scans/{scan_id}/report")
async def get_report(scan_id: str) -> FileResponse:
    doc = await asyncio.to_thread(db.get_scans_collection().find_one, {"scan_id": scan_id})
    if not doc or not doc.get("report_filename"):
        raise HTTPException(status_code=404, detail="report not available")
    report_path = config.REPORTS_DIR / doc["report_filename"]
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="report file missing on disk")
    return FileResponse(report_path, media_type="text/html")


# Static frontend (built into the image at ./app/static — see webapp/backend/Dockerfile).
# Mounted last so it never shadows the /api/* routes above.
_frontend_dir = Path(__file__).parent / "static"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
