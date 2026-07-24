from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, page_ordered
from apps.api.schemas.approvals import (
    ApprovalPage,
    ApprovalResponse,
    CancelApprovalBody,
    RequestApprovalBody,
    ResolveApprovalBody,
)
from friday.application.approval_workflow import (
    ApproveRequest,
    CancelApproval,
    ExpireApproval,
    GetApproval,
    ListApprovalsForRun,
    RejectRequest,
    RequestApproval,
)
from friday.application.commands import ExpireApprovalCommand
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain.identifiers import ApprovalRequestId, RunId

router = APIRouter(tags=["approvals"])


@router.post(
    "/v1/runs/{run_id}/approvals",
    operation_id="requestApproval",
    status_code=status.HTTP_201_CREATED,
)
def request_approval(
    run_id: str,
    body: RequestApprovalBody,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ApprovalResponse:
    result = RequestApproval(uow_factory, clock).execute(body.to_command(RunId.parse(run_id)))
    return ApprovalResponse.from_result(result)


@router.get("/v1/approvals/{approval_id}", operation_id="getApproval")
def get_approval(
    approval_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ApprovalResponse:
    result = GetApproval(uow_factory, clock).execute(ApprovalRequestId.parse(approval_id))
    return ApprovalResponse.from_result(result)


@router.get("/v1/runs/{run_id}/approvals", operation_id="listApprovalsForRun")
def list_approvals_for_run(
    run_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> ApprovalPage:
    results = ListApprovalsForRun(uow_factory, clock).execute(RunId.parse(run_id))
    page, next_cursor = page_ordered(
        results,
        limit=limit,
        cursor=cursor,
        key=lambda a: (a.requested_at.isoformat(), str(a.approval_id)),
    )
    return ApprovalPage(
        items=[ApprovalResponse.from_result(r) for r in page], next_cursor=next_cursor
    )


@router.post("/v1/approvals/{approval_id}/approve", operation_id="approveRequest")
def approve_request(
    approval_id: str,
    body: ResolveApprovalBody,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ApprovalResponse:
    command = body.to_approve_command(ApprovalRequestId.parse(approval_id))
    result = ApproveRequest(uow_factory, clock).execute(command)
    return ApprovalResponse.from_result(result)


@router.post("/v1/approvals/{approval_id}/reject", operation_id="rejectRequest")
def reject_request(
    approval_id: str,
    body: ResolveApprovalBody,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ApprovalResponse:
    command = body.to_reject_command(ApprovalRequestId.parse(approval_id))
    result = RejectRequest(uow_factory, clock).execute(command)
    return ApprovalResponse.from_result(result)


@router.post("/v1/approvals/{approval_id}/cancel", operation_id="cancelApproval")
def cancel_approval(
    approval_id: str,
    body: CancelApprovalBody,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ApprovalResponse:
    command = body.to_command(ApprovalRequestId.parse(approval_id))
    result = CancelApproval(uow_factory, clock).execute(command)
    return ApprovalResponse.from_result(result)


@router.post("/v1/approvals/{approval_id}/expire", operation_id="expireApproval")
def expire_approval(
    approval_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ApprovalResponse:
    command = ExpireApprovalCommand(approval_id=ApprovalRequestId.parse(approval_id))
    result = ExpireApproval(uow_factory, clock).execute(command)
    return ApprovalResponse.from_result(result)
