from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from apps.api.dependencies import get_database_reachable, get_database_schema_current

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


@router.get("/health", operation_id="getHealth")
def get_health() -> HealthResponse:
    """Liveness only: the process can answer HTTP without requiring its dependencies."""
    return HealthResponse(status="ok")


@router.get("/ready", operation_id="getReadiness", response_model=HealthResponse)
def get_readiness(
    reachable: Annotated[bool, Depends(get_database_reachable)],
    schema_current: Annotated[bool, Depends(get_database_schema_current)],
) -> object:
    """Readiness is dependency-aware; worker prerequisites stay in worker-check."""
    if reachable and schema_current:
        return HealthResponse(status="ok")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "unavailable"}
    )
