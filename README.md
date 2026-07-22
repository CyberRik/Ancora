<h1 align="center">⚓ Ancora</h1>
<p align="center"><b>A fault-tolerant runtime for durable AI workflows.</b></p>
<p align="center"><i>Temporal's durable execution + Ray's distributed compute, behind one AI-native programming model.</i></p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="docs/RFC-0001-durable-ai-runtime.md">Architecture (RFC-0001)</a> ·
  <a href="docs/RFC-0001a-architecture-review.md">Review (RFC-0001a)</a> ·
  <a href="docs/IMPLEMENTATION-PLAN.md">Roadmap</a>
</p>

> **Status:** Phase 0 — walking skeleton. The stack boots; the durability engine lands in Phase 1+. See the [implementation plan](docs/IMPLEMENTATION-PLAN.md).

---

## Why

Modern AI agents and pipelines are unreliable: an LLM call 500s, a GPU worker OOMs, a provider rate-limits, a pod is evicted — and the whole multi-step, multi-dollar computation is lost. Ancora makes that loss structurally impossible. A workflow's every non-deterministic, side-effecting step is recorded as an immutable event; if anything dies, execution **replays to exact state and continues where it stopped** — with heavy work fanned out across distributed GPU/CPU workers.

Ancora is **not** an agent framework. It is the runtime *underneath* agent frameworks. See [RFC-0001](docs/RFC-0001-durable-ai-runtime.md).

## Architecture at a glance

- **Durability core — Temporal:** event-sourced workflow state, deterministic replay, retries, timers, signals, human-in-the-loop.
- **Compute core — Ray:** distributed & GPU-aware execution of the heavy work.
- **The bridge:** deterministic workflows schedule **activities**; activities dispatch to Ray (with async completion for long jobs) — the idempotency seam between "exactly-once progress" and "efficient at-least-once compute."

## Repository layout

```
packages/sdk-python/   # ancora — workflow/activity/node authoring SDK
packages/cli/          # ancora — command-line interface
services/api-gateway/  # FastAPI: REST + (later) WebSocket control plane
web/                   # Next.js dashboard
deploy/docker/         # local stack (Temporal, Postgres, Redis, API, web)
docs/                  # RFCs, architecture review, implementation plan
```

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
| Temporal UI | http://localhost:8233 |

### Docker commands

`make up` / `make down` / `make logs` are thin wrappers over Docker Compose. To
drive it directly (compose file lives in `deploy/docker/`):

```bash
# from the repo root — start everything, rebuilding changed images
docker compose -f deploy/docker/docker-compose.yml up --build

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
# migrate api worker activity-worker web ray-head)
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

| Service | Port | What it is |
|---|---|---|
| `web` | 3000 | Next.js dashboard |
| `api` | 8080 | FastAPI control plane |
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

Phased delivery in [`docs/IMPLEMENTATION-PLAN.md`](docs/IMPLEMENTATION-PLAN.md); issue backlog in [`docs/PROJECT-PLAN-github-issues.md`](docs/PROJECT-PLAN-github-issues.md). **By end of Phase 3 you can kill any worker mid-run and watch the workflow recover** — that's the whole point.

## License

[Apache-2.0](LICENSE).
