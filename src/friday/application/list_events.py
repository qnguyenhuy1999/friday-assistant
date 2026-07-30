"""Read-only use cases for the append-only Task/Run event streams."""

from __future__ import annotations

from friday.application.errors import RunNotFound, TaskNotFound
from friday.application.lifecycle_events import LifecycleEvents
from friday.domain.event import RunEvent, RunEventType
from friday.domain.identifiers import RunId, TaskId
from friday.domain.task_event import TaskEvent


def canonical_final_agent_summary(event: RunEvent | None) -> str | None:
    """The sole canonical final-answer projection shared by result readers.

    This intentionally does not reconstruct the event stream or consult
    details.  Callers which externalize this value apply their own safety gate.
    """
    if event is None or event.type is not RunEventType.AGENT_FINISHED:
        return None
    payload = event.payload
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary")
    return summary if isinstance(summary, str) else None


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


class GetRunResult(LifecycleEvents):
    """Read the durable final-result projection without walking the event log."""

    def execute(self, run_id: RunId) -> RunEvent | None:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return uow.events.latest_of_type_for_run(run_id, RunEventType.AGENT_FINISHED)


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
