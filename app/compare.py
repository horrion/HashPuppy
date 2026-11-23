"""Comparison logic for files and folder trees."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .hashing import hash_file
from .paths import to_rel

ProgressCb = Callable[[str | None, int, int], None]
CancelCb = Callable[[], bool]

# Issue codes for imperfect comparisons
ISSUE_CHECKSUM = "checksum_mismatch"
ISSUE_MISSING_IN_A = "missing_in_a"
ISSUE_MISSING_IN_B = "missing_in_b"
ISSUE_READ_ERROR = "read_error"


class JobCancelled(Exception):
    """Raised when a running job is cancelled mid-flight."""


def _check_cancel(is_cancelled: CancelCb | None) -> None:
    if is_cancelled and is_cancelled():
        raise JobCancelled()


def _report(
    on_progress: ProgressCb | None,
    path: str | None,
    done: int,
    total: int,
) -> None:
    if on_progress:
        on_progress(path, done, total)


def _safe_hash(path: Path, algorithm: str) -> dict[str, Any]:
    """Hash a file, capturing errors instead of raising."""
    try:
        return {"hash": hash_file(path, algorithm), "size": path.stat().st_size, "error": None}
    except (OSError, ValueError) as exc:
        return {"hash": None, "size": None, "error": str(exc)}


def _list_files(root: Path) -> list[Path]:
    return sorted(
        item
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    )


def _issue_label(issue: str) -> str:
    return {
        ISSUE_CHECKSUM: "Checksum mismatch",
        ISSUE_MISSING_IN_A: "Missing in A",
        ISSUE_MISSING_IN_B: "Missing in B",
        ISSUE_READ_ERROR: "Read error",
    }.get(issue, issue)


def _classify_pair(
    *,
    path: str,
    path_b: str | None,
    info_a: dict[str, Any] | None,
    info_b: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a detailed difference entry for a path present on one or both sides."""
    a = info_a or {}
    b = info_b or {}
    hash_a = a.get("hash")
    hash_b = b.get("hash")
    size_a = a.get("size")
    size_b = b.get("size")
    err_a = a.get("error")
    err_b = b.get("error")

    present_a = info_a is not None
    present_b = info_b is not None

    if present_a and not present_b:
        issue = ISSUE_MISSING_IN_B
        detail = "File exists in A but is missing in B"
    elif present_b and not present_a:
        issue = ISSUE_MISSING_IN_A
        detail = "File exists in B but is missing in A"
    elif err_a or err_b:
        issue = ISSUE_READ_ERROR
        parts = []
        if err_a:
            parts.append(f"A: {err_a}")
        if err_b:
            parts.append(f"B: {err_b}")
        detail = "; ".join(parts)
    elif hash_a != hash_b:
        issue = ISSUE_CHECKSUM
        size_note = ""
        if size_a is not None and size_b is not None and size_a != size_b:
            size_note = f" (sizes differ: A={size_a} B={size_b} bytes)"
        elif size_a is not None and size_b is not None:
            size_note = f" (same size: {size_a} bytes)"
        detail = f"Same path on both sides, but hashes differ{size_note}"
    else:
        # Should not be called for matches
        issue = ISSUE_CHECKSUM
        detail = "Hashes differ"

    entry = {
        "path": path,
        "issue": issue,
        "issue_label": _issue_label(issue),
        "detail": detail,
        "present_in": (
            (["A"] if present_a else []) + (["B"] if present_b else [])
        ),
        "missing_from": (
            ([] if present_a else ["A"]) + ([] if present_b else ["B"])
        ),
        "hash_a": hash_a,
        "hash_b": hash_b,
        "size_a": size_a,
        "size_b": size_b,
        "error": err_a or err_b,
    }
    if path_b and path_b != path:
        entry["path_b"] = path_b
    return entry


def _build_result(
    matching: list[dict[str, Any]],
    differences: list[dict[str, Any]],
) -> dict[str, Any]:
    mismatching = [d for d in differences if d["issue"] in (ISSUE_CHECKSUM, ISSUE_READ_ERROR)]
    only_in_a = [d for d in differences if d["issue"] == ISSUE_MISSING_IN_B]
    only_in_b = [d for d in differences if d["issue"] == ISSUE_MISSING_IN_A]

    checksum_mismatches = sum(1 for d in differences if d["issue"] == ISSUE_CHECKSUM)
    read_errors = sum(1 for d in differences if d["issue"] == ISSUE_READ_ERROR)
    missing_in_a = sum(1 for d in differences if d["issue"] == ISSUE_MISSING_IN_A)
    missing_in_b = sum(1 for d in differences if d["issue"] == ISSUE_MISSING_IN_B)

    return {
        "matching": matching,
        # Legacy lists (kept for compatibility) — now include issue metadata
        "mismatching": mismatching,
        "only_in_a": only_in_a,
        "only_in_b": only_in_b,
        # Unified detailed report of everything that did not match perfectly
        "differences": differences,
        "summary": {
            "matching": len(matching),
            "mismatching": len(mismatching),
            "only_in_a": len(only_in_a),
            "only_in_b": len(only_in_b),
            "checksum_mismatches": checksum_mismatches,
            "missing_in_a": missing_in_a,
            "missing_in_b": missing_in_b,
            "read_errors": read_errors,
            "differences": len(differences),
            "total": len(matching) + len(differences),
        },
    }


