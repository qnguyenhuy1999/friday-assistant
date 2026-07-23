"""RunEvent: append-only fact record. Positive sequence, UTC normalization,
JSON-compatible payload, immutability."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from friday.domain.errors import DomainValidationError
from friday.domain.event import RunEvent, RunEventType
from friday.domain.identifiers import RunEventId, RunId

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _event(**overrides: object) -> RunEvent:
    defaults: dict[str, object] = {
        "id": RunEventId.new(),
        "run_id": RunId.new(),
        "type": RunEventType.RUN_CREATED,
        "sequence": 1,
        "occurred_at": T0,
    }
    defaults.update(overrides)
    return RunEvent(**defaults)  # type: ignore[arg-type]


def test_valid_event_constructs() -> None:
    event = _event()
    assert event.sequence == 1
    assert event.type is RunEventType.RUN_CREATED
    assert event.payload is None


def test_sequence_zero_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _event(sequence=0)


def test_negative_sequence_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _event(sequence=-1)


def test_naive_occurred_at_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _event(occurred_at=datetime(2026, 1, 1))  # noqa: DTZ001


def test_non_json_payload_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _event(payload=math.nan)


def test_event_is_immutable() -> None:
    event = _event()
    with pytest.raises(AttributeError):
        event.sequence = 2  # type: ignore[misc]


def test_all_event_types_are_constructible() -> None:
    for event_type in RunEventType:
        _event(type=event_type)
