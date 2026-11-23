"""SQLite-backed job queue and history."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .config import DB_PATH

_lock = threading.Lock()

TERMINAL = frozenset({"ok", "error", "cancelled"})
ACTIVE = frozenset({"queued", "running"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _row_to_job(row: sqlite3.Row, *, include_result: bool = False) -> dict[str, Any]:
    item = dict(row)
    for key in ("summary", "params"):
        raw = item.get(key)
        if isinstance(raw, str):
            item[key] = json.loads(raw) if raw else {}
    if include_result:
        raw = item.get("result")
        item["result"] = json.loads(raw) if raw else {}
    elif "result" in item:
        del item["result"]
    return item


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at    TEXT NOT NULL,
                started_at    TEXT,
                finished_at   TEXT,
                kind          TEXT NOT NULL,
                algorithm     TEXT NOT NULL,
                source        TEXT NOT NULL,
                target        TEXT NOT NULL,
                params        TEXT NOT NULL DEFAULT '{}',
                status        TEXT NOT NULL,
                priority      INTEGER NOT NULL DEFAULT 0,
                progress      REAL NOT NULL DEFAULT 0,
                done_files    INTEGER NOT NULL DEFAULT 0,
                total_files   INTEGER NOT NULL DEFAULT 0,
                current_path  TEXT,
                error         TEXT,
                summary       TEXT NOT NULL DEFAULT '{}',
                result        TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_priority "
            "ON jobs(status, priority, id)"
        )
        _migrate_runs(conn)


def _migrate_runs(conn: sqlite3.Connection) -> None:
    """Import legacy `runs` rows once, then drop the old table."""
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "runs" not in tables:
        return
    rows = conn.execute("SELECT * FROM runs ORDER BY id").fetchall()
    for row in rows:
        r = dict(row)
        conn.execute(
            """
            INSERT INTO jobs
                (created_at, started_at, finished_at, kind, algorithm, source, target,
                 params, status, priority, progress, done_files, total_files,
                 current_path, error, summary, result)
            VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, 0, 100, 0, 0, NULL, ?, ?, ?)
            """,
            (
                r["created_at"],
                r["created_at"],
                r["created_at"],
                r["kind"],
                r["algorithm"],
                r["source"],
                r["target"],
                r["status"],
                r.get("error"),
                r.get("summary") or "{}",
                r.get("result") or "{}",
            ),
        )
    conn.execute("DROP TABLE runs")


def enqueue_job(
    *,
    kind: str,
    algorithm: str,
    source: str,
    target: str,
    params: dict[str, Any] | None = None,
) -> int:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(priority), -1) + 1 AS p FROM jobs WHERE status = 'queued'"
        ).fetchone()
        priority = int(row["p"])
        cur = conn.execute(
            """
            INSERT INTO jobs
                (created_at, kind, algorithm, source, target, params, status, priority)
            VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)
            """,
            (
                _now(),
                kind,
                algorithm,
                source,
                target,
                json.dumps(params or {}),
                priority,
            ),
        )
        return int(cur.lastrowid)


def list_jobs() -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, started_at, finished_at, kind, algorithm,
                   source, target, params, status, priority, progress,
                   done_files, total_files, current_path, error, summary
            FROM jobs
            ORDER BY
                CASE status
                    WHEN 'running' THEN 0
                    WHEN 'queued'  THEN 1
                    ELSE 2
                END,
                priority ASC,
                id ASC
            """
        ).fetchall()
    return [_row_to_job(r) for r in rows]


def get_job(job_id: int, *, include_result: bool = True) -> dict[str, Any] | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return _row_to_job(row, include_result=include_result)


def claim_next_job() -> dict[str, Any] | None:
    """Atomically pick the next queued job and mark it running."""
    with _lock, _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'queued'
            ORDER BY priority ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        job_id = int(row["id"])
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = 'running', started_at = ?, progress = 0,
                done_files = 0, total_files = 0, current_path = NULL, error = NULL
            WHERE id = ? AND status = 'queued'
            """,
            (_now(), job_id),
        )
        if cur.rowcount == 0:
            return None
        updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(updated, include_result=False)


def update_progress(
    job_id: int,
    *,
    done_files: int,
    total_files: int,
    current_path: str | None,
) -> None:
    progress = 0.0
    if total_files > 0:
        progress = min(100.0, round(100.0 * done_files / total_files, 2))
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET done_files = ?, total_files = ?, progress = ?, current_path = ?
            WHERE id = ? AND status = 'running'
            """,
            (done_files, total_files, progress, current_path, job_id),
        )


def finish_job(
    job_id: int,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with _lock, _connect() as conn:
        if status == "ok":
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, finished_at = ?, progress = 100,
                    current_path = NULL, error = ?, summary = ?, result = ?
                WHERE id = ?
                """,
                (
                    status,
                    _now(),
                    error,
                    json.dumps(summary or {}),
                    json.dumps(result or {}),
                    job_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, finished_at = ?,
                    current_path = NULL, error = ?, summary = ?, result = ?
                WHERE id = ?
                """,
                (
                    status,
                    _now(),
                    error,
                    json.dumps(summary or {}),
                    json.dumps(result or {}),
                    job_id,
                ),
            )


def cancel_job(job_id: int) -> str | None:
    """Cancel a queued or running job. Returns previous status, or None if not found."""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        status = row["status"]
        if status == "queued":
            conn.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', finished_at = ?, error = 'Cancelled before start'
                WHERE id = ?
                """,
                (_now(), job_id),
            )
            return status
        if status == "running":
            # Mark intent; worker will finalize when it notices.
            conn.execute(
                """
                UPDATE jobs
                SET error = 'Cancel requested'
                WHERE id = ? AND status = 'running'
                """,
                (job_id,),
            )
            return status
        return status


def is_cancel_requested(job_id: int) -> bool:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT status, error FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if row is None:
        return True
    if row["status"] == "cancelled":
        return True
    return row["status"] == "running" and row["error"] == "Cancel requested"


def reorder_queue(ordered_ids: list[int]) -> None:
    """Rewrite priorities for queued jobs in the given order (first = soonest)."""
    with _lock, _connect() as conn:
        queued = conn.execute(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY priority ASC, id ASC"
        ).fetchall()
        queued_ids = {int(r["id"]) for r in queued}
        if set(ordered_ids) != queued_ids:
            raise ValueError(
                "ordered_ids must contain exactly the currently queued job ids"
            )
        for i, job_id in enumerate(ordered_ids):
            conn.execute(
                "UPDATE jobs SET priority = ? WHERE id = ? AND status = 'queued'",
                (i, job_id),
            )


def delete_job(job_id: int) -> bool:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return False
        if row["status"] in ACTIVE:
            raise ValueError("Cannot delete an active job; cancel it first")
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cur.rowcount > 0


def clear_finished() -> int:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM jobs WHERE status IN ('ok', 'error', 'cancelled')"
        )
        return cur.rowcount


def recover_interrupted() -> int:
    """Mark jobs left `running` after a crash/restart as interrupted."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = 'error', finished_at = ?,
                error = 'Interrupted by server restart',
                current_path = NULL
            WHERE status = 'running'
            """,
            (_now(),),
        )
        return cur.rowcount
