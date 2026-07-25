"""Parity and payload-boundary tests for Phase 12 memory RunEvents."""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from friday.domain.event import RunEventType
from tests.contracts.conftest import SCHEMA_ROOT, build_registry, load_schema

_MEMORY_EVENT_TYPES = {
    "memory_context_attached",
    "memory_retrieval_degraded",
    "memory_write_requested",
    "memory_write_committed",
    "memory_write_conflicted",
    "memory_index_marked_stale",
}


def _schema() -> dict[str, Any]:
    return load_schema(SCHEMA_ROOT / "event/run_event.json")


def _memory_event(payload: object) -> dict[str, object]:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "run_id": "00000000-0000-0000-0000-000000000002",
        "type": "memory_context_attached",
        "sequence": 1,
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "payload": payload,
        "step_id": None,
    }


def test_memory_event_members_match_schema_enum_in_both_directions() -> None:
    schema_types = set(_schema()["properties"]["type"]["enum"])
    domain_types = {event_type.value for event_type in RunEventType}

    assert schema_types >= _MEMORY_EVENT_TYPES
    assert domain_types >= _MEMORY_EVENT_TYPES
    assert schema_types == domain_types


def test_run_event_schema_remains_valid() -> None:
    jsonschema.Draft202012Validator.check_schema(_schema())


def test_memory_payload_allows_bounded_metadata() -> None:
    validator = jsonschema.Draft202012Validator(_schema(), registry=build_registry())

    validator.validate(
        _memory_event(
            {
                "record_ids": ["memory-1"],
                "candidate_count": 1,
                "excerpt_count": 1,
                "vault_relative_path": "20-Areas/architecture.md",
                "source_snapshot_id": "source-1",
                "index_snapshot_id": "index-1",
                "retrieval_mode": "lexical",
            }
        )
    )


@pytest.mark.parametrize(
    "payload",
    (
        {"note_content": "untrusted note body"},
        {"query_text": "full user query"},
        {"vault_relative_path": "/Users/example/Documents/secondbrain/note.md"},
        {"graphify_stderr": "diagnostic output"},
        {"model_prompt": "raw model prompt"},
    ),
)
def test_memory_payload_rejects_forbidden_sensitive_fields(payload: dict[str, str]) -> None:
    """Memory events retain metadata only; note/query/model content cannot cross this boundary."""
    validator = jsonschema.Draft202012Validator(_schema(), registry=build_registry())

    with pytest.raises(jsonschema.ValidationError):
        validator.validate(_memory_event(payload))
