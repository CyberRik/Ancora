# Ancora — GitHub Project Plan (Epics & Issues)

> **Source of truth:** RFC-0001 + RFC-0001a + IMPLEMENTATION-PLAN.md
> **Format:** 15 Epics aligned to phases · ~165 issues · every issue is a real implementation task (no placeholders).
> **Issue key:** `AN-###`. **Difficulty:** S (≤1d) · M (2–4d) · L (1–2w) · XL (multi-issue parent).
> **Milestones:** `MVP` (Phases 0–5) · `v2` (Phases 6–7) · `v1.0` (Phase 8). Labels shown per issue.
> **How to read deps:** `Dep:` lists blocking issues; an issue is `ready` when all deps are closed.

**Global label taxonomy:** `area:*` (`repo`, `runtime`, `scheduler`, `nodes`, `state`, `observability`, `web`, `plugins`, `chaos`, `deploy`, `security`, `sdk`, `docs`) · `type:*` (`feat`, `infra`, `test`, `chore`, `spec`) · `prio:*` (`p0`, `p1`, `p2`) · `good-first-issue` where apt.

---

## Epic E0 — Repository & Dev Foundation  *(Phase 0 · MVP)*

**Outcome:** `docker compose up` boots the stack; CI is green; contributors can start in <10 min.

- **AN-001 · Initialize monorepo layout & tooling** — _S · `area:repo` `type:infra` `prio:p0`_
  Create the folder structure from RFC-0001 §16; add root `README`, `LICENSE` (Apache-2.0), `CODEOWNERS`, `.editorconfig`. **Dep:** —. **AC:** tree matches §16; `README` quickstart placeholder present; repo installs with one command.

- **AN-002 · Python workspace & dependency management** — _S · `area:repo` `type:infra`_
  Set up `uv`/Poetry workspaces for `sdk-python`, `cli`, and each `services/*`; shared lint/format config (ruff, black, mypy strict). **Dep:** AN-001. **AC:** `make lint typecheck` passes on empty packages; single lockfile resolves.

- **AN-003 · Web workspace scaffold (Next.js 14 + TS + Tailwind + shadcn/ui)** — _S · `area:web` `type:infra`_
  Bootstrap `web/` with App Router, Tailwind, shadcn/ui, dark theme tokens, ESLint/Prettier. **Dep:** AN-001. **AC:** `pnpm dev` serves an app shell; `tsc --noEmit` clean.

- **AN-004 · Docker Compose stack (Temporal, Postgres, Redis, API, web)** — _M · `area:deploy` `type:infra` `prio:p0`_
  Compose file with Temporal dev server + UI, Postgres 15, Redis 7, api-gateway, web; healthchecks + depends_on ordering. **Dep:** AN-002, AN-003. **AC:** `docker compose up` → all services healthy; Temporal UI reachable.

- **AN-005 · API Gateway skeleton (FastAPI) with health/version** — _S · `area:runtime` `type:feat` `prio:p0`_
  FastAPI app, Pydantic settings, structured logging, `GET /healthz`, `GET /v1/version`. **Dep:** AN-002. **AC:** endpoints return 200 with build metadata; logs are structured JSON.

- **AN-006 · Alembic migration framework + base tables** — _M · `area:state` `type:infra`_
  Wire Alembic; first migration creates `org`, `project`, `user`. **Dep:** AN-004. **AC:** `alembic upgrade head` runs in CI against Postgres; downgrade tested.

- **AN-007 · `ancora` CLI skeleton** — _S · `area:sdk` `type:feat`_
  Typer-based CLI with `ancora version`, `ancora dev` (stub), `ancora --help`. **Dep:** AN-002. **AC:** CLI installs as console script; commands run.

- **AN-008 · GitHub Actions CI (lint, typecheck, test, build)** — _M · `area:repo` `type:infra` `prio:p0`_
  Matrix: Python lint/typecheck/test, web build/typecheck, Docker build. Cache deps. **Dep:** AN-005, AN-003. **AC:** CI green on `main`; fails on lint/type errors; runs <10 min.

- **AN-009 · Pre-commit hooks & conventional commits** — _S · `area:repo` `type:chore` `good-first-issue`_
  ruff/black/mypy/prettier hooks; commit-lint. **Dep:** AN-002. **AC:** `pre-commit run --all-files` passes; bad commit message rejected.

- **AN-010 · Playwright web smoke test in CI** — _S · `area:web` `type:test`_
  Load app shell, assert nav + theme render. **Dep:** AN-003, AN-008. **AC:** Playwright job green in CI headless.

- **AN-011 · Docs site wiring (RFCs + ADRs)** — _S · `area:docs` `type:chore` `good-first-issue`_
  Render `docs/` (RFC-0001/0001a, plan) via MkDocs or Docusaurus; ADR template. **Dep:** AN-001. **AC:** docs build in CI; RFC index page lists all RFCs.

- **AN-012 · CONTRIBUTING, issue/PR templates, governance** — _S · `area:docs` `type:chore` `good-first-issue`_
  Contribution guide, code of conduct, issue templates matching this plan's fields, PR checklist. **Dep:** AN-001. **AC:** templates appear on new issue/PR.

---

## Epic E1 — Durability Core (Temporal)  *(Phase 1 · MVP)*

**Outcome:** start a real durable workflow from the API; kill/restart worker → resume.

- **AN-013 · Temporal client & connection management** — _M · `area:runtime` `type:infra` `prio:p0`_
  Shared Temporal client factory (namespace, TLS-ready, retry), used by workflow-service + workers. **Dep:** AN-004. **AC:** client connects to dev server; connection failure surfaces a clear error + backoff.

- **AN-014 · Workflow Worker service bootstrap** — _M · `area:runtime` `type:feat` `prio:p0`_
  `services/workflow-workers` polls task queue, registers workflow/activity implementations, graceful start/stop. **Dep:** AN-013. **AC:** worker connects, appears in Temporal UI, exits cleanly on SIGTERM.

- **AN-015 · SDK core: `@workflow`, `@workflow.run`, `self.call`** — _L · `area:sdk` `type:feat` `prio:p0`_
  Minimal authoring API compiling to a Temporal workflow; `self.call(node)` schedules an activity. **Dep:** AN-014. **AC:** a 3-activity sequential workflow authors in <20 lines and runs to completion.

- **AN-016 · SDK core: `@activity` + inline activity execution** — _M · `area:sdk` `type:feat`_
  Activity decorator, typed I/O via Pydantic, inline execution (no Ray yet). **Dep:** AN-015. **AC:** activities receive typed input, return typed output; type mismatch fails fast.

