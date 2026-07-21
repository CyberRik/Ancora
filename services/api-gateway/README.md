# api-gateway

The Ancora control-plane HTTP entrypoint (FastAPI).

**Phase 0** exposes:

- `GET /healthz` — liveness + dependency check (DB reachability).
- `GET /v1/version` — build/version metadata.

REST run/workflow endpoints (Phase 1) and WebSocket streams (Phase 4) build on this
skeleton. Config is via environment variables (see `ancora_api/settings.py`).

```bash
uv run uvicorn ancora_api.main:app --reload --port 8080 --app-dir services/api-gateway
```
