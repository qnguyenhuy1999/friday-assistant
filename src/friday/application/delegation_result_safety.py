"""Authority-safe rendering for results crossing a delegation boundary.

A child Run owns its own durable result.  When Friday later exposes that result
to the immediate parent Agent as reasoning context, authority-bearing fields
must remain local to the child execution.  This module performs a narrow,
deterministic redaction at that parent-facing boundary; it does not mutate the
child's durable AGENT_FINISHED event.
"""

from __future__ import annotations

import re

from friday.domain.json_value import JsonValue

REDACTED_DELEGATION_AUTHORITY = "[redacted]"

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


def sanitize_delegation_result_text(value: str) -> str:
    """Redact labelled authority values from free-form delegated result text."""

    return _LABELED_AUTHORITY_VALUE.sub(
        lambda match: f"{match.group(1)}{REDACTED_DELEGATION_AUTHORITY}",
        value,
    )


def sanitize_delegation_result_value(value: JsonValue) -> JsonValue:
    """Recursively redact authority-bearing fields while preserving useful data."""

    if isinstance(value, dict):
        return {
            key: (
                REDACTED_DELEGATION_AUTHORITY
                if _is_sensitive_key(key)
                else sanitize_delegation_result_value(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [sanitize_delegation_result_value(child) for child in value]
    if isinstance(value, str):
        return sanitize_delegation_result_text(value)
    return value
