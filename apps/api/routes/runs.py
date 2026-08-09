from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    cursor_datetime,
    decode_cursor,
    page_from_query,
)
from apps.api.routes.delegations import delegation_response
from apps.api.schemas.agents import RunAgentResolutionResponse
from apps.api.schemas.delegations import CreateDelegationRequestBody, DelegationRequestResponse
from apps.api.schemas.runs import RunPageResponse, RunResponse, RunResultResponse
from apps.api.schemas.skills import (
    AddSkillFeedbackBody,
    RunSkillAuditItem,
    RunSkillBindingResponse,
    SkillFeedbackResponse,
)
from apps.api.schemas.tasks import FailureBody
from friday.application.commands import (
    CancelRunCommand,
    CompleteRunCommand,
    FailRunCommand,
    RetryFailedRunCommand,
    StartQueuedRunCommand,
)
from friday.application.delegation import CreateDelegationRequest, ListDelegationsForRun
from friday.application.errors import RunNotFound, SkillNotFound, SkillRevisionNotFound
from friday.application.list_events import GetRunResult
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import RunResult
from friday.application.run_lifecycle import (
    CancelRun,
    CompleteRun,
    FailRun,
    GetLatestRunForExecution,
    GetRun,
    ListRunsByExecution,
    ListRunsForTask,
    RetryFailedRun,
    StartQueuedRun,
)
from friday.application.skill_usage import AddSkillRunFeedback
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import AgentId, RunId, RunStepId, SkillId, TaskId
from friday.domain.skill_usage import SkillFeedbackRating

router = APIRouter(prefix="/v1", tags=["runs"])
UowDependency = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDependency = Annotated[Clock, Depends(get_clock)]


@router.get(
    "/runs/{run_id}/skills",
    response_model=RunSkillBindingResponse,
    operation_id="getRunSkillsAudit",
)
def list_run_skills(run_id: UUID, uow_factory: UowDependency) -> RunSkillBindingResponse:
    with uow_factory() as uow:
        typed_run_id = RunId.parse(str(run_id))
        if uow.runs.get(typed_run_id) is None:
            raise RunNotFound(typed_run_id)
        resolution = uow.run_skill_resolutions.get(typed_run_id)
        items: list[RunSkillAuditItem] = []
        if resolution is not None:
            for binding in uow.run_skill_bindings.list_for_run(typed_run_id):
                skill = uow.skills.get(binding.skill_id)
                revision = uow.skill_revisions.get(binding.revision_id)
                if skill is None:
                    raise SkillNotFound(binding.skill_id)
                if revision is None:
                    raise SkillRevisionNotFound(binding.revision_id)
                items.append(
                    RunSkillAuditItem(
                        skill_id=str(skill.id),
                        skill_key=skill.key,
                        revision_id=str(revision.id),
                        version=revision.version,
                        instructions=revision.instructions,
                        content_sha256=revision.content_sha256,
                        source_kind=revision.source_kind.value,
                        position=binding.position,
                    )
                )
        return RunSkillBindingResponse(
            run_id=str(typed_run_id),
            resolved=resolution is not None,
            resolved_at=resolution.resolved_at if resolution is not None else None,
            items=items,
        )


@router.post(
    "/runs/{run_id}/skills/{skill_id}/feedback",
    response_model=SkillFeedbackResponse,
    operation_id="addRunSkillFeedback",
)
def add_skill_feedback(
    run_id: UUID,
    skill_id: UUID,
    body: AddSkillFeedbackBody,
    uow_factory: UowDependency,
    clock: ClockDependency,
) -> SkillFeedbackResponse:
    feedback = AddSkillRunFeedback(uow_factory, clock).execute(
        run_id=RunId.parse(str(run_id)),
        skill_id=SkillId.parse(str(skill_id)),
        rating=SkillFeedbackRating(body.rating),
        note=body.note,
        created_by=body.created_by,
    )
    return SkillFeedbackResponse(
        id=str(feedback.id),
        run_id=str(feedback.run_id),
        skill_id=str(feedback.skill_id),
        revision_id=str(feedback.revision_id),
        rating=feedback.rating.value,
        note=feedback.note,
        created_by=feedback.created_by,
        created_at=feedback.created_at,
    )


