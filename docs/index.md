# Ancora

**A fault-tolerant runtime for durable AI workflows** — Temporal's durable execution + Ray's distributed compute, behind one AI-native programming model.

Ancora is the runtime *underneath* AI applications: if an LLM call fails, a GPU worker crashes, a provider rate-limits, or a pod restarts, the workflow never loses state — it replays to exact state and continues where it stopped.

## Start here

- **[RFC-0001 — Vision & Architecture](RFC-0001-durable-ai-runtime.md)** — the north-star design.
- **[RFC-0001a — Architecture Review](RFC-0001a-architecture-review.md)** — the pre-implementation critique and corrections (this wins where it disagrees with RFC-0001).
- **[Implementation Plan](IMPLEMENTATION-PLAN.md)** — phased delivery; each phase leaves the repo working.
- **[GitHub Project Plan](PROJECT-PLAN-github-issues.md)** — the ~170-issue backlog.

## Status

Phase 0 — walking skeleton. `docker compose up` boots Temporal + Postgres + Redis + API + dashboard. The durable engine lands in Phase 1.
