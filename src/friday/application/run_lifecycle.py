"""Run lifecycle use cases, including retry and descendant cancellation."""

from __future__ import annotations

from datetime import datetime

from friday.application.commands import (
    CancelRunCommand,
    CompleteRunCommand,
    FailRunCommand,
    RetryFailedRunCommand,
    StartQueuedRunCommand,
)
from friday.application.errors import EntityConflict, RunNotFound, TaskNotFound
from friday.application.lifecycle_events import LifecycleEvents, run_result
from friday.application.ports import UnitOfWork
from friday.application.results import RunResult
from friday.domain.event import RunEventType
from friday.domain.identifiers import RunId, RunStepId, TaskId
from friday.domain.json_value import JsonValue
from friday.domain.run import TERMINAL_RUN_STATUSES, Run, RunStatus
from friday.domain.step import TERMINAL_RUN_STEP_STATUSES
from friday.domain.task import TaskStatus
from friday.domain.tool import TERMINAL_TOOL_INVOCATION_STATUSES


class _RunCancellation(LifecycleEvents):
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


class GetRun(LifecycleEvents):
    def execute(self, run_id: RunId) -> RunResult:
        with self._uow_factory() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            return run_result(run)


class ListRunsForTask(LifecycleEvents):
    def execute(self, task_id: TaskId) -> list[RunResult]:
        with self._uow_factory() as uow:
            if uow.tasks.get(task_id) is None:
                raise TaskNotFound(task_id)
            return [run_result(run) for run in uow.runs.list_for_task(task_id)]


class StartQueuedRun(LifecycleEvents):
    def execute(self, command: StartQueuedRunCommand) -> RunResult:
        with self._uow_factory() as uow:
            run = uow.runs.get(command.run_id)
            if run is None:
                raise RunNotFound(command.run_id)
            if run.status is RunStatus.RUNNING:
                uow.commit()
                return run_result(run)
            if run.status is not RunStatus.QUEUED:
                raise EntityConflict("run cannot start")
            task = uow.tasks.get(run.task_id)
            if task is None:
                raise TaskNotFound(run.task_id)
            if task.status is not TaskStatus.ACTIVE:
                raise EntityConflict("owning task is not active")
            now = self._clock.now()
            run.start(now)
            uow.runs.save(run)
            self.append_run_events(
                uow, run, now, [(RunEventType.RUN_STARTED, {"run_id": str(run.id)}, None)]
            )
            uow.commit()
            return run_result(run)


class CompleteRun(LifecycleEvents):
    def execute(self, command: CompleteRunCommand) -> RunResult:
        with self._uow_factory() as uow:
            run = uow.runs.get(command.run_id)
            if run is None:
                raise RunNotFound(command.run_id)
            if run.status is RunStatus.SUCCEEDED:
                uow.commit()
                return run_result(run)
            if run.status in TERMINAL_RUN_STATUSES:
                raise EntityConflict("run is terminal")
            if run.status is not RunStatus.RUNNING:
                raise EntityConflict("run cannot complete")
            if any(
                step.status not in TERMINAL_RUN_STEP_STATUSES
                for step in uow.steps.list_for_run(run.id)
            ):
                raise EntityConflict("run has non-terminal steps")
            if any(
                tool.status not in TERMINAL_TOOL_INVOCATION_STATUSES
                for tool in uow.tool_invocations.list_for_run(run.id)
            ):
                raise EntityConflict("run has non-terminal tool invocations")
            now = self._clock.now()
            run.succeed(now)
            uow.runs.save(run)
            self.append_run_events(
                uow, run, now, [(RunEventType.RUN_SUCCEEDED, {"run_id": str(run.id)}, None)]
            )
            uow.commit()
            return run_result(run)


class FailRun(LifecycleEvents):
    def execute(self, command: FailRunCommand) -> RunResult:
        with self._uow_factory() as uow:
            run = uow.runs.get(command.run_id)
            if run is None:
                raise RunNotFound(command.run_id)
            if run.status is RunStatus.FAILED:
                if run.failure != command.failure:
                    raise EntityConflict("run failure is immutable")
                uow.commit()
                return run_result(run)
            if run.status in TERMINAL_RUN_STATUSES:
                raise EntityConflict("run is terminal")
            if run.status is not RunStatus.RUNNING:
                raise EntityConflict("run cannot fail")
            now = self._clock.now()
            run.fail(now, command.failure)
            uow.runs.save(run)
            specs: list[tuple[RunEventType, JsonValue, RunStepId | None]] = []
            for step in uow.steps.list_for_run(run.id):
                if step.status not in TERMINAL_RUN_STEP_STATUSES:
                    step.cancel(now)
                    uow.steps.save(step)
                    specs.append((RunEventType.STEP_CANCELLED, {"step_id": str(step.id)}, step.id))
                    specs.extend(
                        self.cancel_tools(uow, uow.tool_invocations.list_for_step(step.id), now)
                    )
            specs.extend(self.cancel_tools(uow, uow.tool_invocations.list_for_run(run.id), now))
            specs.append(
                (
                    RunEventType.RUN_FAILED,
                    {"run_id": str(run.id), "failure_code": command.failure.code},
                    None,
                )
            )
            self.append_run_events(uow, run, now, specs)
            uow.commit()
            return run_result(run)


class CancelRun(_RunCancellation):
    def execute(self, command: CancelRunCommand) -> RunResult:
        with self._uow_factory() as uow:
            run = uow.runs.get(command.run_id)
            if run is None:
                raise RunNotFound(command.run_id)
            if run.status is RunStatus.CANCELLED:
                uow.commit()
                return run_result(run)
            if run.status in TERMINAL_RUN_STATUSES:
                raise EntityConflict("run is terminal")
            self._cancel_run(uow, run, self._clock.now())
            uow.commit()
            return run_result(run)


class RetryFailedRun(LifecycleEvents):
    def execute(self, command: RetryFailedRunCommand) -> RunResult:
        with self._uow_factory() as uow:
            source = uow.runs.get(command.run_id)
            if source is None:
                raise RunNotFound(command.run_id)
            if source.status is not RunStatus.FAILED:
                raise EntityConflict("only failed runs may be retried")
            task = uow.tasks.get(source.task_id)
            if task is None:
                raise TaskNotFound(source.task_id)
            runs = uow.runs.list_for_task(task.id)
            if runs[-1].id != source.id:
                raise EntityConflict("only latest attempt may be retried")
            if any(run.status not in TERMINAL_RUN_STATUSES for run in runs):
                raise EntityConflict("task already has non-terminal run")
            if task.status is not TaskStatus.ACTIVE:
                raise EntityConflict("task is not retryable")
            now = self._clock.now()
            retry = Run.new(id=RunId.new(), task_id=task.id, created_at=now)
            uow.runs.add(retry)
            self.append_run_events(
                uow,
                retry,
                now,
                [
                    (
                        RunEventType.RUN_CREATED,
                        {"task_id": str(task.id), "retry_of": str(source.id)},
                        None,
                    )
                ],
            )
            uow.commit()
            return run_result(retry)
