from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    cursor_datetime,
    decode_cursor,
    page_from_query,
)
from apps.api.schemas.workflows import (
    CreateWorkflowBody,
    CreateWorkflowRevisionBody,
    WorkflowEdgeResponse,
    WorkflowNodeResponse,
    WorkflowPageResponse,
    WorkflowResponse,
    WorkflowRevisionResponse,
)
from friday.application.errors import WorkflowRevisionNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.workflow_registry import (
    ActivateWorkflowRevision,
    ArchiveWorkflow,
    CreateWorkflow,
    CreateWorkflowRevision,
    DisableWorkflow,
    GetWorkflow,
    ListWorkflows,
    WorkflowEdgeInput,
    WorkflowNodeInput,
)
from friday.domain import Workflow, WorkflowRevision, WorkflowRevisionSourceKind
from friday.domain.identifiers import WorkflowId, WorkflowRevisionId

router = APIRouter(prefix="/v1/workflows", tags=["workflows"])
Uow = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDep = Annotated[Clock, Depends(get_clock)]


def _workflow(x: Workflow) -> WorkflowResponse:
    return WorkflowResponse(
        id=str(x.id),
        key=x.key,
        display_name=x.display_name,
        description=x.description,
        status=x.status.value,
        active_revision_id=str(x.active_revision_id) if x.active_revision_id else None,
        created_at=x.created_at,
        updated_at=x.updated_at,
    )


def _revision(x: WorkflowRevision) -> WorkflowRevisionResponse:
    names = {node.id: node.node_key for node in x.nodes}
    return WorkflowRevisionResponse(
        id=str(x.id),
        workflow_id=str(x.workflow_id),
        version=x.version,
        content_sha256=x.content_sha256,
        source_kind=x.source_kind.value,
        nodes=[
            WorkflowNodeResponse(
                id=str(n.id),
                revision_id=str(n.revision_id),
                node_key=n.node_key,
                target_agent_id=str(n.target_agent_id),
                objective=n.objective,
                input_payload=n.input_payload,
                expected_output_contract=n.expected_output_contract,
                created_at=n.created_at,
            )
            for n in sorted(x.nodes, key=lambda n: n.node_key)
        ],
        edges=[
            WorkflowEdgeResponse(
                id=str(e.id),
                revision_id=str(e.revision_id),
                **{"from": names[e.from_node_id], "to": names[e.to_node_id]},
                created_at=e.created_at,
            )
            for e in sorted(x.edges, key=lambda e: (names[e.from_node_id], names[e.to_node_id]))
        ],
        created_at=x.created_at,
    )


@router.post(
    "",
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createWorkflow",
)
def create(body: CreateWorkflowBody, uow: Uow, clock: ClockDep) -> WorkflowResponse:
    return _workflow(
        CreateWorkflow(uow, clock).execute(
            key=body.key, display_name=body.display_name, description=body.description
        )
    )


@router.get("", response_model=WorkflowPageResponse, operation_id="listWorkflows")
def list_workflows(
    uow: Uow,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> WorkflowPageResponse:
    after = decode_cursor(
        cursor,
        collection="workflows",
        parent_id=None,
        order="created_at_id_asc",
        parts=2,
    )
    results = ListWorkflows(uow).page(
        limit + 1,
        cursor_datetime(after.after[0]) if after else None,
        after.after[1] if after else None,
    )
    page, next_cursor = page_from_query(
        results,
        limit=limit,
        collection="workflows",
        parent_id=None,
        order="created_at_id_asc",
        key=lambda workflow: (workflow.created_at.isoformat(), str(workflow.id)),
    )
    return WorkflowPageResponse(items=[_workflow(x) for x in page], next_cursor=next_cursor)


@router.get("/{workflow_id}", response_model=WorkflowResponse, operation_id="getWorkflow")
def get(workflow_id: UUID, uow: Uow) -> WorkflowResponse:
    return _workflow(GetWorkflow(uow).execute(WorkflowId.parse(str(workflow_id))))


@router.post(
    "/{workflow_id}/revisions",
    response_model=WorkflowRevisionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createWorkflowRevision",
)
def create_revision(
    workflow_id: UUID, body: CreateWorkflowRevisionBody, uow: Uow, clock: ClockDep
) -> WorkflowRevisionResponse:
    nodes: list[WorkflowNodeInput] = [
        {
            "node_key": n.node_key,
            "target_agent_id": n.target_agent_id,
            "objective": n.objective,
            "input_payload": n.input_payload,
            "expected_output_contract": n.expected_output_contract,
        }
        for n in body.nodes
    ]
    edges: list[WorkflowEdgeInput] = [{"from_node": e.from_, "to_node": e.to} for e in body.edges]
    return _revision(
        CreateWorkflowRevision(uow, clock).execute(
            workflow_id=WorkflowId.parse(str(workflow_id)),
            nodes=nodes,
            edges=edges,
            source_kind=WorkflowRevisionSourceKind(body.source_kind),
        )
    )


@router.get(
    "/{workflow_id}/revisions",
    response_model=list[WorkflowRevisionResponse],
    operation_id="listWorkflowRevisions",
)
def list_revisions(workflow_id: UUID, uow: Uow) -> list[WorkflowRevisionResponse]:
    return [
        _revision(x) for x in GetWorkflow(uow).list_revisions(WorkflowId.parse(str(workflow_id)))
    ]


@router.get(
    "/{workflow_id}/revisions/{revision_id}",
    response_model=WorkflowRevisionResponse,
    operation_id="getWorkflowRevision",
)
def get_revision(workflow_id: UUID, revision_id: UUID, uow: Uow) -> WorkflowRevisionResponse:
    value = GetWorkflow(uow).list_revisions(WorkflowId.parse(str(workflow_id)))
    revision = next((x for x in value if x.id == WorkflowRevisionId.parse(str(revision_id))), None)
    if revision is None:
        raise WorkflowRevisionNotFound(WorkflowRevisionId.parse(str(revision_id)))
    return _revision(revision)


@router.post(
    "/{workflow_id}/revisions/{revision_id}/activate",
    response_model=WorkflowResponse,
    operation_id="activateWorkflowRevision",
)
def activate(workflow_id: UUID, revision_id: UUID, uow: Uow, clock: ClockDep) -> WorkflowResponse:
    return _workflow(
        ActivateWorkflowRevision(uow, clock).execute(
            workflow_id=WorkflowId.parse(str(workflow_id)),
            revision_id=WorkflowRevisionId.parse(str(revision_id)),
        )
    )


@router.post(
    "/{workflow_id}/disable", response_model=WorkflowResponse, operation_id="disableWorkflow"
)
def disable(workflow_id: UUID, uow: Uow, clock: ClockDep) -> WorkflowResponse:
    return _workflow(DisableWorkflow(uow, clock).execute(WorkflowId.parse(str(workflow_id))))


@router.post(
    "/{workflow_id}/archive", response_model=WorkflowResponse, operation_id="archiveWorkflow"
)
def archive(workflow_id: UUID, uow: Uow, clock: ClockDep) -> WorkflowResponse:
    return _workflow(ArchiveWorkflow(uow, clock).execute(WorkflowId.parse(str(workflow_id))))
