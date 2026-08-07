from __future__ import annotations

from dataclasses import replace

import pytest

from friday.application.approval_workflow import ApproveRequest
from friday.application.commands import ApproveRequestCommand
from friday.application.errors import EntityConflict
from friday.application.skill_evaluation import (
    CompareSkillImprovementProposal,
    DeterministicEvaluatorRegistry,
    RunSkillEvaluation,
)
from friday.application.skill_improvement import (
    CreateSkillEvidenceSnapshot,
    CreateSkillImprovementProposal,
)
from friday.application.skill_promotion import (
    ExecuteSkillPromotion,
    ExecuteSkillRollback,
    RequestSkillPromotion,
    RequestSkillRollback,
    _promotion_fingerprint,
)
from friday.application.skill_registry import CreateSkill, CreateSkillRevision
from friday.domain import (
    EvaluationSuiteStatus,
    PromotionRequestStatus,
    RollbackRequestStatus,
    SkillEvaluationCase,
    SkillEvaluationCaseId,
    SkillEvaluationSuite,
    SkillEvaluationSuiteId,
    SkillRevisionSourceKind,
)
from friday.domain.approval import ApprovalSubjectKind
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import ApprovalRequestId, SkillRevisionId
from friday.domain.skill import Skill
from friday.domain.skill_improvement import SkillImprovementProposal
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


def _promoted_skill_setup(
    uow: FakeUnitOfWork, factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> tuple[Skill, SkillImprovementProposal]:
    skill = CreateSkill(factory, clock).execute(
        key="promote.test", display_name="P", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(base, clock.now())
    suite = SkillEvaluationSuite(
        id=SkillEvaluationSuiteId.new(),
        skill_id=skill.id,
        name="suite",
        description="",
        status=EvaluationSuiteStatus.ACTIVE,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    case = SkillEvaluationCase(
        id=SkillEvaluationCaseId.new(),
        suite_id=suite.id,
        position=1,
        input="x",
        expected_properties={"value": "ok"},
        grading_kind="exact_match",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    uow.skill_evaluation_suites.add(suite)
    uow.skill_evaluation_cases.add(case)
    baseline = RunSkillEvaluation(factory, clock, DeterministicEvaluatorRegistry()).execute(
        suite_id=suite.id, revision_id=base.id, outputs={str(case.id): "bad"}
    )
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={"items": [{"id": "e"}]},
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
        raw_candidate=(
            '{"version":1,"proposed_instructions":"better","rationale":"r",'
            '"addressed_evidence_ids":["e"]}'
        ),
    )
    CompareSkillImprovementProposal(factory, clock, DeterministicEvaluatorRegistry()).execute(
        proposal_id=proposal.id,
        baseline_evaluation_run_id=baseline.id,
        candidate_outputs={str(case.id): "ok"},
    )
    return skill, proposal


def _approve(
    factory: CountingUnitOfWorkFactory, clock: FakeClock, approval_id: ApprovalRequestId
) -> None:
    ApproveRequest(factory, clock).execute(ApproveRequestCommand(approval_id, "operator"))


def test_exact_approved_promotion_creates_and_activates_one_generated_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)

    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert request.approval_request_id is not None
    approval = uow.approval_repo.get(request.approval_request_id)
    assert approval is not None and isinstance(approval.requested_input, dict)
    assert approval.requested_input["target_revision_id"] == str(request.target_revision_id)
    assert _promotion_fingerprint(replace(request, target_revision_id=SkillRevisionId.new())) != (
        request.authorization_fingerprint
    )
    _approve(factory, clock, request.approval_request_id)
    completed = ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")

    assert completed.promoted_revision_id is not None
    assert completed.promoted_revision_id == request.target_revision_id
    promoted = uow.skill_revision_repo.get(completed.promoted_revision_id)
    assert promoted is not None and promoted.version == 2
    stored_skill = uow.skill_repo.get(request.skill_id)
    assert stored_skill is not None and stored_skill.active_revision_id == promoted.id


def test_promoted_request_rejects_a_different_promoted_revision_identity() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)

    request = RequestSkillPromotion(factory, clock).execute(proposal.id)

    with pytest.raises(DomainValidationError, match="target revision"):
        replace(
            request,
            status=PromotionRequestStatus.PROMOTED,
            promoted_revision_id=SkillRevisionId.new(),
        )


def test_tampered_target_revision_identity_makes_promotion_stale() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)

    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert request.approval_request_id is not None
    _approve(factory, clock, request.approval_request_id)
    uow.skill_promotion_requests.save(replace(request, target_revision_id=SkillRevisionId.new()))

    with pytest.raises(EntityConflict, match="stale"):
        ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")

    stored = uow.skill_promotion_requests.get(request.id)
    assert stored is not None and stored.status is PromotionRequestStatus.STALE
    assert len(uow.skill_revisions.list_for_skill(request.skill_id)) == 1


def test_execute_promotion_before_approval_fails_and_creates_no_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)

    request = RequestSkillPromotion(factory, clock).execute(proposal.id)

    with pytest.raises(EntityConflict):
        ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")

    assert len(uow.skill_revisions.list_for_skill(request.skill_id)) == 1


def test_approving_promotion_alone_does_not_execute_promotion() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)

    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert request.approval_request_id is not None
    _approve(factory, clock, request.approval_request_id)

    stored = uow.skill_promotion_requests.get(request.id)
    assert stored is not None
    assert stored.status.value == "pending"
    assert len(uow.skill_revisions.list_for_skill(request.skill_id)) == 1


