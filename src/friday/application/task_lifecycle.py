"""Task lifecycle use cases and Task-to-Run cancellation coordination."""

from __future__ import annotations

from datetime import datetime

from friday.application.commands import CancelTaskCommand, CompleteTaskCommand, FailTaskCommand
from friday.application.errors import (
    EntityConflict,
    TaskNotFound,
    WorkflowCancelNotSupportedWhileActive,
)
from friday.application.lifecycle_events import LifecycleEvents, task_result
from friday.application.ports import UnitOfWork
from friday.application.results import TaskResult
from friday.application.skill_usage import materialize_skill_usage_in_uow
from friday.domain.event import RunEventType
from friday.domain.identifiers import RunStepId, TaskId
from friday.domain.json_value import JsonValue
from friday.domain.run import TERMINAL_RUN_STATUSES, Run
from friday.domain.step import TERMINAL_RUN_STEP_STATUSES
from friday.domain.task import TERMINAL_TASK_STATUSES, TaskStatus
from friday.domain.task_event import TaskEventType
from friday.domain.tool import TERMINAL_TOOL_INVOCATION_STATUSES
from friday.domain.workflow_execution import WorkflowExecutionStatus


class GetTask(LifecycleEvents):
    def execute(self, task_id: TaskId) -> TaskResult:
        with self._uow_factory() as uow:
            task = uow.tasks.get(task_id)
            if task is None:
                raise TaskNotFound(task_id)
            return task_result(task)


class ListTasks(LifecycleEvents):
    def execute(self, limit: int = 100) -> list[TaskResult]:
        with self._uow_factory() as uow:
            return [task_result(task) for task in uow.tasks.list(limit)]

    def page(
        self, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[TaskResult]:
        with self._uow_factory() as uow:
            return [
                task_result(task) for task in uow.tasks.list_page(limit, after_created_at, after_id)
            ]


class _TaskCancellation(LifecycleEvents):
    def _cancel_run(self, uow: UnitOfWork, run: Run, now: datetime) -> None:
        specs: list[tuple[RunEventType, JsonValue, RunStepId | None]] = [
            (RunEventType.RUN_CANCELLED, {"run_id": str(run.id)}, None)
        ]
        run.cancel(now)
        uow.runs.save(run)
        for step in uow.steps.list_for_run(run.id):
            if step.status not in TERMINAL_RUN_STEP_STATUSES:
                step.cancel(now)
                uow.steps.save(step)
                specs.append((RunEventType.STEP_CANCELLED, {"step_id": str(step.id)}, step.id))
                specs.extend(
                    self.cancel_tools(uow, uow.tool_invocations.list_for_step(step.id), now)
                )
        specs.extend(self.cancel_tools(uow, uow.tool_invocations.list_for_run(run.id), now))
        self.append_run_events(uow, run, now, specs)
        # Task cancellation is a terminal Run path too.  Materialize the
        # frozen ledger in the same transaction as the owning Task/Run change.
        materialize_skill_usage_in_uow(uow, run.id, now)


class CancelTask(_TaskCancellation):
    def execute(self, command: CancelTaskCommand) -> TaskResult:
        with self._uow_factory() as uow:
            task = uow.tasks.get(command.task_id)
            if task is None:
                raise TaskNotFound(command.task_id)
            now = self._clock.now()
            if task.status is TaskStatus.CANCELLED:
                uow.commit()
                return task_result(task)
            if task.status in TERMINAL_TASK_STATUSES:
                raise EntityConflict("task is terminal")
            # A Task whose any non-terminal Run owns a RUNNING Workflow
            # execution cannot be cancelled: Workflow cancellation is a
            # Workflow-boundary concern and child Runs must not keep running
            # while their root is cancelled (an orphaned execution is
            # forbidden by the Step-4 contract).  Fail closed before any
            # Task/Run mutation.
            for run in uow.runs.list_for_task(task.id):
                if run.status not in TERMINAL_RUN_STATUSES and any(
                    execution.status is WorkflowExecutionStatus.RUNNING
                    for execution in uow.workflow_executions.list_by_root_run_id(run.id)
                ):
                    raise WorkflowCancelNotSupportedWhileActive()
            task.cancel(now)
            uow.tasks.save(task)
            uow.schedules.complete_for_task(task.id, now, cancelled=True)
            self.append_task_event(
                uow, task, now, TaskEventType.TASK_CANCELLED, {"task_id": str(task.id)}
            )
            for run in uow.runs.list_for_task(task.id):
                if run.status not in TERMINAL_RUN_STATUSES:
                    self._cancel_run(uow, run, now)
            uow.commit()
            return task_result(task)


class CompleteTask(LifecycleEvents):
    def execute(self, command: CompleteTaskCommand) -> TaskResult:
        with self._uow_factory() as uow:
            task = uow.tasks.get(command.task_id)
            if task is None:
                raise TaskNotFound(command.task_id)
            if task.status is TaskStatus.COMPLETED:
                uow.commit()
                return task_result(task)
            if task.status in TERMINAL_TASK_STATUSES:
                raise EntityConflict("task is terminal")
            if task.status is not TaskStatus.ACTIVE:
                raise EntityConflict("task cannot complete")
            runs = uow.runs.list_for_task(task.id)
            if any(run.status not in TERMINAL_RUN_STATUSES for run in runs):
                raise EntityConflict("task has non-terminal runs")
            if any(
                step.status not in TERMINAL_RUN_STEP_STATUSES
                for run in runs
                for step in uow.steps.list_for_run(run.id)
            ):
                raise EntityConflict("task has non-terminal steps")
            if any(
                tool.status not in TERMINAL_TOOL_INVOCATION_STATUSES
                for run in runs
                for tool in uow.tool_invocations.list_for_run(run.id)
            ):
                raise EntityConflict("task has non-terminal tool invocations")
            now = self._clock.now()
            task.complete(now)
            uow.tasks.save(task)
            uow.schedules.complete_for_task(task.id, now, cancelled=False)
            self.append_task_event(
                uow, task, now, TaskEventType.TASK_COMPLETED, {"task_id": str(task.id)}
            )
            uow.commit()
            return task_result(task)


class FailTask(LifecycleEvents):
    def execute(self, command: FailTaskCommand) -> TaskResult:
        with self._uow_factory() as uow:
            task = uow.tasks.get(command.task_id)
            if task is None:
                raise TaskNotFound(command.task_id)
            if task.status is TaskStatus.FAILED:
                if task.failure != command.failure:
                    raise EntityConflict("task failure is immutable")
                uow.commit()
                return task_result(task)
            if task.status in TERMINAL_TASK_STATUSES:
                raise EntityConflict("task is terminal")
            now = self._clock.now()
            task.fail(now, command.failure)
            uow.tasks.save(task)
            uow.schedules.complete_for_task(task.id, now, cancelled=False)
            self.append_task_event(
                uow,
                task,
                now,
                TaskEventType.TASK_FAILED,
                {"task_id": str(task.id), "failure_code": command.failure.code},
            )
            uow.commit()
            return task_result(task)
