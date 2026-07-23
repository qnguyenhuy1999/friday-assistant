"""CreateTask use case: allocate a Task ID, timestamp via the injected Clock,
construct a valid Task, persist it inside one Unit of Work, commit exactly
once. Any failure before commit leaves nothing durable."""

from __future__ import annotations

from friday.application.commands import CreateTaskCommand
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import CreateTaskResult
from friday.domain.identifiers import TaskId
from friday.domain.task import Task


class CreateTask:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, command: CreateTaskCommand) -> CreateTaskResult:
        task = Task.new(
            id=TaskId.new(),
            title=command.title,
            description=command.description,
            created_at=self._clock.now(),
        )
        with self._uow_factory() as uow:
            uow.tasks.add(task)
            uow.commit()
        return CreateTaskResult(task_id=task.id)
