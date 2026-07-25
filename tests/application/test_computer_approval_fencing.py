"""Computer-use approval and claim fencing, exercised through ExecuteToolAction.

These go through the real use case rather than calling the gateway directly,
because the gateway is not what enforces approval — ExecuteToolAction is. A
gateway test can only show that `approval_required` is True; it cannot show that
an unapproved click never reaches a desktop.

So every assertion here is paired with `driver.mutating_calls == ()`. That tuple
is the actual safety property: proof of a non-event. A test that observed only
`outcome.kind == "approval_required"` would pass just as happily against a
gateway that clicked first and reported the requirement afterwards.

Fingerprint binding is tested by *differing* one field at a time. An approval
that authorized "click element 14" must not authorize element 15, the other
button, the second click, the other window, or the same action against a newer
capture — and the only way to know that is to approve one and attempt the other.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from friday.application.claim_aware_tool_execution import ExecuteToolAction, ToolActionOutcome
from friday.application.errors import ClaimLost, ToolExecutionAmbiguous
from friday.application.ports import UnitOfWorkFactory
from friday.application.tool_authorization import compute_authorization_fingerprint
from friday.application.tool_gateway import ToolCall
from friday.domain.approval import ApprovalCategory, ApprovalRequest
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId
from friday.domain.json_value import JsonValue
from friday.domain.run import Run
from friday.domain.task import Task
from friday.domain.tool import ToolInvocationStatus
from friday.infrastructure.computer.models import ScreenPoint
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from tests.infrastructure.computer_harness import T0, Harness, build_harness

LEASE = timedelta(minutes=1)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


@pytest.fixture
def uow_factory(tmp_path: Path) -> Iterator[UnitOfWorkFactory]:
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "computer.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    yield create_unit_of_work_factory(create_session_factory(engine))
    engine.dispose()


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return build_harness(workspace)


@pytest.fixture
def executor(uow_factory: UnitOfWorkFactory, harness: Harness) -> ExecuteToolAction:
    """Note the shared clock: the snapshot TTL and the execution timestamps move
    together, so a test never accidentally expires its own fence."""
    return ExecuteToolAction(uow_factory, FixedClock(T0), harness.gateway)


@pytest.fixture
def run_id(uow_factory: UnitOfWorkFactory, harness: Harness) -> RunId:
    return _seed_claimed_run(uow_factory, harness.run_id)


def _seed_claimed_run(uow_factory: UnitOfWorkFactory, run_id: RunId) -> RunId:
    """One claimed, RUNNING Run whose id matches the harness's snapshot scope."""
    task = Task.new(id=TaskId.new(), title="desktop", description="", created_at=T0)
    task.start(T0)
    with uow_factory() as uow:
        uow.tasks.add(task)
        uow.commit()
    run = Run.new(id=run_id, task_id=task.id, created_at=T0)
    run.start(T0)
    with uow_factory() as uow:
        uow.runs.add(run)
        uow.commit()
    with uow_factory() as uow:
        uow.work_queue.enqueue(run.id, available_at=T0, enqueued_at=T0)
        assert uow.work_queue.try_claim(run.id, "w1", "tok", T0, T0 + LEASE)
        uow.commit()
    return run.id


def _generation(uow_factory: UnitOfWorkFactory, run_id: RunId) -> int:
    with uow_factory() as uow:
        item = uow.work_queue.get(run_id)
        assert item is not None
        return item.claim_generation


def _approve(uow_factory: UnitOfWorkFactory, run_id: RunId, call: ToolCall) -> ApprovalRequestId:
    """Approve exactly one action, bound by its exact fingerprint."""
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run_id,
        category=ApprovalCategory.COMPUTER_USE,
        summary=call.tool,
        reason="",
        requested_action=call.tool,
        requested_input=call.tool_input,
        requested_at=T0,
        authorization_fingerprint=compute_authorization_fingerprint(
            run_id=run_id, step_id=None, call=call
        ),
    )
    approval.approve(T0, resolver="patrick")
    with uow_factory() as uow:
        uow.approvals.add(approval)
        uow.commit()
    return approval.id


