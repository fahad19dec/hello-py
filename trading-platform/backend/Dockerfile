# API image. The same image runs migrations (alembic) and, in later phases, the workers.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Pinned versions: the lock file is generated from pyproject.toml with
# `uv pip compile pyproject.toml -o requirements.lock`.
COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# Run as an unprivileged user with no shell and no home directory.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2)"]

# --no-proxy-headers: the application decides which forwarded addresses to
# trust itself (TRUSTED_PROXY_HOPS), so the server must not rewrite them first.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
