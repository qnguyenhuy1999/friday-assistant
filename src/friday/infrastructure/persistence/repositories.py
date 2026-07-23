from __future__ import annotations

from sqlalchemy.orm import Session

from friday.domain import Task, TaskId
from friday.infrastructure.persistence.mappers import task_from_row, task_to_row
from friday.infrastructure.persistence.models import TaskRow


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, task: Task) -> None:
        self._session.add(task_to_row(task))

    def get(self, task_id: TaskId) -> Task | None:
        row = self._session.get(TaskRow, str(task_id))
        return task_from_row(row) if row is not None else None

    def save(self, task: Task) -> None:
        self._session.merge(task_to_row(task))
