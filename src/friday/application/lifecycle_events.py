"""Shared lifecycle event batching and result snapshots.

This module owns only mechanics used by multiple aggregate use cases:
application-allocated IDs, transaction-local sequences, and stable event
batching.  Aggregate transition policy remains in its ownership module.
"""

from __future__ import annotations

from datetime import datetime

from friday.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from friday.application.results import RunResult, RunStepResult, TaskResult
from friday.domain.event import RunEvent, RunEventType
from friday.domain.identifiers import RunEventId, RunStepId, TaskEventId
from friday.domain.json_value import JsonValue
from friday.domain.run import Run
from friday.domain.step import RunStep
from friday.domain.task import Task
from friday.domain.task_event import TaskEvent, TaskEventType
from friday.domain.tool import TERMINAL_TOOL_INVOCATION_STATUSES, ToolInvocation


def task_result(task: Task) -> TaskResult:
    return TaskResult(
        task.id, task.title, task.description, task.status, task.created_at, task.failure
    )


def run_result(run: Run) -> RunResult:
    return RunResult(run.id, run.task_id, run.status, run.created_at, run.failure)


def step_result(step: RunStep) -> RunStepResult:
    return RunStepResult(step.id, step.run_id, step.name, step.position, step.status, step.failure)


class LifecycleEvents:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    @staticmethod
    def append_run_events(
        uow: UnitOfWork,
        run: Run,
        now: datetime,
        specs: list[tuple[RunEventType, JsonValue, RunStepId | None]],
    ) -> None:
        start = uow.events.next_sequence(run.id)
        for offset, (type_, payload, step_id) in enumerate(specs):
            uow.events.append(
                RunEvent(RunEventId.new(), run.id, type_, start + offset, now, payload, step_id)
            )

    @staticmethod
    def append_task_event(
        uow: UnitOfWork, task: Task, now: datetime, type_: TaskEventType, payload: JsonValue
    ) -> None:
        uow.task_events.append(
            TaskEvent(
                TaskEventId.new(),
                task.id,
                type_,
                uow.task_events.next_sequence(task.id),
                now,
                payload,
            )
        )

    @staticmethod
    def cancel_tools(
        uow: UnitOfWork, tools: list[ToolInvocation], now: datetime
    ) -> list[tuple[RunEventType, JsonValue, RunStepId | None]]:
        specs: list[tuple[RunEventType, JsonValue, RunStepId | None]] = []
        for tool in tools:
            if tool.status not in TERMINAL_TOOL_INVOCATION_STATUSES:
                tool.cancel(now)
                uow.tool_invocations.save(tool)
                specs.append(
                    (
                        RunEventType.TOOL_INVOCATION_CANCELLED,
                        {"tool_invocation_id": str(tool.id)},
                        tool.step_id,
                    )
                )
        return specs
