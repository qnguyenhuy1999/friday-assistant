from __future__ import annotations

import json

from friday.application.approval_workflow import ApproveRequest
from friday.application.commands import ApproveRequestCommand, CreateTaskCommand
from friday.application.create_task import CreateTask
from friday.application.skill_evaluation import BrainOnlyEvaluationRequest
from friday.application.skill_improvement import CandidateGenerationRequest
from friday.application.skill_promotion import (
    ExecuteSkillPromotion,
    ExecuteSkillRollback,
    RequestSkillPromotion,
    RequestSkillRollback,
)
from friday.application.skill_registry import (
    CreateSkill,
    CreateSkillRevision,
    ReplaceTaskSkills,
)
from friday.application.skill_usage import MaterializeSkillUsage
from friday.application.worker_maintenance import EvaluateDueSkillImprovementPolicies
from friday.domain import (
    EvaluationSuiteStatus,
    SkillEvaluationCase,
    SkillEvaluationCaseId,
    SkillEvaluationSuite,
    SkillEvaluationSuiteId,
    SkillImprovementPolicy,
    SkillProposalStatus,
    SkillRevisionSourceKind,
)
from friday.domain.identifiers import RunId
from friday.domain.run import Run
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork
from tests.application.resolve_helpers import resolve_run_skills_without_claim


class _CandidateGenerator:
    def __init__(self, evidence_id: str) -> None:
        self._evidence_id = evidence_id

    def generate_candidate(self, request: CandidateGenerationRequest) -> str:
        del request
        return json.dumps(
            {
                "version": 1,
                "proposed_instructions": "Use the reviewed, safer procedure.",
                "rationale": "The selected evidence identified the procedure gap.",
                "addressed_evidence_ids": [self._evidence_id],
            }
        )


class _CaseEvaluator:
    def evaluate_skill_cases(self, request: BrainOnlyEvaluationRequest) -> dict[str, str]:
        output = "candidate" if "safer" in request.instructions else "baseline"
        return {case_id: output for case_id, _input in request.cases}


def test_phase20_safe_loop_e2e_freezes_evidence_reviews_promotes_and_rolls_back() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task_id = CreateTask(factory, clock).execute(CreateTaskCommand("T", "")).task_id
    skill = CreateSkill(factory, clock).execute(
        key="phase20.e2e", display_name="E2E", description=""
    )
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="Use the original reviewed procedure.",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    skill.activate(v1, clock.now())
    uow.skill_repo.save(skill)
    ReplaceTaskSkills(factory, clock).execute(task_id=task_id, skill_ids=[skill.id])

    run_a = Run.new(id=RunId.new(), task_id=task_id, created_at=clock.now())
    uow.runs.add(run_a)
    assert resolve_run_skills_without_claim(factory, clock, run_a.id)[0].revision_id == v1.id
    run_a.start(clock.now())
    run_a.succeed(clock.now())
    assert MaterializeSkillUsage(factory, clock).execute(run_a.id)[0].revision_id == v1.id

    suite = SkillEvaluationSuite(
        id=SkillEvaluationSuiteId.new(),
        skill_id=skill.id,
        name="safe",
        description="",
        status=EvaluationSuiteStatus.ACTIVE,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    uow.skill_evaluation_suites.add(suite)
    uow.skill_evaluation_cases.add(
        SkillEvaluationCase(
            id=SkillEvaluationCaseId.new(),
            suite_id=suite.id,
            position=1,
            input="input",
            expected_properties={"value": "candidate"},
            grading_kind="exact_match",
            created_at=clock.now(),
            updated_at=clock.now(),
        )
    )
    uow.skill_improvement_policies.save(
        SkillImprovementPolicy(
            skill_id=skill.id,
            enabled=True,
            minimum_usage_records=1,
            minimum_failures=0,
            minimum_harmful_feedback=0,
            evaluation_suite_id=suite.id,
            cooldown_seconds=60,
            max_open_proposals=1,
            evidence_window_size=10,
            generator_version="brain-candidate-generator-v2",
            comparison_policy_version="comparison-v1",
            created_at=clock.now(),
            updated_at=clock.now(),
        )
    )
    # Use a known frozen ID to prove the candidate cannot cite evidence outside the snapshot.
    usage = uow.skill_usage_records.list_for_skill(skill.id, 10)[0]
    candidate = _CandidateGenerator(f"usage:{usage.id}")
    assert (
        EvaluateDueSkillImprovementPolicies(
            factory,
            clock,
            batch_size=10,
            candidate_generator=candidate,
            candidate_evaluator=_CaseEvaluator(),
        ).execute()
        == 1
    )

    proposal = uow.skill_improvement_proposals.list_for_skill(skill.id)[0]
    assert proposal.status is SkillProposalStatus.READY_FOR_REVIEW
    assert uow.skill_evidence_snapshots.get(proposal.evidence_snapshot_id) is not None
    assert uow.skill_candidate_evaluations.get_for_proposal(proposal.id) is not None
    assert uow.skill_revisions.list_for_skill(skill.id) == [v1]

    promotion = RequestSkillPromotion(factory, clock).execute(proposal.id)
    assert promotion.approval_request_id is not None
    ApproveRequest(factory, clock).execute(
        ApproveRequestCommand(promotion.approval_request_id, "operator")
    )
    promoted = ExecuteSkillPromotion(factory, clock).execute(promotion.id, "operator")
    assert promoted.promoted_revision_id is not None
    v2 = uow.skill_revision_repo.get(promoted.promoted_revision_id)
    assert v2 is not None and v2.version == 2
    assert resolve_run_skills_without_claim(factory, clock, run_a.id)[0].revision_id == v1.id

    run_b = Run.new(id=RunId.new(), task_id=task_id, created_at=clock.now())
    uow.runs.add(run_b)
    assert resolve_run_skills_without_claim(factory, clock, run_b.id)[0].revision_id == v2.id

    rollback = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v1.id, reason="restore reviewed baseline"
    )
    assert rollback.approval_request_id is not None
    ApproveRequest(factory, clock).execute(
        ApproveRequestCommand(rollback.approval_request_id, "operator")
    )
    rollback_result = ExecuteSkillRollback(factory, clock).execute(rollback.id, "operator")
    assert rollback_result.status.value == "completed"
    run_c = Run.new(id=RunId.new(), task_id=task_id, created_at=clock.now())
    uow.runs.add(run_c)
    assert resolve_run_skills_without_claim(factory, clock, run_c.id)[0].revision_id == v1.id
    assert uow.skill_revisions.list_for_skill(skill.id) == [v1, v2]