def _execute(
    executor: ExecuteToolAction,
    uow_factory: UnitOfWorkFactory,
    run_id: RunId,
    call: ToolCall,
    *,
    cancellation_requested: Callable[[], bool] | None = None,
) -> ToolActionOutcome:
    return executor.execute(
        run_id=run_id,
        step_id=None,
        call=call,
        worker_id="w1",
        claim_token="tok",
        claim_generation=_generation(uow_factory, run_id),
        cancellation_requested=cancellation_requested,
    )


def _steal_claim_on_mutation(
    harness: Harness, uow_factory: UnitOfWorkFactory, run_id: RunId
) -> None:
    """Remove the work item the instant a side effect lands.

    This is the window that makes a desktop action irreducibly ambiguous: the
    click has happened, and Friday no longer holds the claim it would need to
    record that fact.
    """

    def hook(name: str) -> None:
        if name in {"capture", "pointer_position", "list_windows", "active_window"}:
            return
        with uow_factory() as other:
            other.work_queue.remove(run_id)
            other.commit()

    harness.driver.on_call = hook


def _click(snapshot_id: str, **overrides: JsonValue) -> ToolCall:
    payload: dict[str, JsonValue] = {
        "snapshot_id": snapshot_id,
        "window_id": "win-mail",
        "element": 14,
    }
    payload.update(overrides)
    return ToolCall(tool="computer.click", tool_input=payload)


# --- nothing happens without approval -------------------------------------


