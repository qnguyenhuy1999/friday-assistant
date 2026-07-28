"""Durable outbound-delivery intent and its lifecycle.

This aggregate deliberately owns no transport or retry policy.  It records
the authority and result of a delivery attempt so a later dispatcher can make
external effects safely.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import DeliveryId, RunId, ScheduleFireId, ToolInvocationId
from friday.domain.time import ensure_utc

MAX_ROUTE_ID_LENGTH = 64
MAX_ROUTE_FINGERPRINT_LENGTH = 64
MAX_SUBJECT_LENGTH = 512
MAX_BODY_LENGTH = 16_000
MAX_CLAIM_VALUE_LENGTH = 256
MAX_PROVIDER_MESSAGE_ID_LENGTH = 512
MAX_FAILURE_CODE_LENGTH = 128
MAX_FAILURE_MESSAGE_LENGTH = 2_048
SHA256_HEX_LENGTH = 64
_LOWER_HEX = re.compile(r"^[0-9a-f]{64}$")


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    DELIVERED = "delivered"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    CANCELLED = "cancelled"


class DeliverySourceKind(StrEnum):
    AGENT_REQUEST = "agent_request"
    SCHEDULED_RUN_ANSWER = "scheduled_run_answer"


TERMINAL_DELIVERY_STATUSES = frozenset(
    {
        DeliveryStatus.DELIVERED,
        DeliveryStatus.FAILED,
        DeliveryStatus.AMBIGUOUS,
        DeliveryStatus.CANCELLED,
    }
)


def _bounded(value: str, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"OutboundDelivery.{field} must not be empty")
    if len(value) > maximum:
        raise DomainValidationError(
            f"OutboundDelivery.{field} must be at most {maximum} characters"
        )
    return value


def _optional_bounded(value: str | None, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded(value, field=field, maximum=maximum)


@dataclass(slots=True)
class OutboundDelivery:
    id: DeliveryId
    source_kind: DeliverySourceKind
    source_run_id: RunId
    source_tool_invocation_id: ToolInvocationId | None
    source_schedule_fire_id: ScheduleFireId | None
    route_id: str
    route_fingerprint: str
    subject: str | None
    body: str
    body_sha256: str
    status: DeliveryStatus
    available_at: datetime
    attempt_count: int
    claim_owner: str | None
    claim_token: str | None
    claim_generation: int
    claim_expires_at: datetime | None
    provider_message_id: str | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None

    @classmethod
    def new(
        cls,
        *,
        id: DeliveryId,
        source_kind: DeliverySourceKind,
        source_run_id: RunId,
        source_tool_invocation_id: ToolInvocationId | None = None,
        source_schedule_fire_id: ScheduleFireId | None = None,
        route_id: str,
        route_fingerprint: str,
        body: str,
        available_at: datetime,
        created_at: datetime,
        subject: str | None = None,
        body_sha256: str | None = None,
    ) -> OutboundDelivery:
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if body_sha256 is not None and body_sha256 != digest:
            raise DomainValidationError("OutboundDelivery.body_sha256 does not match body")
        created = ensure_utc(created_at)
        return cls(
            id=id,
            source_kind=source_kind,
            source_run_id=source_run_id,
            source_tool_invocation_id=source_tool_invocation_id,
            source_schedule_fire_id=source_schedule_fire_id,
            route_id=route_id,
            route_fingerprint=route_fingerprint,
            subject=subject,
            body=body,
            body_sha256=digest,
            status=DeliveryStatus.QUEUED,
            available_at=ensure_utc(available_at),
            attempt_count=0,
            claim_owner=None,
            claim_token=None,
            claim_generation=0,
            claim_expires_at=None,
            provider_message_id=None,
            failure_code=None,
            failure_message=None,
            created_at=created,
            updated_at=created,
            delivered_at=None,
        )

    def __post_init__(self) -> None:
        self.route_id = _bounded(self.route_id, field="route_id", maximum=MAX_ROUTE_ID_LENGTH)
        if not _LOWER_HEX.fullmatch(self.route_fingerprint):
            raise DomainValidationError(
                "OutboundDelivery.route_fingerprint must be a lowercase SHA-256 hex digest"
            )
        if not isinstance(self.body, str) or not self.body:
            raise DomainValidationError("OutboundDelivery.body must not be empty")
        if len(self.body) > MAX_BODY_LENGTH:
            raise DomainValidationError(
                f"OutboundDelivery.body must be at most {MAX_BODY_LENGTH} characters"
            )
        self.subject = _optional_bounded(self.subject, field="subject", maximum=MAX_SUBJECT_LENGTH)
        if not _LOWER_HEX.fullmatch(self.body_sha256):
            raise DomainValidationError(
                "OutboundDelivery.body_sha256 must be a lowercase SHA-256 hex digest"
            )
        if self.body_sha256 != hashlib.sha256(self.body.encode("utf-8")).hexdigest():
            raise DomainValidationError("OutboundDelivery.body_sha256 does not match body")
        self._validate_source_shape()
        if self.attempt_count < 0 or self.claim_generation < 0:
            raise DomainValidationError(
                "OutboundDelivery attempt and claim generations must be non-negative"
            )
        self.claim_owner = _optional_bounded(
            self.claim_owner, field="claim_owner", maximum=MAX_CLAIM_VALUE_LENGTH
        )
        self.claim_token = _optional_bounded(
            self.claim_token, field="claim_token", maximum=MAX_CLAIM_VALUE_LENGTH
        )
        if (self.claim_owner is None) != (self.claim_token is None):
            raise DomainValidationError(
                "OutboundDelivery claim owner and token must be set together"
            )
        self.provider_message_id = _optional_bounded(
            self.provider_message_id,
            field="provider_message_id",
            maximum=MAX_PROVIDER_MESSAGE_ID_LENGTH,
        )
        self.failure_code = _optional_bounded(
            self.failure_code, field="failure_code", maximum=MAX_FAILURE_CODE_LENGTH
        )
        self.failure_message = _optional_bounded(
            self.failure_message, field="failure_message", maximum=MAX_FAILURE_MESSAGE_LENGTH
        )
        self.available_at = ensure_utc(self.available_at)
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        self.claim_expires_at = ensure_utc(self.claim_expires_at) if self.claim_expires_at else None
        self.delivered_at = ensure_utc(self.delivered_at) if self.delivered_at else None

    def _validate_source_shape(self) -> None:
        if self.source_kind is DeliverySourceKind.AGENT_REQUEST:
            valid = (
                self.source_tool_invocation_id is not None and self.source_schedule_fire_id is None
            )
        else:
            valid = (
                self.source_tool_invocation_id is None and self.source_schedule_fire_id is not None
            )
        if not valid:
            raise DomainValidationError(
                "OutboundDelivery source references do not match source_kind"
            )

    def _require_status(self, *allowed: DeliveryStatus, target: DeliveryStatus) -> None:
        if self.status not in allowed:
            raise InvalidStateTransition("OutboundDelivery", self.status.value, target.value)

    def mark_sending(
        self, *, at: datetime, claim_owner: str, claim_token: str, claim_expires_at: datetime
    ) -> None:
        self._require_status(DeliveryStatus.QUEUED, target=DeliveryStatus.SENDING)
        at = ensure_utc(at)
        expiry = ensure_utc(claim_expires_at)
        if expiry <= at:
            raise DomainValidationError("OutboundDelivery claim expiry must be after claim time")
        self.claim_owner = _bounded(
            claim_owner, field="claim_owner", maximum=MAX_CLAIM_VALUE_LENGTH
        )
        self.claim_token = _bounded(
            claim_token, field="claim_token", maximum=MAX_CLAIM_VALUE_LENGTH
        )
        self.claim_expires_at = expiry
        self.claim_generation += 1
        self.attempt_count += 1
        self.status = DeliveryStatus.SENDING
        self.updated_at = at

    claim = mark_sending

    def deliver(self, *, at: datetime, provider_message_id: str | None = None) -> None:
        self._require_status(DeliveryStatus.SENDING, target=DeliveryStatus.DELIVERED)
        self.provider_message_id = _optional_bounded(
            provider_message_id,
            field="provider_message_id",
            maximum=MAX_PROVIDER_MESSAGE_ID_LENGTH,
        )
        self.delivered_at = ensure_utc(at)
        self.updated_at = self.delivered_at
        self.status = DeliveryStatus.DELIVERED

    def fail(self, *, at: datetime, failure_code: str, failure_message: str) -> None:
        self._require_status(DeliveryStatus.SENDING, target=DeliveryStatus.FAILED)
        self.failure_code = _bounded(
            failure_code, field="failure_code", maximum=MAX_FAILURE_CODE_LENGTH
        )
        self.failure_message = _bounded(
            failure_message, field="failure_message", maximum=MAX_FAILURE_MESSAGE_LENGTH
        )
        self.updated_at = ensure_utc(at)
        self.status = DeliveryStatus.FAILED

    def mark_ambiguous(self, *, at: datetime, failure_code: str, failure_message: str) -> None:
        self._require_status(DeliveryStatus.SENDING, target=DeliveryStatus.AMBIGUOUS)
        self.failure_code = _bounded(
            failure_code, field="failure_code", maximum=MAX_FAILURE_CODE_LENGTH
        )
        self.failure_message = _bounded(
            failure_message, field="failure_message", maximum=MAX_FAILURE_MESSAGE_LENGTH
        )
        self.updated_at = ensure_utc(at)
        self.status = DeliveryStatus.AMBIGUOUS

    def cancel(self, *, at: datetime) -> None:
        self._require_status(DeliveryStatus.QUEUED, target=DeliveryStatus.CANCELLED)
        self.updated_at = ensure_utc(at)
        self.status = DeliveryStatus.CANCELLED