- **AN-017 · Workflow Service: start/query/list/cancel** — _M · `area:runtime` `type:feat` `prio:p0`_
  Translate API calls to Temporal client ops; own workflow metadata. **Dep:** AN-013, AN-006. **AC:** start returns run_id; query returns status; cancel transitions to Cancelled.

- **AN-018 · API: run lifecycle endpoints** — _M · `area:runtime` `type:feat`_
  `POST /v1/workflows/{name}/runs`, `GET /v1/runs/{id}`, `GET /v1/runs`, `POST /v1/runs/{id}/cancel`; idempotency-key header enforced. **Dep:** AN-017. **AC:** OpenAPI generated; duplicate idempotency-key returns the same run.

- **AN-019 · DB: workflow_def / workflow_version / workflow_run** — _M · `area:state` `type:feat`_
  Migrations + repositories for definitions, versions (with `dag_spec`, `code_hash`, `determinism_token`), and run projection. **Dep:** AN-006. **AC:** register a version, start a run, row created with correct FK links.

- **AN-020 · Workflow registration & versioning API** — _M · `area:runtime` `type:feat`_
  `POST /v1/workflows`, `GET /v1/workflows`, `GET /v1/workflows/{name}`; monotonic version numbers. **Dep:** AN-019. **AC:** re-registering bumps version; old versions remain runnable.

- **AN-021 · Replay-test harness** — _M · `area:runtime` `type:test` `prio:p0`_
  Record a run's history; `Replayer` replays against current code in CI. **Dep:** AN-015. **AC:** determinism break fails CI; sample workflow has a committed history fixture.

- **AN-022 · Thin determinism lint rule** — _S · `area:sdk` `type:feat`_
  AST check flags `datetime.now()`, `random`, direct `requests`/socket use in workflow modules (warning, local only). **Dep:** AN-015. **AC:** flags the forbidden calls; does not gate CI (per RFC-0001a §1.5).

- **AN-023 · Durability smoke test: kill workflow worker mid-run** — _M · `area:runtime` `type:test` `prio:p0`_
  Integration test kills the worker between activities, restarts, asserts completion. **Dep:** AN-016, AN-021. **AC:** run resumes to Completed with identical output; no duplicate activity execution.

- **AN-024 · Web: run list + run detail (basic)** — _M · `area:web` `type:feat`_
  List runs (status, started, version); detail page (status, input/output). **Dep:** AN-018. **AC:** starting a run from CLI shows it in the UI within one refresh.

---

## Epic E2 — Execution Runtime & Ray Bridge  *(Phase 2 · MVP · RFC-0003/0004)*

**Outcome:** activities run on Ray; long work uses async completion; worker lifecycle is real.

- **AN-025 · Ray head in Compose + cluster client** — _M · `area:runtime` `type:infra`_
  Add Ray head/worker to Compose; shared Ray connection helper for activity workers. **Dep:** AN-004. **AC:** activity worker connects to Ray; `ray.remote` round-trips a task.

- **AN-026 · Activity Worker service bootstrap** — _M · `area:runtime` `type:feat` `prio:p0`_
  `services/activity-workers` polls capability queues, holds Temporal + Ray clients. **Dep:** AN-014, AN-025. **AC:** activity worker registers on gpu/cpu/io queues per config.

- **AN-027 · Inline activity → Ray dispatch (Model A)** — _M · `area:runtime` `type:feat`_
  Short activities submit to Ray, await inline with heartbeats. **Dep:** AN-026. **AC:** an HTTP-ish activity executes on Ray and returns; heartbeats visible in Temporal.

- **AN-028 · Async activity completion (Model B)** — _L · `area:runtime` `type:feat` `prio:p0`_
  Dispatcher submits to Ray, raises `CompleteAsyncError`, frees slot; Ray task calls `client.complete_activity(task_token, result)` on finish. **Dep:** AN-027. **AC:** a 30s activity frees the dispatcher slot during compute (asserted via concurrency test); workflow resumes on callback.

- **AN-029 · Heartbeat-checkpoint tokens + resume** — _M · `area:runtime` `type:feat`_
  Long activities heartbeat a small progress token; on retry, resume from `heartbeat_details`. **Dep:** AN-028. **AC:** a 100-batch activity killed at batch 40 resumes at 40, not 0.

- **AN-030 · Cooperative cancellation → `ray.cancel`** — _M · `area:runtime` `type:feat`_
  Activity handles `CancelledError`, cancels the Ray task/actor, releases resources. **Dep:** AN-028. **AC:** cancelling a run stops in-flight Ray tasks within the drain window; no orphaned actors.

- **AN-031 · Graceful worker drain on SIGTERM** — _M · `area:runtime` `type:feat`_
  Stop polling, finish/hand-off in-flight, final heartbeat, deregister, exit 0. **Dep:** AN-028. **AC:** SIGTERM during a run drains without dropping inline work; async work unaffected.

- **AN-032 · Worker registry (capabilities) + Redis liveness TTL** — _M · `area:runtime` `type:feat`_
  Workers POST capability descriptors; registry in Postgres, liveness via Redis TTL heartbeat. **Dep:** AN-026, AN-006. **AC:** worker appears with pools/queues; disappears when TTL lapses.

- **AN-033 · Capability → task-queue routing** — _M · `area:scheduler` `type:feat`_
  Map node capability (gpu/cpu/io) to the queue it's scheduled on; workers poll only matching queues. **Dep:** AN-032. **AC:** a gpu node never lands on a cpu-only worker.

- **AN-034 · Resource requests → Ray (`num_cpus/gpus`, `accelerator_type`)** — _M · `area:runtime` `type:feat`_
  Node resource spec flows to Ray submit; over-subscription prevented by Ray accounting. **Dep:** AN-027. **AC:** a 1-GPU node only schedules where a GPU resource is free.

- **AN-035 · API: `GET /v1/workers`, `GET /v1/queues`** — _S · `area:runtime` `type:feat`_
  Expose registry + queue depth. **Dep:** AN-032. **AC:** returns live worker health, pools, per-queue depth.

- **AN-036 · Web: Worker view** — _M · `area:web` `type:feat`_
  Pools, per-worker GPU/CPU/mem, running tasks, idle, registered capabilities. **Dep:** AN-035, AN-024. **AC:** matches RFC-0001 §15.3; updates on refresh.

- **AN-037 · Integration tests: worker-kill scenarios 1–3** — _M · `area:runtime` `type:test` `prio:p0`_
  Kill workflow worker (1), inline activity worker (2), async-handed-off worker (3); assert recovery + dup-safety. **Dep:** AN-028, AN-030. **AC:** all three scenarios pass per RFC-0001a §8.

---

## Epic E3 — Scheduler Subsystem  *(Phase 3 · MVP · RFC-0002)*

