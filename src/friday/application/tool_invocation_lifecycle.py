"""ToolInvocation lifecycle use cases: durable execution metadata only.

No tool, subprocess, network request, or vendor runtime is ever executed
here — Phase 11 owns actual execution. These commands record what a future
runtime did (or intends to do) as validated, atomic state transitions.

Authorization rule: an invocation may reference an ApprovalRequest, and when
it does the approval must belong to the same Run (and the same RunStep scope
when both declare one) and must already be `approved`. An invocation without
an approval reference is unauthorised-by-omission — Phase 8 records it; a
later runtime phase decides whether that is allowed.
"""

from __future__ import annotations

from datetime import datetime

from friday.application.commands import (
    CancelToolInvocationCommand,
    MarkToolInvocationFailedCommand,
    MarkToolInvocationRunningCommand,
    MarkToolInvocationSucceededCommand,
    RequestToolInvocationCommand,
)
from friday.application.errors import (
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
    ToolInvocationNotFound,
)
from friday.application.lifecycle_events import LifecycleEvents
from friday.application.ports import UnitOfWork
from friday.application.results import ToolInvocationResult
from friday.domain.approval import ApprovalStatus
from friday.domain.event import RunEventType
from friday.domain.identifiers import RunId, RunStepId, ToolInvocationId
from friday.domain.run import Run, RunStatus
from friday.domain.step import RunStepStatus
from friday.domain.tool import (
    TERMINAL_TOOL_INVOCATION_STATUSES,
    ToolInvocation,
    ToolInvocationStatus,
)


def tool_invocation_result(invocation: ToolInvocation) -> ToolInvocationResult:
    return ToolInvocationResult(
        invocation.id,
        invocation.run_id,
        invocation.step_id,
        invocation.tool_name,
        invocation.status,
        invocation.requested_at,
        invocation.approval_request_id,
        invocation.output,
        invocation.output_set,
        invocation.failure,
    )


class GetToolInvocation(LifecycleEvents):
    def execute(self, invocation_id: ToolInvocationId) -> ToolInvocationResult:
        with self._uow_factory() as uow:
            invocation = uow.tool_invocations.get(invocation_id)
            if invocation is None:
                raise ToolInvocationNotFound(invocation_id)
            return tool_invocation_result(invocation)


class ListToolInvocationsForRun(LifecycleEvents):
    def execute(self, run_id: RunId) -> list[ToolInvocationResult]:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return [tool_invocation_result(i) for i in uow.tool_invocations.list_for_run(run_id)]

    def page(
        self, run_id: RunId, limit: int, after_requested_at: datetime | None, after_id: str | None
    ) -> list[ToolInvocationResult]:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return [
                tool_invocation_result(i)
                for i in uow.tool_invocations.list_for_run_page(
                    run_id, limit, after_requested_at, after_id
                )
            ]


class ListToolInvocationsForStep(LifecycleEvents):
    def execute(self, step_id: RunStepId) -> list[ToolInvocationResult]:
        with self._uow_factory() as uow:
            if uow.steps.get(step_id) is None:
                raise RunStepNotFound(step_id)
            return [tool_invocation_result(i) for i in uow.tool_invocations.list_for_step(step_id)]

    def page(
        self,
        step_id: RunStepId,
        limit: int,
        after_requested_at: datetime | None,
        after_id: str | None,
    ) -> list[ToolInvocationResult]:
        with self._uow_factory() as uow:
            if uow.steps.get(step_id) is None:
                raise RunStepNotFound(step_id)
            return [
                tool_invocation_result(i)
                for i in uow.tool_invocations.list_for_step_page(
                    step_id, limit, after_requested_at, after_id
                )
            ]


class RequestToolInvocation(LifecycleEvents):
    def execute(self, command: RequestToolInvocationCommand) -> ToolInvocationResult:
        with self._uow_factory() as uow:
            run = uow.runs.get(command.run_id)
            if run is None:
                raise RunNotFound(command.run_id)
            if run.status is not RunStatus.RUNNING:
                raise EntityConflict("run cannot accept tool invocations")
            if command.step_id is not None:
                step = uow.steps.get(command.step_id)
                if step is None:
                    raise RunStepNotFound(command.step_id)
                if step.run_id != run.id:
                    raise EntityConflict("step does not belong to run")
                if step.status is not RunStepStatus.RUNNING:
                    raise EntityConflict("step cannot accept tool invocations")
            if command.approval_request_id is not None:
                approval = uow.approvals.get(command.approval_request_id)
                if approval is None:
                    raise EntityConflict("referenced approval request does not exist")
                if approval.run_id != run.id:
                    raise EntityConflict("approval request does not belong to run")
                if approval.step_id is not None and approval.step_id != command.step_id:
                    raise EntityConflict("approval request is scoped to a different step")
                if approval.status is not ApprovalStatus.APPROVED:
                    raise EntityConflict("only an approved request may authorize an invocation")
            now = self._clock.now()
            invocation = ToolInvocation.new(
                id=ToolInvocationId.new(),
                run_id=run.id,
                tool_name=command.tool_name,
                requested_input=command.requested_input,
                requested_at=now,
                step_id=command.step_id,
                approval_request_id=command.approval_request_id,
            )
            uow.tool_invocations.add(invocation)
            self.append_run_events(
                uow,
                run,
                now,
                [
                    (
                        RunEventType.TOOL_INVOCATION_REQUESTED,
                        {
                            "tool_invocation_id": str(invocation.id),
                            "tool_name": invocation.tool_name,
                            "approval_request_id": (
                                str(command.approval_request_id)
                                if command.approval_request_id
                                else None
                            ),
                        },
                        invocation.step_id,
                    )
                ],
            )
            uow.commit()
            return tool_invocation_result(invocation)


