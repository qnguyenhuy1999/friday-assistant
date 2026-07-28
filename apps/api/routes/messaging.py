from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from apps.api.dependencies import get_messaging_routes, get_uow_factory
from apps.api.schemas.messaging import MessagingRoutePageResponse, MessagingRouteResponse
from friday.application.ports import UnitOfWorkFactory
from friday.infrastructure.messaging.config import MessagingRoutes

router = APIRouter(prefix="/v1/messaging", tags=["messaging"])
RoutesDependency = Annotated[MessagingRoutes, Depends(get_messaging_routes)]
UowDependency = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]


@router.get(
    "/routes",
    response_model=MessagingRoutePageResponse,
    operation_id="listMessagingRoutes",
)
def list_messaging_routes(
    routes: RoutesDependency, uow_factory: UowDependency
) -> MessagingRoutePageResponse:
    with uow_factory() as uow:
        items = []
        for route_id, description in routes.safe_descriptions():
            latest = uow.deliveries.latest_for_route(route_id)
            items.append(
                MessagingRouteResponse(
                    route_id=route_id,
                    trusted_description=description,
                    transport="webhook",
                    enabled=True,
                    status=latest.status.value if latest is not None else "unknown",
                    last_success_at=latest.delivered_at if latest is not None else None,
                    failure_code=latest.failure_code if latest is not None else None,
                )
            )
    return MessagingRoutePageResponse(items=items)
