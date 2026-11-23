# HashPuppy 🐶

A lean, browser-based file **hash comparison** tool, packaged as a hardened
Python Docker image. Compare two individual files or entire folder trees, get
separate **matching / mismatching** lists, queue jobs with live **progress**,
and **copy files with automatic hash verification**.

## Features

- **Compare two files** by cryptographic hash.
- **Compare two folders** recursively — every file is hashed and matched by its
  relative path. Results are split into:
  - **Matching** — same path, same hash
  - **Mismatching** — same path, different hash (or read error)
  - **Only in A** / **Only in B** — files present on one side only
- **Copy & Verify** — copy a file or folder to a destination, then automatically
  compare hashes to confirm an intact copy.
- **Background job queue** — comparisons and copies are queued and run one at a
  time. Reload the browser anytime; jobs continue on the server.
- **Live progress** — percent complete, files done/total, and the path currently
  being hashed or copied.
- **Queue priority** — reorder waiting jobs with ↑ / ↓ before they start.
- **Cancel / delete** — cancel queued or running jobs; delete finished ones.
- **Browser UI** — no install, includes a built-in file picker for mounted paths.
- **Multiple algorithms** — `sha256` (default), `md5`, `sha1`, `sha512`, `blake2b`.
- **Lean & hardened** — slim Python base, no Node/build step, runs as a
  non-root user with dropped capabilities and a read-only root filesystem.

## Quick start (Docker Compose)

```bash
# Put files you want to compare/copy under ./data
mkdir -p data config
docker compose up --build -d
```

Then open <http://localhost:8000>.

All paths in the UI are **relative to the mounted `/data` directory**, so mount
whatever you need to compare:

```yaml
volumes:
  - /path/on/host/A:/data/sourceA:ro
  - /path/on/host/B:/data/sourceB:ro
  - ./config:/config
```

> The **Copy & Verify** feature needs a writable destination, so do not mount
> the destination volume read-only (`:ro`). The `./config` mount must stay
> **writable** (job queue / SQLite).

## Quick start (plain Docker)

```bash
docker build -t hashpuppy:latest .

docker run -d --name hashpuppy \
  -p 8000:8000 \
  --user "$(id -u):$(id -g)" \
  --read-only --tmpfs /tmp \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  -v "$PWD/data:/data" \
  -v "$PWD/config:/config" \
  hashpuppy:latest
```

## Configuration

| Environment variable  | Default                 | Description                          |
| --------------------- | ----------------------- | ------------------------------------ |
| `HASHPUPPY_DATA`      | `/data`                 | Root directory users can browse/hash |
| `HASHPUPPY_DB`        | `/config/hashpuppy.db`  | SQLite job database location         |
| `HASHPUPPY_ALGORITHM` | `sha256`                | Default hashing algorithm            |

## Security notes

- Runs as a fixed non-root UID/GID (`1000:1000`); override with `--user` /
  `user:` in compose to match host ownership of copied files.
- `cap_drop: ALL`, `no-new-privileges`, and `read_only` root filesystem are
  enabled in `docker-compose.yml`.
- All path inputs are confined to `HASHPUPPY_DATA`; traversal outside the
  mounted volume (e.g. `../../etc`) is rejected.
- Symlinks are not followed during folder walks.

## API

| Method   | Endpoint                    | Purpose                                      |
| -------- | --------------------------- | -------------------------------------------- |
| `GET`    | `/api/meta`                 | Algorithms, defaults, data dir               |
| `GET`    | `/api/browse?path=`         | List a directory inside `/data`              |
| `POST`   | `/api/jobs/compare`         | Enqueue a file/folder compare job            |
| `POST`   | `/api/jobs/copy`            | Enqueue a copy-and-verify job                |
| `GET`    | `/api/jobs`                 | List all jobs (queue + history)              |
| `GET`    | `/api/jobs/{id}`            | Job detail + result when finished           |
| `PUT`    | `/api/jobs/queue`           | Reorder queued jobs (`ordered_ids`)          |
| `POST`   | `/api/jobs/{id}/cancel`     | Cancel a queued or running job               |
| `DELETE` | `/api/jobs/{id}`            | Delete a finished job                        |
| `DELETE` | `/api/jobs`                 | Clear finished jobs                          |
| `POST`   | `/api/compare`              | Legacy alias → enqueue compare               |
| `POST`   | `/api/copy`                 | Legacy alias → enqueue copy                  |
| `GET`    | `/healthz`                  | Health check                                 |

## Local development (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
HASHPUPPY_DATA="$PWD/data" HASHPUPPY_DB="$PWD/config/hashpuppy.db" \
  uvicorn app.main:app --reload
```