def test_promotion_approval_subject_matches_exact_promotion_request() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)

    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert request.approval_request_id is not None
    approval = uow.approval_repo.get(request.approval_request_id)
    assert approval is not None
    assert approval.subject_kind is ApprovalSubjectKind.SKILL_PROMOTION
    assert approval.subject_id == str(request.id)
    assert approval.authorization_fingerprint == request.authorization_fingerprint


def test_approval_for_another_promotion_request_cannot_authorize_execution() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal_a = _promoted_skill_setup(uow, factory, clock)
    _, proposal_b = _promoted_skill_setup(uow, factory, clock)

    request_a = RequestSkillPromotion(factory, clock).execute(proposal_a.id)
    request_b = RequestSkillPromotion(factory, clock).execute(proposal_b.id)
    assert request_b.approval_request_id is not None
    _approve(factory, clock, request_b.approval_request_id)

    with pytest.raises(EntityConflict):
        ExecuteSkillPromotion(factory, clock).execute(request_a.id, "operator")


def test_consumed_promotion_approval_cannot_be_reused() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)

    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert request.approval_request_id is not None
    _approve(factory, clock, request.approval_request_id)
    ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")

    approval = uow.approval_repo.get(request.approval_request_id)
    assert approval is not None and approval.is_consumed

    # Reset the request back to pending to prove a second execute attempt
    # cannot ride on the already-consumed approval.
    uow.skill_promotion_requests.save(replace(request, status=PromotionRequestStatus.PENDING))

    with pytest.raises(EntityConflict):
        ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")


def test_approved_rollback_reactivates_history_without_creating_a_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.test", display_name="R", description=""
    )
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v1", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    v2 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v2", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v2, clock.now())
    uow.skill_repo.save(skill)

    request = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v1.id, reason="restore known behavior"
    )
    assert request.approval_request_id is not None
    _approve(factory, clock, request.approval_request_id)
    completed = ExecuteSkillRollback(factory, clock).execute(request.id, "operator")

    assert completed.status.value == "completed"
    stored = uow.skill_repo.get(skill.id)
    assert stored is not None and stored.active_revision_id == v1.id
    assert uow.skill_revisions.list_for_skill(skill.id) == [v1, v2]


def test_execute_rollback_before_approval_fails_and_creates_no_change() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.noauth", display_name="R", description=""
    )
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v1", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    v2 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v2", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v2, clock.now())
    uow.skill_repo.save(skill)

    request = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v1.id, reason="restore known behavior"
    )

    with pytest.raises(EntityConflict):
        ExecuteSkillRollback(factory, clock).execute(request.id, "operator")

    stored = uow.skill_repo.get(skill.id)
    assert stored is not None and stored.active_revision_id == v2.id
    assert uow.skill_revisions.list_for_skill(skill.id) == [v1, v2]


def test_approving_rollback_alone_does_not_execute_rollback() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.approveonly", display_name="R", description=""
    )
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v1", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    v2 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v2", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v2, clock.now())
    uow.skill_repo.save(skill)

    request = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v1.id, reason="restore known behavior"
    )
    assert request.approval_request_id is not None
    _approve(factory, clock, request.approval_request_id)

    stored_request = uow.skill_rollback_requests.get(request.id)
    assert stored_request is not None and stored_request.status.value == "pending"
    stored_skill = uow.skill_repo.get(skill.id)
    assert stored_skill is not None and stored_skill.active_revision_id == v2.id


def test_rollback_approval_subject_matches_exact_rollback_request() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.subject", display_name="R", description=""
    )
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v1", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    v2 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v2", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v2, clock.now())
    uow.skill_repo.save(skill)

    request = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v1.id, reason="restore known behavior"
    )
    assert request.approval_request_id is not None
    approval = uow.approval_repo.get(request.approval_request_id)
    assert approval is not None
    assert approval.subject_kind is ApprovalSubjectKind.SKILL_ROLLBACK
    assert approval.subject_id == str(request.id)
    assert approval.authorization_fingerprint == request.authorization_fingerprint


def test_approval_for_another_rollback_request_cannot_authorize_execution() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.crossauth", display_name="R", description=""
    )
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v1", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    v2 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v2", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    v3 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v3", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v3, clock.now())
    uow.skill_repo.save(skill)

    request_a = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v1.id, reason="restore v1"
    )
    request_b = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v2.id, reason="restore v2"
    )
    assert request_b.approval_request_id is not None
    _approve(factory, clock, request_b.approval_request_id)

    with pytest.raises(EntityConflict):
        ExecuteSkillRollback(factory, clock).execute(request_a.id, "operator")


def test_consumed_rollback_approval_cannot_be_reused() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.reuse", display_name="R", description=""
    )
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v1", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    v2 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v2", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v2, clock.now())
    uow.skill_repo.save(skill)

    request = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v1.id, reason="restore known behavior"
    )
    assert request.approval_request_id is not None
    _approve(factory, clock, request.approval_request_id)
    ExecuteSkillRollback(factory, clock).execute(request.id, "operator")

    approval = uow.approval_repo.get(request.approval_request_id)
    assert approval is not None and approval.is_consumed

    uow.skill_rollback_requests.save(replace(request, status=RollbackRequestStatus.PENDING))

    with pytest.raises(EntityConflict):
        ExecuteSkillRollback(factory, clock).execute(request.id, "operator")


def test_no_production_import_named_approve_skill_promotion_or_rollback() -> None:
    import friday.application.skill_promotion as module

    assert not hasattr(module, "ApproveSkillPromotion")
    assert not hasattr(module, "ApproveSkillRollback")
