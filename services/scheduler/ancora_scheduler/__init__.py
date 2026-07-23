"""Ancora scheduler — admission control for node dispatch (Phase 3, RFC-0002).

Temporal guarantees a node *eventually* runs and runs exactly once. It has no
opinion about whether running it *right now* is a good idea. That is this
service's job: it decides admit / defer / reject against provider rate limits,
queue watermarks, tenant fair shares, budgets, and deadlines — the constraints
that live outside the durability model.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
