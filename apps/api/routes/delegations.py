from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from apps.api.dependencies import get_uow_factory
from apps.api.schemas.delegations import DelegationRequestResponse
from friday.application.delegation import GetDelegationRequest
from friday.application.ports import UnitOfWorkFactory
from friday.domain import DelegationRequest, DelegationRequestId

router = APIRouter(prefix="/v1/delegations", tags=["delegations"])
Uow = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]


def delegation_response(x: DelegationRequest) -> DelegationRequestResponse:
    return DelegationRequestResponse(
        id=str(x.id),
        parent_run_id=str(x.parent_run_id),
        parent_run_step_id=str(x.parent_run_step_id) if x.parent_run_step_id else None,
        target_agent_id=str(x.target_agent_id),
        objective=x.objective,
        input_payload=x.input_payload,
        expected_output_contract=x.expected_output_contract,
        authorization_fingerprint=x.authorization_fingerprint,
        status=x.status.value,
        child_task_id=str(x.child_task_id) if x.child_task_id else None,
        child_run_id=str(x.child_run_id) if x.child_run_id else None,
        created_at=x.created_at,
        started_at=x.started_at,
        completed_at=x.completed_at,
        failure_code=x.failure_code,
    )


@router.get(
    "/{delegation_id}",
    response_model=DelegationRequestResponse,
    operation_id="getDelegationRequest",
)
def get_delegation(delegation_id: UUID, uow: Uow) -> DelegationRequestResponse:
    return delegation_response(
        GetDelegationRequest(uow).execute(DelegationRequestId.parse(str(delegation_id)))
    )
