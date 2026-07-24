"""BrainRequest/BrainResponse invariants: bounded turn identity, non-empty
context, JSON-safe usage metadata, and Protocol conformance for fakes."""

from __future__ import annotations

import pytest

from friday.application.brain_runtime import BrainRequest, BrainResponse, BrainRuntime
from friday.application.runtime_actions import FinishAction
from friday.application.tool_gateway import ToolDescriptor
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import RunId, TaskId

_MANIFEST = (
    ToolDescriptor(
        name="workspace.read_text",
        description="Read a UTF-8 text file inside the workspace.",
        read_only=True,
        approval_required=False,
    ),
)


def _request(**overrides: object) -> BrainRequest:
    fields: dict[str, object] = {
        "run_id": RunId.new(),
        "task_id": TaskId.new(),
        "turn_number": 1,
        "attempt_number": 1,
        "context": "# OBJECTIVE\ndo the thing",
        "tool_manifest": _MANIFEST,
        "max_response_bytes": 65536,
    }
    fields.update(overrides)
    return BrainRequest(**fields)  # type: ignore[arg-type]


def test_valid_request_constructs() -> None:
    request = _request()
    assert request.turn_number == 1
    assert request.tool_manifest == _MANIFEST


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("turn_number", 0),
        ("attempt_number", 0),
        ("context", "   "),
        ("max_response_bytes", 0),
    ],
)
def test_invalid_request_rejected(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _request(**{field: value})


def test_response_carries_action_and_safe_metadata() -> None:
    response = BrainResponse(
        action=FinishAction(summary="done"),
        model="claude-x",
        usage={"input_tokens": 10},
        repaired=True,
    )
    assert isinstance(response.action, FinishAction)
    assert response.repaired is True


def test_response_defaults_are_minimal() -> None:
    response = BrainResponse(action=FinishAction(summary="done"))
    assert response.model is None
    assert response.usage is None
    assert response.repaired is False


def test_response_rejects_non_json_usage() -> None:
    with pytest.raises(DomainValidationError):
        BrainResponse(action=FinishAction(summary="done"), usage={"when": object()})  # type: ignore[dict-item]


def test_fake_runtime_satisfies_protocol() -> None:
    class FakeBrain:
        def next_action(self, request: BrainRequest) -> BrainResponse:
            return BrainResponse(action=FinishAction(summary="ok"))

    runtime: BrainRuntime = FakeBrain()
    assert isinstance(runtime.next_action(_request()).action, FinishAction)