def test_unapproved_click_never_calls_driver(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    outcome = _execute(executor, uow_factory, run_id, _click(snapshot_id))

    assert outcome.kind == "approval_required"
    assert harness.driver.mutating_calls == ()
    with uow_factory() as uow:
        assert uow.tool_invocations.list_for_run(run_id) == []


def test_unapproved_type_text_never_calls_driver(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    call = ToolCall(
        tool="computer.type_text",
        tool_input={"snapshot_id": snapshot_id, "window_id": "win-mail", "text": "hello"},
    )

    outcome = _execute(executor, uow_factory, run_id, call)

    assert outcome.kind == "approval_required"
    assert harness.driver.mutating_calls == ()


def test_unapproved_hotkey_never_calls_driver(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    call = ToolCall(
        tool="computer.hotkey",
        tool_input={
            "snapshot_id": snapshot_id,
            "window_id": "win-mail",
            "key": "c",
            "modifiers": ["meta"],
        },
    )

    outcome = _execute(executor, uow_factory, run_id, call)

    assert outcome.kind == "approval_required"
    assert harness.driver.mutating_calls == ()


def test_capture_needs_no_approval_and_reaches_the_driver(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """The read-only counterpart: observation must not need a human in the loop,
    or Claude can never look before it leaps."""
    outcome = _execute(
        executor,
        uow_factory,
        run_id,
        ToolCall(tool="computer.capture", tool_input={"include_screenshot": False}),
    )

    assert outcome.kind == "executed"
    assert harness.driver.call_names == ("capture",)


# --- exact fingerprint binding -------------------------------------------


def test_successful_approved_computer_action_calls_driver_exactly_once(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    call = _click(snapshot_id)
    approval_id = _approve(uow_factory, run_id, call)

    outcome = _execute(executor, uow_factory, run_id, call)

    assert outcome.kind == "executed"
    assert len(harness.driver.mutating_calls) == 1
    click = harness.driver.only_call("click")
    assert click.argument("window_id") == "win-mail"
    with uow_factory() as uow:
        invocations = uow.tool_invocations.list_for_run(run_id)
        assert len(invocations) == 1
        assert invocations[0].status is ToolInvocationStatus.SUCCEEDED
        assert invocations[0].approval_request_id == approval_id
        assert invocations[0].requested_input == call.tool_input
        assert uow.approvals.list_for_run(run_id)[0].is_consumed is True


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("element", 15),
        ("button", "right"),
        ("count", 2),
        ("window_id", "win-notes"),
    ],
    ids=["element", "button", "count", "window"],
)
def test_approval_for_click_a_cannot_authorize_click_b(
    executor: ExecuteToolAction,
    uow_factory: UnitOfWorkFactory,
    run_id: RunId,
    harness: Harness,
    field: str,
    changed: JsonValue,
) -> None:
    """One field differs, so the fingerprint differs, so the grant does not
    apply — and no click happens."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    _approve(uow_factory, run_id, _click(snapshot_id))

    outcome = _execute(executor, uow_factory, run_id, _click(snapshot_id, **{field: changed}))

    assert outcome.kind == "approval_required"
    assert harness.driver.mutating_calls == ()


def test_approval_fingerprint_binds_coordinates(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """One pixel is a different action. Neighbouring controls are closer than
    the difference between Send and Delete sometimes is."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    approved = ToolCall(
        tool="computer.click",
        tool_input={"snapshot_id": snapshot_id, "window_id": "win-mail", "x": 100, "y": 200},
    )
    _approve(uow_factory, run_id, approved)
    shifted = ToolCall(
        tool="computer.click",
        tool_input={"snapshot_id": snapshot_id, "window_id": "win-mail", "x": 101, "y": 200},
    )

    assert _execute(executor, uow_factory, run_id, shifted).kind == "approval_required"
    assert harness.driver.mutating_calls == ()
    assert _execute(executor, uow_factory, run_id, approved).kind == "executed"
    point = harness.driver.only_call("click").argument("point")
    assert isinstance(point, ScreenPoint)
    assert point.x == 100


def test_approval_fingerprint_binds_snapshot(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """A grant is tied to the observation it was reviewed against. Re-capturing
    produces a new fence, and the old approval must not carry over to it."""
    first = harness.capture_snapshot()
    _approve(uow_factory, run_id, _click(first))
    second = harness.capture_snapshot()
    harness.driver.calls.clear()
    assert first != second

    outcome = _execute(executor, uow_factory, run_id, _click(second))

    assert outcome.kind == "approval_required"
    assert harness.driver.mutating_calls == ()


def test_approval_fingerprint_binds_typed_text(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    fence: dict[str, JsonValue] = {"snapshot_id": snapshot_id, "window_id": "win-mail"}
    _approve(
        uow_factory,
        run_id,
        ToolCall(tool="computer.type_text", tool_input={**fence, "text": "ship it"}),
    )

    outcome = _execute(
        executor,
        uow_factory,
        run_id,
        ToolCall(tool="computer.type_text", tool_input={**fence, "text": "ship it now"}),
    )

    assert outcome.kind == "approval_required"
    assert harness.driver.mutating_calls == ()


@pytest.mark.parametrize(
    ("approved_key", "approved_modifiers", "attempted_key", "attempted_modifiers"),
    [
        ("c", ["meta"], "v", ["meta"]),
        ("c", ["meta"], "c", ["ctrl"]),
        ("c", ["meta"], "c", ["meta", "shift"]),
        ("c", ["meta"], "c", []),
    ],
    ids=["other-key", "other-modifier", "extra-modifier", "no-modifier"],
)
def test_approval_fingerprint_binds_keystroke(
    executor: ExecuteToolAction,
    uow_factory: UnitOfWorkFactory,
    run_id: RunId,
    harness: Harness,
    approved_key: str,
    approved_modifiers: list[str],
    attempted_key: str,
    attempted_modifiers: list[str],
) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    fence: dict[str, JsonValue] = {"snapshot_id": snapshot_id, "window_id": "win-mail"}
    _approve(
        uow_factory,
        run_id,
        ToolCall(
            tool="computer.hotkey",
            tool_input={**fence, "key": approved_key, "modifiers": list(approved_modifiers)},
        ),
    )

    outcome = _execute(
        executor,
        uow_factory,
        run_id,
        ToolCall(
            tool="computer.hotkey",
            tool_input={**fence, "key": attempted_key, "modifiers": list(attempted_modifiers)},
        ),
    )

    assert outcome.kind == "approval_required"
    assert harness.driver.mutating_calls == ()


def test_an_approval_is_one_shot(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """Consumed once, and never a second free click: the second attempt hits the
    replay path and reuses the recorded outcome instead of re-executing."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    call = _click(snapshot_id)
    _approve(uow_factory, run_id, call)

    first = _execute(executor, uow_factory, run_id, call)
    second = _execute(executor, uow_factory, run_id, call)

    assert first.kind == "executed"
    assert second.kind == "executed"
    assert second.replayed is True
    assert len(harness.driver.mutating_calls) == 1


# --- claim fencing --------------------------------------------------------


def test_computer_driver_is_never_called_before_durable_tool_invocation(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """Txn A commits a RUNNING invocation before the side effect, so a crash
    mid-click still leaves durable evidence that Friday tried."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    call = _click(snapshot_id)
    _approve(uow_factory, run_id, call)
    observed: list[ToolInvocationStatus] = []

    def record_statuses(name: str) -> None:
        with uow_factory() as uow:
            observed.extend(i.status for i in uow.tool_invocations.list_for_run(run_id))

    harness.driver.on_call = record_statuses
    _execute(executor, uow_factory, run_id, call)

    assert observed == [ToolInvocationStatus.RUNNING]


def test_claim_loss_before_computer_execution_prevents_driver_call(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """A stale generation must not reach a desktop at all."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    call = _click(snapshot_id)
    _approve(uow_factory, run_id, call)

    with pytest.raises(ClaimLost):
        executor.execute(
            run_id=run_id,
            step_id=None,
            call=call,
            worker_id="w1",
            claim_token="tok",
            claim_generation=_generation(uow_factory, run_id) + 1,
        )

    assert harness.driver.mutating_calls == ()
    with uow_factory() as uow:
        assert uow.tool_invocations.list_for_run(run_id) == []
        assert uow.approvals.list_for_run(run_id)[0].is_consumed is False


def test_gateway_cancellation_before_driver_call_prevents_driver_call(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """The cheap in-flight check is defence in depth, not the fence — but it
    must still stop the side effect when it fires."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    call = _click(snapshot_id)
    _approve(uow_factory, run_id, call)

    outcome = _execute(executor, uow_factory, run_id, call, cancellation_requested=lambda: True)

    assert outcome.kind == "executed"
    assert outcome.result is not None
    assert outcome.result.failure is not None
    assert outcome.result.failure.code == "claim_lost"
    assert harness.driver.mutating_calls == ()


def test_claim_loss_after_external_computer_execution_does_not_persist_owned_success(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """The irreducible case: the click landed, then the claim was stolen before
    Txn B. Friday must not claim that outcome as its own success — the
    invocation stays RUNNING and ambiguous."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    call = _click(snapshot_id)
    _approve(uow_factory, run_id, call)
    _steal_claim_on_mutation(harness, uow_factory, run_id)

    with pytest.raises(ClaimLost):
        _execute(executor, uow_factory, run_id, call)

    assert len(harness.driver.mutating_calls) == 1  # the side effect did happen
    with uow_factory() as uow:
        invocation = uow.tool_invocations.list_for_run(run_id)[0]
        assert invocation.status is ToolInvocationStatus.RUNNING
        assert invocation.output_set is False
        assert uow.approvals.list_for_run(run_id)[0].is_consumed is True


def test_consumed_approval_with_running_computer_invocation_is_ambiguous(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """Following on from the above: a later claim finds a consumed approval and a
    non-terminal invocation, and must refuse to decide what happened."""
    snapshot_id = harness.capture_snapshot()
    call = _click(snapshot_id)
    _approve(uow_factory, run_id, call)
    _steal_claim_on_mutation(harness, uow_factory, run_id)
    with pytest.raises(ClaimLost):
        _execute(executor, uow_factory, run_id, call)
    harness.driver.on_call = None

    # a fresh worker re-claims the run and proposes the same action again
    with uow_factory() as uow:
        uow.work_queue.enqueue(run_id, available_at=T0, enqueued_at=T0)
        assert uow.work_queue.try_claim(run_id, "w2", "tok2", T0, T0 + LEASE)
        uow.commit()
    generation = _generation(uow_factory, run_id)
    before = len(harness.driver.mutating_calls)

    with pytest.raises(ToolExecutionAmbiguous):
        executor.execute(
            run_id=run_id,
            step_id=None,
            call=call,
            worker_id="w2",
            claim_token="tok2",
            claim_generation=generation,
        )

    assert len(harness.driver.mutating_calls) == before


def test_ambiguous_computer_action_is_never_automatically_replayed(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """Stated as its own property because it is the whole reason desktop actions
    are non-retryable: a second click is not free."""
    snapshot_id = harness.capture_snapshot()
    call = _click(snapshot_id)
    _approve(uow_factory, run_id, call)
    _steal_claim_on_mutation(harness, uow_factory, run_id)
    with pytest.raises(ClaimLost):
        _execute(executor, uow_factory, run_id, call)
    harness.driver.on_call = None
    clicks_after_first_attempt = len(harness.driver.mutating_calls)

    with uow_factory() as uow:
        uow.work_queue.enqueue(run_id, available_at=T0, enqueued_at=T0)
        assert uow.work_queue.try_claim(run_id, "w2", "tok2", T0, T0 + LEASE)
        uow.commit()
    generation = _generation(uow_factory, run_id)

    # repeated attempts under a perfectly valid claim still refuse to re-click
    for _ in range(3):
        with pytest.raises(ToolExecutionAmbiguous):
            executor.execute(
                run_id=run_id,
                step_id=None,
                call=call,
                worker_id="w2",
                claim_token="tok2",
                claim_generation=generation,
            )

    assert len(harness.driver.mutating_calls) == clicks_after_first_attempt


def test_a_computer_failure_is_recorded_as_non_retryable(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """A timed-out desktop action may already have landed."""
    from friday.infrastructure.computer.errors import ComputerDriverTimeout

    snapshot_id = harness.capture_snapshot()
    call = _click(snapshot_id)
    _approve(uow_factory, run_id, call)
    harness.driver.raises = ComputerDriverTimeout("no answer")

    outcome = _execute(executor, uow_factory, run_id, call)

    assert outcome.result is not None
    failure = outcome.result.failure
    assert failure is not None
    assert failure.code == "computer_driver_timeout"
    assert failure.retryable is False
    with uow_factory() as uow:
        invocation = uow.tool_invocations.list_for_run(run_id)[0]
        assert invocation.status is ToolInvocationStatus.FAILED
        assert invocation.failure is not None
        assert invocation.failure.retryable is False


def test_a_capture_artifact_is_persisted_through_the_normal_artifact_flow(
    executor: ExecuteToolAction, uow_factory: UnitOfWorkFactory, run_id: RunId, harness: Harness
) -> None:
    """No parallel artifact system: the screenshot lands via Txn B exactly like
    a workspace write."""
    outcome = _execute(
        executor, uow_factory, run_id, ToolCall(tool="computer.capture", tool_input={})
    )

    assert outcome.kind == "executed"
    with uow_factory() as uow:
        artifacts = uow.artifacts.list_for_run(run_id)
        assert len(artifacts) == 1
        assert artifacts[0].kind.value == "image"
        assert not artifacts[0].location.startswith("/")
        assert (harness.workspace / artifacts[0].location).is_file()
