# ancora-event-consumer

The event-sourcing backbone of the observability stack (Phase 4, RFC-0001 §8).

Ancora's live UI is event-sourced: workers emit small lifecycle events as work
happens, and this service turns that stream into durable, queryable projections.
It runs two cooperating loops.

## Projector

Drains the Redis Streams event bus (`ancora:events`) via a consumer group into the
append-only `run_event` table. Delivery is at-least-once; writes are idempotent on
the global stream id (`uq_run_event_stream_id`), so a redelivered event is a
no-op and acking after the insert is safe. `run_event` is what the timeline and
the live DAG read — authoritative for the UI, but not the source of truth.

## Reconciler

Run-level lifecycle (started/completed/failed) can't be emitted by a worker
interceptor — a workflow writing to Redis would break deterministic replay. So the
reconciler periodically re-derives each non-terminal run's status from **Temporal
history** (the source of truth) and settles the `workflow_run` projection. This is
also the heal path: if the live stream ever drops an event, the next reconcile
makes the projection correct again. On a terminal transition it publishes a
`run.*` event so a browser watching the run animates the ending immediately.

## Configuration

Standard `ANCORA_*` settings (`DATABASE_URL`, `REDIS_URL`, `TEMPORAL_ADDRESS`,
`TEMPORAL_NAMESPACE`) plus:

| Setting | Default | Meaning |
| --- | --- | --- |
| `ANCORA_CONSUMER_NAME` | `consumer-<host>` | name within the projector group |
| `ANCORA_BATCH_SIZE` | `128` | events claimed per read |
| `ANCORA_RECONCILE_INTERVAL_SECONDS` | `8` | reconcile cadence for open runs |
| `ANCORA_RUN_PROJECTOR` / `ANCORA_RUN_RECONCILER` | `true` | toggle either loop |

Stateless and horizontally scalable: the projector's consumer group shares work
across replicas, and the reconciler's updates are idempotent.
