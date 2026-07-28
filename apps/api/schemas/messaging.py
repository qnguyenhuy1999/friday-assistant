from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MessagingRouteResponse(BaseModel):
    route_id: str
    trusted_description: str
    transport: str
    enabled: bool
    status: str
    last_success_at: datetime | None
    failure_code: str | None


class MessagingRoutePageResponse(BaseModel):
    items: list[MessagingRouteResponse]
