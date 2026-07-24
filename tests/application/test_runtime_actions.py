"""parse_brain_action: valid envelopes map to the correct dataclass; every
malformed variant raises BrainResponseInvalid, never KeyError/TypeError.
Also asserts the schema and parser agree on every valid example."""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from friday.application.errors import BrainResponseInvalid
from friday.application.runtime_actions import (
    FailAction,
    FinishAction,
    InvokeToolAction,
    YieldAction,
    parse_brain_action,
)
from tests.contracts.conftest import SCHEMA_ROOT, build_registry, load_schema

SCHEMA_PATH = SCHEMA_ROOT / "runtime" / "brain_action.json"

VALID_EXAMPLES: dict[str, dict[str, Any]] = {
    "finish": {
        "version": 1,
        "action": "finish",
        "result": {"summary": "done", "details": {"n": 1}},
    },
    "fail": {"version": 1, "action": "fail", "reason": "could not proceed"},
    "yield": {"version": 1, "action": "yield", "delay_seconds": 30, "reason": "waiting"},
    "invoke_tool": {
        "version": 1,
        "action": "invoke_tool",
        "tool": "shell.run",
        "input": {"cmd": "ls"},
        "reason": "list files",
    },
}


def test_parse_finish_returns_finish_action() -> None:
    result = parse_brain_action(VALID_EXAMPLES["finish"])
    assert result == FinishAction(summary="done", details={"n": 1})


def test_parse_fail_returns_fail_action() -> None:
    result = parse_brain_action(VALID_EXAMPLES["fail"])
    assert result == FailAction(reason="could not proceed")


def test_parse_yield_returns_yield_action() -> None:
    result = parse_brain_action(VALID_EXAMPLES["yield"])
    assert result == YieldAction(delay_seconds=30, reason="waiting")


def test_parse_yield_with_no_optional_fields() -> None:
    result = parse_brain_action({"version": 1, "action": "yield"})
    assert result == YieldAction(delay_seconds=None, reason=None)


def test_parse_invoke_tool_returns_invoke_tool_action() -> None:
    result = parse_brain_action(VALID_EXAMPLES["invoke_tool"])
    assert result == InvokeToolAction(
        tool="shell.run", tool_input={"cmd": "ls"}, reason="list files"
    )


@pytest.mark.parametrize("action", sorted(VALID_EXAMPLES))
def test_schema_and_parser_agree_on_valid_examples(action: str) -> None:
    schema = load_schema(SCHEMA_PATH)
    validator = jsonschema.Draft202012Validator(schema, registry=build_registry())
    validator.validate(VALID_EXAMPLES[action])
    parse_brain_action(VALID_EXAMPLES[action])


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not a dict",
        42,
        [1, 2, 3],
    ],
)
def test_non_dict_raises_invalid(raw: object) -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action(raw)


def test_missing_version_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"action": "fail", "reason": "x"})


def test_version_2_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 2, "action": "fail", "reason": "x"})


def test_version_true_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": True, "action": "fail", "reason": "x"})


def test_unknown_action_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "explode", "reason": "x"})


def test_finish_missing_summary_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "finish", "result": {}})


def test_finish_blank_summary_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "finish", "result": {"summary": ""}})


def test_finish_oversized_summary_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "finish", "result": {"summary": "x" * 4001}})


def test_fail_missing_reason_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "fail"})


def test_fail_oversized_reason_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "fail", "reason": "x" * 2001})


def test_unknown_extra_key_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action(
            {"version": 1, "action": "fail", "reason": "x", "unexpected_field": "boom"}
        )


def test_invoke_tool_bad_pattern_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "invoke_tool", "tool": "ShellRun", "input": {}})


def test_invoke_tool_missing_tool_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "invoke_tool", "input": {}})


def test_invoke_tool_input_not_object_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action(
            {"version": 1, "action": "invoke_tool", "tool": "shell.run", "input": "not-a-dict"}
        )


def test_yield_delay_negative_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "yield", "delay_seconds": -1})


def test_yield_delay_true_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "yield", "delay_seconds": True})


def test_yield_delay_oversized_raises_invalid() -> None:
    with pytest.raises(BrainResponseInvalid):
        parse_brain_action({"version": 1, "action": "yield", "delay_seconds": 86401})