**Outcome:** admission/routing/rate-limit/backpressure/fair-queue live; budget/deadline interfaced.

- **AN-038 · Scheduler service bootstrap + admission API** — _M · `area:scheduler` `type:feat` `prio:p0`_
  `services/scheduler`; gRPC/HTTP `Admit(node_ctx) → {admit|defer(backoff)|reject}` consulted by activity workers. **Dep:** AN-026. **AC:** activity worker calls Admit before dispatch; decision honored.

- **AN-039 · Deterministic policy resolution in workflow** — _M · `area:scheduler` `type:feat`_
  Workflow resolves node policy (priority, queue, timeouts, retry) from `dag_spec` into activity options — replay-safe. **Dep:** AN-020, AN-033. **AC:** policy resolution is pure; replay test passes with policies applied.

- **AN-040 · Per-provider rate-limit governor (Redis token buckets)** — _M · `area:scheduler` `type:feat` `prio:p0`_
  Token buckets keyed by provider/model; Admit defers when exhausted; honors `Retry-After`. **Dep:** AN-038. **AC:** concurrent LLM calls respect a configured RPS; no 429 storm (scenario 9).

- **AN-041 · Backpressure via queue-depth watermark** — _M · `area:scheduler` `type:feat`_
  Admit returns `defer(backoff)` when a queue exceeds its watermark; Temporal holds work. **Dep:** AN-038, AN-035. **AC:** under synthetic overload, submissions defer instead of overwhelming Ray.

- **AN-042 · Weighted fair queuing per org/project** — _M · `area:scheduler` `type:feat`_
  Per-tenant token weighting prevents starvation on shared queues. **Dep:** AN-040. **AC:** two orgs on one queue get throughput proportional to weights under contention.

- **AN-043 · Priority keys on task queues** — _M · `area:scheduler` `type:feat`_
  High-priority runs drain first via Temporal priority keys / dedicated high-prio queue. **Dep:** AN-039. **AC:** a high-priority run preempts queue position over a low-priority backlog.

- **AN-044 · Retry policy per node class + jittered backoff** — _M · `area:scheduler` `type:feat`_
  Default retry policies (llm/http/db/gpu) with exponential backoff, jitter, cap; error classification (transient vs terminal). **Dep:** AN-016. **AC:** transient errors retry with backoff; terminal errors fail fast; recorded in `retry_attempt`.

- **AN-045 · Budget governor (interface + soft mode)** — _M · `area:scheduler` `type:feat`_
  Per-run/org budget check in Admit; MVP soft mode logs/alerts, `hard` mode stubbed. **Dep:** AN-038, AN-057. **AC:** exceeding soft budget emits an alert; interface ready for v2 hard-stop.

- **AN-046 · Deadline propagation (interface + timeouts)** — _M · `area:scheduler` `type:feat`_
  Run deadline → derive `schedule_to_close` timeouts; MVP wires timeouts, full deadline-drain in v2. **Dep:** AN-039. **AC:** activities inherit deadline-derived timeouts; past-deadline nodes time out.

- **AN-047 · Autoscale-signal metrics emitter** — _M · `area:scheduler` `type:observability`_
  Export queue backlog + Ray pending-demand as Prometheus metrics for HPA/KEDA + Ray autoscaler. **Dep:** AN-035. **AC:** metrics scrape-able; values track synthetic load.

- **AN-048 · Scheduler config & policy schema** — _S · `area:scheduler` `type:feat`_
  Declarative config for rate limits, watermarks, weights, budgets, priorities. **Dep:** AN-040, AN-041. **AC:** config hot-reloads; invalid config rejected with clear error.

- **AN-049 · Scheduler integration test suite** — _M · `area:scheduler` `type:test`_
  Cover admit/defer/reject, rate-limit, backpressure, fairness, priority. **Dep:** AN-042, AN-043. **AC:** all paths asserted; deterministic under seeded load.

---

## Epic E4 — Built-in Node Library  *(Phase 3 · MVP · RFC-0004)*

**Outcome:** LLM, HTTP, Python, Database, Approval nodes usable end-to-end.

- **AN-050 · Node contract & base class** — _M · `area:nodes` `type:feat` `prio:p0`_
  `Node` base: typed I/O schema, `execute(input, ctx)`, resource/sandbox declaration, cost hook. **Dep:** AN-016. **AC:** a node declares schema + resources; invalid schema rejected at register.

- **AN-051 · LLMNode (multi-provider + streaming + fallback)** — _L · `area:nodes` `type:feat` `prio:p0`_
  Chat/completion across providers via an adapter interface; streaming; primary→secondary fallback chain; token/cost accounting. Ships a **mock provider** for CI. **Dep:** AN-050, AN-040. **AC:** runs against mock; fallback triggers on primary failure; tokens recorded to ledger.

- **AN-052 · HTTPNode (retry-after aware, idempotent)** — _M · `area:nodes` `type:feat`_
  REST call with method/headers/body templating; honors `Retry-After`; idempotency guard. **Dep:** AN-050, AN-066. **AC:** GET/POST work; double-fire produces one effect (via inbox).

- **AN-053 · PythonNode (sandboxed function)** — _M · `area:nodes` `type:feat`_
  Run a user Python callable as a Ray task with declared resources; subprocess isolation. **Dep:** AN-050, AN-034. **AC:** function runs with resource limits; exceeding memory fails cleanly.

- **AN-054 · DatabaseNode (parameterized SQL, pooled)** — _M · `area:nodes` `type:feat`_
  Parameterized queries, connection pooling, read/write split, no string interpolation. **Dep:** AN-050. **AC:** parameterized query runs; injection attempt is not possible (params only).

- **AN-055 · ApprovalGate node (durable wait via signal)** — _M · `area:nodes` `type:feat` `prio:p0`_
  Workflow awaits an approval signal with an expiry timer; zero compute while waiting. **Dep:** AN-015. **AC:** workflow suspends durably for the configured window; survives worker restart; resumes on signal.

- **AN-056 · Node cost/metrics hooks** — _S · `area:nodes` `type:observability`_
  `ctx.record_cost(...)` emits token/$/gpu-seconds into activity result → ledger. **Dep:** AN-050. **AC:** cost appears in `GET /v1/runs/{id}/cost`.

- **AN-057 · Cost accounting flow (in-workflow accumulation)** — _M · `area:nodes` `type:feat`_
  Activity results carry cost; workflow accumulates for enforcement; ledger projection for reporting. **Dep:** AN-056, AN-019. **AC:** per-run cost matches sum of node costs; enforceable in-workflow.

- **AN-058 · Node registry (built-in catalog)** — _S · `area:nodes` `type:feat`_
  Register built-in node types with schemas for discovery. **Dep:** AN-050. **AC:** `GET /v1/plugins` lists built-ins with schemas.