def compare_files(
    path_a: Path,
    path_b: Path,
    algorithm: str,
    *,
    on_progress: ProgressCb | None = None,
    is_cancelled: CancelCb | None = None,
) -> dict[str, Any]:
    """Compare two individual files by hash."""
    total = 2
    _check_cancel(is_cancelled)
    _report(on_progress, to_rel(path_a), 0, total)
    info_a = _safe_hash(path_a, algorithm)
    _check_cancel(is_cancelled)
    _report(on_progress, to_rel(path_b), 1, total)
    info_b = _safe_hash(path_b, algorithm)
    _report(on_progress, None, 2, total)

    rel_a = to_rel(path_a)
    rel_b = to_rel(path_b)
    error = info_a["error"] or info_b["error"]
    is_match = (
        error is None
        and info_a["hash"] is not None
        and info_a["hash"] == info_b["hash"]
    )

    match_entry = {
        "path": rel_a,
        "path_b": rel_b,
        "hash_a": info_a["hash"],
        "hash_b": info_b["hash"],
        "size_a": info_a["size"],
        "size_b": info_b["size"],
        "error": error,
        "issue": None,
        "issue_label": "Match",
    }

    if is_match:
        return _build_result([match_entry], [])

    diff = _classify_pair(
        path=rel_a,
        path_b=rel_b,
        info_a=info_a,
        info_b=info_b,
    )
    return _build_result([], [diff])


def _index_folder(
    root: Path,
    algorithm: str,
    *,
    label: str,
    done_offset: int,
    total: int,
    on_progress: ProgressCb | None,
    is_cancelled: CancelCb | None,
) -> dict[str, dict[str, Any]]:
    """Return {relative_path: {hash, size, error}} for every file in a tree."""
    index: dict[str, dict[str, Any]] = {}
    files = _list_files(root)
    for i, item in enumerate(files):
        _check_cancel(is_cancelled)
        rel = item.relative_to(root).as_posix()
        display = f"{label}/{rel}" if label else rel
        _report(on_progress, display, done_offset + i, total)
        index[rel] = _safe_hash(item, algorithm)
        _report(on_progress, display, done_offset + i + 1, total)
    return index


def compare_folders(
    root_a: Path,
    root_b: Path,
    algorithm: str,
    *,
    on_progress: ProgressCb | None = None,
    is_cancelled: CancelCb | None = None,
) -> dict[str, Any]:
    """Recursively compare two folder trees by relative path + hash."""
    files_a = _list_files(root_a)
    files_b = _list_files(root_b)
    total = len(files_a) + len(files_b)
    if total == 0:
        _report(on_progress, None, 0, 0)

    index_a = _index_folder(
        root_a,
        algorithm,
        label="A",
        done_offset=0,
        total=total,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
    )
    index_b = _index_folder(
        root_b,
        algorithm,
        label="B",
        done_offset=len(files_a),
        total=total,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
    )
    _report(on_progress, None, total, total)

    keys_a = set(index_a)
    keys_b = set(index_b)

    matching: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []

    for rel in sorted(keys_a & keys_b):
        a = index_a[rel]
        b = index_b[rel]
        error = a["error"] or b["error"]
        if error is None and a["hash"] == b["hash"]:
            matching.append(
                {
                    "path": rel,
                    "hash_a": a["hash"],
                    "hash_b": b["hash"],
                    "size_a": a["size"],
                    "size_b": b["size"],
                    "error": None,
                    "issue": None,
                    "issue_label": "Match",
                }
            )
        else:
            differences.append(
                _classify_pair(path=rel, path_b=None, info_a=a, info_b=b)
            )

    for rel in sorted(keys_a - keys_b):
        differences.append(
            _classify_pair(path=rel, path_b=None, info_a=index_a[rel], info_b=None)
        )

    for rel in sorted(keys_b - keys_a):
        differences.append(
            _classify_pair(path=rel, path_b=None, info_a=None, info_b=index_b[rel])
        )

    # Keep differences ordered: checksum/read errors first (shared paths), then missing
    order = {
        ISSUE_CHECKSUM: 0,
        ISSUE_READ_ERROR: 1,
        ISSUE_MISSING_IN_B: 2,
        ISSUE_MISSING_IN_A: 3,
    }
    differences.sort(key=lambda d: (order.get(d["issue"], 9), d["path"]))

    return _build_result(matching, differences)
