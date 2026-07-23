"""Node-type catalog (AN-058).

Lists every node type the runtime can execute, with its JSON schemas, declared
resources, sandbox tier, and idempotency. Today that is the five built-ins; the
same endpoint serves third-party plugins once the registry lands in Phase 5,
which is why the response carries an ``origin`` discriminator from day one.

The API imports ``ancora.nodes`` purely to trigger registration — the catalog is
whatever the SDK says it is, so a node cannot appear here without being runnable,
and cannot be runnable without appearing here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

import ancora.nodes  # noqa: F401 — import registers the built-in node types
from ancora.nodes.base import NodeSchema
from ancora.nodes.registry import catalog
from ancora_api.schemas import NodeTypeOut

router = APIRouter(prefix="/v1", tags=["plugins"])


def _to_out(schema: NodeSchema) -> NodeTypeOut:
    return NodeTypeOut(**schema.model_dump(mode="json"), origin="builtin")


@router.get("/plugins", response_model=list[NodeTypeOut])
async def list_plugins() -> list[NodeTypeOut]:
    """Every registered node type, alphabetically."""
    return sorted((_to_out(s) for s in catalog()), key=lambda n: n.type_name)


@router.get("/plugins/{type_name}", response_model=NodeTypeOut)
async def get_plugin(type_name: str) -> NodeTypeOut:
    for schema in catalog():
        out = _to_out(schema)
        if out.type_name == type_name:
            return out
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"node type '{type_name}' is not registered"
    )
