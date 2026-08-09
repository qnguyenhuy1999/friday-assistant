"""DelegationRequest domain invariants: fingerprint binding, failure-shape
consistency, field bounds."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from friday.domain.delegation import (
    DelegationRequest,
    DelegationStatus,
    compute_delegation_fingerprint,
)
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import AgentId, DelegationRequestId, RunId, RunStepId

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _new_request(**overrides: object) -> DelegationRequest:
    defaults: dict[str, object] = dict(
        id=DelegationRequestId.new(),
        parent_run_id=RunId.new(),
        target_agent_id=AgentId.new(),
        objective="summarize the incident",
        input_payload={"log_ids": ["a", "b"]},
        expected_output_contract="json object with a summary field",
        created_at=T0,
    )
    defaults.update(overrides)
    return DelegationRequest.new(**defaults)  # type: ignore[arg-type]


def test_new_computes_a_binding_fingerprint() -> None:
    request = _new_request()
    expected = compute_delegation_fingerprint(
        delegation_request_id=request.id,
        parent_run_id=request.parent_run_id,
        parent_run_step_id=request.parent_run_step_id,
        target_agent_id=request.target_agent_id,
        objective=request.objective,
        input_payload=request.input_payload,
        expected_output_contract=request.expected_output_contract,
    )
    assert request.authorization_fingerprint == expected
    assert request.status is DelegationStatus.REQUESTED


@pytest.mark.parametrize(
    "field,value",
    [
        ("objective", "a different objective"),
        ("expected_output_contract", "a different contract"),
    ],
)
def test_fingerprint_changes_when_objective_or_contract_changes(field: str, value: str) -> None:
    base = _new_request()
    changed = _new_request(**{field: value})
    assert base.authorization_fingerprint != changed.authorization_fingerprint


def test_fingerprint_changes_when_target_agent_changes() -> None:
    base = _new_request()
    changed = _new_request(target_agent_id=AgentId.new())
    assert base.authorization_fingerprint != changed.authorization_fingerprint


def test_fingerprint_changes_when_input_payload_changes() -> None:
    base = _new_request()
    changed = _new_request(input_payload={"log_ids": ["c"]})
    assert base.authorization_fingerprint != changed.authorization_fingerprint


def test_fingerprint_changes_when_parent_run_step_changes() -> None:
    base = _new_request()
    changed = _new_request(parent_run_step_id=RunStepId.new())
    assert base.authorization_fingerprint != changed.authorization_fingerprint


def test_tampered_fingerprint_fails_closed() -> None:
    request = _new_request()
    with pytest.raises(DomainValidationError, match="delegation_fingerprint_mismatch"):
        DelegationRequest(
            id=request.id,
            parent_run_id=request.parent_run_id,
            target_agent_id=request.target_agent_id,
            objective=request.objective,
            input_payload=request.input_payload,
            expected_output_contract=request.expected_output_contract,
            authorization_fingerprint="0" * 64,
            status=request.status,
            created_at=request.created_at,
        )


@pytest.mark.parametrize("objective", ["", "   ", "x" * 4001])
def test_objective_is_bounded_and_non_blank(objective: str) -> None:
    with pytest.raises(DomainValidationError):
        _new_request(objective=objective)


@pytest.mark.parametrize("contract", ["", "   ", "x" * 4001])
def test_expected_output_contract_is_bounded_and_non_blank(contract: str) -> None:
    with pytest.raises(DomainValidationError):
        _new_request(expected_output_contract=contract)


def test_failed_status_requires_a_failure_code() -> None:
    request = _new_request()
    with pytest.raises(DomainValidationError, match="failed DelegationRequest"):
        DelegationRequest(
            id=request.id,
            parent_run_id=request.parent_run_id,
            target_agent_id=request.target_agent_id,
            objective=request.objective,
            input_payload=request.input_payload,
            expected_output_contract=request.expected_output_contract,
            authorization_fingerprint=request.authorization_fingerprint,
            status=DelegationStatus.FAILED,
            created_at=request.created_at,
        )


def test_non_failed_status_forbids_a_failure_code() -> None:
    request = _new_request()
    with pytest.raises(DomainValidationError, match="only a failed DelegationRequest"):
        DelegationRequest(
            id=request.id,
            parent_run_id=request.parent_run_id,
            target_agent_id=request.target_agent_id,
            objective=request.objective,
            input_payload=request.input_payload,
            expected_output_contract=request.expected_output_contract,
            authorization_fingerprint=request.authorization_fingerprint,
            status=DelegationStatus.REQUESTED,
            created_at=request.created_at,
            failure_code="boom",
        )


def test_malformed_authorization_fingerprint_is_rejected() -> None:
    request = _new_request()
    with pytest.raises(DomainValidationError):
        DelegationRequest(
            id=request.id,
            parent_run_id=request.parent_run_id,
            target_agent_id=request.target_agent_id,
            objective=request.objective,
            input_payload=request.input_payload,
            expected_output_contract=request.expected_output_contract,
            authorization_fingerprint="not-hex",
            status=request.status,
            created_at=request.created_at,
        )
