"""Domain error hierarchy. No HTTP, SQL, or vendor exception types."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain-layer errors."""


class DomainValidationError(DomainError):
    """A value violates a domain invariant (empty id, naive timestamp, ...)."""


class InvalidStateTransition(DomainError):
    """An entity attempted a transition its lifecycle does not allow."""

    def __init__(self, entity: str, current_status: str, target_status: str) -> None:
        self.entity = entity
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(f"{entity} cannot transition from {current_status!r} to {target_status!r}")
