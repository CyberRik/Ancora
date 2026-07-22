"""Shared fixtures for activity-worker tests.

A time-skipping Temporal environment (cached test-server binary), plus automatic
reset of the process-wide runtime seams between tests so backends/clients don't
leak across cases.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from ancora_activity_worker import runtime


@pytest.fixture(autouse=True)
def _reset_runtime() -> Iterator[None]:
    runtime.reset()
    yield
    runtime.reset()


@pytest_asyncio.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        yield environment
    finally:
        await environment.shutdown()
