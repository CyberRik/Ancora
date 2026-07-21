# Contributing to Ancora

Thanks for helping build Ancora. This guide gets you from clone to green PR.

## Ground rules

- **Trunk-based, small PRs.** Branch off `main`, keep PRs focused, rebase before merge.
- **`main` is always releasable.** Guard half-built work behind feature flags.
- **Every durability-touching change ships a replay test; every failure-path change ships a chaos assertion.** This is non-negotiable — it's the whole product.
- **Design before code for anything large.** Land the relevant RFC (`docs/RFC-*`) first.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.11+), [pnpm](https://pnpm.io/) + Node 18+, Docker + Docker Compose.

## Setup

```bash
make install                 # uv sync + pnpm install
uv run pre-commit install    # commit-time gate
uv run pre-commit install --hook-type commit-msg
```

## The local gate (run before pushing)

```bash
make lint typecheck test     # mirrors CI exactly
make up                      # optional: run the full stack
```

## Commit messages

Conventional Commits, enforced by a hook:

```
feat(scheduler): add per-provider rate-limit governor
fix(api): return 503 from /readyz when the DB is down
docs(rfc): amend the bridge design to async completion
```

Types: `feat`, `fix`, `infra`, `chore`, `docs`, `spec`, `test`, `refactor`.

## Working an issue

1. Pick an `AN-###` issue from [`docs/PROJECT-PLAN-github-issues.md`](docs/PROJECT-PLAN-github-issues.md); confirm its dependencies are closed.
2. Open a PR that references it (`Closes #AN-###`) and satisfies the issue's acceptance criteria.
3. Fill in the PR checklist. Get one review. Keep CI green.

## Code style

- **Python:** ruff (lint + format), mypy `strict`. Typed public APIs, Pydantic models at boundaries.
- **TypeScript:** eslint (`next/core-web-vitals`), Prettier, `tsc --noEmit` clean.
- **Migrations:** forward-only, downgrade-tested, no destructive change without a backfill.

## Reporting bugs / proposing features

Use the issue templates. For security issues, follow `SECURITY.md` (do not open a public issue).
