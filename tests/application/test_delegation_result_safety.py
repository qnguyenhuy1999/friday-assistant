from __future__ import annotations

import json

from friday.application.delegation_result_safety import (
    REDACTED_DELEGATION_AUTHORITY,
    sanitize_delegation_result_text,
    sanitize_delegation_result_value,
)


def test_delegation_result_redacts_recursive_authority_fields() -> None:
    source = {
        "finding": "keep this useful result",
        "artifact_id": "ordinary-artifact",
        "token_count": 17,
        "nested": {
            "approval_request_id": "approval-secret",
            "authorization_fingerprint": "fingerprint-secret",
            "claim_token": "claim-secret",
            "claim_generation": 9,
            "credentials": {"api_key": "credential-secret"},
            "provider_handle": "provider-handle-secret",
            "runtime_handle": "runtime-handle-secret",
            "tool_invocation_authorization_state": {
                "tool_invocation_id": "invocation-secret"
            },
        },
        "items": [
            {
                "provider_secret": "provider-secret",
                "message": "claim_token=embedded-secret ordinary=value",
            }
        ],
    }

    sanitized = sanitize_delegation_result_value(source)
    encoded = json.dumps(sanitized, sort_keys=True)

    assert isinstance(sanitized, dict)
    assert sanitized["finding"] == "keep this useful result"
    assert sanitized["artifact_id"] == "ordinary-artifact"
    assert sanitized["token_count"] == 17
    nested = sanitized["nested"]
    assert isinstance(nested, dict)
    assert nested["approval_request_id"] == REDACTED_DELEGATION_AUTHORITY
    assert nested["authorization_fingerprint"] == REDACTED_DELEGATION_AUTHORITY
    assert nested["claim_token"] == REDACTED_DELEGATION_AUTHORITY
    assert nested["claim_generation"] == REDACTED_DELEGATION_AUTHORITY
    assert nested["credentials"] == REDACTED_DELEGATION_AUTHORITY
    assert nested["provider_handle"] == REDACTED_DELEGATION_AUTHORITY
    assert nested["runtime_handle"] == REDACTED_DELEGATION_AUTHORITY
    assert nested["tool_invocation_authorization_state"] == REDACTED_DELEGATION_AUTHORITY
    assert "approval-secret" not in encoded
    assert "fingerprint-secret" not in encoded
    assert "claim-secret" not in encoded
    assert "credential-secret" not in encoded
    assert "provider-secret" not in encoded
    assert "runtime-handle-secret" not in encoded
    assert "invocation-secret" not in encoded
    assert "embedded-secret" not in encoded


def test_delegation_result_text_redacts_only_labelled_authority_values() -> None:
    text = (
        "useful finding; authorization_fingerprint=fp-secret "
        "approval request id: approval-secret claim-token='claim-secret' "
        "provider_handle=provider-secret runtime session: runtime-secret "
        "token_count=23 artifact_id=ordinary-artifact"
    )

    sanitized = sanitize_delegation_result_text(text)

    assert "fp-secret" not in sanitized
    assert "approval-secret" not in sanitized
    assert "claim-secret" not in sanitized
    assert "provider-secret" not in sanitized
    assert "runtime-secret" not in sanitized
    assert "token_count=23" in sanitized
    assert "artifact_id=ordinary-artifact" in sanitized
    assert sanitized.count(REDACTED_DELEGATION_AUTHORITY) == 5
