# Security Policy

## Reporting a vulnerability

**Do not open a public issue for security problems.** Instead, email
`security@ancora.dev` (placeholder) with:

- a description of the issue and its impact,
- steps to reproduce,
- affected version / commit SHA.

We aim to acknowledge within 3 business days and to provide a remediation
timeline after triage. Coordinated disclosure is appreciated; please give us a
reasonable window to ship a fix before any public write-up.

## Supported versions

Pre-1.0: only `main` is supported. A formal support policy lands with `v1.0`
(see the hardening phase in `docs/IMPLEMENTATION-PLAN.md`).

## Scope reminder

Ancora executes user-supplied workflow and plugin code. The plugin sandbox model
(isolation tiers, signing, capability manifests) is specified in RFC-0001a §6 and
lands across Phase 5 / v2. Until then, **run only trusted workflows and nodes.**