@router.get(
    "/runs/{run_id}/skills/{skill_id}/feedback",
    response_model=list[SkillFeedbackResponse],
    operation_id="listRunSkillFeedback",
)
def list_skill_feedback(
    run_id: UUID, skill_id: UUID, uow_factory: UowDependency
) -> list[SkillFeedbackResponse]:
    with uow_factory() as uow:
        typed_run_id = RunId.parse(str(run_id))
        typed_skill_id = SkillId.parse(str(skill_id))
        if uow.runs.get(typed_run_id) is None:
            raise RunNotFound(typed_run_id)
        if uow.skills.get(typed_skill_id) is None:
            raise SkillNotFound(typed_skill_id)
        return [
            SkillFeedbackResponse(
                id=str(x.id),
                run_id=str(x.run_id),
                skill_id=str(x.skill_id),
                revision_id=str(x.revision_id),
                rating=x.rating.value,
                note=x.note,
                created_by=x.created_by,
                created_at=x.created_at,
            )
            for x in uow.skill_run_feedback.list_for_run_skill(typed_run_id, typed_skill_id)
        ]


@router.get(
    "/runs/{run_id}/agent",
    response_model=RunAgentResolutionResponse,
    operation_id="getRunAgent",
)
def get_run_agent(run_id: UUID, uow_factory: UowDependency) -> RunAgentResolutionResponse:
    with uow_factory() as uow:
        typed_run_id = RunId.parse(str(run_id))
        if uow.runs.get(typed_run_id) is None:
            raise RunNotFound(typed_run_id)
        resolution = uow.run_agent_resolutions.get(typed_run_id)
        return RunAgentResolutionResponse(
            run_id=str(typed_run_id),
            resolved=resolution is not None,
            resolved_at=resolution.resolved_at if resolution is not None else None,
            agent_id=str(resolution.agent_id) if resolution is not None else None,
            revision_id=str(resolution.revision_id) if resolution is not None else None,
        )


@router.post(
    "/runs/{run_id}/delegations",
    response_model=DelegationRequestResponse,
    status_code=201,
    operation_id="createDelegationRequest",
)
def create_delegation(
    run_id: UUID,
    body: CreateDelegationRequestBody,
    uow_factory: UowDependency,
    clock: ClockDependency,
) -> DelegationRequestResponse:
    request = CreateDelegationRequest(uow_factory, clock).execute(
        parent_run_id=RunId.parse(str(run_id)),
        target_agent_id=AgentId.parse(body.target_agent_id),
        objective=body.objective,
        input_payload=body.input_payload,
        expected_output_contract=body.expected_output_contract,
        parent_run_step_id=RunStepId.parse(body.parent_run_step_id)
        if body.parent_run_step_id
        else None,
    )
    return delegation_response(request)


@router.get(
    "/runs/{run_id}/delegations",
    response_model=list[DelegationRequestResponse],
    operation_id="listDelegationRequestsForRun",
)
def list_delegations(run_id: UUID, uow_factory: UowDependency) -> list[DelegationRequestResponse]:
    requests = ListDelegationsForRun(uow_factory).execute(RunId.parse(str(run_id)))
    return [delegation_response(x) for x in requests]


def _run_response(result: RunResult) -> RunResponse:
    failure = (
        FailureBody(
            code=result.failure.code,
            message=result.failure.message,
            retryable=result.failure.retryable,
            cause=result.failure.cause,
            details=result.failure.details,
        )
        if result.failure is not None
        else None
    )
    return RunResponse(
        id=str(result.run_id),
        task_id=str(result.task_id),
        status=result.status,
        created_at=result.created_at,
        failure=failure,
        execution_id=str(result.execution_id),
    )


def _failure(body: FailureBody) -> Failure:
    return Failure(body.code, body.message, body.retryable, FailureCause(body.cause), body.details)


