from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.schemas.skills import (
    CreateSkillBody,
    CreateSkillRevisionBody,
    SkillPageResponse,
    SkillResponse,
    SkillRevisionResponse,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.skill_registry import (
    ActivateSkillRevision,
    ArchiveSkill,
    CreateSkill,
    CreateSkillRevision,
    DisableSkill,
)
from friday.domain import Skill, SkillId, SkillRevision, SkillRevisionId, SkillRevisionSourceKind

router = APIRouter(prefix="/v1/skills", tags=["skills"])
Uow = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDep = Annotated[Clock, Depends(get_clock)]


def _skill(x: Skill) -> SkillResponse:
    return SkillResponse(
        id=str(x.id),
        key=x.key,
        display_name=x.display_name,
        description=x.description,
        status=x.status.value,
        active_revision_id=str(x.active_revision_id) if x.active_revision_id else None,
        created_at=x.created_at,
        updated_at=x.updated_at,
    )


def _revision(x: SkillRevision) -> SkillRevisionResponse:
    return SkillRevisionResponse(
        id=str(x.id),
        skill_id=str(x.skill_id),
        version=x.version,
        content_sha256=x.content_sha256,
        source_kind=x.source_kind.value,
        created_at=x.created_at,
    )


@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSkill",
)
def create(body: CreateSkillBody, uow: Uow, clock: ClockDep) -> SkillResponse:
    return _skill(
        CreateSkill(uow, clock).execute(
            key=body.key, display_name=body.display_name, description=body.description
        )
    )


@router.get("", response_model=SkillPageResponse, operation_id="listSkills")
def list_skills(uow: Uow) -> SkillPageResponse:
    with uow() as tx:
        return SkillPageResponse(items=[_skill(x) for x in tx.skills.list(100)])


@router.get("/{skill_id}", response_model=SkillResponse, operation_id="getSkill")
def get_skill(skill_id: UUID, uow: Uow) -> SkillResponse:
    with uow() as tx:
        value = tx.skills.get(SkillId.parse(str(skill_id)))
    if value is None:
        raise HTTPException(404, "skill not found")
    return _skill(value)


@router.post(
    "/{skill_id}/revisions",
    response_model=SkillRevisionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSkillRevision",
)
def create_revision(
    skill_id: UUID, body: CreateSkillRevisionBody, uow: Uow, clock: ClockDep
) -> SkillRevisionResponse:
    return _revision(
        CreateSkillRevision(uow, clock).execute(
            skill_id=SkillId.parse(str(skill_id)),
            instructions=body.instructions,
            source_kind=SkillRevisionSourceKind(body.source_kind),
        )
    )


@router.get(
    "/{skill_id}/revisions",
    response_model=list[SkillRevisionResponse],
    operation_id="listSkillRevisions",
)
def list_revisions(skill_id: UUID, uow: Uow) -> list[SkillRevisionResponse]:
    with uow() as tx:
        return [
            _revision(x) for x in tx.skill_revisions.list_for_skill(SkillId.parse(str(skill_id)))
        ]


@router.post(
    "/{skill_id}/revisions/{revision_id}/activate",
    response_model=SkillResponse,
    operation_id="activateSkillRevision",
)
def activate(skill_id: UUID, revision_id: UUID, uow: Uow, clock: ClockDep) -> SkillResponse:
    return _skill(
        ActivateSkillRevision(uow, clock).execute(
            skill_id=SkillId.parse(str(skill_id)),
            revision_id=SkillRevisionId.parse(str(revision_id)),
        )
    )


@router.post("/{skill_id}/disable", response_model=SkillResponse, operation_id="disableSkill")
def disable(skill_id: UUID, uow: Uow, clock: ClockDep) -> SkillResponse:
    return _skill(DisableSkill(uow, clock).execute(SkillId.parse(str(skill_id))))


@router.post("/{skill_id}/archive", response_model=SkillResponse, operation_id="archiveSkill")
def archive(skill_id: UUID, uow: Uow, clock: ClockDep) -> SkillResponse:
    return _skill(ArchiveSkill(uow, clock).execute(SkillId.parse(str(skill_id))))
