"""Small shared helpers for the consumer's loops."""

from __future__ import annotations

import asyncio
import contextlib


async def sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    """Sleep up to ``seconds``, waking early if ``stop`` is set."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
