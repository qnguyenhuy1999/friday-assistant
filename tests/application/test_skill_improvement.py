from __future__ import annotations

import json

import pytest

from friday.application.errors import EntityConflict
from friday.application.skill_improvement import (
    CreateSkillEvidenceSnapshot,
    CreateSkillImprovementProposal,
    parse_candidate_output,
)
from friday.application.skill_registry import CreateSkill, CreateSkillRevision
from friday.domain import SkillProposalStatus, SkillRevisionSourceKind
from friday.domain.errors import DomainValidationError
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


def _candidate(instructions: str, evidence_ids: list[str] | None = None) -> str:
    return json.dumps(
        {
            "version": 1,
            "proposed_instructions": instructions,
            "rationale": "addresses the selected evidence",
            "addressed_evidence_ids": evidence_ids or ["usage-1"],
        }
    )


def test_candidate_contract_rejects_unknown_evidence_and_extra_fields() -> None:
    with pytest.raises(DomainValidationError, match="outside the frozen snapshot"):
        parse_candidate_output(_candidate("new", ["unknown"]), {"usage-1"})
    invalid = json.loads(_candidate("new"))
    invalid["unexpected"] = True
    with pytest.raises(DomainValidationError, match="strict contract"):
        parse_candidate_output(json.dumps(invalid), {"usage-1"})


def test_proposal_is_inert_and_bound_to_a_frozen_base_and_evidence_snapshot() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="proposal.safe", display_name="P", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="base instructions",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={"items": [{"id": "usage-1", "summary": "frozen-evidence"}]},
    )
    proposal = CreateSkillImprovementProposal(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        trigger_kind="manual",
        evidence_snapshot_id=snapshot.id,
        evidence_snapshot_hash=snapshot.content_sha256,
        generator_version="brain-candidate-generator-v2",
        candidate_prompt_version="candidate-prompt-v1",
        candidate_prompt_sha256="a" * 64,
        raw_candidate=_candidate("improved instructions"),
    )
    assert proposal.status is SkillProposalStatus.READY_FOR_EVALUATION
    assert proposal.base_revision_id == base.id
    assert proposal.evidence_snapshot_id == snapshot.id
    assert proposal.evidence_snapshot_hash == snapshot.content_sha256
    assert uow.skill_revisions.list_for_skill(skill.id) == [base]


def test_proposal_rejects_candidate_identical_to_base_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="proposal.same", display_name="P", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="base instructions",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={"items": [{"id": "usage-1"}]},
    )
    with pytest.raises(EntityConflict, match="differ from base"):
        CreateSkillImprovementProposal(factory, clock).execute(
            skill_id=skill.id,
            base_revision_id=base.id,
            trigger_kind="manual",
            evidence_snapshot_id=snapshot.id,
            evidence_snapshot_hash=snapshot.content_sha256,
            generator_version="brain-candidate-generator-v2",
            candidate_prompt_version="candidate-prompt-v1",
            candidate_prompt_sha256="a" * 64,
            raw_candidate=_candidate("base instructions"),
        )
