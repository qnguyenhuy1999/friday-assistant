"""Read-only use cases for the append-only Task/Run event streams."""

from __future__ import annotations

from friday.application.errors import RunNotFound, TaskNotFound
from friday.application.lifecycle_events import LifecycleEvents
from friday.domain.event import RunEvent
from friday.domain.identifiers import RunId, TaskId
from friday.domain.task_event import TaskEvent


class ListRunEvents(LifecycleEvents):
    def execute(self, run_id: RunId) -> list[RunEvent]:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return uow.events.list_for_run(run_id)

    def after(self, run_id: RunId, after_sequence: int, limit: int) -> list[RunEvent]:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return uow.events.list_after_sequence(run_id, after_sequence, limit)


class ListTaskEvents(LifecycleEvents):
    def execute(self, task_id: TaskId) -> list[TaskEvent]:
        with self._uow_factory() as uow:
            if uow.tasks.get(task_id) is None:
                raise TaskNotFound(task_id)
            return uow.task_events.list_for_task(task_id)

    def after(self, task_id: TaskId, after_sequence: int, limit: int) -> list[TaskEvent]:
        with self._uow_factory() as uow:
            if uow.tasks.get(task_id) is None:
                raise TaskNotFound(task_id)
            return uow.task_events.list_after_sequence(task_id, after_sequence, limit)
