from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.api.dependencies import get_database_reachable

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


@router.get("/health", operation_id="getHealth")
def get_health(reachable: Annotated[bool, Depends(get_database_reachable)]) -> HealthResponse:
    return HealthResponse(status="ok" if reachable else "unavailable")
