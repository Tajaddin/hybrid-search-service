# syntax=docker/dockerfile:1.7
FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/

FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

FROM base AS runtime
RUN groupadd -r app && useradd -r -g app -d /app app
COPY --from=deps /app/.venv /app/.venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev && mkdir -p /data/models /app/reports && chown -R app:app /app /data
ENV PATH="/app/.venv/bin:$PATH" HSS_PREPARED_DIR=/data/prepared HSS_MODELS_DIR=/data/models HSS_REPORTS_DIR=/app/reports \
    HF_HOME=/data/models OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1
CMD ["hss", "serve", "--port", "8000"]
