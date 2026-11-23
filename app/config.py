"""Runtime configuration, sourced from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

# Root directory the user is allowed to browse / hash. Everything is confined
# to this path to prevent traversal outside the mounted volume.
DATA_DIR = Path(os.environ.get("HASHPUPPY_DATA", "/data")).resolve()

# Where the SQLite history database lives. Kept separate from user data.
DB_PATH = Path(os.environ.get("HASHPUPPY_DB", "/config/hashpuppy.db")).resolve()

# Default and allowed hashing algorithms.
DEFAULT_ALGORITHM = os.environ.get("HASHPUPPY_ALGORITHM", "sha256")
ALLOWED_ALGORITHMS = ("md5", "sha1", "sha256", "sha512", "blake2b")

# Read size when streaming files for hashing.
CHUNK_SIZE = 1024 * 1024  # 1 MiB
