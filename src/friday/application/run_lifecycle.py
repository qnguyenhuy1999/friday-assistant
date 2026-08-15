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
from friday.application.delegation_reconciliation import reconcile_child_terminal_in_uow
from friday.application.errors import (
    DelegatedManualRetryForbidden,
    EntityConflict,
    RunNotFound,
    TaskNotFound,
    WorkflowCancelNotSupportedWhileActive,
    WorkflowNodeManualRetryForbidden,
)
from friday.application.lifecycle_events import LifecycleEvents, run_result
from friday.application.ports import UnitOfWork
from friday.application.results import RunResult
from friday.application.retry_inheritance import inherit_frozen_resolutions_in_uow
from friday.application.skill_usage import materialize_skill_usage_in_uow
from friday.domain.event import RunEventType
from friday.domain.failure import Failure
from friday.domain.identifiers import (
    RunId,
    RunStepId,
    TaskId,
)
from friday.domain.json_value import JsonValue
from friday.domain.run import TERMINAL_RUN_STATUSES, Run, RunStatus
from friday.domain.step import TERMINAL_RUN_STEP_STATUSES
from friday.domain.task import TaskStatus
from friday.domain.tool import TERMINAL_TOOL_INVOCATION_STATUSES
from friday.domain.workflow_execution import WorkflowExecutionStatus


def _fail_run_event_specs(
    uow: UnitOfWork, run: Run, now: datetime, failure: Failure
) -> list[tuple[RunEventType, JsonValue, RunStepId | None]]:
    """Fail a run, cascade cancellation, and build its event specifications."""
    run.fail(now, failure)
    uow.runs.save(run)
    specs: list[tuple[RunEventType, JsonValue, RunStepId | None]] = []
    for step in uow.steps.list_for_run(run.id):
        if step.status not in TERMINAL_RUN_STEP_STATUSES:
            step.cancel(now)
            uow.steps.save(step)
            specs.append((RunEventType.STEP_CANCELLED, {"step_id": str(step.id)}, step.id))
            specs.extend(
                LifecycleEvents.cancel_tools(uow, uow.tool_invocations.list_for_step(step.id), now)
            )
    specs.extend(LifecycleEvents.cancel_tools(uow, uow.tool_invocations.list_for_run(run.id), now))
    specs.append(
        (RunEventType.RUN_FAILED, {"run_id": str(run.id), "failure_code": failure.code}, None)
    )
    return specs


def _succeed_run_event_specs(
    uow: UnitOfWork, run: Run, now: datetime
) -> list[tuple[RunEventType, JsonValue, RunStepId | None]]:
    """Succeed a run and build its event specification."""
    run.succeed(now)
    uow.runs.save(run)
    return [(RunEventType.RUN_SUCCEEDED, {"run_id": str(run.id)}, None)]


class _RunCancellation(LifecycleEvents):
    def _cancel_pending_approvals(
        self, uow: UnitOfWork, run: Run, now: datetime
    ) -> list[tuple[RunEventType, JsonValue, RunStepId | None]]:
        specs: list[tuple[RunEventType, JsonValue, RunStepId | None]] = []
        for approval in uow.approvals.list_pending_for_run(run.id):
            approval.cancel(now, resolution_note="run cancelled")
            uow.approvals.save(approval)
            specs.append(
                (
                    RunEventType.APPROVAL_RESOLVED,
                    {"approval_request_id": str(approval.id), "status": approval.status.value},
                    approval.step_id,
                )
            )
        return specs

    def _cancel_run(self, uow: UnitOfWork, run: Run, now: datetime) -> None:
        specs: list[tuple[RunEventType, JsonValue, RunStepId | None]] = [
            (RunEventType.RUN_CANCELLED, {"run_id": str(run.id)}, None)
        ]
        run.cancel(now)
        uow.runs.save(run)
        specs.extend(self._cancel_pending_approvals(uow, run, now))
        uow.work_queue.remove(run.id)
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
        reconcile_child_terminal_in_uow(uow, run, now)
        materialize_skill_usage_in_uow(uow, run.id, now)


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

    def page(
        self, task_id: TaskId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[RunResult]:
        with self._uow_factory() as uow:
            if uow.tasks.get(task_id) is None:
                raise TaskNotFound(task_id)
            return [
                run_result(run)
                for run in uow.runs.list_for_task_page(task_id, limit, after_created_at, after_id)
            ]


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
            if uow.delegation_requests.has_dispatched_for_run(run.id):
                raise EntityConflict("run has an active delegation")
            now = self._clock.now()
            specs = _succeed_run_event_specs(uow, run, now)
            uow.work_queue.remove(run.id)
            self.append_run_events(uow, run, now, specs)
            reconcile_child_terminal_in_uow(uow, run, now)
            materialize_skill_usage_in_uow(uow, run.id, now)
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
            specs = _fail_run_event_specs(uow, run, now, command.failure)
            self.append_run_events(uow, run, now, specs)
            uow.work_queue.remove(run.id)
            reconcile_child_terminal_in_uow(uow, run, now)
            materialize_skill_usage_in_uow(uow, run.id, now)
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
                latest = uow.runs.get_latest_for_execution(run.execution_id)
                if latest is not None and latest.status not in TERMINAL_RUN_STATUSES:
                    self._cancel_run(uow, latest, self._clock.now())
                    uow.commit()
                    return run_result(latest)
                raise EntityConflict("run is terminal")
            active_workflows = [
                execution
                for execution in uow.workflow_executions.list_by_root_run_id(run.id)
                if execution.status is WorkflowExecutionStatus.RUNNING
            ]
            if active_workflows and run.status is RunStatus.WAITING_FOR_WORKFLOW:
                raise WorkflowCancelNotSupportedWhileActive()
            self._cancel_run(uow, run, self._clock.now())
            uow.commit()
            return run_result(run)


class ListRunsByExecution(LifecycleEvents):
    def execute(self, run_id: RunId) -> list[RunResult]:
        with self._uow_factory() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            return [run_result(r) for r in uow.runs.list_for_execution(run.execution_id)]


class GetLatestRunForExecution(LifecycleEvents):
    def execute(self, run_id: RunId) -> RunResult:
        with self._uow_factory() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            latest = uow.runs.get_latest_for_execution(run.execution_id)
            return run_result(latest if latest is not None else run)


class RetryFailedRun(LifecycleEvents):
    def execute(self, command: RetryFailedRunCommand) -> RunResult:
        with self._uow_factory() as uow:
            source = uow.runs.get(command.run_id)
            if source is None:
                raise RunNotFound(command.run_id)
            if source.status is not RunStatus.FAILED:
                raise EntityConflict("only failed runs may be retried")
            if (
                uow.workflow_node_executions.get_by_child_execution_id(source.execution_id)
                is not None
            ):
                raise WorkflowNodeManualRetryForbidden()
            if uow.delegation_requests.get_for_child_execution(source.execution_id) is not None:
                raise DelegatedManualRetryForbidden()
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
            retry = Run.new(
                id=RunId.new(), task_id=task.id, created_at=now, execution_id=source.execution_id
            )
            uow.runs.add(retry)
            inherit_frozen_resolutions_in_uow(uow, source, retry)
            uow.work_queue.enqueue(retry.id, available_at=now, enqueued_at=now)
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
