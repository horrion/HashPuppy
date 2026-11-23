"""Single-threaded background job worker."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from . import db
from .compare import JobCancelled, compare_files, compare_folders
from .copier import copy_and_verify
from .paths import resolve_within_data

log = logging.getLogger("hashpuppy.worker")

_stop = threading.Event()
_thread: threading.Thread | None = None
_wake = threading.Event()


def wake() -> None:
    """Nudge the worker to look for queued jobs immediately."""
    _wake.set()


def start_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    recovered = db.recover_interrupted()
    if recovered:
        log.warning("Marked %s interrupted job(s) as error", recovered)
    _thread = threading.Thread(target=_loop, name="hashpuppy-worker", daemon=True)
    _thread.start()
    log.info("Job worker started")


def stop_worker() -> None:
    _stop.set()
    _wake.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)


def _loop() -> None:
    while not _stop.is_set():
        job = db.claim_next_job()
        if job is None:
            _wake.wait(timeout=1.0)
            _wake.clear()
            continue
        try:
            _run_job(job)
        except Exception:  # noqa: BLE001
            log.exception("Unhandled error in job %s", job.get("id"))
            try:
                db.finish_job(
                    int(job["id"]),
                    status="error",
                    error="Unhandled worker error",
                )
            except Exception:  # noqa: BLE001
                log.exception("Failed to mark job %s as error", job.get("id"))


def _run_job(job: dict[str, Any]) -> None:
    job_id = int(job["id"])
    kind = job["kind"]
    algorithm = job["algorithm"]
    params = job.get("params") or {}

    last_write = 0.0

    def is_cancelled() -> bool:
        return db.is_cancel_requested(job_id)

    def on_progress(path: str | None, done: int, total: int) -> None:
        nonlocal last_write
        now = time.monotonic()
        # Throttle DB writes a bit, but always write on completion of a phase.
        if now - last_write < 0.25 and done < total:
            if is_cancelled():
                raise JobCancelled()
            return
        last_write = now
        db.update_progress(
            job_id,
            done_files=done,
            total_files=total,
            current_path=path,
        )
        if is_cancelled():
            raise JobCancelled()

    try:
        source = resolve_within_data(job["source"])
        target = resolve_within_data(job["target"])

        if kind in ("file", "folder"):
            result = _run_compare(kind, source, target, algorithm, on_progress, is_cancelled)
        elif kind == "copy":
            result = copy_and_verify(
                source,
                target,
                algorithm,
                bool(params.get("overwrite", False)),
                on_progress=on_progress,
                is_cancelled=is_cancelled,
            )
        else:
            raise ValueError(f"Unknown job kind: {kind}")

        if is_cancelled():
            raise JobCancelled()

        db.finish_job(
            job_id,
            status="ok",
            summary=result.get("summary") or {},
            result=result,
        )
        log.info("Job %s finished ok", job_id)

    except JobCancelled:
        db.finish_job(
            job_id,
            status="cancelled",
            error="Cancelled by user",
        )
        log.info("Job %s cancelled", job_id)
    except FileExistsError as exc:
        db.finish_job(job_id, status="error", error=str(exc))
        log.info("Job %s failed: %s", job_id, exc)
    except (OSError, ValueError, FileNotFoundError) as exc:
        db.finish_job(job_id, status="error", error=str(exc))
        log.info("Job %s failed: %s", job_id, exc)
    except Exception as exc:  # noqa: BLE001
        db.finish_job(job_id, status="error", error=str(exc))
        log.exception("Job %s failed", job_id)


def _run_compare(
    kind: str,
    source: Path,
    target: Path,
    algorithm: str,
    on_progress,
    is_cancelled,
) -> dict[str, Any]:
    if kind == "file":
        if not source.is_file() or not target.is_file():
            raise ValueError("Both paths must be files")
        return compare_files(
            source,
            target,
            algorithm,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
        )
    if not source.is_dir() or not target.is_dir():
        raise ValueError("Both paths must be folders")
    return compare_folders(
        source,
        target,
        algorithm,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
    )
