"""Authority-safe rendering for results crossing a delegation boundary.

A child Run owns its own durable result.  When Friday later exposes that result
to the immediate parent Agent as reasoning context, authority-bearing fields
must remain local to the child execution.  This module performs a narrow,
deterministic redaction at that parent-facing boundary; it does not mutate the
child's durable AGENT_FINISHED event.

Redaction is layered.  Key/label heuristics catch structurally labelled
authority.  The fail-closed layer is value-based: callers collect the actual
durable authority values of the child execution lineage (approval ids,
authorization/security-binding fingerprints, claim tokens, and tool
invocation references) and every literal occurrence is scrubbed from summary
text and recursively from JSON values and keys, regardless of field names or
labels.  Ordinary execution and resource provenance is deliberately not part
of that value set.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from friday.domain.json_value import JsonValue

REDACTED_DELEGATION_AUTHORITY = "[redacted]"
AuthorityValue = str | int

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "approval_id",
        "approval_request_id",
        "authorization_fingerprint",
        "authorization_state",
        "authorization_token",
        "bearer_token",
        "claim_generation",
        "claim_token",
        "credential",
        "credentials",
        "password",
        "provider_credentials",
        "provider_handle",
        "provider_secret",
        "provider_session",
        "refresh_token",
        "runtime_handle",
        "runtime_session",
        "secret",
        "tool_authorization",
        "tool_invocation_authorization_state",
        "tool_invocation_id",
    }
)

_SENSITIVE_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_approval_id",
    "_approval_request_id",
    "_authorization_fingerprint",
    "_authorization_state",
    "_authorization_token",
    "_bearer_token",
    "_claim_generation",
    "_claim_token",
    "_credential",
    "_credentials",
    "_password",
    "_provider_handle",
    "_provider_secret",
    "_provider_session",
    "_refresh_token",
    "_runtime_handle",
    "_runtime_session",
    "_secret",
    "_tool_authorization",
    "_tool_invocation_authorization_state",
    "_tool_invocation_id",
)

# Free-form summaries can carry labelled authority values even though they have
# no JSON key to inspect.  Keep the match deliberately narrow: ordinary text
# such as "token_count=12" or "artifact_id=..." must remain useful.
_LABEL_PATTERNS = (
    r"access[_ -]?token",
    r"api[_ -]?key",
    r"approval(?:[_ -]?request)?[_ -]?id",
    r"authorization[_ -]?fingerprint",
    r"authorization[_ -]?state",
    r"authorization[_ -]?token",
    r"bearer[_ -]?token",
    r"claim[_ -]?generation",
    r"claim[_ -]?token",
    r"credentials?",
    r"password",
    r"provider[_ -]?(?:credentials|handle|secret|session)",
    r"refresh[_ -]?token",
    r"runtime[_ -]?(?:handle|session)",
    r"secret",
    r"tool[_ -]?invocation[_ -]?(?:authorization[_ -]?state|id)",
)
_LABELED_AUTHORITY_VALUE = re.compile(
    rf"(?i)(\b(?:{'|'.join(_LABEL_PATTERNS)})\b\s*[:=]\s*)"
    r"(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;}\]]+)"
)


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def _normalize_authority_values(
    values: Iterable[AuthorityValue],
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    strings: set[str] = set()
    integers: set[int] = set()
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, str) and value:
            strings.add(value)
        elif isinstance(value, int) and value > 0:
            integers.add(value)
    return (
        tuple(sorted(strings, key=lambda value: (-len(value), value))),
        tuple(sorted(integers, key=lambda value: (-len(str(value)), value))),
    )


def _scrub_literal_authority_text(
    text: str, string_values: tuple[str, ...], integer_values: tuple[int, ...] = ()
) -> str:
    for known in string_values:
        text = text.replace(known, REDACTED_DELEGATION_AUTHORITY)
    for known_integer in integer_values:
        text = re.sub(
            rf"(?<![A-Za-z0-9_]){known_integer}(?![A-Za-z0-9_])",
            REDACTED_DELEGATION_AUTHORITY,
            text,
        )
    return text


def _sanitize_text_with_known_authority(
    value: str, string_values: tuple[str, ...], integer_values: tuple[int, ...]
) -> str:
    labelled = _LABELED_AUTHORITY_VALUE.sub(
        lambda match: f"{match.group(1)}{REDACTED_DELEGATION_AUTHORITY}",
        value,
    )
    return _scrub_literal_authority_text(labelled, string_values, integer_values)


def sanitize_delegation_result_text(
    value: str, *, authority_values: Iterable[AuthorityValue] = ()
) -> str:
    """Redact labelled and known literal authority values from result text."""

    string_values, integer_values = _normalize_authority_values(authority_values)
    return _sanitize_text_with_known_authority(value, string_values, integer_values)


def sanitize_delegation_result_value(
    value: JsonValue, *, authority_values: Iterable[AuthorityValue] = ()
) -> JsonValue:
    """Recursively redact authority fields and known literals in JSON."""

    string_values, integer_values = _normalize_authority_values(authority_values)

    def sanitize(current: JsonValue) -> JsonValue:
        if isinstance(current, dict):
            return {
                _scrub_literal_authority_text(key, string_values, integer_values): (
                    REDACTED_DELEGATION_AUTHORITY
                    if _is_sensitive_key(key)
                    else sanitize(current_value)
                )
                for key, current_value in current.items()
            }
        if isinstance(current, list):
            return [sanitize(child) for child in current]
        if isinstance(current, str):
            return _sanitize_text_with_known_authority(current, string_values, integer_values)
        if isinstance(current, int) and not isinstance(current, bool) and current in integer_values:
            return REDACTED_DELEGATION_AUTHORITY
        return current

    return sanitize(value)


def project_delegated_result(
    summary: str | None,
    details: JsonValue,
    *,
    authority_values: Iterable[AuthorityValue] = (),
) -> tuple[str | None, JsonValue]:
    """Build the parent-facing projection of one delegated result.

    Applies the key/label heuristics first, then fail-closed literal scrubbing
    of the caller-supplied actual authority values of the child execution
    lineage.  Ordinary data that contains no authority value survives intact.
    """

    known = tuple(authority_values)
    safe_summary = (
        sanitize_delegation_result_text(summary, authority_values=known)
        if summary is not None
        else None
    )
    safe_details = sanitize_delegation_result_value(details, authority_values=known)
    return safe_summary, safe_details
