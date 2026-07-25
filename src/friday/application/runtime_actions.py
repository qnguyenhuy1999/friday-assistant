"""Parsed brain-action types and a stdlib validator. Mirrors
packages/contracts/schemas/v1/runtime/brain_action.json. The brain only
proposes an action; Friday validates it here before authorizing or
executing anything. No vendor SDK, no subprocess."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from friday.application.errors import BrainResponseInvalid
from friday.domain.json_value import JsonValue, ensure_json_value

RUNTIME_ACTION_VERSION = 1
MAX_SUMMARY_CHARS = 4000
MAX_REASON_CHARS = 2000
MAX_TOOL_NAME_CHARS = 128
MAX_YIELD_DELAY_SECONDS = 86400
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class FinishAction:
    summary: str
    details: JsonValue = None


@dataclass(frozen=True, slots=True)
class FailAction:
    reason: str


@dataclass(frozen=True, slots=True)
class YieldAction:
    delay_seconds: int | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class InvokeToolAction:
    tool: str
    tool_input: JsonValue
    reason: str | None


BrainAction = FinishAction | FailAction | YieldAction | InvokeToolAction


def _require_dict(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BrainResponseInvalid(f"{description} must be a JSON object")
    return value


def _require_str(value: object, *, field: str, min_length: int, max_length: int) -> str:
    if not isinstance(value, str):
        raise BrainResponseInvalid(f"{field} must be a string")
    if len(value) < min_length or len(value) > max_length:
        raise BrainResponseInvalid(f"{field} must be between {min_length} and {max_length} chars")
    return value


def _reject_unknown_keys(raw: dict[str, object], allowed: set[str], *, description: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise BrainResponseInvalid(f"{description} has unknown field(s): {sorted(unknown)}")


def _parse_finish(raw: dict[str, object]) -> FinishAction:
    _reject_unknown_keys(raw, {"version", "action", "result"}, description="finish action")
    result = _require_dict(raw.get("result"), "finish action 'result'")
    _reject_unknown_keys(result, {"summary", "details"}, description="finish action 'result'")
    summary = _require_str(
        result.get("summary"), field="result.summary", min_length=1, max_length=MAX_SUMMARY_CHARS
    )
    details = ensure_json_value(result.get("details"), path="$.result.details")
    return FinishAction(summary=summary, details=details)


def _parse_fail(raw: dict[str, object]) -> FailAction:
    _reject_unknown_keys(raw, {"version", "action", "reason"}, description="fail action")
    reason = _require_str(
        raw.get("reason"), field="reason", min_length=1, max_length=MAX_REASON_CHARS
    )
    return FailAction(reason=reason)


def _parse_yield(raw: dict[str, object]) -> YieldAction:
    _reject_unknown_keys(
        raw, {"version", "action", "delay_seconds", "reason"}, description="yield action"
    )
    delay_seconds: int | None = None
    if "delay_seconds" in raw:
        candidate = raw["delay_seconds"]
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            raise BrainResponseInvalid("delay_seconds must be an integer")
        if candidate < 0 or candidate > MAX_YIELD_DELAY_SECONDS:
            raise BrainResponseInvalid(
                f"delay_seconds must be between 0 and {MAX_YIELD_DELAY_SECONDS}"
            )
        delay_seconds = candidate
    reason: str | None = None
    if "reason" in raw:
        reason = _require_str(
            raw["reason"], field="reason", min_length=0, max_length=MAX_REASON_CHARS
        )
    return YieldAction(delay_seconds=delay_seconds, reason=reason)


def _parse_invoke_tool(raw: dict[str, object]) -> InvokeToolAction:
    _reject_unknown_keys(
        raw, {"version", "action", "tool", "input", "reason"}, description="invoke_tool action"
    )
    tool = _require_str(raw.get("tool"), field="tool", min_length=1, max_length=MAX_TOOL_NAME_CHARS)
    if not TOOL_NAME_PATTERN.match(tool):
        raise BrainResponseInvalid(f"tool name does not match the required pattern: {tool!r}")
    tool_input_raw = _require_dict(raw.get("input"), "invoke_tool action 'input'")
    tool_input = ensure_json_value(tool_input_raw, path="$.input")
    reason: str | None = None
    if "reason" in raw:
        reason = _require_str(
            raw["reason"], field="reason", min_length=0, max_length=MAX_REASON_CHARS
        )
    return InvokeToolAction(tool=tool, tool_input=tool_input, reason=reason)


_PARSERS: dict[str, Callable[[dict[str, object]], BrainAction]] = {
    "finish": _parse_finish,
    "fail": _parse_fail,
    "yield": _parse_yield,
    "invoke_tool": _parse_invoke_tool,
}


def parse_brain_action(raw: object) -> BrainAction:
    """Parse and strictly validate a raw brain action envelope.

    Raises BrainResponseInvalid on any deviation from the contract; never
    raises KeyError/TypeError. Does not mutate `raw`.
    """
    envelope = _require_dict(raw, "brain action")

    version = envelope.get("version")
    if isinstance(version, bool) or version != RUNTIME_ACTION_VERSION:
        raise BrainResponseInvalid(f"unsupported brain action version: {version!r}")

    action = envelope.get("action")
    if not isinstance(action, str) or action not in _PARSERS:
        raise BrainResponseInvalid(f"unknown action: {action!r}")

    return _PARSERS[action](envelope)
