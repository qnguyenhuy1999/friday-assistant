"""`message.send`: approved intent becomes one durable queued delivery.

There is deliberately no transport dependency here.  A successful tool result
means only that a durable `OutboundDelivery` was committed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from friday.application.errors import EntityConflict, ToolInputInvalid, ToolNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.tool_gateway import (
    ToolCall,
    ToolDescriptor,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolRiskAssessment,
)
from friday.domain.approval import ApprovalCategory
from friday.domain.identifiers import DeliveryId
from friday.domain.json_value import JsonValue
from friday.domain.outbound_delivery import MAX_BODY_LENGTH, DeliverySourceKind, OutboundDelivery
from friday.domain.tool_provenance import ToolProvenance
from friday.infrastructure.messaging.config import MessagingRoute

TOOL_NAME = "message.send"
MAX_DELIVERY_HORIZON = timedelta(days=365)


@dataclass(frozen=True, slots=True)
class MessageToolGatewaySettings:
    routes: tuple[MessagingRoute, ...]
    uow_factory: UnitOfWorkFactory
    clock: Clock


class MessageToolGateway:
    def __init__(self, settings: MessageToolGatewaySettings) -> None:
        self._routes = {route.route_id: route for route in settings.routes if route.enabled}
        if not self._routes:
            raise ValueError("MessageToolGateway requires at least one enabled route")
        self._uow_factory = settings.uow_factory
        self._clock = settings.clock
        aliases = sorted(self._routes)
        descriptions = "; ".join(
            f"{route.route_id}: {route.trusted_description}" for route in self._routes.values()
        )
        self._descriptor = ToolDescriptor(
            name=TOOL_NAME,
            description=(
                f"Queue an outbound message to an operator-owned route. Routes: {descriptions}"
            ),
            read_only=False,
            approval_required=True,
            input_schema=cast(
                JsonValue,
                {
                    "type": "object",
                    "required": ["route", "body"],
                    "additionalProperties": False,
                    "properties": {
                        "route": {"type": "string", "enum": aliases},
                        "body": {"type": "string", "minLength": 1, "maxLength": MAX_BODY_LENGTH},
                        "deliver_at": {"type": "string", "format": "date-time"},
                    },
                },
            ),
        )

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        return (self._descriptor,)

    def assess(self, call: ToolCall) -> ToolRiskAssessment:
        route, _, _ = self._parse(call, self._clock.now())
        return ToolRiskAssessment(
            tool=TOOL_NAME,
            read_only=False,
            approval_required=True,
            category=ApprovalCategory.EXTERNAL_COMMUNICATION,
            summary=f"Queue outbound message to route {route.route_id}",
            authorization_scope=f"message:{route.fingerprint}",
            provenance=ToolProvenance(
                kind="messaging",
                target=route.route_id,
                remote_name=route.transport,
                binding_fingerprint=route.fingerprint,
            ),
        )

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        now = self._clock.now()
        route, body, available_at = self._parse(request.call, now)
        # The fresh route fingerprint is intentionally re-assessed by
        # ExecuteToolAction before an approval is consumed.  This method still
        # freezes the current safe authority at durable enqueue time.
        with self._uow_factory() as uow:
            existing = uow.deliveries.get_by_source_tool_invocation_id(request.invocation_id)
            if existing is not None:
                self._assert_existing(existing, request, route, body, available_at)
                return ToolExecutionResult.succeeded(
                    {"delivery_id": str(existing.id), "status": "queued"}
                )
            delivery = OutboundDelivery.new(
                id=DeliveryId.new(),
                source_kind=DeliverySourceKind.AGENT_REQUEST,
                source_run_id=request.run_id,
                source_tool_invocation_id=request.invocation_id,
                route_id=route.route_id,
                route_fingerprint=route.fingerprint,
                subject=None,
                body=body,
                available_at=available_at,
                created_at=now,
            )
            try:
                uow.deliveries.add(delivery)
                uow.commit()
            except EntityConflict:
                # The database unique key is the race fence. A concurrent
                # creator can only be accepted when it created the same intent.
                existing = uow.deliveries.get_by_source_tool_invocation_id(request.invocation_id)
                if existing is None:
                    raise
                self._assert_existing(existing, request, route, body, available_at)
                return ToolExecutionResult.succeeded(
                    {"delivery_id": str(existing.id), "status": "queued"}
                )
        return ToolExecutionResult.succeeded({"delivery_id": str(delivery.id), "status": "queued"})

    def _parse(self, call: ToolCall, now: datetime) -> tuple[MessagingRoute, str, datetime]:
        if call.tool != TOOL_NAME:
            raise ToolNotFound(call.tool)
        value = cast(dict[str, object], call.tool_input)
        if set(value) - {"route", "body", "deliver_at"} or not {"route", "body"} <= set(value):
            raise ToolInputInvalid("message.send input fields are invalid")
        route_value = value["route"]
        route = self._routes.get(route_value) if isinstance(route_value, str) else None
        if route is None:
            raise ToolInputInvalid("message.send route is not enabled")
        body = value["body"]
        if (
            not isinstance(body, str)
            or not body
            or len(body) > min(route.max_body_chars, MAX_BODY_LENGTH)
        ):
            raise ToolInputInvalid("message.send body is invalid")
        available_at = now
        if "deliver_at" in value:
            raw = value["deliver_at"]
            if not isinstance(raw, str):
                raise ToolInputInvalid("message.send deliver_at is invalid")
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ToolInputInvalid("message.send deliver_at is invalid") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ToolInputInvalid("message.send deliver_at requires an explicit timezone")
            available_at = parsed.astimezone(UTC)
            if (
                available_at <= now.astimezone(UTC)
                or available_at > now.astimezone(UTC) + MAX_DELIVERY_HORIZON
            ):
                raise ToolInputInvalid("message.send deliver_at is outside the allowed window")
        return route, body, available_at.astimezone(UTC)

    @staticmethod
    def _assert_existing(
        existing: OutboundDelivery,
        request: ToolExecutionRequest,
        route: MessagingRoute,
        body: str,
        available_at: datetime,
    ) -> None:
        if (
            existing.source_kind is not DeliverySourceKind.AGENT_REQUEST
            or existing.source_run_id != request.run_id
            or existing.route_id != route.route_id
            or existing.route_fingerprint != route.fingerprint
            or existing.subject is not None
            or existing.body != body
            or existing.available_at != available_at
        ):
            raise EntityConflict("existing outbound delivery does not match message invocation")
