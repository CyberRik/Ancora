"""Ancora shared server-side library (DB, models, Temporal client, catalog)."""

from __future__ import annotations

import uuid

__version__ = "0.1.0"

# Fixed identifiers for the Phase 1 single-tenant defaults. Real multi-tenancy
# (orgs/projects created via API + RBAC) lands in Phase 6.
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")

__all__ = ["__version__", "DEFAULT_ORG_ID", "DEFAULT_PROJECT_ID"]
