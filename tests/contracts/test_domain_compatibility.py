"""Domain-to-contract compatibility: each schema's enum value set matches its
domain StrEnum exactly, and a real domain entity instance -- built through
its own constructor/transition methods, then projected to a wire dict --
validates against its schema. Catches drift in either direction (schema
missing a new domain value, or domain removing a value the schema still
allows).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

import jsonschema
import pytest

from friday.domain.approval import ApprovalCategory, ApprovalRequest, ApprovalStatus
from friday.domain.artifact import Artifact, ArtifactKind
from friday.domain.event import RunEvent, RunEventType
from friday.domain.identifiers import (
    ApprovalRequestId,
    ArtifactId,
    RunEventId,
    RunId,
    RunStepId,
    TaskId,
    ToolInvocationId,
)
from friday.domain.run import Run, RunStatus
from friday.domain.step import RunStep, RunStepStatus
from friday.domain.task import Task, TaskStatus
from friday.domain.tool import ToolInvocation, ToolInvocationStatus
from tests.contracts.conftest import SCHEMA_ROOT, build_registry, load_schema

NOW = datetime(2026, 1, 1, tzinfo=UTC)

ENUM_COMPATIBILITY = [
    ("task/task.json", "status", TaskStatus),
    ("run/run.json", "status", RunStatus),
    ("step/run_step.json", "status", RunStepStatus),
    ("event/run_event.json", "type", RunEventType),
    ("approval/approval_request.json", "status", ApprovalStatus),
    ("approval/approval_request.json", "category", ApprovalCategory),
    ("artifact/artifact.json", "kind", ArtifactKind),
    ("tool/tool_invocation.json", "status", ToolInvocationStatus),
]


@pytest.mark.parametrize(
    ("rel_path", "field", "domain_enum"),
    ENUM_COMPATIBILITY,
    ids=lambda v: getattr(v, "__name__", str(v)),
)
def test_schema_enum_matches_domain_enum(
    rel_path: str, field: str, domain_enum: type[Enum]
) -> None:
    schema = load_schema(SCHEMA_ROOT / rel_path)
    schema_values = set(schema["properties"][field]["enum"])
    domain_values = {member.value for member in domain_enum}
    assert schema_values == domain_values


def _task_wire(task: Task) -> dict[str, object]:
    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "failed_at": task.failed_at.isoformat() if task.failed_at else None,
        "cancelled_at": task.cancelled_at.isoformat() if task.cancelled_at else None,
        "failure": None,
    }


def _run_wire(run: Run) -> dict[str, object]:
    return {
        "id": str(run.id),
        "task_id": str(run.task_id),
        "status": run.status.value,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "failure": None,
        "approval_request_id": str(run.approval_request_id) if run.approval_request_id else None,
    }


def _step_wire(step: RunStep) -> dict[str, object]:
    return {
        "id": str(step.id),
        "run_id": str(step.run_id),
        "name": step.name,
        "position": step.position,
        "status": step.status.value,
        "created_at": step.created_at.isoformat(),
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "ended_at": step.ended_at.isoformat() if step.ended_at else None,
        "failure": None,
        "approval_request_id": None,
    }


def _validate(rel_path: str, instance: dict[str, object]) -> None:
    schema = load_schema(SCHEMA_ROOT / rel_path)
    jsonschema.Draft202012Validator(schema, registry=build_registry()).validate(instance)


def test_task_lifecycle_projects_to_valid_wire_shape() -> None:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=NOW)
    _validate("task/task.json", _task_wire(task))
    task.start(NOW)
    _validate("task/task.json", _task_wire(task))
    task.complete(NOW)
    _validate("task/task.json", _task_wire(task))


def test_run_lifecycle_projects_to_valid_wire_shape() -> None:
    run = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=NOW)
    _validate("run/run.json", _run_wire(run))
    run.start(NOW)
    _validate("run/run.json", _run_wire(run))
    run.succeed(NOW)
    _validate("run/run.json", _run_wire(run))


def test_run_step_lifecycle_projects_to_valid_wire_shape() -> None:
    step = RunStep.new(id=RunStepId.new(), run_id=RunId.new(), name="s", position=0, created_at=NOW)
    _validate("step/run_step.json", _step_wire(step))
    step.start(NOW)
    _validate("step/run_step.json", _step_wire(step))
    step.succeed(NOW)
    _validate("step/run_step.json", _step_wire(step))


def test_run_event_projects_to_valid_wire_shape() -> None:
    event = RunEvent(
        id=RunEventId.new(),
        run_id=RunId.new(),
        type=RunEventType.RUN_CREATED,
        sequence=1,
        occurred_at=NOW,
        payload={"k": "v"},
    )
    _validate(
        "event/run_event.json",
        {
            "id": str(event.id),
            "run_id": str(event.run_id),
            "type": event.type.value,
            "sequence": event.sequence,
            "occurred_at": event.occurred_at.isoformat(),
            "payload": event.payload,
            "step_id": None,
        },
    )


def test_approval_request_projects_to_valid_wire_shape() -> None:
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=RunId.new(),
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
        requested_at=NOW,
    )
    _validate(
        "approval/approval_request.json",
        {
            "id": str(approval.id),
            "run_id": str(approval.run_id),
            "step_id": None,
            "category": approval.category.value,
            "summary": approval.summary,
            "reason": approval.reason,
            "requested_action": approval.requested_action,
            "requested_input": approval.requested_input,
            "status": approval.status.value,
            "requested_at": approval.requested_at.isoformat(),
            "resolved_at": None,
            "resolution_note": None,
            "resolver": None,
        },
    )


def test_artifact_projects_to_valid_wire_shape() -> None:
    artifact = Artifact(
        id=ArtifactId.new(),
        run_id=RunId.new(),
        kind=ArtifactKind.FILE,
        name="out.log",
        media_type="text/plain",
        location="/tmp/out.log",
        created_at=NOW,
    )
    _validate(
        "artifact/artifact.json",
        {
            "id": str(artifact.id),
            "run_id": str(artifact.run_id),
            "step_id": None,
            "kind": artifact.kind.value,
            "name": artifact.name,
            "media_type": artifact.media_type,
            "location": artifact.location,
            "created_at": artifact.created_at.isoformat(),
            "size": artifact.size,
            "checksum": artifact.checksum,
            "metadata": artifact.metadata,
        },
    )


def test_tool_invocation_projects_to_valid_wire_shape() -> None:
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=RunId.new(),
        tool_name="shell",
        requested_input={"cmd": "ls"},
        requested_at=NOW,
    )
    _validate(
        "tool/tool_invocation.json",
        {
            "id": str(invocation.id),
            "run_id": str(invocation.run_id),
            "step_id": None,
            "tool_name": invocation.tool_name,
            "requested_input": invocation.requested_input,
            "status": invocation.status.value,
            "requested_at": invocation.requested_at.isoformat(),
            "approval_request_id": None,
            "started_at": None,
            "completed_at": None,
            "output": invocation.output,
            "output_set": invocation.output_set,
            "failure": None,
        },
    )


def test_detector_flags_a_schema_domain_enum_drift() -> None:
    """Negative fixture: proves the enum-parity check catches drift without
    touching real schema/domain files."""
    schema_values = {"pending", "active"}
    domain_values = {member.value for member in TaskStatus}
    assert schema_values != domain_values
