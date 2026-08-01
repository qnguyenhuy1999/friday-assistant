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

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from friday.application.errors import (
    EntityConflict,
    SkillIntegrityFailed,
    SkillNotFound,
    SkillRevisionNotFound,
)
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
from friday.domain.skill_evaluation import (
    CANONICAL_BRAIN_EVALUATOR_VERSION,
    CANONICAL_DETERMINISTIC_EVALUATOR_VERSION,
)
from friday.domain.skill_improvement import SkillProposalStatus


class EvaluationKind(StrEnum):
    EXACT_MATCH = "exact_match"
    CONTAINS_ALL = "contains_all"
    CONTAINS_NONE = "contains_none"
    JSON_SCHEMA = "json_schema"
    REQUIRED_KEYS = "required_keys"
    TOOL_PROPOSAL_SHAPE = "tool_proposal_shape"


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


MAX_SUITE_CASES = 100
MAX_SUITE_INPUT_CHARS = 200_000
COMPARISON_POLICIES = frozenset({"comparison-v1"})
RUNTIME_CONFIG_VERSION = "skill-evaluation-runtime-v2"
_RUNTIME_METADATA_KEYS = frozenset(
    {
        "runtime_config_version",
        "model",
        "adapter",
        "adapter_protocol",
        "system_prompt_version",
        "evaluator_version",
        "input_limit_chars",
        "response_limit_chars",
        "tool_simulation_fixture_version",
        "brain_configuration",
    }
)


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
            schema = expected_properties.get("schema")
            if not isinstance(schema, dict):
                raise ValueError("json_schema requires an object-valued schema")
            try:
                Draft202012Validator(schema).validate(obj)
            except SchemaError as exc:
                raise ValueError("json_schema has an invalid schema") from exc
            except ValidationError:
                ok = False
            else:
                ok = obj is not None
        elif evaluator is EvaluationKind.REQUIRED_KEYS:
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
            if ok and expected_properties.get("required_tool") is not None:
                assert obj is not None
                ok = obj.get("tool") == expected_properties["required_tool"]
            if ok and "required_input_keys" in expected_properties:
                required_keys = _expected_list(expected_properties, "required_input_keys")
                assert obj is not None
                tool_input = obj.get("tool_input")
                ok = isinstance(tool_input, dict) and all(
                    key in tool_input for key in required_keys
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


def _runtime_fingerprint(runtime_metadata: JsonValue) -> str:
    return hashlib.sha256(
        json.dumps(runtime_metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _deterministic_runtime_metadata() -> JsonValue:
    return {
        "runtime_config_version": RUNTIME_CONFIG_VERSION,
        "model": None,
        "adapter": "deterministic",
        "adapter_protocol": "deterministic-evaluator-v2",
        "system_prompt_version": None,
        "evaluator_version": CANONICAL_DETERMINISTIC_EVALUATOR_VERSION,
        "input_limit_chars": MAX_SUITE_INPUT_CHARS,
        "response_limit_chars": 0,
        "tool_simulation_fixture_version": "none",
        "brain_configuration": {},
    }


def _validate_runtime_metadata(metadata: JsonValue, evaluator_version: str) -> JsonValue:
    if not isinstance(metadata, dict) or not set(metadata) >= _RUNTIME_METADATA_KEYS:
        raise EntityConflict("evaluation runtime metadata is incomplete")
    input_limit = metadata.get("input_limit_chars")
    response_limit = metadata.get("response_limit_chars")
    if (
        metadata.get("runtime_config_version") != RUNTIME_CONFIG_VERSION
        or metadata.get("evaluator_version") != evaluator_version
        or not isinstance(metadata.get("adapter"), str)
        or not isinstance(metadata.get("adapter_protocol"), str)
        or not (metadata.get("model") is None or isinstance(metadata.get("model"), str))
        or not (
            metadata.get("system_prompt_version") is None
            or isinstance(metadata.get("system_prompt_version"), str)
        )
        or not isinstance(metadata.get("tool_simulation_fixture_version"), str)
        or not isinstance(input_limit, int)
        or input_limit < 1
        or not isinstance(response_limit, int)
        or response_limit < 0
        or not isinstance(metadata.get("brain_configuration"), dict)
    ):
        raise EntityConflict("evaluation runtime metadata is not code-owned")
    return metadata


def _validate_expected_properties(kind: str, expected: object) -> dict[str, Any]:
    if not isinstance(expected, Mapping):
        raise EntityConflict("evaluation expected_properties must be an object")
    try:
        evaluator_kind = EvaluationKind(kind)
    except ValueError as exc:
        raise EntityConflict("evaluation kind is not code-owned") from exc
    if evaluator_kind in {
        EvaluationKind.CONTAINS_ALL,
        EvaluationKind.CONTAINS_NONE,
        EvaluationKind.REQUIRED_KEYS,
    }:
        key = "required_keys" if evaluator_kind is EvaluationKind.REQUIRED_KEYS else "values"
        try:
            _expected_list(expected, key)
        except ValueError as exc:
            raise EntityConflict("evaluation expected_properties shape is invalid") from exc
    elif evaluator_kind is EvaluationKind.EXACT_MATCH:
        if not isinstance(expected.get("value"), str):
            raise EntityConflict("exact_match requires a string value")
    elif evaluator_kind is EvaluationKind.JSON_SCHEMA:
        schema = expected.get("schema")
        if not isinstance(schema, dict):
            raise EntityConflict("json_schema requires an object-valued schema")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise EntityConflict("json_schema is invalid") from exc
    elif evaluator_kind is EvaluationKind.TOOL_PROPOSAL_SHAPE:
        if set(expected) - {"required_tool", "required_input_keys"}:
            raise EntityConflict("tool_proposal_shape expected_properties is invalid")
        if "required_tool" in expected and not isinstance(expected["required_tool"], str):
            raise EntityConflict("tool_proposal_shape required_tool must be a string")
        if "required_input_keys" in expected:
            try:
                _expected_list(expected, "required_input_keys")
            except ValueError as exc:
                raise EntityConflict("tool_proposal_shape expected_properties is invalid") from exc
    return dict(expected)


def _validate_suite_snapshot(snapshot: object) -> tuple[list[dict[str, object]], set[str]]:
    if not isinstance(snapshot, dict) or set(snapshot) != {"cases"}:
        raise EntityConflict("evaluation suite snapshot is malformed")
    raw_cases = snapshot.get("cases")
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= MAX_SUITE_CASES:
        raise EntityConflict("evaluation suite snapshot is malformed")
    cases: list[dict[str, object]] = []
    ids: set[str] = set()
    total_size = 0
    for expected_position, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict) or set(raw_case) != {
            "id",
            "position",
            "input",
            "expected_properties",
            "grading_kind",
        }:
            raise EntityConflict("evaluation suite snapshot is malformed")
        case_id = raw_case["id"]
        position = raw_case["position"]
        input_text = raw_case["input"]
        kind = raw_case["grading_kind"]
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in ids
            or not isinstance(position, int)
            or isinstance(position, bool)
            or position != expected_position
            or not isinstance(input_text, str)
            or not 1 <= len(input_text) <= 32_000
            or not isinstance(kind, str)
        ):
            raise EntityConflict("evaluation suite snapshot is malformed")
        try:
            SkillEvaluationCaseId.parse(case_id)
        except (TypeError, ValueError) as exc:
            raise EntityConflict("evaluation suite snapshot is malformed") from exc
        expected = _validate_expected_properties(kind, raw_case["expected_properties"])
        normalized = {
            "id": case_id,
            "position": position,
            "input": input_text,
            "expected_properties": expected,
            "grading_kind": kind,
        }
        total_size += len(json.dumps(normalized, sort_keys=True, separators=(",", ":")))
        cases.append(normalized)
        ids.add(case_id)
    if total_size > MAX_SUITE_INPUT_CHARS:
        raise EntityConflict("evaluation suite input is too large")
    return cases, ids


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
        evaluator_version: str = CANONICAL_DETERMINISTIC_EVALUATOR_VERSION,
        suite_snapshot: JsonValue | None = None,
        runtime_fingerprint: str | None = None,
        runtime_metadata: JsonValue = None,
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
                if (
                    hashlib.sha256(proposal.proposed_instructions.encode("utf-8")).hexdigest()
                    != proposal.proposed_content_sha256
                ):
                    raise SkillIntegrityFailed()
            cases = uow.skill_evaluation_cases.list_for_suite(suite.id)
            if not cases:
                raise EntityConflict("evaluation suite requires at least one case")
            live_snapshot = [
                {
                    "id": str(c.id),
                    "position": c.position,
                    "input": c.input,
                    "expected_properties": c.expected_properties,
                    "grading_kind": c.grading_kind,
                }
                for c in cases
            ]
            frozen_snapshot = (
                suite_snapshot
                if suite_snapshot is not None
                else cast(JsonValue, {"cases": live_snapshot})
            )
            snapshot_cases, expected_ids = _validate_suite_snapshot(frozen_snapshot)
            if (
                not isinstance(outputs, Mapping)
                or set(outputs) != expected_ids
                or not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in outputs.items()
                )
            ):
                raise EntityConflict("evaluation outputs must contain exactly every frozen case")
            by_id = {str(case.id): case for case in cases}
            if set(by_id) != expected_ids and suite_snapshot is None:
                raise EntityConflict("evaluation suite cases changed during evaluation")
            frozen_cases: list[tuple[str, str, object, str]] = []
            for item in snapshot_cases:
                case_id = str(item["id"])
                if suite_snapshot is None and case_id not in by_id:
                    raise EntityConflict("evaluation suite snapshot references unknown case")
                frozen_cases.append(
                    (
                        case_id,
                        str(item["input"]),
                        item["expected_properties"],
                        str(item["grading_kind"]),
                    )
                )
            if revision_id is not None:
                assert revision is not None
                target_hash = revision.content_sha256
            else:
                assert proposal is not None
                target_hash = proposal.proposed_content_sha256
            now = self._clock.now()
            metadata = _validate_runtime_metadata(
                runtime_metadata
                if runtime_metadata is not None
                else _deterministic_runtime_metadata(),
                evaluator_version,
            )
            computed_runtime_fingerprint = _runtime_fingerprint(metadata)
            if (
                runtime_fingerprint is not None
                and runtime_fingerprint != computed_runtime_fingerprint
            ):
                raise EntityConflict(
                    "evaluation runtime fingerprint does not match its configuration"
                )
            if evaluator_version not in {
                CANONICAL_DETERMINISTIC_EVALUATOR_VERSION,
                CANONICAL_BRAIN_EVALUATOR_VERSION,
            }:
                raise EntityConflict("evaluation runtime version is not code-owned")
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
                suite_snapshot=frozen_snapshot,
                runtime_fingerprint=computed_runtime_fingerprint,
                target_content_sha256=target_hash,
                runtime_metadata=metadata,
            )
            results: list[SkillEvaluationCaseResult] = []
            for case_id, _input, expected_value, grading_kind in frozen_cases:
                output = outputs[case_id]
                expected = _validate_expected_properties(grading_kind, expected_value)
                try:
                    result = self._registry.evaluate(
                        kind=grading_kind, output=output, expected_properties=expected
                    )
                except ValueError as exc:
                    raise EntityConflict(
                        "evaluation case is not valid for its code-owned evaluator"
                    ) from exc
                results.append(
                    SkillEvaluationCaseResult(
                        evaluation_run_id=run.id,
                        case_id=SkillEvaluationCaseId.parse(case_id),
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
                target_content_sha256=run.target_content_sha256,
                runtime_metadata=run.runtime_metadata,
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
        evaluator_version: str = CANONICAL_BRAIN_EVALUATOR_VERSION,
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
                if (
                    hashlib.sha256(proposal.proposed_instructions.encode("utf-8")).hexdigest()
                    != proposal.proposed_content_sha256
                ):
                    raise SkillIntegrityFailed()
                instructions = proposal.proposed_instructions
            cases = uow.skill_evaluation_cases.list_for_suite(suite_id)
            if not cases:
                raise EntityConflict("evaluation suite requires at least one case")
            snapshot = cast(
                JsonValue,
                {
                    "cases": [
                        {
                            "id": str(case.id),
                            "position": case.position,
                            "input": case.input,
                            "expected_properties": case.expected_properties,
                            "grading_kind": case.grading_kind,
                        }
                        for case in cases
                    ]
                },
            )
            request = BrainOnlyEvaluationRequest(
                instructions=instructions,
                cases=tuple((str(case.id), case.input) for case in cases),
            )
        outputs = self._evaluator.evaluate_skill_cases(request)
        expected_ids = {str(case.id) for case in cases}
        if set(outputs) != expected_ids or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in outputs.items()
        ):
            raise EntityConflict("evaluation outputs must contain exactly every frozen case")
        metadata = getattr(self._evaluator, "last_call_metadata", {})
        if callable(metadata):
            metadata = metadata()
        if not isinstance(metadata, dict):
            metadata = {}
        model = metadata.get("model")
        if model is not None and not isinstance(model, str):
            model = None
        runtime_metadata = cast(
            JsonValue,
            {
                "runtime_config_version": RUNTIME_CONFIG_VERSION,
                "model": model,
                "adapter": type(self._evaluator).__name__,
                "adapter_protocol": "brain-only-evaluation-v2",
                "system_prompt_version": "claude-evaluation-v2",
                "evaluator_version": evaluator_version,
                "input_limit_chars": MAX_SUITE_INPUT_CHARS,
                "response_limit_chars": request.max_response_chars,
                "tool_simulation_fixture_version": "none",
                "brain_configuration": {
                    "mode": metadata.get("mode"),
                    "adapter_version": metadata.get("adapter_version"),
                },
                "adapter_metadata": {
                    str(key): value
                    for key, value in metadata.items()
                    if key not in {"model", "mode", "adapter_version"}
                },
            },
        )
        runtime_fingerprint = _runtime_fingerprint(runtime_metadata)
        return RunSkillEvaluation(self._uow_factory, self._clock, self._registry).execute(
            suite_id=suite_id,
            revision_id=revision_id,
            proposal_id=proposal_id,
            outputs=outputs,
            evaluator_version=evaluator_version,
            suite_snapshot=snapshot,
            runtime_fingerprint=runtime_fingerprint,
            runtime_metadata=runtime_metadata,
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
        if comparison_policy_version not in COMPARISON_POLICIES:
            raise EntityConflict("comparison policy is not code-owned")
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
            base_revision = uow.skill_revisions.get(proposal.base_revision_id)
            if (
                base_revision is None
                or baseline.target_content_sha256 != base_revision.content_sha256
            ):
                raise EntityConflict("baseline evaluation target integrity failed")
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
                    suite_snapshot=baseline.suite_snapshot,
                    runtime_fingerprint=baseline.runtime_fingerprint,
                    runtime_metadata=baseline.runtime_metadata,
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
                or baseline.target_content_sha256 == candidate_run.target_content_sha256
                or candidate_run.target_content_sha256 != proposal.proposed_content_sha256
            ):
                raise EntityConflict(
                    "comparison requires identical frozen evaluation configuration"
                )
            baseline_result_list = uow.skill_evaluation_case_results.list_for_run(baseline.id)
            candidate_result_list = uow.skill_evaluation_case_results.list_for_run(candidate_run.id)
            baseline_cases = {str(item.case_id): item for item in baseline_result_list}
            candidate_cases = {str(item.case_id) for item in candidate_result_list}
            candidate_scores = {str(item.case_id): item.score for item in candidate_result_list}
            snapshot_cases = (
                baseline.suite_snapshot.get("cases")
                if isinstance(baseline.suite_snapshot, dict)
                else None
            )
            snapshot_ids = (
                {
                    str(item["id"])
                    for item in snapshot_cases
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                }
                if isinstance(snapshot_cases, list)
                else set()
            )
            complete = (
                bool(snapshot_ids)
                and len(baseline_result_list) == len(snapshot_ids)
                and len(candidate_result_list) == len(snapshot_ids)
                and set(baseline_cases) == snapshot_ids
                and candidate_cases == snapshot_ids
            )
            regressions = improvements = inconclusive = 0
            if not complete:
                result = CandidateComparisonResult.INCONCLUSIVE
                recommendation = CandidateRecommendation.NOT_ELIGIBLE
                inconclusive = max(
                    1,
                    len(snapshot_ids) - min(len(baseline_result_list), len(candidate_result_list)),
                )
                score_delta = 0.0
            else:
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
                    result = CandidateComparisonResult.INCONCLUSIVE
                    recommendation = CandidateRecommendation.NOT_ELIGIBLE
                    score_delta = 0.0
                    inconclusive = len(snapshot_ids)
                else:
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
                comparison_report=cast(JsonValue, report),
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
        if not cases:
            raise EntityConflict("evaluation suite requires at least one case")
        if (
            len(cases) > MAX_SUITE_CASES
            or sum(
                len(
                    json.dumps(
                        {
                            "input": item[0],
                            "expected_properties": item[1],
                            "grading_kind": item[2],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                for item in cases
            )
            > MAX_SUITE_INPUT_CHARS
        ):
            raise EntityConflict("evaluation suite is too large")
        for input_text, expected, kind in cases:
            if not input_text or len(input_text) > 32_000:
                raise EntityConflict("evaluation case input is invalid")
            _validate_expected_properties(kind, expected)
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
