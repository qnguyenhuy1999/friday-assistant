from __future__ import annotations

import json

from friday.application.skill_evaluation import BrainOnlyEvaluationRequest
from friday.application.skill_registry import CreateSkill, CreateSkillRevision
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
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


class _CandidateGenerator:
    def generate_candidate(self, request: object) -> str:
        del request
        return json.dumps(
            {
                "version": 1,
                "proposed_instructions": "policy-generated improvement",
                "rationale": "bounded policy evidence was selected",
                "addressed_evidence_ids": [],
            }
        )


class _CaseEvaluator:
    def evaluate_skill_cases(self, request: BrainOnlyEvaluationRequest) -> dict[str, str]:
        return {case_id: "" for case_id, _input in request.cases}


def _add_case(uow: FakeUnitOfWork, suite: SkillEvaluationSuite, clock: FakeClock) -> None:
    uow.skill_evaluation_cases.add(
        SkillEvaluationCase(
            id=SkillEvaluationCaseId.new(),
            suite_id=suite.id,
            position=1,
            input="input",
            expected_properties={"value": ""},
            grading_kind="exact_match",
            created_at=clock.now(),
            updated_at=clock.now(),
        )
    )


def test_due_policy_freezes_evidence_and_creates_only_an_inert_proposal() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="policy.loop", display_name="Policy", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(base, clock.now())
    uow.skill_repo.save(skill)
    suite = SkillEvaluationSuite(
        id=SkillEvaluationSuiteId.new(),
        skill_id=skill.id,
        name="suite",
        description="",
        status=EvaluationSuiteStatus.ACTIVE,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    uow.skill_evaluation_suites.add(suite)
    _add_case(uow, suite, clock)
    uow.skill_improvement_policies.save(
        SkillImprovementPolicy(
            skill_id=skill.id,
            enabled=True,
            minimum_usage_records=0,
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

    assert (
        EvaluateDueSkillImprovementPolicies(
            factory, clock, batch_size=10, candidate_generator=_CandidateGenerator()
        ).execute()
        == 0
    )

    proposals = uow.skill_improvement_proposals.list_for_skill(skill.id)
    assert proposals == []
    policy = uow.skill_improvement_policies.get(skill.id)
    assert policy is not None and policy.last_triggered_at is None
    assert uow.skill_revisions.list_for_skill(skill.id) == [base]


def test_policy_uses_brain_only_case_evaluator_then_stops_at_human_review() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="policy.evaluate", display_name="Policy", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(base, clock.now())
    uow.skill_repo.save(skill)
    suite = SkillEvaluationSuite(
        id=SkillEvaluationSuiteId.new(),
        skill_id=skill.id,
        name="suite",
        description="",
        status=EvaluationSuiteStatus.ACTIVE,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    uow.skill_evaluation_suites.add(suite)
    _add_case(uow, suite, clock)
    uow.skill_improvement_policies.save(
        SkillImprovementPolicy(
            skill_id=skill.id,
            enabled=True,
            minimum_usage_records=0,
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

    assert (
        EvaluateDueSkillImprovementPolicies(
            factory,
            clock,
            batch_size=10,
            candidate_generator=_CandidateGenerator(),
            candidate_evaluator=_CaseEvaluator(),
        ).execute()
        == 1
    )

    proposal = uow.skill_improvement_proposals.list_for_skill(skill.id)[0]
    assert proposal.status is SkillProposalStatus.READY_FOR_REVIEW
    comparison = uow.skill_candidate_evaluations.get_for_proposal(proposal.id)
    assert comparison is not None
    assert uow.skill_revisions.list_for_skill(skill.id) == [base]
