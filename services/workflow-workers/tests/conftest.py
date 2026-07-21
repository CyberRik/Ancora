"""Shared fixtures for worker tests — a time-skipping Temporal test environment.

The time-skipping environment downloads a small test server binary on first use;
subsequent runs are cached. Tests are marked ``temporal`` so they can be
deselected in fully offline runs with ``-m 'not temporal'``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        yield environment
    finally:
        await environment.shutdown()
