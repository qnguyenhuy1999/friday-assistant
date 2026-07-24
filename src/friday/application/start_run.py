"""StartRun use case: create and enqueue the next Run for an existing Task
inside one Unit of Work / one transaction.

"Start" here means enqueue: the new Run is left in its canonical QUEUED
state. Durable claiming and the QUEUED -> RUNNING transition belong to the
worker (Phase 10) and are intentionally absent.

Application orchestration — not domain entities — allocates the Run ID, the
RunEvent ID, and the event sequence number. `reserve_sequences()` and
`append()` run inside the same transaction as the Task and Run writes, so no
event can commit independently of them.
"""

from __future__ import annotations

from datetime import datetime

from friday.application.commands import StartRunCommand
from friday.application.errors import EntityConflict, TaskNotFound
from friday.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from friday.application.results import StartRunResult
from friday.domain.event import RunEvent, RunEventType
from friday.domain.identifiers import RunEventId, RunId
from friday.domain.run import Run
from friday.domain.task import Task, TaskStatus


class StartRun:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, command: StartRunCommand) -> StartRunResult:
        with self._uow_factory() as uow:
            task = uow.tasks.get(command.task_id)
            if task is None:
                raise TaskNotFound(command.task_id)

            now = self._clock.now()
            self._ensure_task_can_own_a_run(task, uow, at=now)

            run = Run.new(id=RunId.new(), task_id=task.id, created_at=now)
            uow.runs.add(run)
            uow.work_queue.enqueue(run.id, available_at=now, enqueued_at=now)

            event = RunEvent(
                id=RunEventId.new(),
                run_id=run.id,
                type=RunEventType.RUN_CREATED,
                sequence=uow.events.reserve_sequences(run.id, 1),
                occurred_at=now,
                payload={"task_id": str(task.id)},
            )
            uow.events.append(event)

            uow.commit()
        return StartRunResult(task_id=task.id, run_id=run.id)

    @staticmethod
    def _ensure_task_can_own_a_run(task: Task, uow: UnitOfWork, *, at: datetime) -> None:
        """A PENDING Task is activated through its domain transition; an
        ACTIVE Task may own further Runs; any other state is rejected."""
        if task.status is TaskStatus.PENDING:
            task.start(at)
            uow.tasks.save(task)
        elif task.status is not TaskStatus.ACTIVE:
            raise EntityConflict(f"Task {task.id} is '{task.status.value}' and cannot start a run")
