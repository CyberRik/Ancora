# syntax=docker/dockerfile:1
# Multi-stage build for the Ancora API Gateway using uv.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Copy the workspace manifests needed to resolve the api-gateway + its local deps.
COPY pyproject.toml uv.lock* ./
COPY packages/sdk-python/pyproject.toml packages/sdk-python/
COPY packages/common/pyproject.toml packages/common/
COPY services/api-gateway/pyproject.toml services/api-gateway/

# Copy sources for the local workspace members.
COPY packages/sdk-python/ packages/sdk-python/
COPY packages/common/ packages/common/
COPY services/api-gateway/ services/api-gateway/

# Install only the api-gateway package (and its workspace deps) into a venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ancora-api --no-dev --frozen || \
    uv sync --package ancora-api --no-dev

# ---- runtime image ----
FROM python:3.11-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ANCORA_LOG_JSON=true

# Non-root user.
RUN groupadd --system ancora && useradd --system --gid ancora --create-home ancora

WORKDIR /app
COPY --from=builder --chown=ancora:ancora /app /app

USER ancora
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz').status==200 else 1)"

WORKDIR /app/services/api-gateway
CMD ["uvicorn", "ancora_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
