# ancora-activity-worker

The **execution runtime** (Phase 2). Activity workers poll per-capability task
queues (`ancora-cpu` / `ancora-gpu` / `ancora-io`) and dispatch the actual compute
to a backend:

- **Ray** in production (`ray.remote` with the node's resource request), or
- an in-process **thread pool** when no cluster is configured (dev / CI).

Two execution models (RFC-0001a §6):

| Model | Activity | Behavior |
|-------|----------|----------|
| **A — inline** | `ray_compute` | submit → poll → heartbeat each checkpoint. Crash-safe **resume from last batch**; cooperative cancel. Good for short work. |
| **B — async completion** | `ray_compute_async` | submit → `raise_complete_async()` → **slot freed immediately**; a detached completer resolves the activity when compute finishes. A concurrency-1 worker runs many long activities at once, and can die without failing the work. |

Workers register capabilities in Postgres (`worker` table) and refresh a Redis
liveness TTL each heartbeat; `GET /v1/workers` reads both. SIGTERM drains inline
work, deregisters, and exits 0.

```bash
# local run (LocalBackend — no Ray needed)
uv run ancora-activity-worker

# with a Ray cluster
ANCORA_RAY_ADDRESS=ray://localhost:10001 uv run --extra ray ancora-activity-worker
```

Config (env, `ANCORA_` prefix): `POOLS` (default `["cpu"]`), `TOTAL_CPUS`,
`TOTAL_GPUS`, `RAY_ADDRESS`, `REDIS_URL`, `WORKER_ID`, `HEARTBEAT_INTERVAL_SECONDS`.
