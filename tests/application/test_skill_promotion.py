from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

import pytest

from friday.application.approval_workflow import ApproveRequest
from friday.application.commands import ApproveRequestCommand
from friday.application.errors import EntityConflict, SkillNotFound
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
    CancelSkillPromotion,
    ExecuteSkillPromotion,
    ExecuteSkillRollback,
    RejectSkillPromotion,
    RejectSkillRollback,
    RequestSkillPromotion,
    RequestSkillRollback,
    _promotion_fingerprint,
)
from friday.application.skill_registry import CreateSkill, CreateSkillRevision
from friday.domain import (
    ApprovalStatus,
    EvaluationSuiteStatus,
    PromotionRequestStatus,
    RollbackRequestStatus,
    SkillEvaluationCase,
    SkillEvaluationCaseId,
    SkillEvaluationSuite,
    SkillEvaluationSuiteId,
    SkillProposalStatus,
    SkillRevisionSourceKind,
)
from friday.domain.approval import ApprovalSubjectKind
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import (
    ApprovalRequestId,
    SkillId,
    SkillPromotionRequestId,
    SkillRevisionId,
    SkillRollbackRequestId,
)
from friday.domain.skill import Skill
from friday.domain.skill_improvement import SkillImprovementProposal
from tests.application.fakes import (
    CountingUnitOfWorkFactory,
    FakeClock,
    FakeSkillImprovementProposalRepository,
    FakeUnitOfWork,
)


class _FailingProposalRepository(FakeSkillImprovementProposalRepository):
    fail_on_save = False

    def save(self, proposal: SkillImprovementProposal) -> None:
        if self.fail_on_save:
            raise RuntimeError("proposal save failed")
        super().save(proposal)


class _TransactionalFailingUnitOfWork(FakeUnitOfWork):
    """A focused fake that models rollback for the terminal promotion writes."""

    def __init__(self) -> None:
        super().__init__()
        self.skill_improvement_proposal_repo = _FailingProposalRepository()
        self._snapshot: tuple[object, ...] | None = None
        self._committed = False

    def __enter__(self) -> _TransactionalFailingUnitOfWork:
        self._snapshot = tuple(
            deepcopy(repository.items)
            for repository in (
                self.skill_promotion_request_repo,
                self.skill_improvement_proposal_repo,
                self.approval_repo,
            )
        )
        self._committed = False
        self.closed = False
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: object
    ) -> None:
        if exc_type is not None and not self._committed:
            self.rollback()
        self.closed = True

    def commit(self) -> None:
        super().commit()
        self._committed = True

    def rollback(self) -> None:
        if self._snapshot is not None:
            repositories: tuple[Any, ...] = (
                self.skill_promotion_request_repo,
                self.skill_improvement_proposal_repo,
                self.approval_repo,
            )
            for repository, items in zip(
                repositories,
                self._snapshot,
                strict=True,
            ):
                repository.items = cast(Any, items)
        self.rollback_count += 1


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


def _create_next_proposal(
    factory: CountingUnitOfWorkFactory,
    clock: FakeClock,
    skill: Skill,
    base_revision_id: SkillRevisionId,
) -> SkillImprovementProposal:
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base_revision_id,
        evidence={"items": [{"id": "next-evidence"}]},
    )
    return CreateSkillImprovementProposal(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base_revision_id,
        trigger_kind="manual",
        evidence_snapshot_id=snapshot.id,
        evidence_snapshot_hash=snapshot.content_sha256,
        generator_version="brain-candidate-generator-v2",
        candidate_prompt_version="candidate-prompt-v1",
        candidate_prompt_sha256="a" * 64,
        raw_candidate=(
            '{"version":1,"proposed_instructions":"better-next",'
            '"rationale":"r","addressed_evidence_ids":["next-evidence"]}'
        ),
    )


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
    stored_proposal = uow.skill_improvement_proposals.get(request.proposal_id)
    assert stored_proposal is not None and stored_proposal.status is SkillProposalStatus.PROMOTED


def test_rejected_pending_promotion_closes_proposal_and_releases_open_slot() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill, proposal = _promoted_skill_setup(uow, factory, clock)

    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    rejected = RejectSkillPromotion(factory, clock).execute(request.id, "reviewer")

    assert rejected.status is PromotionRequestStatus.REJECTED
    stored_proposal = uow.skill_improvement_proposals.get(proposal.id)
    assert stored_proposal is not None and stored_proposal.status is SkillProposalStatus.REJECTED
    assert request.approval_request_id is not None
    approval = uow.approvals.get(request.approval_request_id)
    assert approval is not None and approval.status is ApprovalStatus.REJECTED
    next_proposal = _create_next_proposal(factory, clock, skill, proposal.base_revision_id)
    assert next_proposal.status is SkillProposalStatus.READY_FOR_EVALUATION


