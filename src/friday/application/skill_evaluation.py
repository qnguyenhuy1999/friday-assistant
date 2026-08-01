"""Offline deterministic grading for skill candidates and persisted revisions.

This module intentionally has no BrainRuntime, ToolGateway, filesystem, network,
process, MCP, or computer imports.  Evaluators operate solely on supplied text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol, cast

from friday.application.errors import EntityConflict, SkillNotFound, SkillRevisionNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import (
    CandidateComparisonResult,
    CandidateRecommendation,
    EvaluationRunStatus,
    EvaluationSuiteStatus,
    SkillCandidateEvaluation,
    SkillCandidateEvaluationId,
    SkillEvaluationCase,
    SkillEvaluationCaseId,
    SkillEvaluationCaseResult,
    SkillEvaluationRun,
    SkillEvaluationRunId,
    SkillEvaluationSuite,
    SkillEvaluationSuiteId,
    SkillId,
    SkillImprovementProposalId,
    SkillRevisionId,
)
from friday.domain.json_value import JsonValue
from friday.domain.skill_improvement import SkillProposalStatus


class EvaluationKind(StrEnum):
    EXACT_MATCH = "exact_match"
    CONTAINS_ALL = "contains_all"
    CONTAINS_NONE = "contains_none"
    JSON_SCHEMA = "json_schema"
    TOOL_PROPOSAL_SHAPE = "tool_proposal_shape"
    APPROVAL_EXPECTED = "approval_expected"


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    passed: bool
    score: float
    reason_code: str | None
    details: str
    output_sha256: str


@dataclass(frozen=True, slots=True)
class BrainOnlyEvaluationRequest:
    """One isolated, exact-instruction evaluation request for a frozen suite."""

    instructions: str
    cases: tuple[tuple[str, str], ...]
    max_response_chars: int = 40_000


class BrainOnlySkillEvaluator(Protocol):
    """Produces text only; implementations must expose no tools or sessions."""

    def evaluate_skill_cases(self, request: BrainOnlyEvaluationRequest) -> Mapping[str, str]: ...


def _output_hash(output: str) -> str:
    return hashlib.sha256(output.encode("utf-8")).hexdigest()


def _expected_list(expected: Mapping[str, Any], key: str) -> list[str]:
    value = expected.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"expected_properties.{key} must be a list of strings")
    return value


def _json_object(output: str) -> dict[str, Any] | None:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class DeterministicEvaluatorRegistry:
    """Code-owned allow-list; DB values never name arbitrary Python or shell."""

    def __init__(self) -> None:
        self._custom: dict[str, Callable[[str, Mapping[str, Any]], EvaluationResult]] = {}

    def register(
        self, key: str, evaluator: Callable[[str, Mapping[str, Any]], EvaluationResult]
    ) -> None:
        if not key or key in EvaluationKind or key in self._custom:
            raise ValueError("evaluation key must be a unique registered custom key")
        self._custom[key] = evaluator

    def evaluate(
        self, *, kind: str, output: str, expected_properties: Mapping[str, Any]
    ) -> EvaluationResult:
        output_hash = _output_hash(output)
        if kind in self._custom:
            return self._custom[kind](output, expected_properties)
        try:
            evaluator = EvaluationKind(kind)
        except ValueError as exc:
            raise ValueError("evaluator is not registered") from exc
        if evaluator is EvaluationKind.EXACT_MATCH:
            expected = expected_properties.get("value")
            if not isinstance(expected, str):
                raise ValueError("exact_match requires string value")
            ok = output == expected
        elif evaluator is EvaluationKind.CONTAINS_ALL:
            ok = all(item in output for item in _expected_list(expected_properties, "values"))
        elif evaluator is EvaluationKind.CONTAINS_NONE:
            ok = not any(item in output for item in _expected_list(expected_properties, "values"))
        elif evaluator is EvaluationKind.JSON_SCHEMA:
            obj = _json_object(output)
            required = _expected_list(expected_properties, "required_keys")
            ok = obj is not None and all(key in obj for key in required)
        elif evaluator is EvaluationKind.TOOL_PROPOSAL_SHAPE:
            obj = _json_object(output)
            ok = (
                obj is not None
                and isinstance(obj.get("tool"), str)
                and isinstance(obj.get("tool_input"), dict)
            )
        else:
            obj = _json_object(output)
            ok = obj is not None and obj.get("approval_required") is expected_properties.get(
                "value"
            )
        return EvaluationResult(
            passed=ok,
            score=1.0 if ok else 0.0,
            reason_code=None if ok else "expectation_not_met",
            details="passed" if ok else "expectation_not_met",
            output_sha256=output_hash,
        )


def _fingerprint(*, evaluator_version: str, cases: list[SkillEvaluationCase]) -> str:
    payload = {
        "evaluator_version": evaluator_version,
        "cases": [
            {
                "id": str(case.id),
                "position": case.position,
                "input": case.input,
                "expected_properties": case.expected_properties,
                "grading_kind": case.grading_kind,
            }
            for case in cases
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class RunSkillEvaluation:
    """Run pure supplied-output evaluation; never calls brain or any gateway."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        registry: DeterministicEvaluatorRegistry,
    ) -> None:
        self._uow_factory, self._clock, self._registry = uow_factory, clock, registry

    def execute(
        self,
        *,
        suite_id: SkillEvaluationSuiteId,
        revision_id: SkillRevisionId | None = None,
        proposal_id: SkillImprovementProposalId | None = None,
        outputs: Mapping[str, str],
        evaluator_version: str = "deterministic-v1",
    ) -> SkillEvaluationRun:
        if (revision_id is None) == (proposal_id is None):
            raise EntityConflict("evaluation must target exactly one revision or proposal")
        with self._uow_factory() as uow:
            suite = uow.skill_evaluation_suites.get(suite_id)
            if suite is None:
                raise EntityConflict("evaluation suite not found")
            if suite.status is not EvaluationSuiteStatus.ACTIVE:
                raise EntityConflict("evaluation suite is not active")
            if uow.skills.get(suite.skill_id) is None:
                raise SkillNotFound(suite.skill_id)
            if revision_id is not None:
                revision = uow.skill_revisions.get(revision_id)
                if revision is None:
                    raise SkillRevisionNotFound(revision_id)
                if revision.skill_id != suite.skill_id:
                    raise EntityConflict("evaluation revision does not belong to suite skill")
            else:
                assert proposal_id is not None
                proposal = uow.skill_improvement_proposals.get(proposal_id)
                if proposal is None or proposal.skill_id != suite.skill_id:
                    raise EntityConflict("evaluation proposal does not belong to suite skill")
            cases = uow.skill_evaluation_cases.list_for_suite(suite.id)
            snapshot = [
                {
                    "id": str(c.id),
                    "position": c.position,
                    "input": c.input,
                    "expected_properties": c.expected_properties,
                    "grading_kind": c.grading_kind,
                }
                for c in cases
            ]
            now = self._clock.now()
            run = SkillEvaluationRun(
                id=SkillEvaluationRunId.new(),
                suite_id=suite.id,
                skill_id=suite.skill_id,
                revision_id=revision_id,
                proposal_id=proposal_id,
                status=EvaluationRunStatus.SUCCEEDED,
                evaluator_version=evaluator_version,
                started_at=now,
                completed_at=now,
                aggregate_result={},
                suite_snapshot=cast(JsonValue, {"cases": snapshot}),
                runtime_fingerprint=_fingerprint(evaluator_version=evaluator_version, cases=cases),
            )
            results: list[SkillEvaluationCaseResult] = []
            for case in cases:
                output = outputs.get(str(case.id), "")
                expected = (
                    case.expected_properties if isinstance(case.expected_properties, dict) else {}
                )
                result = self._registry.evaluate(
                    kind=case.grading_kind, output=output, expected_properties=expected
                )
                results.append(
                    SkillEvaluationCaseResult(
                        evaluation_run_id=run.id,
                        case_id=case.id,
                        status=(
                            EvaluationRunStatus.SUCCEEDED
                            if result.passed
                            else EvaluationRunStatus.FAILED
                        ),
                        score=result.score,
                        reason_code=result.reason_code,
                        bounded_details=result.details[:4000],
                        output_sha256=result.output_sha256,
                    )
                )
            aggregate = {
                "case_count": len(results),
                "passed": sum(x.score == 1 for x in results),
                "score": sum(x.score for x in results) / len(results) if results else 0.0,
            }
            run = SkillEvaluationRun(
                id=run.id,
                suite_id=run.suite_id,
                skill_id=run.skill_id,
                revision_id=run.revision_id,
                proposal_id=run.proposal_id,
                status=run.status,
                evaluator_version=run.evaluator_version,
                started_at=run.started_at,
                completed_at=run.completed_at,
                aggregate_result=cast(JsonValue, aggregate),
                suite_snapshot=run.suite_snapshot,
                runtime_fingerprint=run.runtime_fingerprint,
            )
            uow.skill_evaluation_runs.add(run)
            uow.skill_evaluation_case_results.add_all(results)
            uow.commit()
            return run


