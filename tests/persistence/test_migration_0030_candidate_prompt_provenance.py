"""The 0030 candidate-prompt-provenance migration must never fabricate or
destroy prompt provenance.

An existing 0029 proposal cannot be backfilled with a truthful candidate prompt
version or sha256 (the pre-0030 generator could receive non-persisted caller
controlled summary inputs), and 0030's provenance columns cannot be dropped
while proposals still hold them.  These tests prove the upgrade and downgrade
abort before any DDL on non-empty proposal tables, and that the schema stays
byte-identical when they do.  An empty compatible database still upgrades and
downgrades normally.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = "2026-01-02 03:00:00"

SKILL_ID = "00000000-0000-0000-0000-000000000001"
BASE_REVISION_ID = "00000000-0000-0000-0000-000000000101"
TARGET_REVISION_ID = "00000000-0000-0000-0000-000000000102"
SNAPSHOT_ID = "00000000-0000-0000-0000-000000000201"
PROPOSAL_ID = "00000000-0000-0000-0000-000000000401"
BASELINE_RUN_ID = "00000000-0000-0000-0000-000000000501"
CANDIDATE_RUN_ID = "00000000-0000-0000-0000-000000000502"
EVALUATION_ID = "00000000-0000-0000-0000-000000000601"
PROMOTION_ID = "00000000-0000-0000-0000-000000000701"
APPROVAL_ID = "00000000-0000-0000-0000-000000000801"
VIOLATING_REVISION_ID = "00000000-0000-0000-0000-000000000103"

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
PROMPT_VERSION = "candidate-prompt-builder-v1"
PROMPT_SHA256 = "f" * 64

_AUTHORITY_TRIGGERS = {
    "ck_skill_active_pointer_authority",
    "ck_consumed_skill_approval_success",
    "ck_promoted_revision_success",
    "ck_generated_revision_canonical_approval",
}
_PROVENANCE_COLUMNS = {"candidate_prompt_version", "candidate_prompt_sha256"}


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
    assert _trigger_names(db_path) == _AUTHORITY_TRIGGERS
    assert not _PROVENANCE_COLUMNS & _columns(db_path, "skill_improvement_proposals")


def _assert_0030_schema_present(db_path: Path) -> None:
    assert _trigger_names(db_path) == _AUTHORITY_TRIGGERS
    assert _columns(db_path, "skill_improvement_proposals") >= _PROVENANCE_COLUMNS


def _seed_skill_graph(connection: Connection, provenance: tuple[str, str] | None) -> None:
    connection.execute(
        text(
            "INSERT INTO skills (id, key, display_name, description, status, active_revision_id, "
            "created_at, updated_at) VALUES "
            "(:id, 'migration.0030', 'Migration', '', 'active', :revision_id, :at, :at)"
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
    proposal_columns = (
        "id, skill_id, base_revision_id, status, trigger_kind, evidence_snapshot_hash, "
        "proposed_instructions, proposed_content_sha256, rationale, generator_version, "
        "created_at, evidence_snapshot_id"
    )
    proposal_values = (
        ":id, :skill_id, :base_revision_id, 'ready_for_review', 'manual', :evidence_hash, "
        ":instructions, :content_sha256, 'reviewed', 'generator', :at, :snapshot_id"
    )
    params = {
        "id": PROPOSAL_ID,
        "skill_id": SKILL_ID,
        "base_revision_id": BASE_REVISION_ID,
        "evidence_hash": EVIDENCE_SHA256,
        "instructions": CANDIDATE_INSTRUCTIONS,
        "content_sha256": CANDIDATE_SHA256,
        "snapshot_id": SNAPSHOT_ID,
        "at": AT,
    }
    if provenance is not None:
        proposal_columns += ", candidate_prompt_version, candidate_prompt_sha256"
        proposal_values += ", :prompt_version, :prompt_sha256"
        params["prompt_version"] = provenance[0]
        params["prompt_sha256"] = provenance[1]
    connection.execute(
        text(
            f"INSERT INTO skill_improvement_proposals ({proposal_columns}) "
            f"VALUES ({proposal_values})"
        ),
        params,
    )


def _seed_evaluation_chain(connection: Connection) -> None:
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


def _seed_promotion_request(connection: Connection) -> None:
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
    """Insert one real generated target revision for the full authority chain.

    The 0029 canonical-approval trigger guards generated INSERTs.  Save its DDL,
    drop it briefly to insert the revision, then restore it so the schema
    fingerprint captures the exact restored trigger the migration must preserve.
    """
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


def _seed_full_chain(connection: Connection, provenance: tuple[str, str] | None) -> None:
    _seed_skill_graph(connection, provenance)
    _seed_evaluation_chain(connection)
    _seed_promotion_request(connection)
    _seed_generated_revision(connection)


def _assert_authority_triggers_enforce(connection: Connection) -> None:
    """Every 0029 authority trigger must still abort its violating operation."""
    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO skill_revisions ("
                "id, skill_id, version, instructions, content_sha256, source_kind, created_at, "
                "promotion_request_id"
                ") VALUES (:id, :skill_id, 3, :instructions, :content_sha256, 'generated', "
                ":at, NULL)"
            ),
            {
                "id": VIOLATING_REVISION_ID,
                "skill_id": SKILL_ID,
                "instructions": "unguarded generated instructions",
                "content_sha256": hashlib.sha256(b"unguarded generated instructions").hexdigest(),
                "at": AT,
            },
        )
    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                "UPDATE skill_promotion_requests SET status = 'promoted', "
                "promoted_revision_id = :revision_id WHERE id = :id"
            ),
            {"revision_id": "00000000-0000-0000-0000-000000000999", "id": PROMOTION_ID},
        )
    with pytest.raises(IntegrityError):
        connection.execute(
            text("UPDATE approval_requests SET consumed_at = :at WHERE id = :id"),
            {"at": AT, "id": APPROVAL_ID},
        )
    with pytest.raises(IntegrityError):
        connection.execute(
            text("UPDATE skills SET active_revision_id = :revision_id WHERE id = :id"),
            {"revision_id": TARGET_REVISION_ID, "id": SKILL_ID},
        )


def test_upgrade_succeeds_on_empty_0029_database(tmp_path: Path) -> None:
    db_path = tmp_path / "upgrade-empty.db"
    command.upgrade(_config(db_path), "0029")
    assert _revision(db_path) == "0029"

    command.upgrade(_config(db_path), "0030")

    assert _revision(db_path) == "0030"
    _assert_0030_schema_present(db_path)


def test_upgrade_aborts_before_ddl_when_proposal_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "upgrade-proposal.db"
    command.upgrade(_config(db_path), "0029")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            _seed_skill_graph(connection, None)
    finally:
        engine.dispose()

    schema_before = _schema_fingerprint(db_path)
    _assert_0029_schema_present(db_path)

    with pytest.raises(RuntimeError):
        command.upgrade(_config(db_path), "0030")

    assert _revision(db_path) == "0029"
    assert _schema_fingerprint(db_path) == schema_before
    _assert_0029_schema_present(db_path)


def test_rejected_upgrade_keeps_authority_triggers_intact_and_functioning(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "upgrade-triggers.db"
    command.upgrade(_config(db_path), "0029")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            _seed_full_chain(connection, None)
    finally:
        engine.dispose()

    schema_before = _schema_fingerprint(db_path)
    _assert_0029_schema_present(db_path)

    with pytest.raises(RuntimeError):
        command.upgrade(_config(db_path), "0030")

    assert _revision(db_path) == "0029"
    assert _schema_fingerprint(db_path) == schema_before
    _assert_0029_schema_present(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            _assert_authority_triggers_enforce(connection)
    finally:
        engine.dispose()


def test_downgrade_succeeds_on_empty_0030_database(tmp_path: Path) -> None:
    db_path = tmp_path / "downgrade-empty.db"
    command.upgrade(_config(db_path), "0030")
    assert _revision(db_path) == "0030"
    _assert_0030_schema_present(db_path)

    command.downgrade(_config(db_path), "0029")

    assert _revision(db_path) == "0029"
    _assert_0029_schema_present(db_path)


def test_downgrade_aborts_before_ddl_when_proposal_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "downgrade-proposal.db"
    command.upgrade(_config(db_path), "0030")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            _seed_skill_graph(connection, (PROMPT_VERSION, PROMPT_SHA256))
    finally:
        engine.dispose()

    schema_before = _schema_fingerprint(db_path)
    _assert_0030_schema_present(db_path)

    with pytest.raises(RuntimeError):
        command.downgrade(_config(db_path), "0029")

    assert _revision(db_path) == "0030"
    assert _schema_fingerprint(db_path) == schema_before
    _assert_0030_schema_present(db_path)


def test_rejected_downgrade_keeps_provenance_and_authority_triggers(tmp_path: Path) -> None:
    db_path = tmp_path / "downgrade-triggers.db"
    command.upgrade(_config(db_path), "0030")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            _seed_full_chain(connection, (PROMPT_VERSION, PROMPT_SHA256))
    finally:
        engine.dispose()

    schema_before = _schema_fingerprint(db_path)
    _assert_0030_schema_present(db_path)

    with pytest.raises(RuntimeError):
        command.downgrade(_config(db_path), "0029")

    assert _revision(db_path) == "0030"
    assert _schema_fingerprint(db_path) == schema_before
    _assert_0030_schema_present(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            _assert_authority_triggers_enforce(connection)
    finally:
        engine.dispose()


def test_0029_0030_0029_0030_round_trip_succeeds_on_empty_database(tmp_path: Path) -> None:
    db_path = tmp_path / "round-trip.db"
    config = _config(db_path)

    command.upgrade(config, "0029")
    assert _revision(db_path) == "0029"
    _assert_0029_schema_present(db_path)

    command.upgrade(config, "0030")
    assert _revision(db_path) == "0030"
    _assert_0030_schema_present(db_path)

    command.downgrade(config, "0029")
    assert _revision(db_path) == "0029"
    _assert_0029_schema_present(db_path)

    command.upgrade(config, "0030")
    assert _revision(db_path) == "0030"
    _assert_0030_schema_present(db_path)
