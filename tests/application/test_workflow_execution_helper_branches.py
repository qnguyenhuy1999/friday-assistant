from __future__ import annotations

import pytest

from friday.application.errors import WorkflowIntegrityError
from friday.application.workflow_execution_use_cases import (
    _bounded_child_result,
    _failure_from_child,
    _load_frozen_revision,
)
from friday.domain import (
    Failure,
    FailureCause,
    Run,
    RunEvent,
    RunEventId,
    RunEventType,
    RunId,
    TaskId,
)
from tests.application.test_workflow_execution_use_cases import T0, T1, _bootstrap


def test_frozen_revision_and_bounded_child_result_branches() -> None:
    uow, _, _, workflow, revision, _, _, _, _ = _bootstrap(edges=[])
    loaded_workflow, loaded_revision = _load_frozen_revision(
        uow, workflow.id, revision.id, revision.content_sha256
    )
    assert loaded_workflow.id == workflow.id
    assert loaded_revision.id == revision.id
    with pytest.raises(WorkflowIntegrityError):
        _load_frozen_revision(uow, workflow.id, revision.id, "0" * 64)
    child = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    child.start(T0)
    child.succeed(T1)
    uow.events.append(
        RunEvent(
            id=RunEventId.new(),
            run_id=child.id,
            type=RunEventType.AGENT_FINISHED,
            sequence=1,
            occurred_at=T1,
            payload={"summary": "done", "details": {"answer": 42}},
        )
    )
    result = _bounded_child_result(uow, child, T1)
    assert result["summary"] == "done"
    assert result["details"] == {"answer": 42}
    failed = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    assert _failure_from_child(failed) == (
        "child_run_failed",
        "Workflow child Run failed",
    )
    failed.start(T0)
    failed.fail(T1, Failure("bad", "broken", False, FailureCause.RUNTIME))
    assert _failure_from_child(failed) == ("bad", "broken")
