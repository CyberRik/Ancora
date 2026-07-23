# ancora-scheduler

Admission control for node dispatch (Phase 3, RFC-0002).

Temporal guarantees a node *eventually* runs, and runs exactly once. It has no
opinion on whether running it **right now** is a good idea. This service decides
that, against the constraints that live outside the durability model:

| Governor | Issue | Outcome |
|---|---|---|
| Deadline | AN-046 | `reject` once the run is out of time |
| Budget | AN-045 | `reject` in `hard` mode; warn in `soft` |
| Backpressure | AN-041 | `defer` when a queue passes its watermark |
| Fair share | AN-042 | `defer` a tenant running ahead of its weight |
| Rate limit | AN-040 | `defer` when a provider's token bucket is empty |

A **defer is not a dropped request.** The caller is a Temporal activity, so it
turns the verdict into a retryable failure carrying `next_retry_delay`; the work
returns to durable history and is re-delivered after the backoff. That is why the
scheduler holds no queue and can be restarted mid-overload without losing work.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/admit` | The hot path — may this node start? |
| `POST` | `/v1/complete` | Release the in-flight slot; report spend |
| `GET` | `/v1/scheduler/config` | The policy in force + any rejected reload |
| `GET` | `/v1/scheduler/state` | Live governor state ("why is my work stuck") |
| `GET` | `/metrics` | Prometheus exposition (AN-047) |

## Policy configuration (AN-048)

Point `ANCORA_SCHEDULER_CONFIG_PATH` at a JSON or YAML document. It is re-read
when its mtime changes, so a policy edit takes effect on the next admission — no
restart. **A document that fails validation is rejected and the previous policy
keeps serving**, with the error surfaced at `GET /v1/scheduler/config`.

```yaml
rate_limits:
  gemini/gemini-3.5-flash-lite: { rps: 5, burst: 10 }
  gemini: { rps: 5, burst: 10 }
  default: { rps: 20, burst: 40 }

watermarks:
  ancora-cpu: { soft: 50, hard: 200, backoff_seconds: 1, max_backoff_seconds: 30 }
  default: { soft: 50, hard: 200 }

tenants:
  acme: { weight: 3, budget_usd: 25.0 }
  default: { weight: 1 }

fairness: { enabled: true, idle_seconds: 30, defer_seconds: 0.25 }
budget: { mode: soft, default_run_usd: 5.0, warn_at: 0.8 }
backpressure_priority_cutoff: 1
```

## Failure behaviour

The worker-side client (`ancora_common.scheduler_client`) **fails open**: if this
service is unreachable, nodes are admitted anyway and the degradation is logged.
Admission control protects providers and queues; it must never become the single
point of failure that halts a durable fleet.