class _ToolInvocationTransition(LifecycleEvents):
    def _load(self, uow: UnitOfWork, invocation_id: ToolInvocationId) -> tuple[ToolInvocation, Run]:
        invocation = uow.tool_invocations.get(invocation_id)
        if invocation is None:
            raise ToolInvocationNotFound(invocation_id)
        run = uow.runs.get(invocation.run_id)
        if run is None:
            raise RunNotFound(invocation.run_id)
        return invocation, run

    def _commit_with_event(
        self,
        uow: UnitOfWork,
        run: Run,
        invocation: ToolInvocation,
        type_: RunEventType,
        extra: dict[str, str] | None = None,
    ) -> ToolInvocationResult:
        payload: dict[str, str] = {"tool_invocation_id": str(invocation.id)}
        payload.update(extra or {})
        now = self._clock.now()
        uow.tool_invocations.save(invocation)
        self.append_run_events(uow, run, now, [(type_, dict(payload), invocation.step_id)])
        uow.commit()
        return tool_invocation_result(invocation)


class MarkToolInvocationRunning(_ToolInvocationTransition):
    def execute(self, command: MarkToolInvocationRunningCommand) -> ToolInvocationResult:
        with self._uow_factory() as uow:
            invocation, run = self._load(uow, command.invocation_id)
            if invocation.status is ToolInvocationStatus.RUNNING:
                uow.commit()
                return tool_invocation_result(invocation)
            if invocation.status is not ToolInvocationStatus.REQUESTED:
                raise EntityConflict("tool invocation cannot start")
            invocation.start(self._clock.now())
            return self._commit_with_event(
                uow, run, invocation, RunEventType.TOOL_INVOCATION_STARTED
            )


class MarkToolInvocationSucceeded(_ToolInvocationTransition):
    def execute(self, command: MarkToolInvocationSucceededCommand) -> ToolInvocationResult:
        with self._uow_factory() as uow:
            invocation, run = self._load(uow, command.invocation_id)
            if invocation.status is ToolInvocationStatus.SUCCEEDED:
                if invocation.output != command.output:
                    raise EntityConflict("tool invocation output is immutable")
                uow.commit()
                return tool_invocation_result(invocation)
            if invocation.status in TERMINAL_TOOL_INVOCATION_STATUSES:
                raise EntityConflict("tool invocation is terminal")
            if invocation.status is not ToolInvocationStatus.RUNNING:
                raise EntityConflict("tool invocation cannot succeed")
            invocation.succeed(self._clock.now(), command.output)
            return self._commit_with_event(
                uow, run, invocation, RunEventType.TOOL_INVOCATION_SUCCEEDED
            )


class MarkToolInvocationFailed(_ToolInvocationTransition):
    def execute(self, command: MarkToolInvocationFailedCommand) -> ToolInvocationResult:
        with self._uow_factory() as uow:
            invocation, run = self._load(uow, command.invocation_id)
            if invocation.status is ToolInvocationStatus.FAILED:
                if invocation.failure != command.failure:
                    raise EntityConflict("tool invocation failure is immutable")
                uow.commit()
                return tool_invocation_result(invocation)
            if invocation.status in TERMINAL_TOOL_INVOCATION_STATUSES:
                raise EntityConflict("tool invocation is terminal")
            if invocation.status is not ToolInvocationStatus.RUNNING:
                raise EntityConflict("tool invocation cannot fail")
            invocation.fail(self._clock.now(), command.failure)
            return self._commit_with_event(
                uow,
                run,
                invocation,
                RunEventType.TOOL_INVOCATION_FAILED,
                {"failure_code": command.failure.code},
            )


class CancelToolInvocation(_ToolInvocationTransition):
    def execute(self, command: CancelToolInvocationCommand) -> ToolInvocationResult:
        with self._uow_factory() as uow:
            invocation, run = self._load(uow, command.invocation_id)
            if invocation.status is ToolInvocationStatus.CANCELLED:
                uow.commit()
                return tool_invocation_result(invocation)
            if invocation.status in TERMINAL_TOOL_INVOCATION_STATUSES:
                raise EntityConflict("tool invocation is terminal")
            invocation.cancel(self._clock.now())
            return self._commit_with_event(
                uow, run, invocation, RunEventType.TOOL_INVOCATION_CANCELLED
            )
