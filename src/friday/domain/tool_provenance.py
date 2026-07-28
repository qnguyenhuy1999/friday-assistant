"""Where an external tool invocation was aimed, in four bounded fields."""

from __future__ import annotations

from dataclasses import dataclass

from friday.domain.errors import DomainValidationError

MAX_PROVENANCE_FIELD_CHARS = 200


@dataclass(frozen=True, slots=True)
class ToolProvenance:
    """Vendor-free durable external-target provenance."""

    kind: str
    target: str
    remote_name: str
    binding_fingerprint: str

    def __post_init__(self) -> None:
        for name, value in (
            ("kind", self.kind),
            ("target", self.target),
            ("remote_name", self.remote_name),
            ("binding_fingerprint", self.binding_fingerprint),
        ):
            if not value.strip():
                raise DomainValidationError(f"ToolProvenance.{name} must not be empty")
            if len(value) > MAX_PROVENANCE_FIELD_CHARS:
                raise DomainValidationError(
                    f"ToolProvenance.{name} must not exceed {MAX_PROVENANCE_FIELD_CHARS} characters"
                )
