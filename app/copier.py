"""Copy files/folders and verify the result by comparing hashes."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .compare import (
    CancelCb,
    ProgressCb,
    _check_cancel,
    _list_files,
    _report,
    compare_files,
    compare_folders,
)
from .hashing import validate_algorithm
from .paths import to_rel


def _copy_tree_with_progress(
    source: Path,
    dest: Path,
    overwrite: bool,
    *,
    on_progress: ProgressCb | None,
    is_cancelled: CancelCb | None,
    total: int,
) -> None:
    if dest.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    files = _list_files(source)
    for i, src_file in enumerate(files):
        _check_cancel(is_cancelled)
        rel = src_file.relative_to(source)
        dst_file = dest / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        _report(on_progress, f"copy:{to_rel(src_file)}", i, total)
        shutil.copy2(src_file, dst_file)


def copy_and_verify(
    source: Path,
    dest: Path,
    algorithm: str,
    overwrite: bool,
    *,
    on_progress: ProgressCb | None = None,
    is_cancelled: CancelCb | None = None,
) -> dict[str, Any]:
    """Copy ``source`` to ``dest`` then verify integrity via hash comparison.

    Supports both single files and directory trees. Returns a comparison
    result identical in shape to the compare endpoints.
    """
    validate_algorithm(algorithm)

    if not source.exists():
        raise FileNotFoundError(f"Source does not exist: {source}")

    if source.is_dir():
        files = _list_files(source)
        # copy N files + verify hashes of N files on each side = 3N steps
        # (copy phase + hash A + hash B)
        copy_steps = len(files)
        verify_steps = len(files) * 2
        total = copy_steps + verify_steps

        def during_copy(path: str | None, done: int, _total: int) -> None:
            _report(on_progress, path, done, total)

        _copy_tree_with_progress(
            source,
            dest,
            overwrite,
            on_progress=during_copy,
            is_cancelled=is_cancelled,
            total=total,
        )

        def during_verify(path: str | None, done: int, _total: int) -> None:
            # done is within verify (0..verify_steps); shift by copy_steps
            label = f"verify:{path}" if path else path
            _report(on_progress, label, copy_steps + done, total)

        result = compare_folders(
            source,
            dest,
            algorithm,
            on_progress=during_verify,
            is_cancelled=is_cancelled,
        )
    else:
        if dest.is_dir():
            dest = dest / source.name
        if dest.exists() and not overwrite:
            raise FileExistsError(f"Destination already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)

        total = 3  # copy + hash source + hash dest
        _check_cancel(is_cancelled)
        _report(on_progress, f"copy:{to_rel(source)}", 0, total)
        shutil.copy2(source, dest)

        def during_verify(path: str | None, done: int, _total: int) -> None:
            label = f"verify:{path}" if path else path
            _report(on_progress, label, 1 + done, total)

        result = compare_files(
            source,
            dest,
            algorithm,
            on_progress=during_verify,
            is_cancelled=is_cancelled,
        )

    return result
