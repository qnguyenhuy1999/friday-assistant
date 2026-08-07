from __future__ import annotations

import hashlib

import pytest

from friday.application.errors import EntityConflict
from friday.application.skill_evaluation import (
    CompareSkillImprovementProposal,
    DeterministicEvaluatorRegistry,
    EvaluationKind,
    RunSkillEvaluation,
    _deterministic_runtime_metadata,
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
from friday.domain.skill_evaluation import CANONICAL_BRAIN_EVALUATOR_VERSION
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


def _suite_and_case(
    uow: FakeUnitOfWork, clock: FakeClock, skill_id: object
) -> tuple[SkillEvaluationSuite, SkillEvaluationCase]:
    suite = SkillEvaluationSuite(
        id=SkillEvaluationSuiteId.new(),
        skill_id=skill_id,
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
    return suite, case


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
        generator_version="brain-candidate-generator-v2",
        candidate_prompt_version="candidate-prompt-v1",
        candidate_prompt_sha256="a" * 64,
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


def _run_with_metadata(
    factory: CountingUnitOfWorkFactory,
    clock: FakeClock,
    suite_id: object,
    case_id: object,
    revision_id: object,
    *,
    runtime_metadata: dict[str, object] | None = None,
    call_usage: dict[str, object] | None = None,
) -> object:
    return RunSkillEvaluation(factory, clock, DeterministicEvaluatorRegistry()).execute(
        suite_id=suite_id,
        revision_id=revision_id,
        outputs={str(case_id): "ok"},
        runtime_metadata=runtime_metadata,
        call_usage=call_usage,
    )


def test_different_call_usage_does_not_change_runtime_fingerprint_but_is_kept_separate() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(key="eval.usage", display_name="E", description="")
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    suite, case = _suite_and_case(uow, clock, skill.id)

    baseline = _run_with_metadata(
        factory, clock, suite.id, case.id, revision.id, call_usage={"input_tokens": 10}
    )
    candidate = _run_with_metadata(
        factory, clock, suite.id, case.id, revision.id, call_usage={"input_tokens": 999}
    )

    assert baseline.runtime_fingerprint == candidate.runtime_fingerprint
    assert baseline.call_usage != candidate.call_usage
    assert baseline.call_usage == {"input_tokens": 10}
    assert candidate.call_usage == {"input_tokens": 999}


@pytest.mark.parametrize(
    "changed_key",
    ["model", "adapter", "adapter_protocol", "evaluator_version", "system_prompt_version"],
)
def test_changing_stable_configuration_changes_runtime_fingerprint(changed_key: str) -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(key="eval.config", display_name="E", description="")
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    suite, case = _suite_and_case(uow, clock, skill.id)

    baseline_metadata = _deterministic_runtime_metadata()
    changed_metadata = dict(baseline_metadata)
    changed_metadata[changed_key] = "changed-value"
    if changed_key == "evaluator_version":
        # evaluator_version must match the execute() argument, so vary it there too.
        baseline = _run_with_metadata(
            factory, clock, suite.id, case.id, revision.id, runtime_metadata=baseline_metadata
        )
        run = RunSkillEvaluation(factory, clock, DeterministicEvaluatorRegistry()).execute(
            suite_id=suite.id,
            revision_id=revision.id,
            outputs={str(case.id): "ok"},
            evaluator_version=CANONICAL_BRAIN_EVALUATOR_VERSION,
            runtime_metadata={
                **baseline_metadata,
                "evaluator_version": CANONICAL_BRAIN_EVALUATOR_VERSION,
            },
        )
    else:
        baseline = _run_with_metadata(
            factory, clock, suite.id, case.id, revision.id, runtime_metadata=baseline_metadata
        )
        run = _run_with_metadata(
            factory, clock, suite.id, case.id, revision.id, runtime_metadata=changed_metadata
        )
    assert baseline.runtime_fingerprint != run.runtime_fingerprint


def test_response_and_input_limit_changes_change_runtime_fingerprint() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(key="eval.limits", display_name="E", description="")
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    suite, case = _suite_and_case(uow, clock, skill.id)
    baseline_metadata = _deterministic_runtime_metadata()
    changed_metadata = {**baseline_metadata, "response_limit_chars": 12345}

    baseline = _run_with_metadata(
        factory, clock, suite.id, case.id, revision.id, runtime_metadata=baseline_metadata
    )
    run = _run_with_metadata(
        factory, clock, suite.id, case.id, revision.id, runtime_metadata=changed_metadata
    )
    assert baseline.runtime_fingerprint != run.runtime_fingerprint


def test_reordering_runtime_metadata_keys_does_not_change_fingerprint() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="eval.reorder", display_name="E", description=""
    )
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    suite, case = _suite_and_case(uow, clock, skill.id)
    baseline_metadata = _deterministic_runtime_metadata()
    reordered_metadata = dict(reversed(list(baseline_metadata.items())))

    baseline = _run_with_metadata(
        factory, clock, suite.id, case.id, revision.id, runtime_metadata=baseline_metadata
    )
    run = _run_with_metadata(
        factory, clock, suite.id, case.id, revision.id, runtime_metadata=reordered_metadata
    )
    assert baseline.runtime_fingerprint == run.runtime_fingerprint


def test_adding_per_call_usage_fields_cannot_change_fingerprint() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="eval.no-usage", display_name="E", description=""
    )
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    suite, case = _suite_and_case(uow, clock, skill.id)

    without_usage = _run_with_metadata(factory, clock, suite.id, case.id, revision.id)
    with_usage = _run_with_metadata(
        factory,
        clock,
        suite.id,
        case.id,
        revision.id,
        call_usage={"input_tokens": 5, "output_tokens": 7, "request_id": "req-1"},
    )
    assert without_usage.runtime_fingerprint == with_usage.runtime_fingerprint
    assert without_usage.call_usage is None
    assert with_usage.call_usage == {
        "input_tokens": 5,
        "output_tokens": 7,
        "request_id": "req-1",
    }


def test_unknown_caller_controlled_runtime_metadata_keys_are_rejected() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(key="eval.strict", display_name="E", description="")
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    suite, case = _suite_and_case(uow, clock, skill.id)
    tampered_metadata = {
        **_deterministic_runtime_metadata(),
        "usage": {"input_tokens": 999999},
    }
    with pytest.raises(EntityConflict, match="code-owned"):
        _run_with_metadata(
            factory, clock, suite.id, case.id, revision.id, runtime_metadata=tampered_metadata
        )


def test_unknown_caller_controlled_runtime_metadata_extra_key_is_rejected() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="eval.strict-extra", display_name="E", description=""
    )
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    suite, case = _suite_and_case(uow, clock, skill.id)
    tampered_metadata = {
        **_deterministic_runtime_metadata(),
        "adapter_metadata": {"secret": "leak"},
    }
    with pytest.raises(EntityConflict, match="code-owned"):
        _run_with_metadata(
            factory, clock, suite.id, case.id, revision.id, runtime_metadata=tampered_metadata
        )
