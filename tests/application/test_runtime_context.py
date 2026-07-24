"""Deterministic context builder: exact ordering, inclusion of every
lifecycle entity, bounded per-item rendering, deterministic truncation with
explicit omission markers, and a hard character budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from friday.application.runtime_context import (
    MIN_CONTEXT_CHARS,
    RunSnapshot,
    build_runtime_context,
)
from friday.application.tool_gateway import ToolDescriptor
from friday.domain.approval import ApprovalCategory, ApprovalRequest
from friday.domain.artifact import Artifact, ArtifactKind
from friday.domain.event import RunEvent, RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import (
    ApprovalRequestId,
    ArtifactId,
    RunEventId,
    RunId,
    RunStepId,
    TaskId,
    ToolInvocationId,
)
from friday.domain.json_value import JsonValue
from friday.domain.run import Run
from friday.domain.step import RunStep
from friday.domain.task import Task
from friday.domain.tool import ToolInvocation

NOW = datetime(2026, 1, 1, tzinfo=UTC)

TASK_ID = TaskId.parse("11111111-1111-1111-1111-111111111111")
RUN_ID = RunId.parse("22222222-2222-2222-2222-222222222222")

MANIFEST = (
    ToolDescriptor(
        name="workspace.write_text",
        description="Write a text file.",
        read_only=False,
        approval_required=True,
    ),
    ToolDescriptor(
        name="workspace.list",
        description="List entries.",
        read_only=True,
        approval_required=False,
    ),
)


def _task() -> Task:
    task = Task.new(id=TASK_ID, title="Ship it", description="Do the work.", created_at=NOW)
    task.start(NOW)
    return task


def _run() -> Run:
    run = Run.new(id=RUN_ID, task_id=TASK_ID, created_at=NOW)
    run.start(NOW)
    return run


def _step(position: int, name: str) -> RunStep:
    return RunStep.new(
        id=RunStepId.new(), run_id=RUN_ID, name=name, position=position, created_at=NOW
    )


def _approval(requested_at: datetime, action: str) -> ApprovalRequest:
    return ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=RUN_ID,
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="r",
        requested_action=action,
        requested_input={"path": "a.txt"},
        requested_at=requested_at,
    )


def _invocation(requested_at: datetime, tool: str, output: JsonValue = None) -> ToolInvocation:
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=RUN_ID,
        tool_name=tool,
        requested_input={"path": "README.md"},
        requested_at=requested_at,
    )
    if output is not None:
        invocation.start(requested_at)
        invocation.succeed(requested_at, output)
    return invocation


def _artifact(created_at: datetime, name: str) -> Artifact:
    return Artifact(
        id=ArtifactId.new(),
        run_id=RUN_ID,
        kind=ArtifactKind.FILE,
        name=name,
        media_type="text/plain",
        location=f"out/{name}",
        created_at=created_at,
    )


def _event(sequence: int, type_: RunEventType) -> RunEvent:
    return RunEvent(
        id=RunEventId.new(), run_id=RUN_ID, type=type_, sequence=sequence, occurred_at=NOW
    )


def _snapshot(**overrides: object) -> RunSnapshot:
    fields: dict[str, object] = {
        "task": _task(),
        "run": _run(),
        "steps": (),
        "approvals": (),
        "invocations": (),
        "artifacts": (),
        "events": (),
        "previous_turns": (),
    }
    fields.update(overrides)
    return RunSnapshot(**fields)  # type: ignore[arg-type]


def _build(snapshot: RunSnapshot, max_chars: int = 20_000) -> str:
    return build_runtime_context(
        snapshot,
        tool_manifest=MANIFEST,
        attempt_number=1,
        turn_number=1,
        max_chars=max_chars,
    )


def test_minimal_snapshot_renders_objective_run_and_tools() -> None:
    document = _build(_snapshot())
    assert "# OBJECTIVE" in document
    assert f"Task {TASK_ID}: Ship it" in document
    assert f"Run {RUN_ID} status=running attempt=1 turn=1" in document
    assert "# TOOLS" in document
    # manifest is sorted by name regardless of registration order
    assert document.index("workspace.list") < document.index("workspace.write_text")
    # empty sections are omitted entirely
    for absent in ("# STEPS", "# APPROVALS", "# TOOL INVOCATIONS", "# ARTIFACTS"):
        assert absent not in document


def test_all_entities_render_in_deterministic_order() -> None:
    snapshot = _snapshot(
        steps=(_step(1, "second"), _step(0, "first")),
        approvals=(_approval(NOW + timedelta(minutes=2), "later"), _approval(NOW, "earlier")),
        invocations=(
            _invocation(NOW + timedelta(minutes=1), "workspace.list", output={"entries": ["a"]}),
            _invocation(NOW, "workspace.read_text"),
        ),
        artifacts=(_artifact(NOW + timedelta(minutes=3), "zzz.txt"), _artifact(NOW, "aaa.txt")),
        events=(_event(2, RunEventType.RUN_STARTED), _event(1, RunEventType.RUN_CREATED)),
        previous_turns=("proposed workspace.list", "read the result"),
    )
    document = _build(snapshot)
    assert document.index("[0] first") < document.index("[1] second")
    assert document.index("action=earlier") < document.index("action=later")
    assert document.index("workspace.read_text") < document.index(
        "tool=workspace.list status=succeeded"
    )
    assert document.index("aaa.txt") < document.index("zzz.txt")
    assert document.index("[1] run_created") < document.index("[2] run_started")
    assert 'output: {"entries":["a"]}' in document
    assert "- turn 1: proposed workspace.list" in document
    assert "- turn 2: read the result" in document


def test_identical_input_produces_identical_output() -> None:
    snapshot = _snapshot(
        events=tuple(_event(i, RunEventType.RUN_CREATED) for i in range(1, 6)),
    )
    assert _build(snapshot) == _build(snapshot)


def test_failure_lines_are_rendered_with_stable_codes() -> None:
    run = _run()
    run.fail(
        NOW,
        Failure(
            code="tool_timeout", message="took too long", retryable=True, cause=FailureCause.TIMEOUT
        ),
    )
    document = _build(_snapshot(run=run))
    assert "failure=tool_timeout: took too long" in document


def test_truncation_drops_oldest_events_first_with_marker() -> None:
    events = tuple(_event(i, RunEventType.RUN_CREATED) for i in range(1, 101))
    snapshot = _snapshot(events=events)
    full = _build(snapshot)
    budget = len(full) - 200
    document = _build(snapshot, max_chars=budget)
    assert len(document) <= budget
    assert "older event(s) omitted]" in document
    # newest event always survives; the oldest goes first
    assert "[100] run_created" in document
    assert "- [1] run_created" not in document


def test_truncation_is_deterministic() -> None:
    events = tuple(_event(i, RunEventType.RUN_CREATED) for i in range(1, 101))
    snapshot = _snapshot(events=events)
    assert _build(snapshot, max_chars=2000) == _build(snapshot, max_chars=2000)


def test_hard_budget_never_exceeded_even_when_nothing_droppable() -> None:
    task = Task.new(id=TASK_ID, title="T" * 500, description="D" * 3000, created_at=NOW)
    snapshot = _snapshot(task=task)
    document = _build(snapshot, max_chars=MIN_CONTEXT_CHARS)
    assert len(document) <= MIN_CONTEXT_CHARS
    assert document.endswith("[context truncated to budget]")


def test_oversized_invocation_output_is_clipped_per_item() -> None:
    big_output: JsonValue = {"data": "x" * 10_000}
    snapshot = _snapshot(invocations=(_invocation(NOW, "workspace.read_text", output=big_output),))
    document = _build(snapshot, max_chars=50_000)
    assert "…[truncated]" in document


def test_budget_below_floor_is_rejected() -> None:
    with pytest.raises(ValueError):
        _build(_snapshot(), max_chars=MIN_CONTEXT_CHARS - 1)


def test_no_environment_or_secret_content_included() -> None:
    document = _build(_snapshot())
    for forbidden in ("ANTHROPIC", "API_KEY", "PATH=", "HOME="):
        assert forbidden not in document