- **AN-059 · Example: research-agent workflow** — _M · `area:sdk` `type:test` `prio:p0`_
  search→summarize×N→synthesize→approve→publish using built-in nodes. **Dep:** AN-051, AN-052, AN-055. **AC:** runs end-to-end against mock provider; kill-any-worker resumes correctly.

- **AN-060 · Parallel fan-out/fan-in (`self.gather`)** — _M · `area:sdk` `type:feat`_
  Map a node over a list on distributed Ray workers; join results deterministically. **Dep:** AN-015, AN-028. **AC:** N parallel LLM calls run concurrently; results ordered deterministically.

---

## Epic E5 — Idempotency & Human-in-the-Loop  *(Phase 3 · MVP)*

**Outcome:** exactly-once side effects; durable approvals.

- **AN-061 · Inbox/idempotency table + guard** — _M · `area:state` `type:feat` `prio:p0`_
  `inbox` table keyed by `idempotency_key`; side-effecting activities check-then-act, store result. **Dep:** AN-006, AN-016. **AC:** replaying/retrying a side-effect returns stored result; effect happens once.

- **AN-062 · Deterministic idempotency-key derivation in SDK** — _S · `area:sdk` `type:feat`_
  `self.call` derives a stable key from `(node_id, input-hash)`; override for custom semantics. **Dep:** AN-015, AN-061. **AC:** same logical call yields same key across attempts.

- **AN-063 · Signal API + approval decision endpoints** — _M · `area:runtime` `type:feat`_
  `POST /v1/runs/{id}/signals/{name}`, `POST /v1/approvals/{gate_id}/decision`. **Dep:** AN-055, AN-018. **AC:** approve/reject signals resume the workflow correctly.

- **AN-064 · Approval projection + pending-review index** — _M · `area:state` `type:feat`_
  `approval_gate` as UI index (authoritative decision stays in Temporal). **Dep:** AN-055, AN-006. **AC:** `GET /v1/approvals?status=waiting` lists pending gates; decision updates index.

- **AN-065 · Web: Approval inbox** — _M · `area:web` `type:feat`_
  List waiting gates, show payload, approve/reject with comment. **Dep:** AN-064, AN-024. **AC:** approving in UI resumes the run; expiry shown.

- **AN-066 · Idempotency-key HTTP header middleware** — _S · `area:runtime` `type:feat`_
  Enforce `Idempotency-Key` on mutating endpoints; dedupe at API layer. **Dep:** AN-005. **AC:** duplicate request returns original response, not a second effect.

- **AN-067 · Human-gate timeout branch (scenario 12)** — _M · `area:nodes` `type:test`_
  Expiry timer fires → workflow takes timeout branch (auto-reject/escalate). **Dep:** AN-055. **AC:** multi-day wait simulated via time-skipping; timeout branch executes.

---

## Epic E6 — State, Projections & Persistence  *(Phase 4 · MVP · RFC-0007)*

**Outcome:** interceptor→stream→consumer projections replace polling; rebuildable.

- **AN-068 · Domain-event model + reporter activity** — _M · `area:state` `type:feat` `prio:p0`_
  Define domain events (node started/completed, cost accrued, approval requested); emit via a durable reporter activity. **Dep:** AN-016. **AC:** events emitted are replay-safe (go through an activity, not raw I/O in workflow).

- **AN-069 · Workflow/activity interceptors emit domain events** — _M · `area:state` `type:feat`_
  Temporal interceptors publish domain events to a Redis Stream. **Dep:** AN-068, AN-025. **AC:** each node transition produces exactly one stream entry.

- **AN-070 · Redis Streams transport + consumer group** — _M · `area:state` `type:infra`_
  Streams with consumer groups; at-least-once; ack semantics. **Dep:** AN-069. **AC:** consumer restart replays unacked entries; no loss.

- **AN-071 · Projection consumer → PG (idempotent upserts)** — _M · `area:state` `type:feat` `prio:p0`_
  `services/event-consumer` writes `workflow_run`, `node_execution`, `retry_attempt`, cost ledger; idempotent on `(run_id,node_id,seq)`. **Dep:** AN-070, AN-019. **AC:** projections match Temporal history; duplicate delivery is a no-op.

- **AN-072 · Projection reconciler (rebuild from history)** — _M · `area:state` `type:feat`_
  Job replays a run's history and re-derives projections to catch consumer gaps. **Dep:** AN-071, AN-021. **AC:** wiping projections and reconciling reproduces identical rows.

- **AN-073 · Object store for large payloads (MinIO/S3)** — _M · `area:state` `type:infra`_
  Content-addressed store; SDK offloads oversized inputs/outputs, passes references. **Dep:** AN-016. **AC:** a >256KB payload is stored by hash; history carries the pointer, not bytes.

- **AN-074 · Payload reference resolution in API/SDK** — _S · `area:state` `type:feat`_
  Transparent get/put of referenced payloads. **Dep:** AN-073. **AC:** node sees full payload; storage layer is invisible to node code.

- **AN-075 · Advanced Visibility wiring (search/list)** — _M · `area:state` `type:infra`_
  Use Temporal Advanced Visibility (Elasticsearch/OpenSearch) for list/search; optional in dev. **Dep:** AN-017. **AC:** "list failed runs by version in last 24h" served by visibility, not PG scan.

- **AN-076 · Ownership audit test (one-owner rule)** — _S · `area:state` `type:test`_
  Test asserts no execution-critical state read from PG/Redis on the resume path. **Dep:** AN-071. **AC:** dropping PG projections + Redis does not change workflow outcomes (only UI lags).

- **AN-077 · History endpoint + payload lazy-load** — _M · `area:runtime` `type:feat`_
  `GET /v1/runs/{id}/history` returns event stream; large payloads lazy-loaded from object store. **Dep:** AN-073. **AC:** history renders fast; big payloads fetched on demand.

---

## Epic E7 — Observability  *(Phase 4 · MVP · RFC-0007)*

**Outcome:** traces span the whole path; metrics/logs/cost/replay all live.

- **AN-078 · OTel bootstrap across all services** — _M · `area:observability` `type:infra` `prio:p0`_
  OTel SDK + collector in Compose; resource attributes per service. **Dep:** AN-004. **AC:** every service exports spans to the collector.

- **AN-079 · Temporal OTel interceptor (workflow→activity)** — _M · `area:observability` `type:feat`_
  Propagate trace context workflow→activity automatically. **Dep:** AN-078, AN-016. **AC:** activity spans are children of the workflow span.

