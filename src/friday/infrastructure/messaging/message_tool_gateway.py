"""Friday-owned ``message.send`` tool: approve then enqueue, never send."""

from __future__ import annotations

from datetime import datetime, timedelta

from friday.application.errors import ToolInputInvalid, ToolNotFound
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
from friday.domain.outbound_delivery import DeliverySourceKind, OutboundDelivery
from friday.domain.tool_provenance import ToolProvenance
from friday.infrastructure.messaging.config import MessagingRoute, MessagingRoutes

MAX_FUTURE_DELIVERY = timedelta(days=365)

_DESCRIPTOR = ToolDescriptor(
    name="message.send",
    description="Queue an approved outbound message to an operator-owned route.",
    read_only=False,
    approval_required=True,
    input_schema={
        "type": "object",
        "properties": {
            "route": {"type": "string"},
            "body": {"type": "string"},
            "subject": {"type": "string"},
            "deliver_at": {"type": "string"},
        },
        "required": ["route", "body"],
        "additionalProperties": False,
    },
)


class MessageToolGateway:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, clock: Clock, routes: MessagingRoutes
    ) -> None:
        if not routes.enabled:
            raise ValueError("MessageToolGateway requires at least one enabled route")
        self._uow_factory = uow_factory
        self._clock = clock
        self._routes = routes

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        return (_DESCRIPTOR,)

    def assess(self, call: ToolCall) -> ToolRiskAssessment:
        route, _, _, _ = self._parse(call)
        return ToolRiskAssessment(
            tool="message.send",
            read_only=False,
            approval_required=True,
            category=ApprovalCategory.EXTERNAL_COMMUNICATION,
            summary=f"message.send to {route.route_id}: {route.trusted_description}",
            authorization_scope=f"message:{route.fingerprint}",
            provenance=ToolProvenance(
                kind="messaging",
                target=route.route_id,
                remote_name="webhook",
                binding_fingerprint=route.fingerprint,
            ),
        )

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        route, body, subject, available_at = self._parse(request.call)
        now = self._clock.now()
        # This short independent transaction is deliberately all that the
        # tool does. The dispatcher owns every transport effect later.
        with self._uow_factory() as uow:
            existing = uow.deliveries.get_by_source_tool_invocation(request.invocation_id)
            if existing is not None:
                uow.commit()
                return ToolExecutionResult.succeeded(
                    {"delivery_id": str(existing.id), "status": existing.status.value}
                )
            delivery = OutboundDelivery.new(
                id=DeliveryId.new(),
                source_kind=DeliverySourceKind.AGENT_REQUEST,
                source_run_id=request.run_id,
                source_tool_invocation_id=request.invocation_id,
                route_id=route.route_id,
                route_fingerprint=route.fingerprint,
                subject=subject,
                body=body,
                available_at=available_at,
                created_at=now,
            )
            uow.deliveries.add(delivery)
            uow.commit()
        return ToolExecutionResult.succeeded({"delivery_id": str(delivery.id), "status": "queued"})

    def _parse(self, call: ToolCall) -> tuple[MessagingRoute, str, str | None, datetime]:
        if call.tool != "message.send":
            raise ToolNotFound(call.tool)
        if not isinstance(call.tool_input, dict):
            raise ToolInputInvalid("message.send input must be an object")
        value = call.tool_input
        permitted = {"route", "body", "subject", "deliver_at"}
        route_id = value.get("route")
        body = value.get("body")
        if set(value) - permitted or not isinstance(route_id, str) or not isinstance(body, str):
            raise ToolInputInvalid(
                "message.send requires only route, body, optional subject, deliver_at"
            )
        route = self._routes.get_enabled(route_id)
        if route is None:
            raise ToolInputInvalid("message route is not configured")
        if not body or len(body) > 16_000:
            raise ToolInputInvalid("message body must be between 1 and 16000 characters")
        subject = value.get("subject")
        if subject is not None and (
            not isinstance(subject, str) or not subject or len(subject) > 512
        ):
            raise ToolInputInvalid("message subject must be between 1 and 512 characters")
        if subject is not None:
            raise ToolInputInvalid("message subject is not supported by configured webhook routes")
        available_at = self._clock.now()
        raw_deliver_at = value.get("deliver_at")
        if raw_deliver_at is not None:
            if not isinstance(raw_deliver_at, str):
                raise ToolInputInvalid("deliver_at must be an RFC3339 timestamp")
            try:
                available_at = datetime.fromisoformat(raw_deliver_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ToolInputInvalid("deliver_at must be an RFC3339 timestamp") from exc
            if available_at.tzinfo is None or available_at.utcoffset() is None:
                raise ToolInputInvalid("deliver_at must include an explicit timezone offset")
            available_at = available_at.astimezone(self._clock.now().tzinfo)
            now = self._clock.now()
            if available_at <= now or available_at > now + MAX_FUTURE_DELIVERY:
                raise ToolInputInvalid("deliver_at must be in the allowed future horizon")
        return route, body, subject, available_at
