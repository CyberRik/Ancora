# workflow-workers

Runs the deterministic **workflow worker**: it polls a Temporal task queue,
executes registered Ancora workflows, and (in Phase 1) runs their activities
inline in the same process. Phase 2 splits activity execution into dedicated
activity workers that dispatch to Ray.

On startup the worker also reports its registered workflows into the catalog
(`workflow_def`/`workflow_version`) so the API can start runs by name.

```bash
uv run ancora-worker
```

Config (env, `ANCORA_` prefix): `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`,
`TASK_QUEUE`, `DATABASE_URL`.
