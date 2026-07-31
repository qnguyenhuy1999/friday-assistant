"""Secret-free scheduled-delivery authority boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from friday.domain.errors import DomainValidationError

MAX_ROUTE_ID_LENGTH = 64
_ROUTE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


def validate_route_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_ROUTE_ID_LENGTH
        or not _ROUTE_ID.fullmatch(value)
    ):
        raise DomainValidationError("delivery route_id is invalid")
    return value


@dataclass(frozen=True, slots=True)
class DeliveryRouteAuthority:
    route_id: str
    enabled: bool
    fingerprint: str
    max_body_chars: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "route_id", validate_route_id(self.route_id))
        if (
            not isinstance(self.enabled, bool)
            or not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint)
            or not isinstance(self.max_body_chars, int)
            or isinstance(self.max_body_chars, bool)
            or self.max_body_chars <= 0
        ):
            raise DomainValidationError("delivery route authority is invalid")


class DeliveryRouteAuthorityResolver(Protocol):
    def resolve(self, route_id: str) -> DeliveryRouteAuthority | None: ...
