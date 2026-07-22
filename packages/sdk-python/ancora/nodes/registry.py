"""Built-in node catalog (AN-058).

A process-wide registry of node *types* keyed by ``type_name``. The activity
worker looks a node up by name to execute it; the API gateway lists the catalog
(with JSON schemas) at ``GET /v1/plugins`` for discovery. Registration validates
that a node declared its I/O models, so a malformed node is rejected at import
rather than at first use.
"""

from __future__ import annotations

from ancora.nodes.base import Node, NodeSchema

_REGISTRY: dict[str, type[Node]] = {}


def register(node_cls: type[Node]) -> type[Node]:
    """Register a node type. Usable as a decorator. Idempotent for the same class."""
    node_cls._check_declared()
    existing = _REGISTRY.get(node_cls.type_name)
    if existing is not None and existing is not node_cls:
        raise ValueError(
            f"node type '{node_cls.type_name}' already registered by {existing.__name__}"
        )
    _REGISTRY[node_cls.type_name] = node_cls
    return node_cls


def get(type_name: str) -> type[Node]:
    try:
        return _REGISTRY[type_name]
    except KeyError:
        raise KeyError(f"no node registered for type '{type_name}'") from None


def all_nodes() -> list[type[Node]]:
    return list(_REGISTRY.values())


def catalog() -> list[NodeSchema]:
    """Discovery records for every registered node, sorted by type name."""
    return [cls.schema() for cls in sorted(_REGISTRY.values(), key=lambda c: c.type_name)]


def clear() -> None:
    """Test helper — empty the registry."""
    _REGISTRY.clear()
