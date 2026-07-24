"""Ordered RunStep lifecycle use cases."""

from __future__ import annotations

from friday.application.commands import (
    CancelStepCommand,
    CompleteStepCommand,
    CreateOrderedStepCommand,
    FailStepCommand,
    SkipPendingStepCommand,
    StartStepCommand,
)
from friday.application.errors import EntityConflict, RunNotFound, RunStepNotFound
from friday.application.lifecycle_events import LifecycleEvents, step_result
from friday.application.ports import UnitOfWork
from friday.application.results import RunStepResult
from friday.domain.event import RunEventType
from friday.domain.identifiers import RunId, RunStepId
from friday.domain.json_value import JsonValue
from friday.domain.run import Run, RunStatus
from friday.domain.step import TERMINAL_RUN_STEP_STATUSES, RunStep, RunStepStatus


class CreateOrderedStep(LifecycleEvents):
    def execute(self, command: CreateOrderedStepCommand) -> RunStepResult:
        with self._uow_factory() as uow:
            run = uow.runs.get(command.run_id)
            if run is None:
                raise RunNotFound(command.run_id)
            if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                raise EntityConflict("run cannot accept steps")
            steps = uow.steps.list_for_run(run.id)
            position = max((step.position for step in steps), default=-1) + 1
            now = self._clock.now()
            step = RunStep.new(
                id=RunStepId.new(),
                run_id=run.id,
                name=command.name,
                position=position,
                created_at=now,
            )
            uow.steps.add(step)
            self.append_run_events(
                uow,
                run,
                now,
                [
                    (
                        RunEventType.STEP_CREATED,
                        {"step_id": str(step.id), "position": position},
                        step.id,
                    )
                ],
            )
            uow.commit()
            return step_result(step)


class GetRunStep(LifecycleEvents):
    def execute(self, step_id: RunStepId) -> RunStepResult:
        with self._uow_factory() as uow:
            step = uow.steps.get(step_id)
            if step is None:
                raise RunStepNotFound(step_id)
            return step_result(step)


class ListRunStepsForRun(LifecycleEvents):
    def execute(self, run_id: RunId) -> list[RunStepResult]:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return [step_result(step) for step in uow.steps.list_for_run(run_id)]


class _StepLifecycle(LifecycleEvents):
    def _load(self, uow: UnitOfWork, step_id: RunStepId) -> tuple[RunStep, Run]:
        step = uow.steps.get(step_id)
        if step is None:
            raise RunStepNotFound(step_id)
        run = uow.runs.get(step.run_id)
        if run is None:
            raise RunNotFound(step.run_id)
        return step, run


class StartStep(_StepLifecycle):
    def execute(self, command: StartStepCommand) -> RunStepResult:
        with self._uow_factory() as uow:
            step, run = self._load(uow, command.step_id)
            if step.status is RunStepStatus.RUNNING:
                uow.commit()
                return step_result(step)
            if step.status is not RunStepStatus.PENDING or run.status is not RunStatus.RUNNING:
                raise EntityConflict("step cannot start")
            now = self._clock.now()
            step.start(now)
            uow.steps.save(step)
            self.append_run_events(
                uow, run, now, [(RunEventType.STEP_STARTED, {"step_id": str(step.id)}, step.id)]
            )
            uow.commit()
            return step_result(step)


class CompleteStep(_StepLifecycle):
    def execute(self, command: CompleteStepCommand) -> RunStepResult:
        with self._uow_factory() as uow:
            step, run = self._load(uow, command.step_id)
            if step.status is RunStepStatus.SUCCEEDED:
                uow.commit()
                return step_result(step)
            if step.status is not RunStepStatus.RUNNING:
                raise EntityConflict("step cannot complete")
            now = self._clock.now()
            step.succeed(now)
            uow.steps.save(step)
            self.append_run_events(
                uow, run, now, [(RunEventType.STEP_SUCCEEDED, {"step_id": str(step.id)}, step.id)]
            )
            uow.commit()
            return step_result(step)


class FailStep(_StepLifecycle):
    def execute(self, command: FailStepCommand) -> RunStepResult:
        with self._uow_factory() as uow:
            step, run = self._load(uow, command.step_id)
            if step.status is RunStepStatus.FAILED:
                if step.failure != command.failure:
                    raise EntityConflict("step failure is immutable")
                uow.commit()
                return step_result(step)
            if step.status is not RunStepStatus.RUNNING:
                raise EntityConflict("step cannot fail")
            now = self._clock.now()
            step.fail(now, command.failure)
            uow.steps.save(step)
            specs: list[tuple[RunEventType, JsonValue, RunStepId | None]] = self.cancel_tools(
                uow, uow.tool_invocations.list_for_step(step.id), now
            )
            specs.append(
                (
                    RunEventType.STEP_FAILED,
                    {"step_id": str(step.id), "failure_code": command.failure.code},
                    step.id,
                )
            )
            self.append_run_events(uow, run, now, specs)
            uow.commit()
            return step_result(step)


class SkipPendingStep(_StepLifecycle):
    def execute(self, command: SkipPendingStepCommand) -> RunStepResult:
        with self._uow_factory() as uow:
            step, run = self._load(uow, command.step_id)
            if step.status is RunStepStatus.SKIPPED:
                uow.commit()
                return step_result(step)
            if step.status is not RunStepStatus.PENDING:
                raise EntityConflict("step cannot skip")
            now = self._clock.now()
            step.skip(now)
            uow.steps.save(step)
            self.append_run_events(
                uow, run, now, [(RunEventType.STEP_SKIPPED, {"step_id": str(step.id)}, step.id)]
            )
            uow.commit()
            return step_result(step)


class CancelStep(_StepLifecycle):
    def execute(self, command: CancelStepCommand) -> RunStepResult:
        with self._uow_factory() as uow:
            step, run = self._load(uow, command.step_id)
            if step.status is RunStepStatus.CANCELLED:
                uow.commit()
                return step_result(step)
            if step.status in TERMINAL_RUN_STEP_STATUSES:
                raise EntityConflict("step is terminal")
            now = self._clock.now()
            step.cancel(now)
            uow.steps.save(step)
            specs: list[tuple[RunEventType, JsonValue, RunStepId | None]] = [
                (RunEventType.STEP_CANCELLED, {"step_id": str(step.id)}, step.id)
            ]
            specs.extend(self.cancel_tools(uow, uow.tool_invocations.list_for_step(step.id), now))
            self.append_run_events(uow, run, now, specs)
            uow.commit()
            return step_result(step)
