"""The 0029 -> 0028 downgrade must abort before any schema mutation when
final-state data cannot be represented by revision 0028.

0029's downgrade() drops 0029-only columns, indexes, tables and triggers.  If a
database holds rows that live only in those structures, an unguarded downgrade
would silently destroy provenance.  These tests prove the preflight refuses the
downgrade and that the schema stays byte-identical when it does.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, inspect, text

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = "2026-01-02 03:00:00"

SeedFn = Callable[[Connection], None]

SKILL_ID = "00000000-0000-0000-0000-000000000001"
BASE_REVISION_ID = "00000000-0000-0000-0000-000000000101"
TARGET_REVISION_ID = "00000000-0000-0000-0000-000000000102"
SNAPSHOT_ID = "00000000-0000-0000-0000-000000000201"
PROPOSAL_ID = "00000000-0000-0000-0000-000000000401"
BASELINE_RUN_ID = "00000000-0000-0000-0000-000000000501"
CANDIDATE_RUN_ID = "00000000-0000-0000-0000-000000000502"
EVALUATION_ID = "00000000-0000-0000-0000-000000000601"
PROMOTION_ID = "00000000-0000-0000-0000-000000000701"
ROLLBACK_ID = "00000000-0000-0000-0000-000000000702"
APPROVAL_ID = "00000000-0000-0000-0000-000000000801"
WORK_ITEM_ID = "00000000-0000-0000-0000-000000000901"

BASE_INSTRUCTIONS = "reviewed base instructions"
CANDIDATE_INSTRUCTIONS = "approved candidate instructions"
BASE_SHA256 = hashlib.sha256(BASE_INSTRUCTIONS.encode("utf-8")).hexdigest()
CANDIDATE_SHA256 = hashlib.sha256(CANDIDATE_INSTRUCTIONS.encode("utf-8")).hexdigest()
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
SECOND_REVISION_ID = "00000000-0000-0000-0000-000000000104"
ROLLBACK_APPROVAL_ID = "00000000-0000-0000-0000-000000000802"


def _config(db_path: Path) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def _revision(db_path: Path) -> str | None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    finally:
        engine.dispose()


def _trigger_names(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).all()
        return {row[0] for row in rows}
    finally:
        engine.dispose()


def _columns(db_path: Path, table: str) -> set[str]:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        return {column["name"] for column in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def _schema_fingerprint(db_path: Path) -> tuple[tuple[tuple[str, str, str], ...], ...]:
    """The full sqlite_master DDL: every table, index, trigger and view."""
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            sql = text(
                "SELECT name, type, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index', 'trigger', 'view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name, sql"
            )
            master = tuple((row[0], row[1], row[2]) for row in connection.execute(sql))
        return (master,)
    finally:
        engine.dispose()


def _assert_0029_schema_present(db_path: Path) -> None:
    assert _trigger_names(db_path) == {
        "ck_skill_active_pointer_authority",
        "ck_consumed_skill_approval_success",
        "ck_promoted_revision_success",
        "ck_generated_revision_canonical_approval",
    }
    assert _columns(db_path, "skill_improvement_work_items")  # table still exists
    assert "comparison_report" in _columns(db_path, "skill_candidate_evaluations")
    assert "promotion_request_id" in _columns(db_path, "skill_revisions")
    assert "target_content_sha256" in _columns(db_path, "skill_evaluation_runs")
    assert "runtime_metadata" in _columns(db_path, "skill_evaluation_runs")
    assert "call_usage" in _columns(db_path, "skill_evaluation_runs")
    assert "subject_kind" in _columns(db_path, "approval_requests")
    assert "subject_id" in _columns(db_path, "approval_requests")
    assert "approval_request_id" in _columns(db_path, "skill_promotion_requests")
    assert "target_revision_id" in _columns(db_path, "skill_promotion_requests")


def _seed_base_skill(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO skills (id, key, display_name, description, status, active_revision_id, "
            "created_at, updated_at) VALUES "
            "(:id, 'downgrade.preflight', 'Downgrade', '', 'active', :revision_id, :at, :at)"
        ),
        {"id": SKILL_ID, "revision_id": BASE_REVISION_ID, "at": AT},
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
            "INSERT INTO skill_improvement_proposals ("
            "id, skill_id, base_revision_id, status, trigger_kind, evidence_snapshot_hash, "
            "proposed_instructions, proposed_content_sha256, rationale, generator_version, "
            "created_at, evidence_snapshot_id"
            ") VALUES "
            "(:id, :skill_id, :base_revision_id, 'ready_for_review', 'manual', :evidence_hash, "
            ":instructions, :content_sha256, 'reviewed', 'generator', :at, :snapshot_id)"
        ),
        {
            "id": PROPOSAL_ID,
            "skill_id": SKILL_ID,
            "base_revision_id": BASE_REVISION_ID,
            "evidence_hash": EVIDENCE_SHA256,
            "instructions": CANDIDATE_INSTRUCTIONS,
            "content_sha256": CANDIDATE_SHA256,
            "snapshot_id": SNAPSHOT_ID,
            "at": AT,
        },
    )


def _seed_evaluation_runs(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO skill_evaluation_suites ("
            "id, skill_id, name, description, status, created_at, updated_at"
            ") VALUES (:id, :skill_id, 'suite', '', 'active', :at, :at)"
        ),
        {"id": "00000000-0000-0000-0000-000000000301", "skill_id": SKILL_ID, "at": AT},
    )
    connection.execute(
        text(
            "INSERT INTO skill_evaluation_runs ("
            "id, suite_id, skill_id, revision_id, proposal_id, status, evaluator_version, "
            "started_at, completed_at, aggregate_result, suite_snapshot, runtime_fingerprint, "
            "target_content_sha256, runtime_metadata"
            ") VALUES "
            "(:baseline_id, :suite_id, :skill_id, :revision_id, NULL, 'succeeded', 'test', "
            ":at, :at, '{}', '{}', :baseline_fp, :base_sha, '{}'), "
            "(:candidate_id, :suite_id, :skill_id, NULL, :proposal_id, 'succeeded', 'test', "
            ":at, :at, '{}', '{}', :candidate_fp, :candidate_sha, '{}')"
        ),
        {
            "baseline_id": BASELINE_RUN_ID,
            "candidate_id": CANDIDATE_RUN_ID,
            "suite_id": "00000000-0000-0000-0000-000000000301",
            "skill_id": SKILL_ID,
            "revision_id": BASE_REVISION_ID,
            "proposal_id": PROPOSAL_ID,
            "baseline_fp": "d" * 64,
            "candidate_fp": "e" * 64,
            "base_sha": BASE_SHA256,
            "candidate_sha": CANDIDATE_SHA256,
            "at": AT,
        },
    )


def _seed_candidate_evaluation(connection: Connection) -> None:
    """Seed the base skill and one candidate evaluation row (0029-only report)."""
    _seed_base_skill(connection)
    _seed_evaluation_runs(connection)
    connection.execute(
        text(
            "INSERT INTO skill_candidate_evaluations ("
            "id, proposal_id, baseline_evaluation_run_id, candidate_evaluation_run_id, "
            "comparison_policy_version, result, recommendation, score_delta, regression_count, "
            "improvement_count, inconclusive_count, report_sha256, created_at, comparison_report"
            ") VALUES (:id, :proposal_id, :baseline_id, :candidate_id, 'comparison-v1', "
            "'better', 'eligible', 1.0, 0, 1, 0, :report_sha, :at, :report)"
        ),
        {
            "id": EVALUATION_ID,
            "proposal_id": PROPOSAL_ID,
            "baseline_id": BASELINE_RUN_ID,
            "candidate_id": CANDIDATE_RUN_ID,
            "report_sha": REPORT_SHA256,
            "report": REPORT_JSON,
            "at": AT,
        },
    )


def _seed_promotion_chain(connection: Connection) -> None:
    _seed_candidate_evaluation(connection)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"version": 1, "promotion_request_id": PROMOTION_ID},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
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
            "id": APPROVAL_ID,
            "promotion_id": PROMOTION_ID,
            "requested_input": json.dumps(
                {"promotion_request_id": PROMOTION_ID}, sort_keys=True, separators=(",", ":")
            ),
            "authorization_fingerprint": fingerprint,
            "at": AT,
        },
    )
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
            ":target_revision_id, 2, :authorization_fingerprint, 'pending', :at, NULL, NULL, NULL, "
            ":approval_id"
            ")"
        ),
        {
            "id": PROMOTION_ID,
            "proposal_id": PROPOSAL_ID,
            "skill_id": SKILL_ID,
            "base_revision_id": BASE_REVISION_ID,
            "candidate_sha256": CANDIDATE_SHA256,
            "candidate_evaluation_id": EVALUATION_ID,
            "comparison_report_sha256": REPORT_SHA256,
            "target_revision_id": TARGET_REVISION_ID,
            "authorization_fingerprint": fingerprint,
            "approval_id": APPROVAL_ID,
            "at": AT,
        },
    )


def _seed_generated_revision(connection: Connection) -> None:
    """Seed one generated skill revision.

    The 0029 canonical-approval trigger guards generated INSERTs.  The preflight
    only needs the row to exist; save the trigger DDL, drop it briefly to insert
    the revision, then restore it.  The schema fingerprint is captured after
    this round-trip, so the restored trigger is what the downgrade must preserve.
    """
    _seed_promotion_chain(connection)
    trigger_sql = connection.execute(
        text(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = "
            "'ck_generated_revision_canonical_approval'"
        )
    ).scalar_one()
    connection.exec_driver_sql("DROP TRIGGER ck_generated_revision_canonical_approval")
    connection.execute(
        text(
            "INSERT INTO skill_revisions ("
            "id, skill_id, version, instructions, content_sha256, source_kind, created_at, "
            "promotion_request_id"
            ") VALUES (:id, :skill_id, 2, :instructions, :content_sha256, 'generated', :at, "
            ":promotion_request_id)"
        ),
        {
            "id": TARGET_REVISION_ID,
            "skill_id": SKILL_ID,
            "instructions": CANDIDATE_INSTRUCTIONS,
            "content_sha256": CANDIDATE_SHA256,
            "promotion_request_id": PROMOTION_ID,
            "at": AT,
        },
    )
    connection.exec_driver_sql(trigger_sql)


def _seed_promotion_request(connection: Connection) -> None:
    _seed_promotion_chain(connection)


def _seed_rollback_request(connection: Connection) -> None:
    _seed_base_skill(connection)
    connection.execute(
        text(
            "INSERT INTO skill_revisions ("
            "id, skill_id, version, instructions, content_sha256, source_kind, created_at, "
            "promotion_request_id"
            ") VALUES (:id, :skill_id, 2, :instructions, :content_sha256, 'operator', :at, NULL)"
        ),
        {
            "id": SECOND_REVISION_ID,
            "skill_id": SKILL_ID,
            "instructions": "rolled-back instructions",
            "content_sha256": hashlib.sha256(b"rolled-back instructions").hexdigest(),
            "at": AT,
        },
    )
    fingerprint = hashlib.sha256(b"rollback-fingerprint").hexdigest()
    connection.execute(
        text(
            "INSERT INTO approval_requests ("
            "id, run_id, step_id, category, summary, reason, requested_action, requested_input, "
            "status, requested_at, expires_at, resolved_at, resolution_note, resolver, "
            "authorization_fingerprint, consumed_at, subject_kind, subject_id"
            ") VALUES ("
            ":id, NULL, NULL, 'other', 'Approve Skill rollback', 'reviewed', 'skill.rollback', "
            "'{}', 'approved', :at, NULL, :at, NULL, 'operator', "
            ":authorization_fingerprint, NULL, 'skill_rollback', :rollback_id"
            ")"
        ),
        {
            "id": ROLLBACK_APPROVAL_ID,
            "rollback_id": ROLLBACK_ID,
            "authorization_fingerprint": fingerprint,
            "at": AT,
        },
    )
    connection.execute(
        text(
            "INSERT INTO skill_rollback_requests ("
            "id, skill_id, expected_current_revision_id, target_revision_id, reason, "
            "authorization_fingerprint, status, created_at, resolved_at, resolver, "
            "approval_request_id"
            ") VALUES ("
            ":id, :skill_id, :current_revision_id, :target_revision_id, 'test rollback', "
            ":authorization_fingerprint, 'pending', :at, NULL, NULL, :approval_id"
            ")"
        ),
        {
            "id": ROLLBACK_ID,
            "skill_id": SKILL_ID,
            "current_revision_id": BASE_REVISION_ID,
            "target_revision_id": SECOND_REVISION_ID,
            "authorization_fingerprint": fingerprint,
            "approval_id": ROLLBACK_APPROVAL_ID,
            "at": AT,
        },
    )


def _seed_approval_subject_only(connection: Connection) -> None:
    """A skill_promotion approval that is not referenced by any request."""
    fingerprint = hashlib.sha256(b"approval-only-fp").hexdigest()
    connection.execute(
        text(
            "INSERT INTO approval_requests ("
            "id, run_id, step_id, category, summary, reason, requested_action, requested_input, "
            "status, requested_at, expires_at, resolved_at, resolution_note, resolver, "
            "authorization_fingerprint, consumed_at, subject_kind, subject_id"
            ") VALUES ("
            ":id, NULL, NULL, 'other', 'Approve Skill promotion', 'reviewed', 'skill.promote', "
            "'{}', 'pending', :at, NULL, NULL, NULL, 'operator', "
            ":authorization_fingerprint, NULL, 'skill_promotion', :subject_id"
            ")"
        ),
        {
            "id": "00000000-0000-0000-0000-000000000804",
            "subject_id": "00000000-0000-0000-0000-000000000805",
            "authorization_fingerprint": fingerprint,
            "at": AT,
        },
    )


def _seed_work_item(connection: Connection) -> None:
    _seed_base_skill(connection)
    connection.execute(
        text(
            "INSERT INTO skill_improvement_work_items ("
            "id, skill_id, state, proposal_id, attempt_count, next_attempt_at, claimed_by, "
            "claim_token, claim_generation, lease_expires_at, failure_code, failure_detail, "
            "created_at, updated_at"
            ") VALUES ("
            ":id, :skill_id, 'failed', NULL, 1, :at, NULL, NULL, 0, NULL, 'timeout', NULL, "
            ":at, :at)"
        ),
        {"id": WORK_ITEM_ID, "skill_id": SKILL_ID, "at": AT},
    )


def _seed_evaluation_run(connection: Connection) -> None:
    """Seed one evaluation run holding 0029-only runtime configuration."""
    _seed_base_skill(connection)
    _seed_evaluation_runs(connection)


# Each incompatible category seeds exactly one representative row.
INCOMPATIBLE_SEEDERS = [
    pytest.param(_seed_generated_revision, id="generated_skill_revision"),
    pytest.param(_seed_promotion_request, id="skill_promotion_request"),
    pytest.param(_seed_rollback_request, id="skill_rollback_request"),
    pytest.param(_seed_approval_subject_only, id="approval_subject_skill"),
    pytest.param(_seed_work_item, id="skill_improvement_work_item"),
    pytest.param(_seed_evaluation_run, id="evaluation_run"),
    pytest.param(_seed_candidate_evaluation, id="candidate_evaluation"),
]


@pytest.mark.parametrize("seeder", INCOMPATIBLE_SEEDERS)
def test_downgrade_aborts_and_preserves_schema_when_incompatible_data_exists(
    tmp_path: Path, seeder: SeedFn
) -> None:
    db_path = tmp_path / "incompatible.db"
    command.upgrade(_config(db_path), "0029")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            seeder(connection)
    finally:
        engine.dispose()

    schema_before = _schema_fingerprint(db_path)
    _assert_0029_schema_present(db_path)
    assert _revision(db_path) == "0029"

    with pytest.raises(RuntimeError):
        command.downgrade(_config(db_path), "0028")

    # No schema mutation: same sqlite_master, same triggers, same columns.
    assert _revision(db_path) == "0029"
    assert _schema_fingerprint(db_path) == schema_before
    _assert_0029_schema_present(db_path)
    work_columns = _columns(db_path, "skill_improvement_work_items")
    assert {
        "id",
        "skill_id",
        "state",
        "proposal_id",
        "attempt_count",
        "next_attempt_at",
        "claimed_by",
        "claim_token",
        "claim_generation",
        "lease_expires_at",
        "failure_code",
        "failure_detail",
        "created_at",
        "updated_at",
    } <= work_columns


def test_downgrade_succeeds_on_compatible_empty_database(tmp_path: Path) -> None:
    db_path = tmp_path / "compatible.db"
    command.upgrade(_config(db_path), "0029")
    assert _revision(db_path) == "0029"

    command.downgrade(_config(db_path), "0028")

    assert _revision(db_path) == "0028"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        assert "skill_improvement_work_items" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_downgrade_succeeds_after_incompatible_data_is_removed(tmp_path: Path) -> None:
    db_path = tmp_path / "cleaned.db"
    command.upgrade(_config(db_path), "0029")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            _seed_work_item(connection)
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError):
        command.downgrade(_config(db_path), "0028")
    assert _revision(db_path) == "0029"

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM skill_improvement_work_items"))
    finally:
        engine.dispose()

    command.downgrade(_config(db_path), "0028")
    assert _revision(db_path) == "0028"
