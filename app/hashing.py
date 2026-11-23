"""File hashing helpers."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import ALLOWED_ALGORITHMS, CHUNK_SIZE


def validate_algorithm(algorithm: str) -> str:
    algorithm = (algorithm or "").lower()
    if algorithm not in ALLOWED_ALGORITHMS:
        raise ValueError(
            f"Unsupported algorithm '{algorithm}'. "
            f"Allowed: {', '.join(ALLOWED_ALGORITHMS)}"
        )
    return algorithm


def hash_file(path: Path, algorithm: str) -> str:
    """Compute the hex digest of a single file, streaming it in chunks."""
    digest = hashlib.new(validate_algorithm(algorithm))
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
