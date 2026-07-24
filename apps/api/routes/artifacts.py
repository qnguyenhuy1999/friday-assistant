from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.pagination import CursorQuery, LimitQuery, paginate
from apps.api.schemas.artifacts import ArtifactPage, ArtifactResponse, RecordArtifactBody
from friday.application.artifact_use_cases import GetArtifact, ListArtifactsForRun, RecordArtifact
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain.identifiers import ArtifactId, RunId

router = APIRouter(tags=["artifacts"])


@router.post(
    "/v1/runs/{run_id}/artifacts",
    operation_id="recordArtifact",
    status_code=status.HTTP_201_CREATED,
)
def record_artifact(
    run_id: str,
    body: RecordArtifactBody,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ArtifactResponse:
    result = RecordArtifact(uow_factory, clock).execute(body.to_command(RunId.parse(run_id)))
    return ArtifactResponse.from_result(result)


@router.get("/v1/artifacts/{artifact_id}", operation_id="getArtifact")
def get_artifact(
    artifact_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ArtifactResponse:
    result = GetArtifact(uow_factory, clock).execute(ArtifactId.parse(artifact_id))
    return ArtifactResponse.from_result(result)


@router.get("/v1/runs/{run_id}/artifacts", operation_id="listArtifactsForRun")
def list_artifacts_for_run(
    run_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> ArtifactPage:
    results = ListArtifactsForRun(uow_factory, clock).execute(RunId.parse(run_id))
    return ArtifactPage.from_page(paginate(results, cursor=cursor, limit=limit))
