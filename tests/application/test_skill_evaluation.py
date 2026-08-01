from __future__ import annotations

import hashlib

import pytest

from friday.application.skill_evaluation import (
    CompareSkillImprovementProposal,
    DeterministicEvaluatorRegistry,
    EvaluationKind,
    RunSkillEvaluation,
)
from friday.application.skill_improvement import (
    CreateSkillEvidenceSnapshot,
    CreateSkillImprovementProposal,
)
from friday.application.skill_registry import (
    ActivateSkillRevision,
    CreateSkill,
    CreateSkillRevision,
)
from friday.domain import (
    EvaluationSuiteStatus,
    SkillEvaluationCase,
    SkillEvaluationCaseId,
    SkillEvaluationSuite,
    SkillEvaluationSuiteId,
    SkillRevisionSourceKind,
)
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


@pytest.mark.parametrize(
    ("kind", "output", "expected"),
    [
        (EvaluationKind.EXACT_MATCH, "x", {"value": "x"}),
        (EvaluationKind.CONTAINS_ALL, "a b", {"values": ["a", "b"]}),
        (EvaluationKind.CONTAINS_NONE, "safe", {"values": ["unsafe"]}),
        (
            EvaluationKind.JSON_SCHEMA,
            '{"a": 1}',
            {"schema": {"type": "object", "required": ["a"]}},
        ),
        (EvaluationKind.TOOL_PROPOSAL_SHAPE, '{"tool":"x","tool_input":{}}', {}),
    ],
)
def test_builtin_evaluators_are_deterministic(
    kind: EvaluationKind, output: str, expected: dict[str, object]
) -> None:
    registry = DeterministicEvaluatorRegistry()
    result = registry.evaluate(kind=kind, output=output, expected_properties=expected)
    assert result.passed and result.score == 1.0
    assert result.output_sha256 == hashlib.sha256(output.encode()).hexdigest()


def test_unregistered_evaluator_is_rejected_and_custom_is_code_registered() -> None:
    registry = DeterministicEvaluatorRegistry()
    with pytest.raises(ValueError, match="not registered"):
        registry.evaluate(kind="python:os.system", output="x", expected_properties={})
    registry.register(
        "always",
        lambda output, expected: registry.evaluate(
            kind="exact_match", output=output, expected_properties={"value": expected["value"]}
        ),
    )
    assert registry.evaluate(kind="always", output="x", expected_properties={"value": "x"}).passed


def test_run_snapshots_cases_and_exact_revision_without_any_tool_runtime() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(key="eval.safe", display_name="E", description="")
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="never execute",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    ActivateSkillRevision(factory, clock).execute(skill_id=skill.id, revision_id=revision.id)
    suite = SkillEvaluationSuite(
        id=SkillEvaluationSuiteId.new(),
        skill_id=skill.id,
        name="main",
        description="",
        status=EvaluationSuiteStatus.ACTIVE,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    case = SkillEvaluationCase(
        id=SkillEvaluationCaseId.new(),
        suite_id=suite.id,
        position=1,
        input="input",
        expected_properties={"value": "ok"},
        grading_kind="exact_match",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    uow.skill_evaluation_suites.add(suite)
    uow.skill_evaluation_cases.add(case)
    run = RunSkillEvaluation(factory, clock, DeterministicEvaluatorRegistry()).execute(
        suite_id=suite.id, revision_id=revision.id, outputs={str(case.id): "ok"}
    )
    assert run.revision_id == revision.id
    assert run.aggregate_result == {"case_count": 1, "passed": 1, "score": 1.0}
    assert len(uow.skill_evaluation_case_results.items) == 1


def test_candidate_comparison_requires_identical_frozen_evaluation_configuration() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="eval.compare", display_name="E", description=""
    )
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    suite = SkillEvaluationSuite(
        id=SkillEvaluationSuiteId.new(),
        skill_id=skill.id,
        name="main",
        description="",
        status=EvaluationSuiteStatus.ACTIVE,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    case = SkillEvaluationCase(
        id=SkillEvaluationCaseId.new(),
        suite_id=suite.id,
        position=1,
        input="input",
        expected_properties={"value": "ok"},
        grading_kind="exact_match",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    uow.skill_evaluation_suites.add(suite)
    uow.skill_evaluation_cases.add(case)
    baseline = RunSkillEvaluation(factory, clock, DeterministicEvaluatorRegistry()).execute(
        suite_id=suite.id, revision_id=revision.id, outputs={str(case.id): "ok"}
    )
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=revision.id,
        evidence={"items": [{"id": "u1"}]},
    )
    proposal = CreateSkillImprovementProposal(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=revision.id,
        trigger_kind="manual",
        evidence_snapshot_id=snapshot.id,
        evidence_snapshot_hash=snapshot.content_sha256,
        evidence_ids={"u1"},
        generator_version="brain-candidate-generator-v2",
        raw_candidate=(
            '{"version":1,"proposed_instructions":"candidate","rationale":"r",'
            '"addressed_evidence_ids":["u1"]}'
        ),
    )
    comparison = CompareSkillImprovementProposal(
        factory, clock, DeterministicEvaluatorRegistry()
    ).execute(
        proposal_id=proposal.id,
        baseline_evaluation_run_id=baseline.id,
        candidate_outputs={str(case.id): "ok"},
    )
    assert comparison.score_delta == 0
    assert comparison.recommendation.value == "requires_manual_override"
    stored = uow.skill_improvement_proposals.get(proposal.id)
    assert stored is not None and stored.status.value == "ready_for_review"
