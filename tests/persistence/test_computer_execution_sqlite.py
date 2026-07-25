"""Production-path E2E for computer use, against real SQLite.

Walks the whole documented sequence with nothing stubbed except the driver
itself: claim a Run, propose a click, get parked for approval, have a human
approve that exact action, resume, and execute once.

Every step is verified against a *fresh* Unit of Work, so the assertions are
about what was durably committed rather than about in-memory objects that
happen to be correct. The point of this test is that the safety story survives
the transaction boundaries, which is exactly what an in-process test cannot show.

The negative case is the important half. A claim lost after the click but before
Txn B leaves an invocation RUNNING with its approval consumed — Friday cannot
know whether the side effect landed, so it must not record a success and must
never replay. That ambiguity is a deliberate design outcome, not a gap.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from friday.application.approval_workflow import ApproveRequest
from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.commands import ApproveRequestCommand, RequestApprovalCommand
from friday.application.errors import ClaimLost, ToolExecutionAmbiguous
from friday.application.ports import UnitOfWorkFactory
from friday.application.tool_authorization import RequestToolApproval
from friday.application.tool_gateway import ToolCall
from friday.domain.approval import ApprovalStatus
from friday.domain.event import RunEventType
from friday.domain.identifiers import RunId, TaskId
from friday.domain.json_value import JsonValue
from friday.domain.run import Run, RunStatus
from friday.domain.task import Task
from friday.domain.tool import ToolInvocationStatus
from friday.infrastructure.computer.models import ScreenPoint
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from tests.infrastructure.computer_harness import T0, Harness, build_harness

LEASE = timedelta(minutes=1)
WORKER = "w1"
TOKEN = "tok"


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


@pytest.fixture
def uow_factory(tmp_path: Path) -> Iterator[UnitOfWorkFactory]:
    db_path = tmp_path / "computer-e2e.db"
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
def clock() -> FixedClock:
    return FixedClock(T0)


def _seed_claimed_run(uow_factory: UnitOfWorkFactory, run_id: RunId) -> None:
    task = Task.new(id=TaskId.new(), title="reply to mail", description="", created_at=T0)
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
        uow.work_queue.enqueue(run_id, available_at=T0, enqueued_at=T0)
        assert uow.work_queue.try_claim(run_id, WORKER, TOKEN, T0, T0 + LEASE)
        uow.commit()


def _generation(uow_factory: UnitOfWorkFactory, run_id: RunId) -> int:
    with uow_factory() as uow:
        item = uow.work_queue.get(run_id)
        assert item is not None
        return item.claim_generation


def _reclaim(uow_factory: UnitOfWorkFactory, run_id: RunId) -> int:
    """Claim the resumed run, exactly as the worker loop would.

    ApproveRequest already performed the resume and re-enqueue as part of
    resolving the approval, so a worker's only remaining job is to win the
    claim — and it gets a *fresh* generation, which is what makes the
    pre-approval generation unusable afterwards.
    """
    with uow_factory() as uow:
        run = uow.runs.get(run_id)
        assert run is not None
        assert run.status is RunStatus.RUNNING
        assert uow.work_queue.try_claim(run_id, WORKER, TOKEN, T0, T0 + LEASE)
        uow.commit()
    return _generation(uow_factory, run_id)


def test_the_full_approved_click_path(
    uow_factory: UnitOfWorkFactory, harness: Harness, clock: FixedClock
) -> None:
    run_id = harness.run_id
    _seed_claimed_run(uow_factory, run_id)
    executor = ExecuteToolAction(uow_factory, clock, harness.gateway)
    request_approval = RequestToolApproval(uow_factory, clock)

    # --- Claude observes the desktop (read-only, no approval) --------------
    capture = harness.capture()
    snapshot_id = capture["snapshot_id"]
    assert isinstance(snapshot_id, str)
    harness.driver.calls.clear()

    call = ToolCall(
        tool="computer.click",
        tool_input={"snapshot_id": snapshot_id, "window_id": "win-mail", "element": 14},
    )

    # --- first attempt: parked for approval, nothing touched --------------
    first = executor.execute(
        run_id=run_id,
        step_id=None,
        call=call,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )

    assert first.kind == "approval_required"
    assert harness.driver.mutating_calls == ()
    with uow_factory() as fresh:
        assert fresh.tool_invocations.list_for_run(run_id) == []

    # --- Friday requests approval for that exact action -------------------
    approval = request_approval.execute(
        RequestApprovalCommand(
            run_id=run_id,
            category=first.risk.category,
            summary=first.risk.summary,
            reason="clicking Search in Mail",
            requested_action=call.tool,
            requested_input=call.tool_input,
            authorization_fingerprint=first.fingerprint,
        ),
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )

    with uow_factory() as fresh:
        run = fresh.runs.get(run_id)
        assert run is not None
        assert run.status is RunStatus.WAITING_FOR_APPROVAL
        assert fresh.work_queue.get(run_id) is None  # parked, not runnable
        assert harness.driver.mutating_calls == ()

    # --- a human approves it ---------------------------------------------
    ApproveRequest(uow_factory, clock).execute(
        ApproveRequestCommand(approval_id=approval.approval_id, resolver="patrick")
    )
    generation = _reclaim(uow_factory, run_id)

    # --- second attempt: executes exactly once ---------------------------
    second = executor.execute(
        run_id=run_id,
        step_id=None,
        call=call,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=generation,
    )

    assert second.kind == "executed"
    assert second.replayed is False

    # exactly one click, at the resolved centre of element 14, in that window
    assert len(harness.driver.mutating_calls) == 1
    click = harness.driver.only_call("click")
    assert click.argument("window_id") == "win-mail"
    point = click.argument("point")
    assert isinstance(point, ScreenPoint)
    assert (point.x, point.y) == (200, 65)
    assert click.argument("count") == 1

    # everything durable, read back through a fresh session
    with uow_factory() as fresh:
        invocations = fresh.tool_invocations.list_for_run(run_id)
        assert len(invocations) == 1
        invocation = invocations[0]
        assert invocation.status is ToolInvocationStatus.SUCCEEDED
        assert invocation.tool_name == "computer.click"
        assert invocation.approval_request_id == approval.approval_id
        assert invocation.requested_input == call.tool_input
        assert invocation.output == {
            "window_id": "win-mail",
            "x": 200,
            "y": 65,
            "button": "left",
            "count": 1,
        }

        stored = fresh.approvals.list_for_run(run_id)
        assert len(stored) == 1
        assert stored[0].status is ApprovalStatus.APPROVED
        assert stored[0].is_consumed is True
        assert stored[0].authorization_fingerprint == first.fingerprint

        event_types = [event.type for event in fresh.events.list_for_run(run_id)]
        assert RunEventType.APPROVAL_REQUESTED in event_types
        assert RunEventType.TOOL_INVOCATION_STARTED in event_types
        assert RunEventType.TOOL_INVOCATION_SUCCEEDED in event_types

    # --- and no second execution -----------------------------------------
    replay = executor.execute(
        run_id=run_id,
        step_id=None,
        call=call,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=generation,
    )

    assert replay.replayed is True
    assert len(harness.driver.mutating_calls) == 1


def test_the_approved_click_cannot_be_repurposed_for_a_different_element(
    uow_factory: UnitOfWorkFactory, harness: Harness, clock: FixedClock
) -> None:
    """The whole point of exact binding, on the production path: an approval a
    human granted for one control does not authorize its neighbour."""
    run_id = harness.run_id
    _seed_claimed_run(uow_factory, run_id)
    executor = ExecuteToolAction(uow_factory, clock, harness.gateway)
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    fence: dict[str, JsonValue] = {"snapshot_id": snapshot_id, "window_id": "win-mail"}
    approved = ToolCall(tool="computer.click", tool_input={**fence, "element": 14})

    outcome = executor.execute(
        run_id=run_id,
        step_id=None,
        call=approved,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )
    approval = RequestToolApproval(uow_factory, clock).execute(
        RequestApprovalCommand(
            run_id=run_id,
            category=outcome.risk.category,
            summary=outcome.risk.summary,
            reason="",
            requested_action=approved.tool,
            requested_input=approved.tool_input,
            authorization_fingerprint=outcome.fingerprint,
        ),
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )
    ApproveRequest(uow_factory, clock).execute(
        ApproveRequestCommand(approval_id=approval.approval_id, resolver="patrick")
    )
    generation = _reclaim(uow_factory, run_id)

    # the neighbouring control, under the same live fence
    diverted = executor.execute(
        run_id=run_id,
        step_id=None,
        call=ToolCall(tool="computer.click", tool_input={**fence, "element": 15}),
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=generation,
    )

    assert diverted.kind == "approval_required"
    assert harness.driver.mutating_calls == ()
    with uow_factory() as fresh:
        assert fresh.tool_invocations.list_for_run(run_id) == []
        assert fresh.approvals.list_for_run(run_id)[0].is_consumed is False


def test_a_claim_lost_before_the_side_effect_never_reaches_the_desktop(
    uow_factory: UnitOfWorkFactory, harness: Harness, clock: FixedClock
) -> None:
    """The negative E2E: a stale generation is refused in Txn A, so the approval
    is not consumed, no invocation exists, and nothing was clicked."""
    run_id = harness.run_id
    _seed_claimed_run(uow_factory, run_id)
    executor = ExecuteToolAction(uow_factory, clock, harness.gateway)
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    call = ToolCall(
        tool="computer.click",
        tool_input={"snapshot_id": snapshot_id, "window_id": "win-mail", "element": 14},
    )

    with pytest.raises(ClaimLost):
        executor.execute(
            run_id=run_id,
            step_id=None,
            call=call,
            worker_id=WORKER,
            claim_token=TOKEN,
            claim_generation=_generation(uow_factory, run_id) + 5,
        )

    assert harness.driver.mutating_calls == ()
    with uow_factory() as fresh:
        assert fresh.tool_invocations.list_for_run(run_id) == []
        types = [event.type for event in fresh.events.list_for_run(run_id)]
        assert RunEventType.TOOL_INVOCATION_STARTED not in types


def test_a_claim_lost_after_the_side_effect_leaves_an_ambiguous_invocation(
    uow_factory: UnitOfWorkFactory, harness: Harness, clock: FixedClock
) -> None:
    """The irreducible window, end to end: the click landed and the claim was
    then stolen. Friday must not own that outcome as a success, and must never
    replay it — a second click is not free."""
    run_id = harness.run_id
    _seed_claimed_run(uow_factory, run_id)
    executor = ExecuteToolAction(uow_factory, clock, harness.gateway)
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    call = ToolCall(
        tool="computer.click",
        tool_input={"snapshot_id": snapshot_id, "window_id": "win-mail", "element": 14},
    )
    outcome = executor.execute(
        run_id=run_id,
        step_id=None,
        call=call,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )
    approval = RequestToolApproval(uow_factory, clock).execute(
        RequestApprovalCommand(
            run_id=run_id,
            category=outcome.risk.category,
            summary=outcome.risk.summary,
            reason="",
            requested_action=call.tool,
            requested_input=call.tool_input,
            authorization_fingerprint=outcome.fingerprint,
        ),
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )
    ApproveRequest(uow_factory, clock).execute(
        ApproveRequestCommand(approval_id=approval.approval_id, resolver="patrick")
    )
    generation = _reclaim(uow_factory, run_id)

    def steal_the_claim(name: str) -> None:
        if name == "click":
            with uow_factory() as other:
                other.work_queue.remove(run_id)
                other.commit()

    harness.driver.on_call = steal_the_claim

    with pytest.raises(ClaimLost):
        executor.execute(
            run_id=run_id,
            step_id=None,
            call=call,
            worker_id=WORKER,
            claim_token=TOKEN,
            claim_generation=generation,
        )
    harness.driver.on_call = None

    # the side effect really did happen
    assert len(harness.driver.mutating_calls) == 1
    with uow_factory() as fresh:
        invocation = fresh.tool_invocations.list_for_run(run_id)[0]
        assert invocation.status is ToolInvocationStatus.RUNNING
        assert invocation.output_set is False
        assert fresh.approvals.list_for_run(run_id)[0].is_consumed is True
        types = [event.type for event in fresh.events.list_for_run(run_id)]
        assert RunEventType.TOOL_INVOCATION_SUCCEEDED not in types

    # a fresh worker takes over and must refuse to guess
    with uow_factory() as uow:
        uow.work_queue.enqueue(run_id, available_at=T0, enqueued_at=T0)
        assert uow.work_queue.try_claim(run_id, "w2", "tok2", T0, T0 + LEASE)
        uow.commit()

    with pytest.raises(ToolExecutionAmbiguous):
        executor.execute(
            run_id=run_id,
            step_id=None,
            call=call,
            worker_id="w2",
            claim_token="tok2",
            claim_generation=_generation(uow_factory, run_id),
        )

    assert len(harness.driver.mutating_calls) == 1


def test_a_capture_persists_its_screenshot_artifact_durably(
    uow_factory: UnitOfWorkFactory, harness: Harness, clock: FixedClock
) -> None:
    """Capture needs no approval, and its artifact lands through the same Txn B
    path as any other tool's — no parallel artifact system."""
    run_id = harness.run_id
    _seed_claimed_run(uow_factory, run_id)
    executor = ExecuteToolAction(uow_factory, clock, harness.gateway)

    outcome = executor.execute(
        run_id=run_id,
        step_id=None,
        call=ToolCall(tool="computer.capture", tool_input={}),
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )

    assert outcome.kind == "executed"
    with uow_factory() as fresh:
        artifacts = fresh.artifacts.list_for_run(run_id)
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.kind.value == "image"
        assert artifact.media_type == "image/png"
        assert not artifact.location.startswith("/")
        assert artifact.checksum is not None
        assert (harness.workspace / artifact.location).is_file()

        invocation = fresh.tool_invocations.list_for_run(run_id)[0]
        assert invocation.approval_request_id is None  # read-only: never approved
        # no image bytes anywhere in the durable record
        assert "PNG" not in str(invocation.output)

        types = [event.type for event in fresh.events.list_for_run(run_id)]
        assert RunEventType.ARTIFACT_CREATED in types
