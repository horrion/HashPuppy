"""HashPuppy FastAPI application."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, db
from .config import ALLOWED_ALGORITHMS, DATA_DIR, DEFAULT_ALGORITHM
from .hashing import validate_algorithm
from .paths import UnsafePathError, resolve_within_data, to_rel
from .worker import start_worker, stop_worker, wake

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="HashPuppy", version=__version__)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    start_worker()


@app.on_event("shutdown")
def _shutdown() -> None:
    stop_worker()


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class CompareJobRequest(BaseModel):
    mode: Literal["file", "folder"]
    path_a: str
    path_b: str
    algorithm: str = Field(default=DEFAULT_ALGORITHM)


class CopyJobRequest(BaseModel):
    source: str
    dest: str
    algorithm: str = Field(default=DEFAULT_ALGORITHM)
    overwrite: bool = False


class ReorderRequest(BaseModel):
    ordered_ids: list[int]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _resolve(rel: str) -> Path:
    try:
        return resolve_within_data(rel)
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_algo(algorithm: str) -> str:
    try:
        return validate_algorithm(algorithm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Meta / browse
# --------------------------------------------------------------------------- #
@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "version": __version__,
        "algorithms": list(ALLOWED_ALGORITHMS),
        "default_algorithm": DEFAULT_ALGORITHM,
        "data_dir": str(DATA_DIR),
    }


@app.get("/api/browse")
def browse(path: str = "") -> dict[str, Any]:
    target = _resolve(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    entries = []
    try:
        raw_items = list(target.iterdir())
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    def _sort_key(p: Path) -> tuple[bool, str]:
        try:
            return (p.is_file(), p.name.lower())
        except OSError:
            return (True, p.name.lower())

    for item in sorted(raw_items, key=_sort_key):
        try:
            is_dir = item.is_dir()
            size = item.stat().st_size if item.is_file() else None
        except OSError:
            # Skip broken mount points / unreadable entries instead of failing
            # the whole listing (common on NAS bind mounts).
            continue
        try:
            rel = to_rel(item)
        except UnsafePathError:
            continue
        entries.append(
            {
                "name": item.name,
                "rel": rel,
                "is_dir": is_dir,
                "size": size,
            }
        )

    parent = "" if target == DATA_DIR else to_rel(target.parent)
    return {"path": to_rel(target), "parent": parent, "entries": entries}


# --------------------------------------------------------------------------- #
# Jobs (async queue)
# --------------------------------------------------------------------------- #
@app.get("/api/jobs")
def jobs_list() -> list[dict[str, Any]]:
    return db.list_jobs()


@app.post("/api/jobs/compare")
def jobs_enqueue_compare(req: CompareJobRequest) -> dict[str, Any]:
    algorithm = _validate_algo(req.algorithm)
    path_a = _resolve(req.path_a)
    path_b = _resolve(req.path_b)

    for p in (path_a, path_b):
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"Path not found: {to_rel(p)}")

    if req.mode == "file":
        if not path_a.is_file() or not path_b.is_file():
            raise HTTPException(status_code=400, detail="Both paths must be files")
    else:
        if not path_a.is_dir() or not path_b.is_dir():
            raise HTTPException(status_code=400, detail="Both paths must be folders")

    job_id = db.enqueue_job(
        kind=req.mode,
        algorithm=algorithm,
        source=to_rel(path_a),
        target=to_rel(path_b),
    )
    wake()
    job = db.get_job(job_id, include_result=False)
    return job or {"id": job_id, "status": "queued"}


@app.post("/api/jobs/copy")
def jobs_enqueue_copy(req: CopyJobRequest) -> dict[str, Any]:
    algorithm = _validate_algo(req.algorithm)
    source = _resolve(req.source)
    dest = _resolve(req.dest)

    if not source.exists():
        raise HTTPException(status_code=404, detail=f"Source not found: {to_rel(source)}")

    job_id = db.enqueue_job(
        kind="copy",
        algorithm=algorithm,
        source=to_rel(source),
        target=to_rel(dest),
        params={"overwrite": req.overwrite},
    )
    wake()
    job = db.get_job(job_id, include_result=False)
    return job or {"id": job_id, "status": "queued"}


@app.put("/api/jobs/queue")
def jobs_reorder(req: ReorderRequest) -> list[dict[str, Any]]:
    try:
        db.reorder_queue(req.ordered_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    wake()
    return db.list_jobs()


@app.post("/api/jobs/{job_id}/cancel")
def jobs_cancel(job_id: int) -> dict[str, Any]:
    prev = db.cancel_job(job_id)
    if prev is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if prev in ("ok", "error", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Job already finished ({prev})")
    wake()
    job = db.get_job(job_id, include_result=False)
    return job or {"id": job_id, "status": "cancelled"}


@app.get("/api/jobs/{job_id}")
def jobs_detail(job_id: int) -> dict[str, Any]:
    job = db.get_job(job_id, include_result=True)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.delete("/api/jobs/{job_id}")
def jobs_delete(job_id: int) -> dict[str, Any]:
    try:
        if not db.delete_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": job_id}


@app.delete("/api/jobs")
def jobs_clear_finished() -> dict[str, Any]:
    return {"deleted": db.clear_finished()}


# --------------------------------------------------------------------------- #
# Legacy aliases (enqueue instead of blocking)
# --------------------------------------------------------------------------- #
@app.post("/api/compare")
def compare_legacy(req: CompareJobRequest) -> dict[str, Any]:
    return jobs_enqueue_compare(req)


@app.post("/api/copy")
def copy_legacy(req: CopyJobRequest) -> dict[str, Any]:
    return jobs_enqueue_copy(req)


@app.get("/api/history")
def history_legacy() -> list[dict[str, Any]]:
    return db.list_jobs()


@app.get("/api/history/{run_id}")
def history_detail_legacy(run_id: int) -> dict[str, Any]:
    return jobs_detail(run_id)


@app.delete("/api/history/{run_id}")
def history_delete_legacy(run_id: int) -> dict[str, Any]:
    return jobs_delete(run_id)


@app.delete("/api/history")
def history_clear_legacy() -> dict[str, Any]:
    return jobs_clear_finished()


# --------------------------------------------------------------------------- #
# Static frontend
# --------------------------------------------------------------------------- #
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
