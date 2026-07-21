# syntax=docker/dockerfile:1
# Multi-stage build for the Ancora workflow worker using uv.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY packages/sdk-python/pyproject.toml packages/sdk-python/
COPY packages/common/pyproject.toml packages/common/
COPY services/workflow-workers/pyproject.toml services/workflow-workers/

COPY packages/sdk-python/ packages/sdk-python/
COPY packages/common/ packages/common/
COPY services/workflow-workers/ services/workflow-workers/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ancora-worker --no-dev --frozen || \
    uv sync --package ancora-worker --no-dev

FROM python:3.11-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ANCORA_LOG_JSON=true

RUN groupadd --system ancora && useradd --system --gid ancora --create-home ancora

WORKDIR /app
COPY --from=builder --chown=ancora:ancora /app /app

USER ancora
CMD ["ancora-worker"]