def test_stale_promotion_closes_proposal_as_superseded_and_releases_open_slot() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill, proposal = _promoted_skill_setup(uow, factory, clock)

    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert request.approval_request_id is not None
    _approve(factory, clock, request.approval_request_id)
    uow.skill_promotion_requests.save(replace(request, target_revision_id=SkillRevisionId.new()))

    with pytest.raises(EntityConflict, match="stale"):
        ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")

    stored_request = uow.skill_promotion_requests.get(request.id)
    stored_proposal = uow.skill_improvement_proposals.get(proposal.id)
    assert stored_request is not None and stored_request.status is PromotionRequestStatus.STALE
    assert stored_proposal is not None and stored_proposal.status is SkillProposalStatus.SUPERSEDED
    assert request.approval_request_id is not None
    approval = uow.approvals.get(request.approval_request_id)
    assert approval is not None and approval.status is ApprovalStatus.APPROVED
    assert not approval.is_consumed
    next_proposal = _create_next_proposal(factory, clock, skill, proposal.base_revision_id)
    assert next_proposal.status is SkillProposalStatus.READY_FOR_EVALUATION


def test_cancelled_pending_promotion_closes_proposal_and_releases_open_slot() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill, proposal = _promoted_skill_setup(uow, factory, clock)

    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    cancelled = CancelSkillPromotion(factory, clock).execute(request.id)

    assert cancelled.status is PromotionRequestStatus.CANCELLED
    stored_proposal = uow.skill_improvement_proposals.get(proposal.id)
    assert stored_proposal is not None and stored_proposal.status is SkillProposalStatus.CANCELLED
    assert request.approval_request_id is not None
    approval = uow.approvals.get(request.approval_request_id)
    assert approval is not None and approval.status is ApprovalStatus.CANCELLED
    next_proposal = _create_next_proposal(factory, clock, skill, proposal.base_revision_id)
    assert next_proposal.status is SkillProposalStatus.READY_FOR_EVALUATION


def test_failure_between_terminal_promotion_writes_rolls_back_both_rows() -> None:
    uow, clock = _TransactionalFailingUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)
    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert request.approval_request_id is not None
    approval = uow.approvals.get(request.approval_request_id)
    assert approval is not None
    approval_before = deepcopy(approval)
    proposal_before = uow.skill_improvement_proposals.get(proposal.id)
    assert proposal_before is not None
    commits_before = uow.commit_count
    rollbacks_before = uow.rollback_count
    cast(_FailingProposalRepository, uow.skill_improvement_proposal_repo).fail_on_save = True

    with pytest.raises(RuntimeError, match="proposal save failed"):
        RejectSkillPromotion(factory, clock).execute(request.id, "reviewer")

    assert uow.skill_promotion_requests.get(request.id) == request
    assert uow.skill_improvement_proposals.get(proposal.id) == proposal_before
    assert uow.approvals.get(approval.id) == approval_before
    assert uow.commit_count == commits_before
    assert uow.rollback_count == rollbacks_before + 1


def test_repeated_rejected_promotion_command_is_rejected_without_changes() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)
    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    rejected = RejectSkillPromotion(factory, clock).execute(request.id, "reviewer")
    stored_proposal = uow.skill_improvement_proposals.get(proposal.id)
    assert stored_proposal is not None
    assert request.approval_request_id is not None
    approval = uow.approvals.get(request.approval_request_id)
    assert approval is not None

    with pytest.raises(EntityConflict, match="not pending"):
        RejectSkillPromotion(factory, clock).execute(request.id, "another-reviewer")

    assert uow.skill_promotion_requests.get(request.id) == rejected
    assert uow.skill_improvement_proposals.get(proposal.id) == stored_proposal
    assert uow.approvals.get(approval.id) == approval


