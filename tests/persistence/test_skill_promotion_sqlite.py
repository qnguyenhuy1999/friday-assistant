"""Raw-SQL proofs for the immutable Skill promotion target fence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from friday.application.skill_promotion import ExecuteSkillPromotion
from friday.domain.identifiers import SkillPromotionRequestId
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = "2026-01-02 03:00:00"


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 1, 2, 3, tzinfo=UTC)


SKILL_ID = "00000000-0000-0000-0000-000000000001"
OTHER_SKILL_ID = "00000000-0000-0000-0000-000000000002"
BASE_REVISION_ID = "00000000-0000-0000-0000-000000000101"
TARGET_REVISION_ID = "00000000-0000-0000-0000-000000000102"
OTHER_TARGET_REVISION_ID = "00000000-0000-0000-0000-000000000103"
SNAPSHOT_ID = "00000000-0000-0000-0000-000000000201"
SUITE_ID = "00000000-0000-0000-0000-000000000301"
PROPOSAL_ID = "00000000-0000-0000-0000-000000000401"
OTHER_PROPOSAL_ID = "00000000-0000-0000-0000-000000000402"
BASELINE_RUN_ID = "00000000-0000-0000-0000-000000000501"
CANDIDATE_RUN_ID = "00000000-0000-0000-0000-000000000502"
OTHER_CANDIDATE_RUN_ID = "00000000-0000-0000-0000-000000000503"
EVALUATION_ID = "00000000-0000-0000-0000-000000000601"
OTHER_EVALUATION_ID = "00000000-0000-0000-0000-000000000602"
PROMOTION_ID = "00000000-0000-0000-0000-000000000701"
OTHER_PROMOTION_ID = "00000000-0000-0000-0000-000000000702"
APPROVAL_ID = "00000000-0000-0000-0000-000000000801"
OTHER_APPROVAL_ID = "00000000-0000-0000-0000-000000000802"

BASE_INSTRUCTIONS = "reviewed base instructions"
CANDIDATE_INSTRUCTIONS = "approved candidate instructions"
OTHER_CANDIDATE_INSTRUCTIONS = "other candidate instructions"
BASE_SHA256 = hashlib.sha256(BASE_INSTRUCTIONS.encode("utf-8")).hexdigest()
CANDIDATE_SHA256 = hashlib.sha256(CANDIDATE_INSTRUCTIONS.encode("utf-8")).hexdigest()
OTHER_CANDIDATE_SHA256 = hashlib.sha256(OTHER_CANDIDATE_INSTRUCTIONS.encode("utf-8")).hexdigest()
EVIDENCE = {
    "version": 1,
    "entries": [{"id": "manual:e", "kind": "manual", "payload": {"id": "manual:e"}}],
}
EVIDENCE_JSON = json.dumps(EVIDENCE, sort_keys=True, separators=(",", ":"))
EVIDENCE_SHA256 = hashlib.sha256(EVIDENCE_JSON.encode("utf-8")).hexdigest()
REPORT = {
    "proposal_id": PROPOSAL_ID,
    "baseline_run_id": BASELINE_RUN_ID,
    "candidate_run_id": CANDIDATE_RUN_ID,
    "runtime_fingerprint": "c" * 64,
    "score_delta": 1.0,
    "regression_count": 0,
    "improvement_count": 1,
    "inconclusive_count": 0,
    "result": "better",
    "recommendation": "eligible",
    "comparison_policy_version": "comparison-v1",
}
REPORT_JSON = json.dumps(REPORT, sort_keys=True, separators=(",", ":"))
REPORT_SHA256 = hashlib.sha256(REPORT_JSON.encode("utf-8")).hexdigest()
OTHER_REPORT = {
    **REPORT,
    "proposal_id": OTHER_PROPOSAL_ID,
    "candidate_run_id": OTHER_CANDIDATE_RUN_ID,
}
OTHER_REPORT_JSON = json.dumps(OTHER_REPORT, sort_keys=True, separators=(",", ":"))
OTHER_REPORT_SHA256 = hashlib.sha256(OTHER_REPORT_JSON.encode("utf-8")).hexdigest()


def _migrated_engine(tmp_path: Path) -> Engine:
    db_path = tmp_path / "skill-promotion.db"
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{db_path}")


def _promotion_fingerprint(
    *,
    promotion_id: str,
    approval_id: str,
    proposal_id: str,
    skill_id: str,
    base_revision_id: str,
    target_revision_id: str,
    candidate_sha256: str,
    candidate_evaluation_id: str,
    comparison_report_sha256: str,
    target_version: int,
) -> str:
    payload = {
        "version": 1,
        "promotion_request_id": promotion_id,
        "approval_request_id": approval_id,
        "proposal_id": proposal_id,
        "skill_id": skill_id,
        "base_revision_id": base_revision_id,
        "current_active_revision_id": base_revision_id,
        "candidate_sha256": candidate_sha256,
        "candidate_evaluation_id": candidate_evaluation_id,
        "comparison_report_sha256": comparison_report_sha256,
        "target_revision_id": target_revision_id,
        "target_version": target_version,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _approval_input(
    *,
    promotion_id: str,
    approval_id: str,
    proposal_id: str,
    target_revision_id: str,
    candidate_instructions: str,
    candidate_sha256: str,
    candidate_evaluation_id: str,
    comparison_report_sha256: str,
    comparison_report: str,
    evidence_snapshot_id: str = SNAPSHOT_ID,
    evidence_snapshot_hash: str = EVIDENCE_SHA256,
    target_version: int = 2,
) -> str:
    fingerprint = _promotion_fingerprint(
        promotion_id=promotion_id,
        approval_id=approval_id,
        proposal_id=proposal_id,
        skill_id=SKILL_ID,
        base_revision_id=BASE_REVISION_ID,
        target_revision_id=target_revision_id,
        candidate_sha256=candidate_sha256,
        candidate_evaluation_id=candidate_evaluation_id,
        comparison_report_sha256=comparison_report_sha256,
        target_version=target_version,
    )
    return json.dumps(
        {
            "promotion_request_id": promotion_id,
            "approval_request_id": approval_id,
            "proposal_id": proposal_id,
            "skill_id": SKILL_ID,
            "base_revision_id": BASE_REVISION_ID,
            "current_active_revision_id": BASE_REVISION_ID,
            "target_revision_id": target_revision_id,
            "candidate_instructions": candidate_instructions,
            "candidate_sha256": candidate_sha256,
            "candidate_evaluation_id": candidate_evaluation_id,
            "comparison_report_sha256": comparison_report_sha256,
            "target_version": target_version,
            "evidence_snapshot_id": evidence_snapshot_id,
            "evidence_snapshot_hash": evidence_snapshot_hash,
            "comparison_report": json.loads(comparison_report),
            "recommendation": "eligible",
            "authorization_fingerprint": fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _insert_approval(
    connection: Connection,
    *,
    approval_id: str,
    promotion_id: str,
    requested_input: str,
    authorization_fingerprint: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO approval_requests ("
            "id, run_id, step_id, category, summary, reason, requested_action, requested_input, "
            "status, requested_at, expires_at, resolved_at, resolution_note, resolver, "
            "authorization_fingerprint, consumed_at, subject_kind, subject_id"
            ") VALUES ("
            ":id, NULL, NULL, 'other', 'Approve Skill promotion', 'reviewed', 'skill.promote', "
            ":requested_input, 'approved', :at, NULL, :at, NULL, 'operator', "
            ":authorization_fingerprint, NULL, 'skill_promotion', :promotion_id"
            ")"
        ),
        {
            "id": approval_id,
            "promotion_id": promotion_id,
            "requested_input": requested_input,
            "authorization_fingerprint": authorization_fingerprint,
            "at": AT,
        },
    )


def _insert_promotion(
    connection: Connection,
    *,
    promotion_id: str,
    approval_id: str,
    proposal_id: str,
    target_revision_id: str,
    candidate_evaluation_id: str,
    candidate_sha256: str,
    comparison_report_sha256: str,
    authorization_fingerprint: str,
    status: str = "pending",
) -> None:
    connection.execute(
        text(
            "INSERT INTO skill_promotion_requests ("
            "id, proposal_id, skill_id, base_revision_id, expected_active_revision_id, "
            "candidate_sha256, candidate_evaluation_id, comparison_report_sha256, "
            "target_revision_id, target_version, authorization_fingerprint, status, created_at, "
            "resolved_at, resolver, promoted_revision_id, approval_request_id"
            ") VALUES ("
            ":id, :proposal_id, :skill_id, :base_revision_id, :base_revision_id, "
            ":candidate_sha256, :candidate_evaluation_id, :comparison_report_sha256, "
            ":target_revision_id, 2, :authorization_fingerprint, :status, :at, NULL, NULL, NULL, "
            ":approval_id"
            ")"
        ),
        {
            "id": promotion_id,
            "proposal_id": proposal_id,
            "skill_id": SKILL_ID,
            "base_revision_id": BASE_REVISION_ID,
            "candidate_sha256": candidate_sha256,
            "candidate_evaluation_id": candidate_evaluation_id,
            "comparison_report_sha256": comparison_report_sha256,
            "target_revision_id": target_revision_id,
            "authorization_fingerprint": authorization_fingerprint,
            "status": status,
            "approval_id": approval_id,
            "at": AT,
        },
    )


def _seed_valid_promotion(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO skills (id, key, display_name, description, status, active_revision_id, "
            "created_at, updated_at) VALUES "
            "(:id, 'promotion.valid', 'Valid', '', 'active', NULL, :at, :at), "
            "(:other_id, 'promotion.other', 'Other', '', 'active', NULL, :at, :at)"
        ),
        {"id": SKILL_ID, "other_id": OTHER_SKILL_ID, "at": AT},
    )
    connection.execute(
        text(
            "INSERT INTO skill_revisions ("
            "id, skill_id, version, instructions, content_sha256, source_kind, created_at, "
            "promotion_request_id"
            ") VALUES (:id, :skill_id, 1, :instructions, :content_sha256, 'operator', :at, NULL)"
        ),
        {
            "id": BASE_REVISION_ID,
            "skill_id": SKILL_ID,
            "instructions": BASE_INSTRUCTIONS,
            "content_sha256": BASE_SHA256,
            "at": AT,
        },
    )
    connection.execute(
        text("UPDATE skills SET active_revision_id = :revision_id WHERE id = :skill_id"),
        {"revision_id": BASE_REVISION_ID, "skill_id": SKILL_ID},
    )
    connection.execute(
        text(
            "INSERT INTO skill_evidence_snapshots ("
            "id, skill_id, base_revision_id, evidence, content_sha256, created_at"
            ") VALUES (:id, :skill_id, :base_revision_id, :evidence, :content_sha256, :at)"
        ),
        {
            "id": SNAPSHOT_ID,
            "skill_id": SKILL_ID,
            "base_revision_id": BASE_REVISION_ID,
            "evidence": EVIDENCE_JSON,
            "content_sha256": EVIDENCE_SHA256,
            "at": AT,
        },
    )
    connection.execute(
        text(
            "INSERT INTO skill_evaluation_suites ("
            "id, skill_id, name, description, status, created_at, updated_at"
            ") VALUES (:id, :skill_id, 'promotion', '', 'active', :at, :at)"
        ),
        {"id": SUITE_ID, "skill_id": SKILL_ID, "at": AT},
    )
    connection.execute(
        text(
            "INSERT INTO skill_improvement_proposals ("
            "id, skill_id, base_revision_id, status, trigger_kind, evidence_snapshot_hash, "
            "proposed_instructions, proposed_content_sha256, rationale, generator_version, "
            "candidate_prompt_version, candidate_prompt_sha256, created_at, evidence_snapshot_id"
            ") VALUES "
            "(:id, :skill_id, :base_revision_id, 'ready_for_review', 'manual', :evidence_hash, "
            ":instructions, :content_sha256, 'reviewed', :generator, :prompt_version, "
            ":prompt_sha256, :at, :snapshot_id), "
            "(:other_id, :skill_id, :base_revision_id, 'rejected', 'manual', :evidence_hash, "
            ":other_instructions, :other_content_sha256, 'other', 'other-generator', "
            ":prompt_version, :other_prompt_sha256, :at, :snapshot_id)"
        ),
        {
            "id": PROPOSAL_ID,
            "other_id": OTHER_PROPOSAL_ID,
            "skill_id": SKILL_ID,
            "base_revision_id": BASE_REVISION_ID,
            "evidence_hash": EVIDENCE_SHA256,
            "instructions": CANDIDATE_INSTRUCTIONS,
            "content_sha256": CANDIDATE_SHA256,
            "generator": "candidate-generator",
            "other_instructions": OTHER_CANDIDATE_INSTRUCTIONS,
            "other_content_sha256": OTHER_CANDIDATE_SHA256,
            "snapshot_id": SNAPSHOT_ID,
            "prompt_version": "candidate-prompt-v1",
            "prompt_sha256": hashlib.sha256(b"prompt").hexdigest(),
            "other_prompt_sha256": hashlib.sha256(b"other-prompt").hexdigest(),
            "at": AT,
        },
    )
    connection.execute(
        text(
            "INSERT INTO skill_evaluation_runs ("
            "id, suite_id, skill_id, revision_id, proposal_id, status, evaluator_version, "
            "started_at, completed_at, aggregate_result, suite_snapshot, runtime_fingerprint, "
            "target_content_sha256, runtime_metadata"
            ") VALUES "
            "(:baseline_id, :suite_id, :skill_id, :base_revision_id, NULL, 'succeeded', 'test', "
            ":at, :at, '{}', '{}', :baseline_fingerprint, :base_sha, '{}'), "
            "(:candidate_id, :suite_id, :skill_id, NULL, :proposal_id, 'succeeded', "
            "'test', "
            ":at, :at, '{}', '{}', :candidate_fingerprint, :candidate_sha, '{}'), "
            "(:other_candidate_id, :suite_id, :skill_id, NULL, :other_proposal_id, 'succeeded', "
            "'test', "
            ":at, :at, '{}', '{}', :other_fingerprint, :other_candidate_sha, '{}')"
        ),
        {
            "baseline_id": BASELINE_RUN_ID,
            "candidate_id": CANDIDATE_RUN_ID,
            "other_candidate_id": OTHER_CANDIDATE_RUN_ID,
            "suite_id": SUITE_ID,
            "skill_id": SKILL_ID,
            "base_revision_id": BASE_REVISION_ID,
            "proposal_id": PROPOSAL_ID,
            "other_proposal_id": OTHER_PROPOSAL_ID,
            "baseline_fingerprint": "d" * 64,
            "candidate_fingerprint": "e" * 64,
            "other_fingerprint": "f" * 64,
            "base_sha": BASE_SHA256,
            "candidate_sha": CANDIDATE_SHA256,
            "other_candidate_sha": OTHER_CANDIDATE_SHA256,
            "at": AT,
        },
    )
    connection.execute(
        text(
            "INSERT INTO skill_candidate_evaluations ("
            "id, proposal_id, baseline_evaluation_run_id, candidate_evaluation_run_id, "
            "comparison_policy_version, result, recommendation, score_delta, regression_count, "
            "improvement_count, inconclusive_count, report_sha256, created_at, comparison_report"
            ") VALUES "
            "(:id, :proposal_id, :baseline_id, :candidate_id, 'comparison-v1', 'better', "
            "'eligible', 1.0, 0, 1, 0, :report_sha, :at, :report), "
            "(:other_id, :other_proposal_id, :baseline_id, :other_candidate_id, 'comparison-v1', "
            "'better', 'eligible', 1.0, 0, 1, 0, :other_report_sha, :at, :other_report)"
        ),
        {
            "id": EVALUATION_ID,
            "other_id": OTHER_EVALUATION_ID,
            "proposal_id": PROPOSAL_ID,
            "other_proposal_id": OTHER_PROPOSAL_ID,
            "baseline_id": BASELINE_RUN_ID,
            "candidate_id": CANDIDATE_RUN_ID,
            "other_candidate_id": OTHER_CANDIDATE_RUN_ID,
            "report_sha": REPORT_SHA256,
            "other_report_sha": OTHER_REPORT_SHA256,
            "report": REPORT_JSON,
            "other_report": OTHER_REPORT_JSON,
            "at": AT,
        },
    )

    fingerprint = _promotion_fingerprint(
        promotion_id=PROMOTION_ID,
        approval_id=APPROVAL_ID,
        proposal_id=PROPOSAL_ID,
        skill_id=SKILL_ID,
        base_revision_id=BASE_REVISION_ID,
        target_revision_id=TARGET_REVISION_ID,
        candidate_sha256=CANDIDATE_SHA256,
        candidate_evaluation_id=EVALUATION_ID,
        comparison_report_sha256=REPORT_SHA256,
        target_version=2,
    )
    requested_input = _approval_input(
        promotion_id=PROMOTION_ID,
        approval_id=APPROVAL_ID,
        proposal_id=PROPOSAL_ID,
        target_revision_id=TARGET_REVISION_ID,
        candidate_instructions=CANDIDATE_INSTRUCTIONS,
        candidate_sha256=CANDIDATE_SHA256,
        candidate_evaluation_id=EVALUATION_ID,
        comparison_report_sha256=REPORT_SHA256,
        comparison_report=REPORT_JSON,
    )
    _insert_approval(
        connection,
        approval_id=APPROVAL_ID,
        promotion_id=PROMOTION_ID,
        requested_input=requested_input,
        authorization_fingerprint=fingerprint,
    )
    _insert_promotion(
        connection,
        promotion_id=PROMOTION_ID,
        approval_id=APPROVAL_ID,
        proposal_id=PROPOSAL_ID,
        target_revision_id=TARGET_REVISION_ID,
        candidate_evaluation_id=EVALUATION_ID,
        candidate_sha256=CANDIDATE_SHA256,
        comparison_report_sha256=REPORT_SHA256,
        authorization_fingerprint=fingerprint,
    )

    other_fingerprint = _promotion_fingerprint(
        promotion_id=OTHER_PROMOTION_ID,
        approval_id=OTHER_APPROVAL_ID,
        proposal_id=OTHER_PROPOSAL_ID,
        skill_id=SKILL_ID,
        base_revision_id=BASE_REVISION_ID,
        target_revision_id=OTHER_TARGET_REVISION_ID,
        candidate_sha256=OTHER_CANDIDATE_SHA256,
        candidate_evaluation_id=OTHER_EVALUATION_ID,
        comparison_report_sha256=OTHER_REPORT_SHA256,
        target_version=2,
    )
    other_input = _approval_input(
        promotion_id=OTHER_PROMOTION_ID,
        approval_id=OTHER_APPROVAL_ID,
        proposal_id=OTHER_PROPOSAL_ID,
        target_revision_id=OTHER_TARGET_REVISION_ID,
        candidate_instructions=OTHER_CANDIDATE_INSTRUCTIONS,
        candidate_sha256=OTHER_CANDIDATE_SHA256,
        candidate_evaluation_id=OTHER_EVALUATION_ID,
        comparison_report_sha256=OTHER_REPORT_SHA256,
        comparison_report=OTHER_REPORT_JSON,
    )
    _insert_approval(
        connection,
        approval_id=OTHER_APPROVAL_ID,
        promotion_id=OTHER_PROMOTION_ID,
        requested_input=other_input,
        authorization_fingerprint=other_fingerprint,
    )
    _insert_promotion(
        connection,
        promotion_id=OTHER_PROMOTION_ID,
        approval_id=OTHER_APPROVAL_ID,
        proposal_id=OTHER_PROPOSAL_ID,
        target_revision_id=OTHER_TARGET_REVISION_ID,
        candidate_evaluation_id=OTHER_EVALUATION_ID,
        candidate_sha256=OTHER_CANDIDATE_SHA256,
        comparison_report_sha256=OTHER_REPORT_SHA256,
        authorization_fingerprint=other_fingerprint,
    )


def _insert_generated_revision(
    connection: Connection,
    *,
    revision_id: str = TARGET_REVISION_ID,
    skill_id: str = SKILL_ID,
    promotion_request_id: str = PROMOTION_ID,
    version: int = 2,
    instructions: str = CANDIDATE_INSTRUCTIONS,
    content_sha256: str = CANDIDATE_SHA256,
) -> None:
    connection.execute(
        text(
            "INSERT INTO skill_revisions ("
            "id, skill_id, version, instructions, content_sha256, source_kind, created_at, "
            "promotion_request_id"
            ") VALUES (:id, :skill_id, :version, :instructions, :content_sha256, 'generated', "
            ":at, :promotion_request_id)"
        ),
        {
            "id": revision_id,
            "skill_id": skill_id,
            "version": version,
            "instructions": instructions,
            "content_sha256": content_sha256,
            "at": AT,
            "promotion_request_id": promotion_request_id,
        },
    )


def _alter_requested_input(connection: Connection, path: str, value: object) -> None:
    connection.execute(
        text(
            "UPDATE approval_requests SET requested_input = json_set("
            "requested_input, :path, :value) WHERE id = :approval_id"
        ),
        {"path": path, "value": value, "approval_id": APPROVAL_ID},
    )


def _alter_requested_report(connection: Connection, path: str, report: str) -> None:
    connection.execute(
        text(
            "UPDATE approval_requests SET requested_input = json_set("
            "requested_input, :path, json(:report)) WHERE id = :approval_id"
        ),
        {"path": path, "report": report, "approval_id": APPROVAL_ID},
    )


class RevisionSubstitution(TypedDict, total=False):
    """Typed optional keyword overrides for one raw generated-revision insert."""

    revision_id: str
    skill_id: str
    promotion_request_id: str
    version: int
    instructions: str
    content_sha256: str


def _alter_candidate_evaluation(connection: Connection) -> RevisionSubstitution:
    _alter_requested_input(connection, "$.candidate_evaluation_id", OTHER_EVALUATION_ID)
    return RevisionSubstitution()


def _alter_comparison_report(connection: Connection) -> RevisionSubstitution:
    _alter_requested_report(connection, "$.comparison_report", '{"tampered":true}')
    return RevisionSubstitution()


def _alter_comparison_report_hash(connection: Connection) -> RevisionSubstitution:
    _alter_requested_input(connection, "$.comparison_report_sha256", "1" * 64)
    return RevisionSubstitution()


def _alter_authorization_fingerprint(connection: Connection) -> RevisionSubstitution:
    _alter_requested_input(connection, "$.authorization_fingerprint", "2" * 64)
    return RevisionSubstitution()


def _alter_persisted_authorization_fingerprint(connection: Connection) -> RevisionSubstitution:
    connection.execute(
        text(
            "UPDATE approval_requests SET authorization_fingerprint = :fingerprint "
            "WHERE id = :approval_id"
        ),
        {"fingerprint": "3" * 64, "approval_id": APPROVAL_ID},
    )
    return RevisionSubstitution()


@pytest.mark.parametrize(
    "substitution",
    [
        pytest.param(
            lambda _connection: RevisionSubstitution(instructions="arbitrary instructions"),
            id="arbitrary-instructions",
        ),
        pytest.param(
            lambda _connection: RevisionSubstitution(content_sha256="0" * 64),
            id="arbitrary-content-sha256",
        ),
        pytest.param(
            lambda _connection: RevisionSubstitution(version=99),
            id="arbitrary-version",
        ),
        pytest.param(
            lambda _connection: RevisionSubstitution(
                revision_id="00000000-0000-0000-0000-0000000000ff"
            ),
            id="arbitrary-revision-id",
        ),
        pytest.param(
            lambda _connection: RevisionSubstitution(promotion_request_id=OTHER_PROMOTION_ID),
            id="another-promotion-request-id",
        ),
        pytest.param(
            lambda _connection: RevisionSubstitution(skill_id=OTHER_SKILL_ID),
            id="another-skill-id",
        ),
        pytest.param(_alter_candidate_evaluation, id="another-candidate-evaluation"),
        pytest.param(_alter_comparison_report, id="altered-comparison-report"),
        pytest.param(
            _alter_comparison_report_hash,
            id="altered-comparison-report-hash",
        ),
        pytest.param(
            _alter_authorization_fingerprint,
            id="altered-authorization-fingerprint",
        ),
        pytest.param(
            _alter_persisted_authorization_fingerprint,
            id="altered-persisted-authorization-fingerprint",
        ),
    ],
)
def test_raw_sql_generated_revision_rejects_every_target_substitution(
    tmp_path: Path, substitution: Callable[[Connection], RevisionSubstitution]
) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        with engine.begin() as connection:
            _seed_valid_promotion(connection)
        with engine.begin() as connection:
            overrides = substitution(connection)
            with pytest.raises(IntegrityError):
                _insert_generated_revision(connection, **overrides)
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM skill_revisions WHERE source_kind = 'generated'")
                )
                == 0
            )
    finally:
        engine.dispose()


def test_raw_sql_exact_approved_target_revision_is_accepted(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        with engine.begin() as connection:
            _seed_valid_promotion(connection)
            _insert_generated_revision(connection)
            revision = (
                connection.execute(
                    text(
                        "SELECT id, skill_id, version, instructions, content_sha256, "
                        "promotion_request_id FROM skill_revisions WHERE id = :id"
                    ),
                    {"id": TARGET_REVISION_ID},
                )
                .mappings()
                .one()
            )
        assert dict(revision) == {
            "id": TARGET_REVISION_ID,
            "skill_id": SKILL_ID,
            "version": 2,
            "instructions": CANDIDATE_INSTRUCTIONS,
            "content_sha256": CANDIDATE_SHA256,
            "promotion_request_id": PROMOTION_ID,
        }
    finally:
        engine.dispose()


def test_execute_skill_promotion_uses_the_approved_target_identity(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        with engine.begin() as connection:
            _seed_valid_promotion(connection)
        completed = ExecuteSkillPromotion(
            create_unit_of_work_factory(create_session_factory(engine)), Clock()
        ).execute(SkillPromotionRequestId.parse(PROMOTION_ID), "operator")
        assert completed.promoted_revision_id == completed.target_revision_id
        assert str(completed.promoted_revision_id) == TARGET_REVISION_ID
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT r.id, r.promotion_request_id, p.status, p.promoted_revision_id, "
                        "a.consumed_at FROM skill_revisions r "
                        "JOIN skill_promotion_requests p ON p.id = r.promotion_request_id "
                        "JOIN approval_requests a ON a.id = p.approval_request_id "
                        "WHERE r.id = :revision_id"
                    ),
                    {"revision_id": TARGET_REVISION_ID},
                )
                .mappings()
                .one()
            )
        assert row["id"] == TARGET_REVISION_ID
        assert row["promotion_request_id"] == PROMOTION_ID
        assert row["status"] == "promoted"
        assert row["promoted_revision_id"] == TARGET_REVISION_ID
        assert row["consumed_at"] is not None
    finally:
        engine.dispose()
