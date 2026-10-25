# Changelog

All notable changes to Ancora are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Each release is tagged in git as `vX.Y.Z`.

## [Unreleased]

### Added
- CodeQL static analysis on every push, pull request and weekly.
- Dependabot updates for Python, web, GitHub Actions and Docker dependencies.

### Changed
- Vulnerabilities are now reported privately through GitHub's Security tab
  instead of a placeholder email (see `SECURITY.md`).

## [0.1.0] - 2026-08-27

First tagged release: the durable core, the execution runtime, observability
and the chaos engine. Pre-1.0, so only `main` is supported (see `SECURITY.md`).

### Added
- **Durable core:** start a workflow, run activities, and resume after a worker
  is killed mid-run, with a durability smoke test.
- **Execution runtime:** Temporal activity workers with dual-mode Ray compute,
  async task completion, heartbeat checkpointing and capability-based queue routing.
- **Scheduler and node library:** built-in nodes, deterministic node policy
  resolution (routing, retries, deadlines) and an exactly-once inbox.
- **Observability:** event-sourced run projections, a live run DAG over
  WebSocket, OpenTelemetry tracing across the Ray boundary, Prometheus metrics and
  Grafana dashboards, critical-path analysis and replay verification.
- **Chaos engine:** chaos experiments that assert recovery invariants, and fast
  failover through worker heartbeats (about 60s to about 6s kill detection).
- **Dashboard:** resilience views, Chaos Lab, recovery explanations and the run graph.
- REST and WebSocket API under `/v1`, documented through OpenAPI at `/docs`.
- Gemini LLM provider and example workflows.

### Security
- No known vulnerabilities were fixed in this release.

[Unreleased]: https://github.com/CyberRik/Ancora/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CyberRik/Ancora/releases/tag/v0.1.0
