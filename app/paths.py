"""Safe path resolution confined to the data directory."""
from __future__ import annotations

from pathlib import Path

from .config import DATA_DIR


class UnsafePathError(ValueError):
    """Raised when a requested path escapes the data directory."""


def resolve_within_data(rel_path: str) -> Path:
    """Resolve a user-supplied path relative to DATA_DIR.

    The path is normalised and verified to stay inside DATA_DIR so that
    inputs such as ``../../etc/passwd`` cannot escape the mounted volume.
    """
    rel_path = (rel_path or "").strip().lstrip("/")
    # Treat "." / "./" as the data root (legacy picker quirk).
    if rel_path in (".", "./"):
        rel_path = ""
    candidate = (DATA_DIR / rel_path).resolve()
    if candidate != DATA_DIR and DATA_DIR not in candidate.parents:
        raise UnsafePathError(f"Path '{rel_path}' is outside the data directory")
    return candidate


def to_rel(path: Path) -> str:
    """Return a path expressed relative to DATA_DIR (POSIX style).

    The data root itself is returned as ``""`` (not ``"."``) so the UI and
    APIs can treat an empty string as "/data".
    """
    resolved = path.resolve()
    if resolved == DATA_DIR:
        return ""
    try:
        return resolved.relative_to(DATA_DIR).as_posix()
    except ValueError as exc:
        raise UnsafePathError(f"Path '{path}' is outside the data directory") from exc
