"""Immutable, type-distinct identifier value objects.

Each identifier wraps a UUID string but is its own type, so a `TaskId` and a
`RunId` built from the same UUID string never compare equal — dataclass
equality already checks `type(self) is type(other)`, so subclassing a shared
base gets that for free without a custom `__eq__` per class.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Self

from friday.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class _Id:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise DomainValidationError(f"{type(self).__name__} must not be empty")
        try:
            uuid.UUID(self.value)
        except ValueError as exc:
            raise DomainValidationError(
                f"{type(self).__name__} must be a canonical UUID string, got {self.value!r}"
            ) from exc

    def __str__(self) -> str:
        return self.value

    @classmethod
    def new(cls) -> Self:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str) -> Self:
        return cls(value)


class TaskId(_Id):
    pass


class RunId(_Id):
    pass


class RunStepId(_Id):
    pass


class RunEventId(_Id):
    pass


class TaskEventId(_Id):
    pass


class ApprovalRequestId(_Id):
    pass


class ArtifactId(_Id):
    pass


class ToolInvocationId(_Id):
    pass
