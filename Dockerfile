# syntax=docker/dockerfile:1

# --- Builder: install dependencies into an isolated prefix ------------------ #
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /install
COPY requirements.txt .
RUN pip install --prefix=/install/prefix -r requirements.txt

# --- Runtime: minimal image, non-root, no build tooling -------------------- #
FROM python:3.12-slim AS runtime

# Create an unprivileged user/group with a fixed UID/GID.
RUN groupadd --gid 1000 hashpuppy \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin hashpuppy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/prefix/bin:$PATH" \
    PYTHONPATH="/opt/prefix/lib/python3.12/site-packages" \
    HASHPUPPY_DATA=/data \
    HASHPUPPY_DB=/config/hashpuppy.db

COPY --from=builder /install/prefix /opt/prefix

WORKDIR /srv
COPY app ./app

# Volumes: /data holds user files, /config holds the history database.
RUN mkdir -p /data /config && chown -R 1000:1000 /config /srv

USER 1000:1000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else sys.exit(1)"

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