@router.get("/runs/{run_id}", response_model=RunResponse, operation_id="getRun")
def get_run(run_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> RunResponse:
    return _run_response(GetRun(uow_factory, clock).execute(RunId.parse(str(run_id))))


@router.get(
    "/runs/{run_id}/execution", response_model=RunPageResponse, operation_id="listRunsByExecution"
)
def list_runs_by_execution(
    run_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> RunPageResponse:
    results = ListRunsByExecution(uow_factory, clock).execute(RunId.parse(str(run_id)))
    return RunPageResponse(items=[_run_response(r) for r in results], next_cursor=None)


@router.get(
    "/runs/{run_id}/latest-in-execution",
    response_model=RunResponse,
    operation_id="getLatestRunForExecution",
)
def get_latest_run_for_execution(
    run_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> RunResponse:
    return _run_response(
        GetLatestRunForExecution(uow_factory, clock).execute(RunId.parse(str(run_id)))
    )


@router.get("/runs/{run_id}/result", response_model=RunResultResponse, operation_id="getRunResult")
def get_run_result(
    run_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> RunResultResponse:
    event = GetRunResult(uow_factory, clock).execute(RunId.parse(str(run_id)))
    payload = event.payload if event is not None and isinstance(event.payload, dict) else {}
    summary = payload.get("summary")
    return RunResultResponse(
        summary=summary if isinstance(summary, str) else None,
        details=payload.get("details", None),
    )


@router.get("/tasks/{task_id}/runs", response_model=RunPageResponse, operation_id="listRunsForTask")
def list_runs(
    task_id: UUID,
    uow_factory: UowDependency,
    clock: ClockDependency,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> RunPageResponse:
    parent_id = str(task_id)
    after = decode_cursor(
        cursor, collection="task_runs", parent_id=parent_id, order="created_at_id_asc", parts=2
    )
    results = ListRunsForTask(uow_factory, clock).page(
        TaskId.parse(parent_id),
        limit + 1,
        cursor_datetime(after.after[0]) if after else None,
        after.after[1] if after else None,
    )
    page, next_cursor = page_from_query(
        results,
        limit=limit,
        collection="task_runs",
        parent_id=parent_id,
        order="created_at_id_asc",
        key=lambda run: (run.created_at.isoformat(), str(run.run_id)),
    )
    return RunPageResponse(items=[_run_response(item) for item in page], next_cursor=next_cursor)


@router.post("/runs/{run_id}/start", response_model=RunResponse, operation_id="startQueuedRun")
def start_queued_run(
    run_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> RunResponse:
    return _run_response(
        StartQueuedRun(uow_factory, clock).execute(StartQueuedRunCommand(RunId.parse(str(run_id))))
    )


@router.post("/runs/{run_id}/complete", response_model=RunResponse, operation_id="completeRun")
def complete_run(run_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> RunResponse:
    return _run_response(
        CompleteRun(uow_factory, clock).execute(CompleteRunCommand(RunId.parse(str(run_id))))
    )


@router.post("/runs/{run_id}/fail", response_model=RunResponse, operation_id="failRun")
def fail_run(
    run_id: UUID, body: FailureBody, uow_factory: UowDependency, clock: ClockDependency
) -> RunResponse:
    return _run_response(
        FailRun(uow_factory, clock).execute(
            FailRunCommand(RunId.parse(str(run_id)), _failure(body))
        )
    )


@router.post("/runs/{run_id}/cancel", response_model=RunResponse, operation_id="cancelRun")
def cancel_run(run_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> RunResponse:
    return _run_response(
        CancelRun(uow_factory, clock).execute(CancelRunCommand(RunId.parse(str(run_id))))
    )


@router.post("/runs/{run_id}/retry", response_model=RunResponse, operation_id="retryFailedRun")
def retry_run(run_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> RunResponse:
    return _run_response(
        RetryFailedRun(uow_factory, clock).execute(RetryFailedRunCommand(RunId.parse(str(run_id))))
    )
