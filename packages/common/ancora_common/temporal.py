"""Temporal client factory.

Centralizes two things every server-side component needs identically:
  1. the Pydantic data converter, so workflow/activity payloads that are Pydantic
     models (or dicts) round-trip correctly through Temporal history;
  2. a bounded, backoff'd connect so a not-yet-ready Temporal frontend during
     startup doesn't crash the process.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

logger = logging.getLogger("ancora.temporal")


async def connect(
    address: str,
    namespace: str = "default",
    *,
    retries: int = 30,
    backoff_seconds: float = 2.0,
) -> Client:
    """Connect to Temporal, retrying transient failures with linear backoff."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await Client.connect(
                address,
                namespace=namespace,
                data_converter=pydantic_data_converter,
            )
        except Exception as exc:  # noqa: BLE001 — connect surfaces many error types
            last_exc = exc
            logger.warning("temporal connect failed (attempt %d/%d): %s", attempt, retries, exc)
            await asyncio.sleep(backoff_seconds)
    raise RuntimeError(
        f"could not connect to Temporal at {address} after {retries} attempts"
    ) from last_exc
