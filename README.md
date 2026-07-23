<h1 align="center">⚓ Ancora</h1>
<p align="center"><b>A fault-tolerant runtime for durable AI workflows.</b></p>
<p align="center"><i>Temporal's durable execution + Ray's distributed compute, behind one AI-native programming model.</i></p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="docs/RFC-0001-durable-ai-runtime.md">Architecture (RFC-0001)</a> ·
  <a href="docs/RFC-0001a-architecture-review.md">Review (RFC-0001a)</a> ·
  <a href="docs/IMPLEMENTATION-PLAN.md">Roadmap</a>
</p>

> **Status:** Phase 3 — scheduler, built-in node library, and idempotency. Kill any
> worker at any node and the run resumes with zero duplicated effects. See the
> [implementation plan](docs/IMPLEMENTATION-PLAN.md).

---

## Why

Modern AI agents and pipelines are unreliable: an LLM call 500s, a GPU worker OOMs, a provider rate-limits, a pod is evicted — and the whole multi-step, multi-dollar computation is lost. Ancora makes that loss structurally impossible. A workflow's every non-deterministic, side-effecting step is recorded as an immutable event; if anything dies, execution **replays to exact state and continues where it stopped** — with heavy work fanned out across distributed GPU/CPU workers.

Ancora is **not** an agent framework. It is the runtime *underneath* agent frameworks. See [RFC-0001](docs/RFC-0001-durable-ai-runtime.md).

## Architecture at a glance

- **Durability core — Temporal:** event-sourced workflow state, deterministic replay, retries, timers, signals, human-in-the-loop.
- **Compute core — Ray:** distributed & GPU-aware execution of the heavy work.
- **The bridge:** deterministic workflows schedule **activities**; activities dispatch to Ray (with async completion for long jobs) — the idempotency seam between "exactly-once progress" and "efficient at-least-once compute."
- **The governor — scheduler:** Temporal guarantees a node *eventually* runs, exactly once. It has no opinion on whether running it **now** is wise. The scheduler decides that against provider rate limits, queue watermarks, tenant fair shares, budgets, and deadlines — and expresses "not yet" as a durable deferral, not a dropped request.

## Repository layout

```
packages/sdk-python/   # ancora — workflow/activity/node authoring SDK + node library
packages/common/       # shared server library — ORM, projections, rate limiter, inbox
packages/cli/          # ancora — command-line interface
services/api-gateway/  # FastAPI: REST + (later) WebSocket control plane
services/scheduler/    # admission control: rate limits, backpressure, fairness, budgets
services/workflow-workers/   # orchestration workers (deterministic workflow code)
services/activity-workers/   # execution workers (nodes, Ray dispatch)
web/                   # Next.js dashboard
deploy/docker/         # local stack (Temporal, Postgres, Redis, API, scheduler, web)
deploy/scheduler/      # declarative scheduler policy (hot-reloaded)
docs/                  # RFCs, architecture review, implementation plan
```

## The node library

Workflows orchestrate; **nodes** do the side-effecting work, inside activities, so
Temporal can retry and replay them safely. Five ship built in:

| Node | What it does | Notes |
|---|---|---|
| `llm` | Chat/completion across providers | Primary→secondary fallback chain; token/cost accounting; mock provider for CI |
| `http` | REST call with templating | Honours `Retry-After`; 4xx terminal, 5xx/429 transient |
| `database` | One parameterized statement | Named datasources only; bound params only; read/write split |
| `python` | A registered Python callable | Allow-listed by name, never an importable path; optional subprocess + memory cap |
| `approval` | Durable human gate | Resolved by signal in workflow code; optional expiry branch |

Browse them (with JSON schemas) at `GET /v1/plugins`, or in the dashboard's
**Nodes** view.

