"""Parsing for the Claude CLI's machine-readable result envelope.

`claude -p --output-format json` prints exactly one JSON object to stdout:

    {"type": "result", "subtype": "success", "is_error": false,
     "result": "<model text>", "usage": {...}, "modelUsage": {...}, ...}

Only the fields Friday needs are extracted; everything else (cost, session
ids, diagnostics) is deliberately dropped and never persisted or logged.
Any deviation raises BrainProtocolError with a bounded, content-free
message — envelope text is untrusted and may embed prompt-injection."""

from __future__ import annotations

import json
from dataclasses import dataclass

from friday.application.errors import BrainProtocolError
from friday.domain.json_value import JsonValue


@dataclass(frozen=True, slots=True)
class CliEnvelope:
    """The safe subset of one CLI result envelope."""

    result_text: str
    model: str | None
    usage: JsonValue


def parse_cli_envelope(stdout_text: str) -> CliEnvelope:
    """Parse the CLI's JSON envelope, extracting only the model text and
    persistence-safe metadata. Raises BrainProtocolError on any deviation;
    the raw envelope content never appears in the error message."""
    try:
        envelope = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise BrainProtocolError("CLI stdout is not valid JSON") from exc
    if not isinstance(envelope, dict):
        raise BrainProtocolError("CLI envelope is not a JSON object")
    if envelope.get("type") != "result":
        raise BrainProtocolError("CLI envelope has an unexpected type")
    if envelope.get("is_error") is not False:
        raise BrainProtocolError("CLI reported an error result")
    result_text = envelope.get("result")
    if not isinstance(result_text, str) or not result_text.strip():
        raise BrainProtocolError("CLI envelope carries no result text")
    return CliEnvelope(
        result_text=result_text,
        model=_first_model_name(envelope),
        usage=_safe_usage(envelope),
    )


def _first_model_name(envelope: dict[str, object]) -> str | None:
    model_usage = envelope.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        return str(sorted(model_usage)[0])
    return None


def _safe_usage(envelope: dict[str, object]) -> JsonValue:
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return None
    safe: dict[str, JsonValue] = {}
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            safe[key] = value
    return safe or None