- **AN-080 · Trace context propagation across the Ray boundary** — _L · `area:observability` `type:feat` `prio:p0`_
  Inject serialized trace context as a Ray task arg; re-activate inside the task (Ray doesn't auto-propagate). **Dep:** AN-079, AN-028. **AC:** ray_task + provider spans join the same trace unbroken (asserted).

- **AN-081 · Structured logging with run/node/trace correlation** — _M · `area:observability` `type:feat`_
  JSON logs carrying `run_id,node_id,attempt,worker_id,trace_id`; Ray task logs shipped + correlated. **Dep:** AN-078. **AC:** a run's logs filterable by run_id; correlate to its trace.

- **AN-082 · Prometheus metrics catalog (RED/USE/scheduler/cost)** — _M · `area:observability` `type:feat`_
  Per-node RED, worker USE, scheduler decisions, durability counters, cost. **Dep:** AN-078, AN-047. **AC:** all metrics from RFC-0001a §5.2 exported + documented.

- **AN-083 · Grafana dashboards** — _M · `area:observability` `type:infra`_
  Dashboards for runs, nodes, workers, scheduler, cost. **Dep:** AN-082. **AC:** provisioned dashboards render live data from a sample workload.

- **AN-084 · PII/secret redaction filter** — _M · `area:security` `type:feat`_
  Logging/trace pipeline redaction; deny-list + patterns. **Dep:** AN-081. **AC:** injected secret never appears in logs/traces (asserted).

- **AN-085 · Cost breakdown API + aggregation** — _M · `area:observability` `type:feat`_
  `GET /v1/runs/{id}/cost`; slice by node/model/provider; org rollups. **Dep:** AN-057. **AC:** breakdown matches ledger; org burndown correct.

- **AN-086 · Critical-path / bottleneck analysis** — _M · `area:observability` `type:feat`_
  Compute longest dependency chain over span tree; split queue-wait vs exec time per node. **Dep:** AN-080. **AC:** slowest chain identified; a "waiting for worker" node distinguished from "slow model".

- **AN-087 · WebSocket fan-out via Redis Streams (reconnect-safe)** — _L · `area:observability` `type:feat` `prio:p0`_
  `WS /v1/stream/runs/{id}` + `/workers`; clients track last-id, replay on reconnect. **Dep:** AN-070. **AC:** dropping + reconnecting the WS replays missed events; no gaps in DAG state.

- **AN-088 · Execution replay endpoint** — _M · `area:observability` `type:feat`_
  `POST /v1/runs/{id}/replay` runs `Replayer` vs current code for debug/repro. **Dep:** AN-021. **AC:** replay reports determinism pass/fail and reconstructs terminal state.

- **AN-089 · Observability integration tests** — _M · `area:observability` `type:test`_
  Assert unbroken trace, metric presence, log correlation, WS reconnect. **Dep:** AN-080, AN-087. **AC:** all four asserted in CI.

---

## Epic E8 — Web Dashboard  *(Phases 1–5 · MVP · RFC-0006)*

**Outcome:** polished, live, keyboard-first dashboard across all core screens.

- **AN-090 · Design system & layout primitives** — _M · `area:web` `type:feat`_
  Tokens, spacing, typography, status badges, command palette (⌘K), theme toggle. **Dep:** AN-003. **AC:** shared components documented in a Storybook/preview.
- **AN-091 · API client + typed SDK for web (generated from OpenAPI)** — _M · `area:web` `type:infra`_
  Codegen a typed client; React Query data layer. **Dep:** AN-018. **AC:** all endpoints typed; stale-while-revalidate wired.
- **AN-092 · WebSocket client with reconnect + last-id** — _M · `area:web` `type:feat`_
  Reconnect-safe live channel consuming Redis-Stream fan-out. **Dep:** AN-087. **AC:** survives network blips; resumes from last event.
- **AN-093 · Dashboard home (KPIs, queue, GPU, health, recent runs)** — _M · `area:web` `type:feat` `prio:p0`_
  RFC-0001 §15.1 layout with live tiles + sparklines. **Dep:** AN-091, AN-036. **AC:** matches wireframe; tiles update live.
- **AN-094 · Workflow DAG view (React Flow) with node states** — _L · `area:web` `type:feat` `prio:p0`_
  Interactive DAG; node badges (pending/running/completed/failed/retrying/waiting). **Dep:** AN-091, AN-071. **AC:** matches §15.2; edges + branches render.
- **AN-095 · DAG live animation via WS** — _M · `area:web` `type:feat`_
  Node transitions animate in real time. **Dep:** AN-094, AN-092. **AC:** starting a run animates without polling.
- **AN-096 · Node inspector (logs/input/output/metadata/retries/cost)** — _M · `area:web` `type:feat`_
  Click node → detail panel with streaming logs + retry ladder. **Dep:** AN-094, AN-077. **AC:** matches §15.2 inspector; streams live output.
- **AN-097 · DAG virtualization + minimap + collapsible fan-out** — _L · `area:web` `type:feat`_
  Handle 500+ node graphs; collapse map sub-DAGs; minimap. **Dep:** AN-094. **AC:** 500-node graph renders <100ms interaction latency.
- **AN-098 · History timeline scrubber + replay** — _L · `area:web` `type:feat`_
  Scrub events; view reconstructed state at event N; replay-from-selected. **Dep:** AN-077, AN-088. **AC:** matches §15.4; scrubbing shows state at each event.
- **AN-099 · Worker view (upgraded: warm-model residency, drain events)** — _M · `area:web` `type:feat`_
  Extend AN-036 with model residency + scale/drain timeline. **Dep:** AN-036. **AC:** shows which node holds which model; drain events listed.
- **AN-100 · Queue visualization** — _M · `area:web` `type:feat`_
  Per-queue depth, inflight, wait-time histograms. **Dep:** AN-035. **AC:** live queue depths per capability class.
- **AN-101 · Scheduler view** — _L · `area:web` `type:feat`_
  Priority lanes, admission decisions, per-provider rate-limit status, per-org budget burn. **Dep:** AN-049, AN-085. **AC:** answers "is my work stuck and why"; shows defer/reject reasons live.
- **AN-102 · Cost & budget view** — _M · `area:web` `type:feat`_
  Burndown per run/org, top-cost nodes/models, budget alerts. **Dep:** AN-085. **AC:** matches ledger; alert on threshold.
- **AN-103 · Plugin registry view** — _M · `area:web` `type:feat`_
  Installed node types, versions, sandbox tier, signature status, schemas. **Dep:** AN-121. **AC:** lists built-ins + custom; signature state visible.
- **AN-104 · Chaos Lab UI** — _L · `area:web` `type:feat` `prio:p0`_
  Scenario picker, blast-radius selector, live recovery timeline, expected-vs-actual RTO, pass/fail invariant. **Dep:** AN-130, AN-092. **AC:** matches §15.5 (upgraded); shows measured RTO + assertion result.
- **AN-105 · Empty/loading/error states + a11y + keyboard nav** — _M · `area:web` `type:chore`_
  Skeletons, error boundaries, focus management, ARIA. **Dep:** AN-093. **AC:** all screens keyboard-navigable; axe passes.

---

## Epic E9 — Plugin Runtime  *(Phase 5 · MVP/v2 · RFC-0005)*

**Outcome:** third-party nodes register, run sandboxed, are signed & versioned.

- **AN-106 · Plugin manifest schema & validation** — _M · `area:plugins` `type:feat`_
  `name@semver`, entrypoint/image, I/O schema, resource limits, capability manifest, sandbox tier. **Dep:** AN-050. **AC:** invalid manifest rejected with actionable error.
- **AN-107 · Plugin registry service + DB** — _M · `area:plugins` `type:feat`_
  `services/plugin-registry`; `plugin`/`node_type` tables; resolve `name@semver → entrypoint+schema+policy`. **Dep:** AN-106, AN-006. **AC:** publish/list/resolve/deprecate work; versions immutable.
- **AN-108 · Publish API with signature verification (Sigstore/cosign)** — _L · `area:security` `type:feat` `prio:p0`_
  `POST /v1/plugins`; verify signature + provenance before install. **Dep:** AN-107. **AC:** unsigned plugin rejected in prod mode; signed accepted; provenance recorded.
- **AN-109 · Tier T0 (in-process trusted) execution** — _S · `area:plugins` `type:feat`_
  Built-in nodes run in-process. **Dep:** AN-050. **AC:** built-ins execute at T0.
- **AN-110 · Tier T1 (subprocess + Ray runtime-env deps)** — _L · `area:plugins` `type:feat`_
  Per-plugin dependency isolation via Ray runtime environments; subprocess + resource cgroups. **Dep:** AN-107, AN-034. **AC:** two plugins with conflicting deps run without collision.
- **AN-111 · Capability manifest enforcement (network/fs default-deny)** — _M · `area:security` `type:feat`_
  Enforce declared network hosts/fs scope; deny by default. **Dep:** AN-110. **AC:** a plugin without network permission cannot reach the network.
- **AN-112 · Resource-limit enforcement (cpu/gpu/mem/timeout/concurrency)** — _M · `area:plugins` `type:feat`_
  Enforce manifest ceilings via Ray + activity timeouts + scheduler concurrency. **Dep:** AN-110, AN-038. **AC:** exceeding declared memory/timeout fails cleanly; concurrency capped.
- **AN-113 · Secrets-by-reference at execution time** — _M · `area:security` `type:feat`_
  Plugin declares secrets by ref; activity fetches from secrets manager; never in history. **Dep:** AN-110. **AC:** secret usable in node, absent from history (asserted).
- **AN-114 · Example custom node (cross-encoder rerank, GPU)** — _M · `area:plugins` `type:test`_
  Implements RFC-0001 §14.2 example end-to-end. **Dep:** AN-110, AN-034. **AC:** registers signed, runs in a workflow with declared GPU resources.
- **AN-115 · Plugin version pinning in workflow versions** — _M · `area:plugins` `type:feat`_
  `dag_spec` pins exact plugin versions; replay uses pinned code. **Dep:** AN-107, AN-020. **AC:** upgrading a plugin creates a new workflow version; old runs replay against old plugin.
- **AN-116 · Tier T2 (container/gVisor) — v2** — _XL · `area:plugins` `type:feat`_
  OCI-image-per-node untrusted isolation; default-deny network. **Dep:** AN-110. **AC (v2):** arbitrary untrusted node runs isolated; ShellNode always T2.

---

## Epic E10 — Chaos Engine  *(Phase 5 · MVP · RFC-0010)*

**Outcome:** chaos is a first-class, invariant-asserting, regression-tested feature.

- **AN-117 · Chaos controller service + injection API** — _M · `area:chaos` `type:feat` `prio:p0`_
  `services/chaos-controller`; `POST /v1/chaos/inject`, `GET /v1/chaos/experiments`; `chaos_event` table. **Dep:** AN-026. **AC:** inject records fault + measures recovery time.
- **AN-118 · Fault: kill workflow worker (scenario 1)** — _S · `area:chaos` `type:feat`_
  Terminate a workflow worker mid-task. **Dep:** AN-117, AN-023. **AC:** run resumes; RTO measured; invariant asserted.
- **AN-119 · Fault: kill activity worker inline/async (scenarios 2–3)** — _M · `area:chaos` `type:feat`_
  Kill inline and async-handed-off workers. **Dep:** AN-117, AN-037. **AC:** scenario 2 retries dup-safe; scenario 3 unaffected.
- **AN-120 · Fault: kill Ray node mid-task (scenario 4)** — _M · `area:chaos` `type:feat`_
  Remove a Ray node during compute. **Dep:** AN-117, AN-034. **AC:** task reschedules; autoscaler adds capacity if needed; recovery asserted.
- **AN-121 · Fault: GPU OOM (scenario 5)** — _M · `area:chaos` `type:feat`_
  Force OOM; reschedule on larger node / batch downshift via checkpoint. **Dep:** AN-117, AN-029. **AC:** resumes from last checkpoint; completes.
- **AN-122 · Fault: Redis restart (scenario 6)** — _M · `area:chaos` `type:test`_
  Restart Redis; assert fail-open buckets + stream catch-up + zero execution impact. **Dep:** AN-117, AN-070. **AC:** no workflow affected; projections/WS catch up.
- **AN-123 · Fault: Postgres restart (scenario 7)** — _M · `area:chaos` `type:test`_
  Restart PG; Temporal buffers; workflows pause then resume; projections reconcile. **Dep:** AN-117, AN-072. **AC:** no execution loss; projections consistent after.
- **AN-124 · Fault: Temporal unavailable (scenario 8)** — _M · `area:chaos` `type:test`_
  Make Temporal unreachable; workers back off; resume on return. **Dep:** AN-117, AN-013. **AC:** nothing lost; full progress on recovery.
- **AN-125 · Fault: provider 429 storm (scenario 9)** — _M · `area:chaos` `type:test`_
  Inject rate limits; assert governor prevents amplification. **Dep:** AN-117, AN-040. **AC:** no retry storm; eventual completion honoring `Retry-After`.
- **AN-126 · Fault: network partition worker↔Ray (scenario 10, v2)** — _M · `area:chaos` `type:feat`_
  Partition; retryable failure + re-dispatch. **Dep:** AN-117. **AC (v2):** no committed dup; recovers on heal.
- **AN-127 · Fault: slow-LLM latency injection (scenario 11, v2)** — _S · `area:chaos` `type:feat`_
  Inject latency; deadline-aware cancel/fallback. **Dep:** AN-117, AN-046. **AC (v2):** deadline honored; fallback used if configured.
- **AN-128 · Invariant assertion engine (0 lost / 0 dup) + RTO measurement** — _L · `area:chaos` `type:test` `prio:p0`_
  Generic harness asserting state integrity + side-effect uniqueness; records expected-vs-actual RTO. **Dep:** AN-118. **AC:** each scenario returns a pass/fail invariant + measured RTO.
- **AN-129 · Chaos regression suite in CI** — _M · `area:chaos` `type:test`_
  Run scenarios 1–9 as gating CI tests. **Dep:** AN-128, AN-125. **AC:** suite green gates releases; a regression fails the build.
- **AN-130 · Chaos scenario library + config API** — _S · `area:chaos` `type:feat`_
  Declarative scenario definitions surfaced to the UI. **Dep:** AN-117. **AC:** UI lists scenarios with blast-radius + expected RTO.

---

## Epic E11 — Deployment & Autoscaling  *(Phase 6 · v2 · RFC-0008)*

**Outcome:** Kubernetes deploy with autoscaling and HA.

- **AN-131 · Production Dockerfiles (multi-stage, non-root, SBOM)** — _M · `area:deploy` `type:infra`_
  Slim images per service; non-root; SBOM generated. **Dep:** AN-008. **AC:** images build reproducibly; SBOM attached; runs as non-root.
- **AN-132 · Helm chart skeleton for all services** — _L · `area:deploy` `type:infra` `prio:p0`_
  Chart with values for each service, config, secrets refs. **Dep:** AN-131. **AC:** `helm install` on kind brings up the stack healthy.
- **AN-133 · Temporal deployment (Helm or Cloud) integration** — _M · `area:deploy` `type:infra`_
  Wire self-hosted Temporal Helm or Temporal Cloud config. **Dep:** AN-132. **AC:** workers connect to the deployed Temporal.
- **AN-134 · KubeRay operator + autoscaled GPU/CPU pools** — _L · `area:deploy` `type:infra` `prio:p0`_
  RayCluster CRD; autoscaler on pending demand; distinct pools. **Dep:** AN-132, AN-034. **AC:** pending GPU demand scales up a GPU pool; idle scales down.
- **AN-135 · KEDA activity-worker autoscaling on queue backlog** — _M · `area:deploy` `type:infra`_
  Scale activity workers on Temporal queue depth metric. **Dep:** AN-047, AN-132. **AC:** backlog scales workers up; drain scales down with warm-model retention.
- **AN-136 · Postgres HA + backup/restore** — _M · `area:deploy` `type:infra`_
  HA Postgres (operator), PITR backups, restore runbook. **Dep:** AN-132. **AC:** failover tested; restore verified.
- **AN-137 · Redis (cluster) deployment** — _S · `area:deploy` `type:infra`_
  Redis for streams/buckets/cache. **Dep:** AN-132. **AC:** streams + buckets function under HA.
- **AN-138 · Object store (S3/MinIO) provisioning** — _S · `area:deploy` `type:infra`_
  Buckets, lifecycle, credentials via secrets. **Dep:** AN-073, AN-132. **AC:** payloads persist across pod restarts.
- **AN-139 · HPA for stateless tiers (API, web, scheduler)** — _S · `area:deploy` `type:infra`_
  HPA on RPS/CPU. **Dep:** AN-132. **AC:** load spike scales API; scales back down.
- **AN-140 · TLS/mTLS + cert-manager + ingress** — _M · `area:deploy` `type:infra`_
  Ingress with TLS; mTLS between services. **Dep:** AN-132. **AC:** external TLS; intra-cluster mTLS enforced.
- **AN-141 · Autoscaling load-test (scale up & down)** — _M · `area:deploy` `type:test`_
  Synthetic load exercises Ray + KEDA autoscaling. **Dep:** AN-134, AN-135. **AC:** scales up under load, down when idle; warm models retained.

---

## Epic E12 — Security & Multi-tenancy  *(Phase 6 · v2 · RFC-0009)*

**Outcome:** authn/z, tenant isolation, secrets, supply chain.

- **AN-142 · OIDC/SSO login** — _M · `area:security` `type:feat` `prio:p0`_
  OIDC provider integration; session/token handling. **Dep:** AN-005. **AC:** login via OIDC; token refresh works.
- **AN-143 · RBAC model (org→project→run scopes)** — _L · `area:security` `type:feat` `prio:p0`_
  Roles/permissions; enforcement middleware; RBAC tables. **Dep:** AN-142, AN-006. **AC:** authz matrix enforced; unauthorized denied with 403.
- **AN-144 · API key management** — _M · `area:security` `type:feat`_
  Create/rotate/revoke scoped API keys; `api_key` table. **Dep:** AN-143. **AC:** keys scoped to project; revocation immediate.
- **AN-145 · Tenant isolation (Temporal namespaces + queues + Ray env)** — _L · `area:security` `type:feat`_
  Per-tenant namespaces, task-queue prefixes, Ray runtime-env boundaries. **Dep:** AN-033, AN-143. **AC:** tenant A cannot see/affect tenant B's runs/workers.
- **AN-146 · Row-level project scoping in projections** — _M · `area:security` `type:feat`_
  All PG reads scoped by project; enforced at repository layer. **Dep:** AN-143, AN-071. **AC:** cross-project data access impossible via API.
- **AN-147 · Secrets manager integration (external-secrets)** — _M · `area:security` `type:infra`_
  Fetch provider keys/DB creds from a manager; rotation. **Dep:** AN-113. **AC:** no secret in env dumps/history; rotation without downtime.
- **AN-148 · Network policies (default-deny) in K8s** — _M · `area:security` `type:infra`_
  Zero-trust intra-cluster policies. **Dep:** AN-132. **AC:** only declared service-to-service traffic allowed.
- **AN-149 · Encryption at rest + in transit** — _S · `area:security` `type:infra`_
  DB/object-store encryption; TLS everywhere. **Dep:** AN-140, AN-136. **AC:** verified encrypted at rest; TLS on all hops.
- **AN-150 · Immutable audit log (event-sourced) + export** — _M · `area:security` `type:feat`_
  Surface Temporal history as tamper-evident audit; export API. **Dep:** AN-077. **AC:** every state change/approval/retry has actor+timestamp; exportable.
- **AN-151 · Supply-chain: image signing + provenance + SBOM gate** — _M · `area:security` `type:infra`_
  Sign images (cosign), SLSA provenance, CI gate on SBOM. **Dep:** AN-131, AN-108. **AC:** unsigned image blocked in deploy; provenance verifiable.

---

## Epic E13 — Declarative SDK & v2 Nodes  *(Phase 7 · v2 · RFC-0004)*

**Outcome:** declarative authoring, full scheduler, LangGraph adapter.

- **AN-152 · Declarative Graph SDK + `g.compile()` → dag_spec** — _L · `area:sdk` `type:feat` `prio:p0`_
  `Graph`, `node.*`, `depends_on`, `map_over`, `edge().on()`; compiles to validated `dag_spec`. **Dep:** AN-020, AN-060. **AC:** RFC-0001 §13.2 example compiles + runs.
- **AN-153 · Declarative↔imperative parity test** — _M · `area:sdk` `type:test`_
  Same workflow both ways → identical histories. **Dep:** AN-152, AN-021. **AC:** histories match byte-for-byte on equivalent inputs.
- **AN-154 · LangGraph adapter** — _L · `area:sdk` `type:feat`_
  Compile a LangGraph graph to an Ancora workflow. **Dep:** AN-152. **AC:** a sample LangGraph runs durably on Ancora unchanged in behavior.
- **AN-155 · Budget governor hard mode** — _M · `area:scheduler` `type:feat`_
  `budget_policy=hard` → non-retryable `BudgetExceeded` at node. **Dep:** AN-045, AN-057. **AC:** run halts at budget with clear terminal error.
- **AN-156 · Deadline-aware draining + cancellation** — _M · `area:scheduler` `type:feat`_
  Near-deadline priority boost; past-deadline cancel remaining nodes. **Dep:** AN-046, AN-030. **AC:** deadline honored end-to-end (scenario 11).
- **AN-157 · EmbeddingNode with Ray auto-batching** — _M · `area:nodes` `type:feat`_
  Batch vectorization on Ray; dynamic batch sizing. **Dep:** AN-050, AN-034. **AC:** throughput scales with batch size; correctness preserved.
- **AN-158 · RetrievalNode (pgvector + pluggable backends)** — _M · `area:nodes` `type:feat`_
  Vector/keyword search; backend interface. **Dep:** AN-050. **AC:** retrieval returns ranked results from pgvector.
- **AN-159 · ToolCallNode + WebhookNode + ShellNode(T2)** — _M · `area:nodes` `type:feat`_
  Schema-validated tool calls; at-least-once webhook w/ dedupe; sandboxed shell. **Dep:** AN-116, AN-061. **AC:** each runs; ShellNode isolated at T2.
- **AN-160 · Run comparison / diff (eval) UI** — _L · `area:web` `type:feat`_
  Compare two runs of a version: path, cost, latency, outputs. **Dep:** AN-098, AN-102. **AC:** diff highlights divergent nodes + cost/latency deltas.

---

## Epic E14 — Hardening, Docs & Release  *(Phase 8 · v1.0)*

**Outcome:** production credibility; `v1.0-rc`.

- **AN-161 · 24h soak test with periodic chaos** — _L · `area:chaos` `type:test` `prio:p0`_
  Long-running workload + injected faults; assert zero lost workflows. **Dep:** AN-129. **AC:** soak passes; no lost/duplicated work; leak-free.
- **AN-162 · Performance benchmarks + published results** — _M · `area:observability` `type:test`_
  Throughput/latency/overhead vs raw loop; document. **Dep:** AN-141. **AC:** benchmark suite reproducible; results in docs.
- **AN-163 · Failure-mode runbooks** — _M · `area:docs` `type:chore`_
  Runbook per chaos scenario + infra failure. **Dep:** AN-129. **AC:** each scenario has detection→mitigation→recovery steps.
- **AN-164 · Full API reference (generated OpenAPI) + SDK docs** — _M · `area:docs` `type:chore`_
  Publish OpenAPI + SDK API docs + guides. **Dep:** AN-018. **AC:** every endpoint + SDK symbol documented; examples run.
- **AN-165 · Tutorials + example gallery** — _M · `area:docs` `type:chore` `good-first-issue`_
  Quickstart, research-agent, batch-eval, custom-node, chaos walkthrough. **Dep:** AN-059, AN-114. **AC:** a new user reaches a running durable workflow in <15 min.
- **AN-166 · Upgrade & workflow-versioning guide** — _S · `area:docs` `type:chore`_
  Document `continue-as-new`, patching, plugin version pinning. **Dep:** AN-115. **AC:** safe-upgrade procedure documented + validated.
- **AN-167 · Release automation (semver, changelog, signed artifacts)** — _M · `area:repo` `type:infra`_
  Tagged releases, changelog, signed images/wheels. **Dep:** AN-151. **AC:** `v1.0-rc` produced by CI with signed artifacts.
- **AN-168 · Governance, security policy, disclosure process** — _S · `area:docs` `type:chore`_
  SECURITY.md, disclosure flow, maintainer governance. **Dep:** AN-012. **AC:** policies published; disclosure inbox live.
- **AN-169 · Load-test harness for scheduler & rate limits** — _M · `area:scheduler` `type:test`_
  Reproducible harness for fairness/backpressure/rate-limit at scale. **Dep:** AN-049. **AC:** harness demonstrates fairness + no starvation under load.
- **AN-170 · v1.0-rc release checklist & sign-off** — _S · `area:repo` `type:chore` `prio:p0`_
  Gate: chaos suite green, soak passed, docs complete, security review done. **Dep:** AN-161, AN-164, AN-151. **AC:** checklist satisfied; `v1.0-rc` tagged.

---

## Rollup

| Epic | Issues | Milestone focus |
|---|---|---|
| E0 Foundation | 12 | MVP |
| E1 Durability Core | 12 | MVP |
| E2 Runtime & Ray Bridge | 13 | MVP |
| E3 Scheduler | 12 | MVP |
| E4 Node Library | 11 | MVP |
| E5 Idempotency & HITL | 7 | MVP |
| E6 State & Projections | 10 | MVP |
| E7 Observability | 12 | MVP |
| E8 Web Dashboard | 16 | MVP |
| E9 Plugin Runtime | 11 | MVP/v2 |
| E10 Chaos Engine | 14 | MVP |
| E11 Deployment | 11 | v2 |
| E12 Security | 10 | v2 |
| E13 Declarative SDK | 9 | v2 |
| E14 Hardening & Release | 10 | v1.0 |
| **Total** | **170** | — |

**Critical path to the durability demo (the pitch):** AN-001 → AN-004 → AN-013 → AN-014 → AN-015 → AN-026 → AN-028 → AN-037 → AN-051/052/055 → AN-059 → AN-117 → AN-128. Everything else parallelizes around it.
