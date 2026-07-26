"""Ancora event consumer (Phase 4).

Drains the Redis Streams event bus into the durable ``run_event`` projection and
reconciles run status from Temporal history — the two halves of "projections are
authoritative for UI reads, and healed from the source of truth."
"""
