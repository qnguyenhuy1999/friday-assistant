"""The brain_action.json oneOf envelope accepts one valid example per action
and rejects every malformed variant enumerated in the Phase 11 task spec."""

from __future__ import annotations

import copy
from typing import Any

import jsonschema
import pytest

from tests.contracts.conftest import SCHEMA_ROOT, build_registry, load_schema

SCHEMA_PATH = SCHEMA_ROOT / "runtime" / "brain_action.json"

VALID_EXAMPLES: dict[str, dict[str, Any]] = {
    "finish": {"version": 1, "action": "finish", "result": {"summary": "done"}},
    "fail": {"version": 1, "action": "fail", "reason": "could not proceed"},
    "yield": {"version": 1, "action": "yield"},
    "invoke_tool": {
        "version": 1,
        "action": "invoke_tool",
        "tool": "shell.run",
        "input": {"cmd": "ls"},
    },
}


@pytest.fixture(name="validator")
def _validator() -> jsonschema.Draft202012Validator:
    schema = load_schema(SCHEMA_PATH)
    return jsonschema.Draft202012Validator(schema, registry=build_registry())


@pytest.mark.parametrize("action", sorted(VALID_EXAMPLES))
def test_valid_example_passes(validator: jsonschema.Draft202012Validator, action: str) -> None:
    validator.validate(VALID_EXAMPLES[action])


def test_unknown_action_string_is_rejected(validator: jsonschema.Draft202012Validator) -> None:
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"version": 1, "action": "explode", "reason": "x"})


def test_wrong_version_is_rejected(validator: jsonschema.Draft202012Validator) -> None:
    invalid = {**VALID_EXAMPLES["fail"], "version": 2}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)


@pytest.mark.parametrize(
    ("action", "required_key"),
    [("finish", "result"), ("fail", "reason"), ("invoke_tool", "tool"), ("invoke_tool", "input")],
)
def test_missing_required_field_is_rejected(
    validator: jsonschema.Draft202012Validator, action: str, required_key: str
) -> None:
    invalid = copy.deepcopy(VALID_EXAMPLES[action])
    del invalid[required_key]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)


def test_extra_property_is_rejected(validator: jsonschema.Draft202012Validator) -> None:
    invalid = {**VALID_EXAMPLES["fail"], "unexpected_field": "boom"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)


def test_tool_not_matching_dotted_pattern_is_rejected(
    validator: jsonschema.Draft202012Validator,
) -> None:
    invalid = {**VALID_EXAMPLES["invoke_tool"], "tool": "ShellRun"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)


def test_yield_delay_seconds_negative_is_rejected(
    validator: jsonschema.Draft202012Validator,
) -> None:
    invalid = {**VALID_EXAMPLES["yield"], "delay_seconds": -1}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)


def test_yield_delay_seconds_as_string_is_rejected(
    validator: jsonschema.Draft202012Validator,
) -> None:
    invalid = {**VALID_EXAMPLES["yield"], "delay_seconds": "5"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)
