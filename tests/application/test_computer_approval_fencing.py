"""Approval binds semantic intent; execution resolves it freshly."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.ports import UnitOfWorkFactory
from friday.application.tool_authorization import compute_authorization_fingerprint
from friday.application.tool_gateway import ToolCall
from friday.domain.approval import ApprovalCategory, ApprovalRequest
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId
from friday.domain.run import Run
from friday.domain.task import Task
from friday.infrastructure.computer.models import ElementTarget
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from tests.infrastructure.computer_fakes import MAIL_PID, MAIL_WINDOW_ID, FakeComputerDriver
from tests.infrastructure.computer_harness import T0, build_harness


class FixedClock:
    def now(self) -> datetime:
        return T0


@pytest.fixture
def uow_factory(tmp_path: Path) -> Iterator[UnitOfWorkFactory]:
    database = tmp_path / "computer.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database}")
    yield create_unit_of_work_factory(create_session_factory(engine))
    engine.dispose()


def _claimed_run(uows: UnitOfWorkFactory, run_id: RunId) -> int:
    task = Task.new(id=TaskId.new(), title="desktop", description="", created_at=T0)
    task.start(T0)
    run = Run.new(id=run_id, task_id=task.id, created_at=T0)
    run.start(T0)
    with uows() as uow:
        uow.tasks.add(task)
        uow.commit()
    with uows() as uow:
        uow.runs.add(run)
        uow.commit()
    with uows() as uow:
        uow.work_queue.enqueue(run.id, available_at=T0, enqueued_at=T0)
        assert uow.work_queue.try_claim(run.id, "worker-b", "token", T0, T0 + timedelta(minutes=1))
        uow.commit()
    with uows() as uow:
        item = uow.work_queue.get(run_id)
        assert item is not None
        return item.claim_generation


def _approve(uows: UnitOfWorkFactory, run_id: RunId, call: ToolCall) -> None:
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run_id,
        category=ApprovalCategory.COMPUTER_USE,
        summary="click Send",
        reason="",
        requested_action=call.tool,
        requested_input=call.tool_input,
        requested_at=T0,
        authorization_fingerprint=compute_authorization_fingerprint(
            run_id=run_id, step_id=None, call=call
        ),
    )
    approval.approve(T0, resolver="human")
    with uows() as uow:
        uow.approvals.add(approval)
        uow.commit()


def test_approved_descriptor_is_revalidated_by_a_new_worker_instance(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    """S0 on worker A never becomes a durable element-index authorization."""
    worker_a = build_harness(tmp_path)
    worker_a.capture()  # S0 observes Send at index 15.
    call = ToolCall(
        tool="computer.click",
        tool_input={
            "pid": MAIL_PID,
            "window_id": MAIL_WINDOW_ID,
            "element": {"role": "button", "label": "Send"},
        },
    )
    generation = _claimed_run(uow_factory, worker_a.run_id)
    _approve(uow_factory, worker_a.run_id, call)

    # A handoff creates a wholly new gateway/driver; the live index has changed.
    worker_b_driver = FakeComputerDriver(index_offset=100)
    worker_b = build_harness(tmp_path, driver=worker_b_driver)
    result = ExecuteToolAction(uow_factory, FixedClock(), worker_b.gateway).execute(
        run_id=worker_a.run_id,
        step_id=None,
        call=call,
        worker_id="worker-b",
        claim_token="token",
        claim_generation=generation,
    )

    assert result.kind == "executed"
    target = worker_b_driver.only_call("click").argument("target")
    assert isinstance(target, ElementTarget)
    assert target.element_index == 115
    assert len(worker_b_driver.mutating_calls) == 1
