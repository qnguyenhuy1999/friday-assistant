"""ToolGateway model invariants: dotted tool names, JSON-object inputs,
workspace-relative artifact locations, and failed/succeeded result shape."""

from __future__ import annotations

import pytest

from friday.application.errors import ToolInputInvalid
from friday.application.tool_gateway import (
    ArtifactCandidate,
    ToolCall,
    ToolDescriptor,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolRiskAssessment,
)
from friday.domain.approval import ApprovalCategory
from friday.domain.artifact import ArtifactKind
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import RunId, ToolInvocationId

_FAILURE = Failure(
    code="tool_timeout",
    message="tool exceeded its timeout",
    retryable=True,
    cause=FailureCause.TIMEOUT,
)


def test_descriptor_requires_dotted_name_and_description() -> None:
    descriptor = ToolDescriptor(
        name="workspace.list",
        description="List entries.",
        read_only=True,
        approval_required=False,
    )
    assert descriptor.name == "workspace.list"
    with pytest.raises(ValueError):
        ToolDescriptor(
            name="Workspace.List", description="x", read_only=True, approval_required=False
        )
    with pytest.raises(ValueError):
        ToolDescriptor(
            name="workspace.list", description="  ", read_only=True, approval_required=False
        )


def test_tool_call_validates_name_and_input() -> None:
    call = ToolCall(tool="workspace.read_text", tool_input={"path": "README.md"})
    assert call.tool_input == {"path": "README.md"}
    with pytest.raises(ToolInputInvalid):
        ToolCall(tool="no-dots", tool_input={})
    with pytest.raises(ToolInputInvalid):
        ToolCall(tool="workspace.read_text", tool_input=["not", "an", "object"])


def test_risk_assessment_requires_summary() -> None:
    assessment = ToolRiskAssessment(
        tool="process.run",
        read_only=False,
        approval_required=True,
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="Run one configured command.",
    )
    assert assessment.approval_required is True
    with pytest.raises(ValueError):
        ToolRiskAssessment(
            tool="process.run",
            read_only=False,
            approval_required=True,
            category=ApprovalCategory.TOOL_EXECUTION,
            summary="  ",
        )


def test_artifact_candidate_location_must_be_workspace_relative() -> None:
    candidate = ArtifactCandidate(
        kind=ArtifactKind.FILE,
        name="notes.md",
        media_type="text/markdown",
        location="docs/notes.md",
        size=12,
        checksum="abc",
    )
    assert candidate.location == "docs/notes.md"
    for bad_location in ("/etc/passwd", "../outside.txt"):
        with pytest.raises(ValueError):
            ArtifactCandidate(
                kind=ArtifactKind.FILE,
                name="x",
                media_type="text/plain",
                location=bad_location,
            )
    with pytest.raises(ValueError):
        ArtifactCandidate(
            kind=ArtifactKind.FILE,
            name="x",
            media_type="text/plain",
            location="ok.txt",
            size=-1,
        )


def test_execution_result_shape_invariants() -> None:
    ok = ToolExecutionResult.succeeded({"entries": []})
    assert ok.status == "succeeded"
    assert ok.failure is None

    failed = ToolExecutionResult.failed(_FAILURE)
    assert failed.status == "failed"
    assert failed.failure is _FAILURE

    with pytest.raises(ValueError):
        ToolExecutionResult(status="failed")
    with pytest.raises(ValueError):
        ToolExecutionResult(status="succeeded", failure=_FAILURE)


def test_execution_request_binds_invocation_identity() -> None:
    request = ToolExecutionRequest(
        invocation_id=ToolInvocationId.new(),
        run_id=RunId.new(),
        step_id=None,
        call=ToolCall(tool="workspace.list", tool_input={}),
    )
    assert request.step_id is None
    assert request.call.tool == "workspace.list"
