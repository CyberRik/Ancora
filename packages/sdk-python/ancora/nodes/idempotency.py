"""Deterministic idempotency-key derivation (AN-062).

A side-effecting node must produce the *same* key every time the same logical
call is attempted — across retries, across worker restarts, across replay — so the
inbox guard (AN-061) can collapse duplicates to a single effect. The key is a hash
of ``(workflow_id, node_id, canonical(input))``; identical inputs at the same node
in the same run always hash the same, and nothing else does.

The canonicalization is stable regardless of dict ordering or Pydantic vs. plain
dict input, so a key computed in workflow code matches one recomputed inside the
activity on a retry.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_json(value: Any) -> str:
    """Serialize ``value`` deterministically (sorted keys, no whitespace)."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_fallback)


def _fallback(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, set | frozenset):
        return sorted(obj, key=repr)
    return str(obj)


def derive_idempotency_key(
    *,
    workflow_id: str,
    node_id: str,
    payload: Any,
    override: str | None = None,
) -> str:
    """Return a stable key for one logical node call.

    ``override`` lets a caller pin custom semantics (e.g. an external request id);
    when given it is used verbatim so callers can dedupe on their own identity.
    """
    if override is not None:
        return override
    digest = hashlib.sha256()
    digest.update(workflow_id.encode())
    digest.update(b"\x00")
    digest.update(node_id.encode())
    digest.update(b"\x00")
    digest.update(canonical_json(payload).encode())
    return f"{node_id}-{digest.hexdigest()[:32]}"