def test_repeated_promoted_promotion_command_is_rejected_without_changes() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)
    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert request.approval_request_id is not None
    _approve(factory, clock, request.approval_request_id)
    completed = ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")
    stored_proposal = uow.skill_improvement_proposals.get(proposal.id)
    assert stored_proposal is not None
    stored_skill = uow.skill_repo.get(request.skill_id)
    assert stored_skill is not None

    with pytest.raises(EntityConflict, match="not pending"):
        ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")

    assert uow.skill_promotion_requests.get(request.id) == completed
    assert uow.skill_improvement_proposals.get(proposal.id) == stored_proposal
    assert uow.skill_repo.get(request.skill_id) == stored_skill


def test_repeated_cancelled_promotion_command_is_rejected_without_changes() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)
    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    cancelled = CancelSkillPromotion(factory, clock).execute(request.id)
    stored_proposal = uow.skill_improvement_proposals.get(proposal.id)
    assert stored_proposal is not None
    assert request.approval_request_id is not None
    approval = uow.approvals.get(request.approval_request_id)
    assert approval is not None

    with pytest.raises(EntityConflict, match="not pending"):
        CancelSkillPromotion(factory, clock).execute(request.id)

    assert uow.skill_promotion_requests.get(request.id) == cancelled
    assert uow.skill_improvement_proposals.get(proposal.id) == stored_proposal
    assert uow.approvals.get(approval.id) == approval


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


def test_execute_promotion_rejects_missing_proposal() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)
    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert request.approval_request_id is not None
    _approve(factory, clock, request.approval_request_id)
    uow.skill_improvement_proposal_repo.items.pop(proposal.id)

    with pytest.raises(EntityConflict, match="promotion proposal was not found"):
        ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")


def test_execute_promotion_rejects_proposal_of_another_skill() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)
    other_skill = CreateSkill(factory, clock).execute(
        key="promote.other", display_name="O", description=""
    )
    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert request.approval_request_id is not None
    _approve(factory, clock, request.approval_request_id)
    stored = uow.skill_improvement_proposals.get(proposal.id)
    assert stored is not None
    uow.skill_improvement_proposal_repo.save(replace(stored, skill_id=other_skill.id))

    with pytest.raises(EntityConflict, match="does not belong to request"):
        ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")


def test_execute_promotion_rejects_missing_approval() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal = _promoted_skill_setup(uow, factory, clock)
    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    uow.skill_promotion_request_repo.save(
        replace(request, approval_request_id=ApprovalRequestId.new())
    )

    with pytest.raises(EntityConflict, match="approval was not found"):
        ExecuteSkillPromotion(factory, clock).execute(request.id, "operator")


def test_execute_promotion_rejects_approval_of_another_request() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _, proposal_a = _promoted_skill_setup(uow, factory, clock)
    _, proposal_b = _promoted_skill_setup(uow, factory, clock)
    request_a = RequestSkillPromotion(factory, clock).execute(proposal_a.id)
    request_b = RequestSkillPromotion(factory, clock).execute(proposal_b.id)
    uow.skill_promotion_request_repo.save(
        replace(request_a, approval_request_id=request_b.approval_request_id)
    )

    with pytest.raises(EntityConflict, match="does not belong to request"):
        ExecuteSkillPromotion(factory, clock).execute(request_a.id, "operator")


def test_execute_promotion_unknown_request_raises() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    with pytest.raises(EntityConflict, match="promotion request was not found"):
        ExecuteSkillPromotion(factory, clock).execute(SkillPromotionRequestId.new(), "operator")


def test_request_promotion_rejects_proposal_not_ready_for_review() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="promote.unready", display_name="P", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(base, clock.now())
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id, base_revision_id=base.id, evidence={"items": [{"id": "e"}]}
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

    with pytest.raises(EntityConflict, match="not ready"):
        RequestSkillPromotion(factory, clock).execute(proposal.id)


def test_request_promotion_rejects_non_eligible_comparison() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="promote.noteligible", display_name="P", description=""
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
        suite_id=suite.id, revision_id=base.id, outputs={str(case.id): "ok"}
    )
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id, base_revision_id=base.id, evidence={"items": [{"id": "e"}]}
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
        candidate_outputs={str(case.id): "bad"},
    )

    with pytest.raises(EntityConflict, match="not eligible"):
        RequestSkillPromotion(factory, clock).execute(proposal.id)


def test_request_promotion_rejects_missing_skill() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill, proposal = _promoted_skill_setup(uow, factory, clock)
    uow.skill_repo.items.pop(skill.id)

    with pytest.raises(SkillNotFound):
        RequestSkillPromotion(factory, clock).execute(proposal.id)