## Quickstart

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/) + Docker Compose. (For local dev outside Docker: [uv](https://docs.astral.sh/uv/), Python 3.11+, [pnpm](https://pnpm.io/), Node 18+.)

### Run the whole stack

```bash
git clone https://github.com/ancora/ancora.git
cd ancora
make up          # docker compose up --build
```

Then open:

| Service | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| API health | http://localhost:8080/healthz |
| API version | http://localhost:8080/v1/version |
| Chaos Lab | http://localhost:3000/chaos |
| Approvals | http://localhost:3000/approvals |
| Scheduler state | http://localhost:8090/v1/scheduler/state |
| Scheduler metrics | http://localhost:8090/metrics |
| Temporal UI | http://localhost:8233 |

### Docker commands

`make up` / `make down` / `make logs` are thin wrappers over Docker Compose. To
drive it directly (compose file lives in `deploy/docker/`):

```bash
# from the repo root — start everything, rebuilding changed images
docker compose -f deploy/docker/docker-compose.yml up --build

# start everything and renew anonymous volumes (useful for clean starts)
docker compose -f deploy/docker/docker-compose.yml up --build --renew-anon-volumes

# run in the background (detached), then follow logs
docker compose -f deploy/docker/docker-compose.yml up --build -d
docker compose -f deploy/docker/docker-compose.yml logs -f

# stop the stack; add -v to also wipe the Postgres volume (fresh DB)
docker compose -f deploy/docker/docker-compose.yml down
docker compose -f deploy/docker/docker-compose.yml down -v
```

> **Tip:** export `COMPOSE_FILE=deploy/docker/docker-compose.yml` once and you can
> drop the `-f …` flag from every command below.

Everyday operations:

```bash
# see what's running / crashed
docker compose -f deploy/docker/docker-compose.yml ps

# tail one service's logs (services: postgres redis temporal temporal-ui
# migrate api scheduler worker activity-worker web ray-head)
docker compose -f deploy/docker/docker-compose.yml logs -f api

# rebuild + restart just one service after a code change
docker compose -f deploy/docker/docker-compose.yml up -d --build activity-worker

# apply DB migrations on demand (also runs automatically on `up`)
docker compose -f deploy/docker/docker-compose.yml run --rm migrate

# open a shell inside a running container
docker compose -f deploy/docker/docker-compose.yml exec api bash
```

Optional **Ray head node** (distributed/GPU backend) is behind a profile, so it
only starts when asked:

```bash
docker compose -f deploy/docker/docker-compose.yml --profile ray up --build
# Ray dashboard → http://localhost:8265 · Ray client → localhost:10001
```

Without the `ray` profile, activity workers use the in-process local backend — no
Ray required for dev or CI.

### Kill a worker (the whole point)

The easiest way is the **Chaos Lab** in the dashboard
([localhost:3000/chaos](http://localhost:3000/chaos)): start a run, press
`SIGKILL` on a worker, watch the run finish anyway, press `Restart`. The kill is
real — the API asks the Docker daemon to `SIGKILL` the container, so the worker
gets no chance to drain or acknowledge.

The Lab then shows you the recovery as it happens, because the interesting part
is a *pause* and a pause is easy to misread as a failure. You get a live
countdown of the clock the run is actually waiting on, and a time axis where the
attempt that died with its worker is drawn at full width next to the attempt that
replaced it. The same view is on every run page under **Recovery**.

> Chaos injection needs the Docker socket, which lets the API control this host's
> containers. It is therefore **off unless `ANCORA_CHAOS_ENABLED=true`**, which
> the local compose stack sets and nothing else should. Even then it is scoped to
> this Compose project and an allow-list of worker services — it cannot touch
> Postgres, Temporal, or another stack.

From the terminal:

```bash
C="docker compose -f deploy/docker/docker-compose.yml"

# 1. start a run that does real work and then parks at a human gate
curl -s -X POST localhost:8080/v1/workflows/research_agent/runs \
  -H 'Content-Type: application/json' \
  -d '{"input":{"topic":"durable execution","summaries":2}}'

# 2. SIGKILL a worker mid-flight — no drain, no warning
$C kill activity-worker      # the one running nodes
$C kill worker               # the one running workflow code
$C kill worker activity-worker   # or both at once

# 3. the run does not fail. Watch it stay Running with nothing alive to serve it:
curl -s localhost:8080/v1/runs/<run-id> | jq .status

# 4. bring the worker back — it picks the run up from history
$C start activity-worker worker
```

Two things to know when you try this:

- **A manually killed container does not come back on its own.** Docker treats
  `kill`/`stop` as intentional, so `restart: on-failure` does not fire — you
  restart it with `$C start`. (A worker that *crashes* on its own does restart.)
- **Resume is not always instant.** Work that had already completed is replayed
  from history immediately and never re-executed. But an activity that was
  *in flight* when the process died is only rescheduled once its
  `start_to_close_timeout` elapses — Temporal cannot tell a dead worker from a
  slow one any sooner. LLM nodes allow 5 minutes per attempt, so an unlucky kill
  looks idle for a while before it retries. Heartbeats are what shrink that
  window, which is why long-running nodes declare one.

`GET /v1/runs/{id}/recovery` is the machine-readable form of the Recovery view:
every attempt on a time axis, the fleet events around them, and the clock any
pending work is blocked on. It distinguishes the three waits that look identical
from outside — `queued` (nobody polling; free, clears instantly), `detecting`
(an attempt stranded on a process that is gone; costs one timeout), and
`backoff` (the retry policy holding the next attempt back). Only `detecting` is
a design decision, and its length is the node's own timeout.

Verify no work was duplicated afterwards with
`GET /v1/runs/{id}/cost` — one ledger line per node that actually executed.

### Tuning admission control

Scheduler policy — provider rate limits, queue watermarks, tenant weights,
budgets — lives in [`deploy/scheduler/policy.yaml`](deploy/scheduler/policy.yaml).
It is re-read whenever its mtime changes, so **edit and save and the next
admission uses it**; no restart. A document that fails validation is rejected and
the previous policy keeps serving, with the error at
`GET /v1/scheduler/config`. See [`services/scheduler/README.md`](services/scheduler/README.md).

The worker-side client **fails open**: if the scheduler is down, nodes are
admitted anyway and the degradation is logged. Admission control protects
providers and queues; it must never become the thing that halts a durable fleet.

| Service | Port | What it is |
|---|---|---|
| `web` | 3000 | Next.js dashboard |
| `api` | 8080 | FastAPI control plane |
| `scheduler` | 8090 | Admission control + Prometheus metrics |
| `temporal-ui` | 8233 | Temporal web UI |
| `temporal` | 7233 | Temporal gRPC frontend |
| `postgres` | 5432 | Catalog + run projection |
| `redis` | 6379 | Worker liveness |
| `ray-head` | 8265 / 10001 | Ray dashboard / client (profile `ray`) |

### Local development (without Docker)

```bash
make install     # uv sync + pnpm install
make lint typecheck test   # the CI gate, locally
uv run pre-commit install  # enable the commit-time hooks

# API only:
uv run uvicorn ancora_api.main:app --reload --port 8080 --app-dir services/api-gateway

# Web only:
cd web && pnpm dev
```

## Development

| Command | Does |
|---|---|
| `make install` | Install Python + web deps |
| `make lint` | ruff + eslint |
| `make typecheck` | mypy (strict) + tsc |
| `make test` | pytest + Playwright smoke |
| `make up` / `make down` | Start / stop the local stack |
| `make docs` | Serve the docs site |

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Roadmap

Phased delivery in [`docs/IMPLEMENTATION-PLAN.md`](docs/IMPLEMENTATION-PLAN.md); issue backlog in [`docs/PROJECT-PLAN-github-issues.md`](docs/PROJECT-PLAN-github-issues.md).

**Phase 3 is done: kill any worker at any node and the run resumes with zero
duplicated effects** — that's the whole point. Next up is Phase 4: the
event-sourced projection pipeline, OTel tracing across the Ray boundary, and a
live-animating DAG.

## License

[Apache-2.0](LICENSE).
