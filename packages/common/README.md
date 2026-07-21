# ancora-common

Shared **server-side** library used by the API gateway and the workflow workers:

- `settings` — `CommonSettings` (DB URL, Temporal address/namespace, task queue).
- `db` — async SQLAlchemy engine, session scope, health ping.
- `models` — the ORM schema (tenancy roots + workflow catalog/run projection).
- `temporal` — Temporal client factory (with the Pydantic data converter) + retrying connect.
- `catalog` — register/version workflow definitions; create/update run projections.

This is **not** the user-facing SDK (that is `ancora`). Nothing here should be imported
into deterministic workflow code.