def test_request_promotion_rejects_inactive_skill() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill, proposal = _promoted_skill_setup(uow, factory, clock)
    skill.disable(clock.now())
    uow.skill_repo.save(skill)

    with pytest.raises(EntityConflict, match="active skill"):
        RequestSkillPromotion(factory, clock).execute(proposal.id)


def test_request_promotion_rejects_moved_base_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill, proposal = _promoted_skill_setup(uow, factory, clock)
    newer = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="newer", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(newer, clock.now())
    uow.skill_repo.save(skill)

    with pytest.raises(EntityConflict, match="base is no longer active"):
        RequestSkillPromotion(factory, clock).execute(proposal.id)


def test_execute_rollback_unknown_request_raises() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    with pytest.raises(EntityConflict, match="rollback request was not found"):
        ExecuteSkillRollback(factory, clock).execute(SkillRollbackRequestId.new(), "operator")


def test_execute_rollback_completed_request_is_idempotent() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.idempotent", display_name="R", description=""
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
    assert completed.status is RollbackRequestStatus.COMPLETED

    again = ExecuteSkillRollback(factory, clock).execute(request.id, "operator")
    assert again == completed
    stored = uow.skill_repo.get(skill.id)
    assert stored is not None and stored.active_revision_id == v1.id


def test_execute_rollback_rejects_not_pending_request() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.notpending", display_name="R", description=""
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
    uow.skill_rollback_request_repo.save(replace(request, status=RollbackRequestStatus.REJECTED))

    with pytest.raises(EntityConflict, match="not pending"):
        ExecuteSkillRollback(factory, clock).execute(request.id, "operator")


def test_execute_rollback_rejects_missing_canonical_approval() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.noapproval", display_name="R", description=""
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
    uow.skill_rollback_request_repo.save(replace(request, approval_request_id=None))

    with pytest.raises(EntityConflict, match="no canonical approval"):
        ExecuteSkillRollback(factory, clock).execute(request.id, "operator")


def test_execute_rollback_rejects_missing_approval() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.missingapproval", display_name="R", description=""
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
    uow.skill_rollback_request_repo.save(
        replace(request, approval_request_id=ApprovalRequestId.new())
    )

    with pytest.raises(EntityConflict, match="rollback approval was not found"):
        ExecuteSkillRollback(factory, clock).execute(request.id, "operator")


def test_execute_rollback_rejects_approval_of_another_request() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.crossreq", display_name="R", description=""
    )
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v1", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    v2 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v2", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v2, clock.now())
    uow.skill_repo.save(skill)
    request_a = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v1.id, reason="restore v1"
    )
    v3 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v3", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v3, clock.now())
    uow.skill_repo.save(skill)
    request_b = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v2.id, reason="restore v2"
    )
    uow.skill_rollback_request_repo.save(
        replace(request_a, approval_request_id=request_b.approval_request_id)
    )

    with pytest.raises(EntityConflict, match="rollback approval does not belong"):
        RejectSkillRollback(factory, clock).execute(request_a.id, "operator")


def test_execute_rollback_marks_stale_when_active_pointer_moved() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.stale", display_name="R", description=""
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
    v3 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v3", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v3, clock.now())
    uow.skill_repo.save(skill)

    with pytest.raises(EntityConflict, match="stale"):
        ExecuteSkillRollback(factory, clock).execute(request.id, "operator")

    stored = uow.skill_rollback_requests.get(request.id)
    assert stored is not None and stored.status is RollbackRequestStatus.STALE


def test_request_rollback_rejects_missing_skill() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    with pytest.raises(SkillNotFound):
        RequestSkillRollback(factory, clock).execute(
            skill_id=SkillId.new(), target_revision_id=SkillRevisionId.new(), reason="nope"
        )


def test_request_rollback_rejects_invalid_target() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.invalid", display_name="R", description=""
    )
    with pytest.raises(EntityConflict, match="invalid"):
        RequestSkillRollback(factory, clock).execute(
            skill_id=skill.id, target_revision_id=SkillRevisionId.new(), reason="nope"
        )


def test_request_rollback_rejects_already_active_target() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.active", display_name="R", description=""
    )
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v1", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v1, clock.now())
    uow.skill_repo.save(skill)
    with pytest.raises(EntityConflict, match="already active"):
        RequestSkillRollback(factory, clock).execute(
            skill_id=skill.id, target_revision_id=v1.id, reason="nope"
        )