class RunBrainOnlySkillEvaluation:
    """Obtain case outputs outside a UoW, then persist deterministic grading."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        registry: DeterministicEvaluatorRegistry,
        evaluator: BrainOnlySkillEvaluator,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._registry = registry
        self._evaluator = evaluator

    def execute(
        self,
        *,
        suite_id: SkillEvaluationSuiteId,
        revision_id: SkillRevisionId | None = None,
        proposal_id: SkillImprovementProposalId | None = None,
        evaluator_version: str = "brain-only-v1",
    ) -> SkillEvaluationRun:
        if (revision_id is None) == (proposal_id is None):
            raise EntityConflict("evaluation must target exactly one revision or proposal")
        with self._uow_factory() as uow:
            suite = uow.skill_evaluation_suites.get(suite_id)
            if suite is None:
                raise EntityConflict("evaluation suite not found")
            if suite.status is not EvaluationSuiteStatus.ACTIVE:
                raise EntityConflict("evaluation suite is not active")
            if revision_id is not None:
                revision = uow.skill_revisions.get(revision_id)
                if revision is None or revision.skill_id != suite.skill_id:
                    raise EntityConflict("evaluation revision does not belong to suite skill")
                instructions = revision.instructions
            else:
                assert proposal_id is not None
                proposal = uow.skill_improvement_proposals.get(proposal_id)
                if proposal is None or proposal.skill_id != suite.skill_id:
                    raise EntityConflict("evaluation proposal does not belong to suite skill")
                instructions = proposal.proposed_instructions
            cases = uow.skill_evaluation_cases.list_for_suite(suite_id)
            request = BrainOnlyEvaluationRequest(
                instructions=instructions,
                cases=tuple((str(case.id), case.input) for case in cases),
            )
        outputs = self._evaluator.evaluate_skill_cases(request)
        return RunSkillEvaluation(self._uow_factory, self._clock, self._registry).execute(
            suite_id=suite_id,
            revision_id=revision_id,
            proposal_id=proposal_id,
            outputs=outputs,
            evaluator_version=evaluator_version,
        )


class CompareSkillImprovementProposal:
    """Evaluate an inert candidate and compare it to one exact baseline run."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        registry: DeterministicEvaluatorRegistry,
    ) -> None:
        self._uow_factory, self._clock, self._registry = uow_factory, clock, registry

    def execute(
        self,
        *,
        proposal_id: SkillImprovementProposalId,
        baseline_evaluation_run_id: SkillEvaluationRunId,
        candidate_outputs: Mapping[str, str] | None = None,
        candidate_evaluation_run_id: SkillEvaluationRunId | None = None,
        comparison_policy_version: str = "comparison-v1",
    ) -> SkillCandidateEvaluation:
        if (candidate_outputs is None) == (candidate_evaluation_run_id is None):
            raise EntityConflict(
                "comparison requires candidate outputs or one candidate evaluation run"
            )
        with self._uow_factory() as uow:
            proposal = uow.skill_improvement_proposals.get(proposal_id)
            baseline = uow.skill_evaluation_runs.get(baseline_evaluation_run_id)
            if proposal is None or baseline is None:
                raise EntityConflict("proposal or baseline evaluation was not found")
            if (
                baseline.skill_id != proposal.skill_id
                or baseline.revision_id != proposal.base_revision_id
            ):
                raise EntityConflict("baseline evaluation does not match proposal base revision")
            if uow.skill_candidate_evaluations.get_for_proposal(proposal_id) is not None:
                raise EntityConflict("proposal already has an immutable comparison")
            suite_id = baseline.suite_id
        if candidate_evaluation_run_id is None:
            assert candidate_outputs is not None
            candidate_run_id = (
                RunSkillEvaluation(self._uow_factory, self._clock, self._registry)
                .execute(
                    suite_id=suite_id,
                    proposal_id=proposal_id,
                    outputs=candidate_outputs,
                    evaluator_version=baseline.evaluator_version,
                )
                .id
            )
        else:
            with self._uow_factory() as uow:
                if uow.skill_evaluation_runs.get(candidate_evaluation_run_id) is None:
                    raise EntityConflict("candidate evaluation run was not found")
            candidate_run_id = candidate_evaluation_run_id
        with self._uow_factory() as uow:
            proposal = uow.skill_improvement_proposals.get(proposal_id)
            baseline = uow.skill_evaluation_runs.get(baseline_evaluation_run_id)
            candidate_run = uow.skill_evaluation_runs.get(candidate_run_id)
            if proposal is None or baseline is None or candidate_run is None:
                raise EntityConflict("comparison inputs disappeared")
            if (
                candidate_run.proposal_id != proposal.id
                or candidate_run.skill_id != proposal.skill_id
                or baseline.runtime_fingerprint != candidate_run.runtime_fingerprint
                or baseline.suite_snapshot != candidate_run.suite_snapshot
                or baseline.evaluator_version != candidate_run.evaluator_version
            ):
                raise EntityConflict(
                    "comparison requires identical frozen evaluation configuration"
                )
            baseline_cases = {
                str(item.case_id): item
                for item in uow.skill_evaluation_case_results.list_for_run(baseline.id)
            }
            candidate_cases = {
                str(item.case_id)
                for item in uow.skill_evaluation_case_results.list_for_run(candidate_run.id)
            }
            candidate_scores = {
                str(item.case_id): item.score
                for item in uow.skill_evaluation_case_results.list_for_run(candidate_run.id)
            }
            if set(baseline_cases) != candidate_cases:
                raise EntityConflict(
                    "comparison case results do not share the same frozen snapshot"
                )
            regressions = improvements = inconclusive = 0
            for case_id, baseline_case in baseline_cases.items():
                delta = candidate_scores[case_id] - baseline_case.score
                if delta < 0:
                    regressions += 1
                elif delta > 0:
                    improvements += 1
                else:
                    inconclusive += 1
            candidate_aggregate = cast(dict[str, object], candidate_run.aggregate_result)
            baseline_aggregate = cast(dict[str, object], baseline.aggregate_result)
            candidate_score = candidate_aggregate.get("score")
            baseline_score = baseline_aggregate.get("score")
            if not isinstance(candidate_score, (int, float)) or not isinstance(
                baseline_score, (int, float)
            ):
                raise EntityConflict("evaluation aggregate has no numeric score")
            score_delta = float(candidate_score) - float(baseline_score)
            if regressions:
                result = (
                    CandidateComparisonResult.MIXED
                    if improvements
                    else CandidateComparisonResult.WORSE
                )
                recommendation = CandidateRecommendation.NOT_ELIGIBLE
            elif improvements:
                result, recommendation = (
                    CandidateComparisonResult.BETTER,
                    CandidateRecommendation.ELIGIBLE,
                )
            else:
                result = CandidateComparisonResult.EQUIVALENT
                recommendation = CandidateRecommendation.REQUIRES_MANUAL_OVERRIDE
            report = {
                "proposal_id": str(proposal.id),
                "baseline_run_id": str(baseline.id),
                "candidate_run_id": str(candidate_run.id),
                "runtime_fingerprint": baseline.runtime_fingerprint,
                "score_delta": score_delta,
                "regression_count": regressions,
                "improvement_count": improvements,
                "inconclusive_count": inconclusive,
                "result": result.value,
                "recommendation": recommendation.value,
                "comparison_policy_version": comparison_policy_version,
            }
            report_sha256 = hashlib.sha256(
                json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            comparison = SkillCandidateEvaluation(
                id=SkillCandidateEvaluationId.new(),
                proposal_id=proposal.id,
                baseline_evaluation_run_id=baseline.id,
                candidate_evaluation_run_id=candidate_run.id,
                comparison_policy_version=comparison_policy_version,
                result=result,
                recommendation=recommendation,
                score_delta=score_delta,
                regression_count=regressions,
                improvement_count=improvements,
                inconclusive_count=inconclusive,
                report_sha256=report_sha256,
                created_at=self._clock.now(),
            )
            uow.skill_candidate_evaluations.add(comparison)
            uow.skill_improvement_proposals.save(
                replace(proposal, status=SkillProposalStatus.READY_FOR_REVIEW)
            )
            uow.commit()
            return comparison


class CreateSkillEvaluationSuite:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self,
        *,
        skill_id: SkillId,
        name: str,
        description: str,
        cases: list[tuple[str, Mapping[str, Any], str]],
    ) -> SkillEvaluationSuite:
        with self._uow_factory() as uow:
            if uow.skills.get(skill_id) is None:
                raise SkillNotFound(skill_id)
            now = self._clock.now()
            suite = SkillEvaluationSuite(
                id=SkillEvaluationSuiteId.new(),
                skill_id=skill_id,
                name=name,
                description=description,
                status=EvaluationSuiteStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            uow.skill_evaluation_suites.add(suite)
            for position, (input_text, expected, kind) in enumerate(cases, start=1):
                uow.skill_evaluation_cases.add(
                    SkillEvaluationCase(
                        id=SkillEvaluationCaseId.new(),
                        suite_id=suite.id,
                        position=position,
                        input=input_text,
                        expected_properties=cast(JsonValue, dict(expected)),
                        grading_kind=kind,
                        created_at=now,
                        updated_at=now,
                    )
                )
            uow.commit()
            return suite
