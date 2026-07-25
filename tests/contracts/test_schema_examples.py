"""Each entity schema accepts one valid example and rejects a mutated
invalid variant (missing required field, wrong enum value, or an
additional property) -- proves the schemas actually constrain shape, not
just parse as valid Draft 2020-12 documents."""

from __future__ import annotations

import copy
from typing import Any

import jsonschema
import pytest

from tests.contracts.conftest import SCHEMA_ROOT, build_registry, load_schema

VALID_EXAMPLES: dict[str, dict[str, Any]] = {
    "task/task.json": {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Investigate flaky test",
        "description": "",
        "status": "pending",
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": None,
        "completed_at": None,
        "failed_at": None,
        "cancelled_at": None,
        "failure": None,
    },
    "run/run.json": {
        "id": "22222222-2222-2222-2222-222222222222",
        "task_id": "11111111-1111-1111-1111-111111111111",
        "status": "queued",
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": None,
        "ended_at": None,
        "failure": None,
        "approval_request_id": None,
    },
    "step/run_step.json": {
        "id": "33333333-3333-3333-3333-333333333333",
        "run_id": "22222222-2222-2222-2222-222222222222",
        "name": "clone repo",
        "position": 0,
        "status": "pending",
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": None,
        "ended_at": None,
        "failure": None,
        "approval_request_id": None,
    },
    "event/run_event.json": {
        "id": "44444444-4444-4444-4444-444444444444",
        "run_id": "22222222-2222-2222-2222-222222222222",
        "type": "run_created",
        "sequence": 1,
        "occurred_at": "2026-01-01T00:00:00Z",
        "payload": None,
        "step_id": None,
    },
    "approval/approval_request.json": {
        "id": "55555555-5555-5555-5555-555555555555",
        "run_id": "22222222-2222-2222-2222-222222222222",
        "step_id": None,
        "category": "tool_execution",
        "summary": "run rm -rf",
        "reason": "",
        "requested_action": "delete build artifacts",
        "requested_input": None,
        "status": "pending",
        "requested_at": "2026-01-01T00:00:00Z",
        "expires_at": None,
        "resolved_at": None,
        "resolution_note": None,
        "resolver": None,
        "authorization_fingerprint": None,
        "consumed_at": None,
    },
    "artifact/artifact.json": {
        "id": "66666666-6666-6666-6666-666666666666",
        "run_id": "22222222-2222-2222-2222-222222222222",
        "step_id": None,
        "kind": "file",
        "name": "output.log",
        "media_type": "text/plain",
        "location": "/tmp/output.log",
        "created_at": "2026-01-01T00:00:00Z",
        "size": None,
        "checksum": None,
        "metadata": None,
    },
    "tool/tool_invocation.json": {
        "id": "77777777-7777-7777-7777-777777777777",
        "run_id": "22222222-2222-2222-2222-222222222222",
        "step_id": None,
        "tool_name": "shell",
        "requested_input": {"cmd": "ls"},
        "status": "requested",
        "requested_at": "2026-01-01T00:00:00Z",
        "approval_request_id": None,
        "started_at": None,
        "completed_at": None,
        "output": None,
        "output_set": False,
        "failure": None,
    },
}


@pytest.fixture(params=sorted(VALID_EXAMPLES), name="rel_path")
def _rel_path(request: pytest.FixtureRequest) -> str:
    value: str = request.param
    return value


def test_valid_example_passes(rel_path: str) -> None:
    schema = load_schema(SCHEMA_ROOT / rel_path)
    validator = jsonschema.Draft202012Validator(schema, registry=build_registry())
    validator.validate(VALID_EXAMPLES[rel_path])


def test_example_with_extra_property_is_rejected(rel_path: str) -> None:
    schema = load_schema(SCHEMA_ROOT / rel_path)
    validator = jsonschema.Draft202012Validator(schema, registry=build_registry())
    invalid = {**VALID_EXAMPLES[rel_path], "unexpected_field": "boom"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)


def test_example_missing_a_required_field_is_rejected(rel_path: str) -> None:
    schema = load_schema(SCHEMA_ROOT / rel_path)
    validator = jsonschema.Draft202012Validator(schema, registry=build_registry())
    example = copy.deepcopy(VALID_EXAMPLES[rel_path])
    dropped_key = next(iter(example))
    del example[dropped_key]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(example)


def test_example_with_invalid_status_enum_is_rejected(rel_path: str) -> None:
    schema = load_schema(SCHEMA_ROOT / rel_path)
    if "status" not in VALID_EXAMPLES[rel_path]:
        pytest.skip("no status enum on this entity")
    validator = jsonschema.Draft202012Validator(schema, registry=build_registry())
    invalid = {**VALID_EXAMPLES[rel_path], "status": "not_a_real_status"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)
