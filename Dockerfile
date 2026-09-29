FROM python:3.13-slim-trixie AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BADGE_DATA_DIR=/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libfreetype6 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt \
    && python -m pip check

COPY app/ ./app/

# Docker initializes a new named volume with this directory's ownership.
RUN groupadd --gid 10001 badge \
    && useradd --uid 10001 --gid badge --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin badge \
    && mkdir /data \
    && chown badge:badge /data \
    && chmod 0700 /data \
    && printf '/data\n' > /etc/conbadge-data-root

USER 10001:10001

# Optional test image: tests are excluded from the final runtime image.
FROM base AS test
COPY tests/ /tests/
CMD ["python", "-m", "unittest", "discover", "-s", "/tests", "-v"]

FROM base AS runtime
EXPOSE 8000

# This endpoint reads a local catalog and does not decode images or download fonts.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/google-fonts', timeout=3).close()"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "8", "--no-proxy-headers"]
