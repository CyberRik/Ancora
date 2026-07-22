"""Worker + queue discovery endpoints (AN-035)."""

from __future__ import annotations

from fastapi import APIRouter

from ancora_api.schemas import QueueOut, WorkerOut
from ancora_api.settings import get_settings
from ancora_api.worker_service import WorkerService

router = APIRouter(prefix="/v1", tags=["runtime"])


def _service() -> WorkerService:
    # Cheap to construct (a lazy Redis client); one per request keeps it simple.
    return WorkerService(get_settings().redis_url)


@router.get("/workers", response_model=list[WorkerOut])
async def list_workers() -> list[WorkerOut]:
    service = _service()
    try:
        return await service.list_workers()
    finally:
        await service.aclose()


@router.get("/queues", response_model=list[QueueOut])
async def list_queues() -> list[QueueOut]:
    service = _service()
    try:
        return await service.list_queues()
    finally:
        await service.aclose()
