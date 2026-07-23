"""Structured failure record used by every entity that can fail.

Deliberately not a free-form string and not a wrapped exception: domain
state must stay serializable and vendor/framework-free, so failures carry a
stable code, a message, retryability, and JSON-compatible details instead of
a stack trace or exception object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from friday.domain.errors import DomainValidationError
from friday.domain.json_value import JsonValue, ensure_json_value


class FailureCause(StrEnum):
    VALIDATION = "validation"
    TOOL = "tool"
    RUNTIME = "runtime"
    APPROVAL = "approval"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class Failure:
    code: str
    message: str
    retryable: bool
    cause: FailureCause
    details: JsonValue = field(default=None)

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise DomainValidationError("Failure.code must not be empty")
        if not self.message.strip():
            raise DomainValidationError("Failure.message must not be empty")
        ensure_json_value(self.details, path="Failure.details")
