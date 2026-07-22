# syntax=docker/dockerfile:1
# Multi-stage build for the Ancora activity worker using uv.
#
# By default this image ships the LocalBackend (no Ray) — a fully functional
# execution runtime for the local stack. To run against a real Ray cluster,
# build with the `ray` extra (uncomment the marked line) and set
# ANCORA_RAY_ADDRESS=ray://ray-head:10001.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY packages/sdk-python/pyproject.toml packages/sdk-python/
COPY packages/common/pyproject.toml packages/common/
COPY services/activity-workers/pyproject.toml services/activity-workers/

COPY packages/sdk-python/ packages/sdk-python/
COPY packages/common/ packages/common/
COPY services/activity-workers/ services/activity-workers/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ancora-activity-worker --no-dev --frozen || \
    uv sync --package ancora-activity-worker --no-dev
# For the Ray-enabled image instead, replace the line above with:
#   uv sync --package ancora-activity-worker --extra ray --no-dev

FROM python:3.11-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ANCORA_LOG_JSON=true

RUN groupadd --system ancora && useradd --system --gid ancora --create-home ancora

WORKDIR /app
COPY --from=builder --chown=ancora:ancora /app /app

USER ancora
CMD ["ancora-activity-worker"]
