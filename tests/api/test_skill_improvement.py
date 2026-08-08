from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import cast

from fastapi import FastAPI
from starlette.testclient import TestClient

from friday.domain import (
    RunId,
    RunSkillBinding,
    RunSkillResolution,
    RunSkillResolutionId,
    SkillEvidenceSnapshot,
    SkillEvidenceSnapshotId,
    SkillFeedbackRating,
    SkillId,
    SkillImprovementProposal,
    SkillImprovementProposalId,
    SkillProposalStatus,
    SkillRevisionId,
    SkillRunFeedback,
    SkillRunFeedbackId,
    SkillUsageOutcome,
    SkillUsageRecord,
    SkillUsageRecordId,
    TaskId,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)

_MISSING = "00000000-0000-4000-8000-000000000099"


class _Clock:
    def now(self) -> datetime:
        return NOW


def _create_skill(client: TestClient, *, key: str) -> dict[str, object]:
    response = client.post(
        "/v1/skills",
        json={"key": key, "display_name": "Improvement", "description": "desc"},
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def _create_revision(
    client: TestClient, skill_id: str, *, instructions: str = "base"
) -> dict[str, object]:
    response = client.post(
        f"/v1/skills/{skill_id}/revisions",
        json={"instructions": instructions, "source_kind": "operator"},
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def _activate(client: TestClient, skill_id: str, revision_id: str) -> None:
    assert client.post(f"/v1/skills/{skill_id}/revisions/{revision_id}/activate").status_code == 200


def _create_suite(client: TestClient, skill_id: str) -> dict[str, object]:
    response = client.post(
        f"/v1/skills/{skill_id}/evaluation-suites",
        json={
            "name": "suite",
            "description": "",
            "cases": [
                {
                    "input": "x",
                    "expected_properties": {"value": "good"},
                    "grading_kind": "exact_match",
                }
            ],
        },
    )
    assert response.status_code == 200
    return cast(dict[str, object], response.json())


def _first_case_id(suite: dict[str, object]) -> str:
    cases = cast(list[dict[str, object]], suite["cases"])
    return str(cases[0]["id"])


def _run_evaluation(
    client: TestClient,
    suite_id: str,
    *,
    revision_id: str,
    case_id: str,
    output: str,
) -> dict[str, object]:
    response = client.post(
        f"/v1/skills/evaluation-suites/{suite_id}/runs",
        json={"revision_id": revision_id, "outputs": {case_id: output}},
    )
    assert response.status_code == 200
    return cast(dict[str, object], response.json())


def _seed_snapshot(app: FastAPI, skill_id: str, base_revision_id: str) -> SkillEvidenceSnapshot:
    snapshot = SkillEvidenceSnapshot.new(
        id=SkillEvidenceSnapshotId.new(),
        skill_id=SkillId.parse(skill_id),
        base_revision_id=SkillRevisionId.parse(base_revision_id),
        evidence={"items": [{"id": "evidence-a"}]},
        created_at=NOW,
    )
    with app.state.uow_factory() as uow:
        uow.skill_evidence_snapshots.add(snapshot)
        uow.commit()
    return snapshot


def _seed_proposal(
    app: FastAPI,
    skill_id: str,
    base_revision_id: str,
    snapshot: SkillEvidenceSnapshot,
    *,
    status: SkillProposalStatus = SkillProposalStatus.READY_FOR_EVALUATION,
    instructions: str = "better",
) -> SkillImprovementProposal:
    proposal = SkillImprovementProposal(
        id=SkillImprovementProposalId.new(),
        skill_id=SkillId.parse(skill_id),
        base_revision_id=SkillRevisionId.parse(base_revision_id),
        status=status,
        trigger_kind="manual",
        evidence_snapshot_id=snapshot.id,
        evidence_snapshot_hash=snapshot.content_sha256,
        proposed_instructions=instructions,
        proposed_content_sha256=hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        rationale="r",
        generator_version="brain-candidate-generator-v2",
        candidate_prompt_version="candidate-prompt-v1",
        candidate_prompt_sha256="a" * 64,
        created_at=NOW,
    )
    with app.state.uow_factory() as uow:
        uow.skill_improvement_proposals.add(proposal)
        uow.commit()
    return proposal


def test_skill_list_and_usage_records(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client, key="improve.usage")
        base = _create_revision(client, str(skill["id"]), instructions="base")
        task = client.post("/v1/tasks", json={"title": "Ship"}).json()
        started = client.post(f"/v1/tasks/{task['id']}/runs")
        assert started.status_code == 201
        run_id = started.json()["run_id"]

        resolution_id = RunSkillResolutionId.new()
        with app.state.uow_factory() as uow:
            uow.run_skill_resolutions.add(
                RunSkillResolution(resolution_id, RunId.parse(run_id), NOW)
            )
            uow.run_skill_bindings.add_all(
                [
                    RunSkillBinding(
                        RunId.parse(run_id),
                        SkillId.parse(str(skill["id"])),
                        SkillRevisionId.parse(str(base["id"])),
                        position=1,
                    )
                ]
            )
            uow.commit()

        record = SkillUsageRecord(
            id=SkillUsageRecordId.new(),
            run_id=RunId.parse(run_id),
            task_id=TaskId.parse(str(task["id"])),
            skill_id=SkillId.parse(str(skill["id"])),
            revision_id=SkillRevisionId.parse(str(base["id"])),
            position=1,
            resolution_id=str(resolution_id),
            execution_id=RunId.parse(run_id),
            attempt_number=1,
            started_at=NOW,
            completed_at=NOW,
            outcome=SkillUsageOutcome.SUCCEEDED,
            failure_code=None,
            tool_call_count=2,
            approval_count=0,
            duration_ms=100,
            created_at=NOW,
        )
        with app.state.uow_factory() as uow:
            uow.skill_usage_records.add(record)
            uow.commit()

        listed = client.get("/v1/skills")
        usage = client.get(f"/v1/skills/{skill['id']}/usage")
        missing_usage = client.get(f"/v1/skills/{_MISSING}/usage")

    assert listed.status_code == 200
    assert str(skill["id"]) in [item["id"] for item in listed.json()["items"]]
    assert usage.status_code == 200
    assert usage.json()[0]["run_id"] == run_id
    assert usage.json()[0]["outcome"] == "succeeded"
    assert missing_usage.status_code == 404
    assert missing_usage.json()["error"]["type"] == "skill_not_found"


def test_evidence_snapshot_read_and_404(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client, key="improve.snapshot")
        base = _create_revision(client, str(skill["id"]), instructions="base")
        snapshot = _seed_snapshot(app, str(skill["id"]), str(base["id"]))

        fetched = client.get(f"/v1/skills/evidence-snapshots/{snapshot.id}")
        missing = client.get(f"/v1/skills/evidence-snapshots/{_MISSING}")

    assert fetched.status_code == 200
    assert fetched.json()["skill_id"] == str(skill["id"])
    assert fetched.json()["evidence"]["version"] == 1
    assert missing.status_code == 404
    assert missing.json()["error"]["type"] == "skill_evidence_snapshot_not_found"


def test_improvement_proposal_list_get_cancel(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client, key="improve.proposals")
        base = _create_revision(client, str(skill["id"]), instructions="base")
        snapshot = _seed_snapshot(app, str(skill["id"]), str(base["id"]))
        proposal = _seed_proposal(app, str(skill["id"]), str(base["id"]), snapshot)

        listed = client.get(f"/v1/skills/{skill['id']}/improvement-proposals")
        fetched = client.get(f"/v1/skills/improvement-proposals/{proposal.id}")
        cancelled = client.post(f"/v1/skills/improvement-proposals/{proposal.id}/cancel")
        missing = client.get(f"/v1/skills/improvement-proposals/{_MISSING}")
        missing_skill = client.get(f"/v1/skills/{_MISSING}/improvement-proposals")

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == str(proposal.id)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "ready_for_evaluation"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert missing.status_code == 404
    assert missing.json()["error"]["type"] == "skill_improvement_proposal_not_found"
    assert missing_skill.status_code == 404


def test_proposal_evaluate_get_comparison_and_promote_flow(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client, key="improve.flow")
        base = _create_revision(client, str(skill["id"]), instructions="base")
        _activate(client, str(skill["id"]), str(base["id"]))
        suite = _create_suite(client, str(skill["id"]))
        case_id = _first_case_id(suite)
        baseline = _run_evaluation(
            client, str(suite["id"]), revision_id=str(base["id"]), case_id=case_id, output="bad"
        )
        snapshot = _seed_snapshot(app, str(skill["id"]), str(base["id"]))
        proposal = _seed_proposal(app, str(skill["id"]), str(base["id"]), snapshot)

        evaluated = client.post(
            f"/v1/skills/improvement-proposals/{proposal.id}/evaluate",
            json={
                "baseline_evaluation_run_id": baseline["id"],
                "candidate_outputs": {case_id: "good"},
            },
        )
        fetched_comparison = client.get(
            f"/v1/skills/improvement-proposals/{proposal.id}/evaluation"
        )
        requested = client.post(f"/v1/skills/improvement-proposals/{proposal.id}/request-promotion")
        promotion = requested.json()
        fetched_promotion = client.get(f"/v1/skills/promotions/{promotion['id']}")
        approved = client.post(
            f"/v1/approvals/{promotion['approval_request_id']}/approve",
            json={"resolver": "operator"},
        )
        assert approved.status_code == 200
        executed = client.post(f"/v1/skills/promotions/{promotion['id']}/approve")
        skill_after = client.get(f"/v1/skills/{skill['id']}")

    assert evaluated.status_code == 200
    assert evaluated.json()["recommendation"] == "eligible"
    assert evaluated.json()["result"] == "better"
    assert fetched_comparison.status_code == 200
    assert fetched_comparison.json()["score_delta"] == 1.0
    assert requested.status_code == 200
    assert promotion["status"] == "pending"
    assert fetched_promotion.status_code == 200
    assert fetched_promotion.json()["status"] == "pending"
    assert executed.status_code == 200
    assert executed.json()["status"] == "promoted"
    promoted_revision_id = executed.json()["promoted_revision_id"]
    assert promoted_revision_id is not None
    assert skill_after.json()["active_revision_id"] == promoted_revision_id


def test_promotion_reject_and_cancel(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client, key="improve.reject")
        base = _create_revision(client, str(skill["id"]), instructions="base")
        _activate(client, str(skill["id"]), str(base["id"]))
        suite = _create_suite(client, str(skill["id"]))
        case_id = _first_case_id(suite)
        baseline = _run_evaluation(
            client, str(suite["id"]), revision_id=str(base["id"]), case_id=case_id, output="bad"
        )
        snapshot = _seed_snapshot(app, str(skill["id"]), str(base["id"]))
        proposal = _seed_proposal(app, str(skill["id"]), str(base["id"]), snapshot)
        client.post(
            f"/v1/skills/improvement-proposals/{proposal.id}/evaluate",
            json={
                "baseline_evaluation_run_id": baseline["id"],
                "candidate_outputs": {case_id: "good"},
            },
        )
        promotion = client.post(
            f"/v1/skills/improvement-proposals/{proposal.id}/request-promotion"
        ).json()

        rejected = client.post(
            f"/v1/skills/promotions/{promotion['id']}/reject", json={"resolver": "reviewer"}
        )
        missing_promotion = client.get(f"/v1/skills/promotions/{_MISSING}")

        skill_b = _create_skill(client, key="improve.cancel")
        base_b = _create_revision(client, str(skill_b["id"]), instructions="base")
        _activate(client, str(skill_b["id"]), str(base_b["id"]))
        suite_b = _create_suite(client, str(skill_b["id"]))
        case_id_b = _first_case_id(suite_b)
        baseline_b = _run_evaluation(
            client,
            str(suite_b["id"]),
            revision_id=str(base_b["id"]),
            case_id=case_id_b,
            output="bad",
        )
        snapshot_b = _seed_snapshot(app, str(skill_b["id"]), str(base_b["id"]))
        proposal_b = _seed_proposal(app, str(skill_b["id"]), str(base_b["id"]), snapshot_b)
        client.post(
            f"/v1/skills/improvement-proposals/{proposal_b.id}/evaluate",
            json={
                "baseline_evaluation_run_id": baseline_b["id"],
                "candidate_outputs": {case_id_b: "good"},
            },
        )
        promotion_b = client.post(
            f"/v1/skills/improvement-proposals/{proposal_b.id}/request-promotion"
        ).json()
        cancelled = client.post(f"/v1/skills/promotions/{promotion_b['id']}/cancel")

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert missing_promotion.status_code == 404
    assert missing_promotion.json()["error"]["type"] == "skill_promotion_request_not_found"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def _seed_rollback_skill(
    app: FastAPI, client: TestClient, *, key: str
) -> tuple[dict[str, object], dict[str, object]]:
    skill = _create_skill(client, key=key)
    v1 = _create_revision(client, str(skill["id"]), instructions="v1")
    v2 = _create_revision(client, str(skill["id"]), instructions="v2")
    _activate(client, str(skill["id"]), str(v2["id"]))
    return skill, v1


def test_rollback_request_get_approve_reject_cancel(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill, v1 = _seed_rollback_skill(app, client, key="rollback.approve")
        requested = client.post(
            f"/v1/skills/{skill['id']}/request-rollback",
            json={"target_revision_id": str(v1["id"]), "reason": "restore known behavior"},
        )
        rollback = requested.json()
        fetched = client.get(f"/v1/skills/rollbacks/{rollback['id']}")
        approved = client.post(
            f"/v1/approvals/{rollback['approval_request_id']}/approve",
            json={"resolver": "operator"},
        )
        assert approved.status_code == 200
        executed = client.post(f"/v1/skills/rollbacks/{rollback['id']}/approve")
        skill_after = client.get(f"/v1/skills/{skill['id']}")
        missing = client.get(f"/v1/skills/rollbacks/{_MISSING}")

    assert requested.status_code == 200
    assert rollback["status"] == "pending"
    assert fetched.status_code == 200
    assert executed.status_code == 200
    assert executed.json()["status"] == "completed"
    assert skill_after.json()["active_revision_id"] == str(v1["id"])
    assert missing.status_code == 404
    assert missing.json()["error"]["type"] == "skill_rollback_request_not_found"


def test_rollback_reject_and_cancel(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill, v1 = _seed_rollback_skill(app, client, key="rollback.reject")
        rollback = client.post(
            f"/v1/skills/{skill['id']}/request-rollback",
            json={"target_revision_id": str(v1["id"]), "reason": "restore known behavior"},
        ).json()
        rejected = client.post(
            f"/v1/skills/rollbacks/{rollback['id']}/reject", json={"resolver": "reviewer"}
        )

        skill_b, v1_b = _seed_rollback_skill(app, client, key="rollback.cancel")
        rollback_b = client.post(
            f"/v1/skills/{skill_b['id']}/request-rollback",
            json={"target_revision_id": str(v1_b["id"]), "reason": "restore known behavior"},
        ).json()
        cancelled = client.post(f"/v1/skills/rollbacks/{rollback_b['id']}/cancel")

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_improvement_policy_put_get_and_run_now(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client, key="improve.policy")
        suite = _create_suite(client, str(skill["id"]))
        body = {
            "enabled": True,
            "evaluation_suite_id": str(suite["id"]),
            "generator_version": "brain-candidate-generator-v2",
            "comparison_policy_version": "comparison-v1",
        }
        put = client.put(f"/v1/skills/{skill['id']}/improvement-policy", json=body)
        fetched = client.get(f"/v1/skills/{skill['id']}/improvement-policy")
        due = client.post(f"/v1/skills/{skill['id']}/improvement-policy/run-now")
        missing = client.get(f"/v1/skills/{_MISSING}/improvement-policy")

    assert put.status_code == 200
    assert put.json()["skill_id"] == str(skill["id"])
    assert put.json()["enabled"] is True
    assert fetched.status_code == 200
    assert fetched.json()["evaluation_suite_id"] == str(suite["id"])
    assert due.status_code == 200
    assert due.json() == {"due": False}
    assert missing.status_code == 404


def test_evaluation_suite_list_get_run_and_404s(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client, key="improve.suite")
        base = _create_revision(client, str(skill["id"]), instructions="base")
        suite = _create_suite(client, str(skill["id"]))
        case_id = _first_case_id(suite)
        run = _run_evaluation(
            client, str(suite["id"]), revision_id=str(base["id"]), case_id=case_id, output="good"
        )

        listed = client.get(f"/v1/skills/{skill['id']}/evaluation-suites")
        fetched = client.get(f"/v1/skills/evaluation-suites/{suite['id']}")
        run_fetched = client.get(f"/v1/skills/evaluation-runs/{run['id']}")
        missing_suite = client.get(f"/v1/skills/evaluation-suites/{_MISSING}")
        missing_run = client.get(f"/v1/skills/evaluation-runs/{_MISSING}")
        missing_skill = client.get(f"/v1/skills/{_MISSING}/evaluation-suites")

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == str(suite["id"])
    assert fetched.status_code == 200
    assert fetched.json()["cases"][0]["id"] == case_id
    assert run_fetched.status_code == 200
    assert run_fetched.json()["case_results"][0]["score"] == 1.0
    assert missing_suite.status_code == 404
    assert missing_suite.json()["error"]["type"] == "skill_evaluation_suite_not_found"
    assert missing_run.status_code == 404
    assert missing_run.json()["error"]["type"] == "skill_evaluation_run_not_found"
    assert missing_skill.status_code == 404


def test_skill_usage_feedback_roundtrip(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client, key="improve.feedback")
        revision = _create_revision(client, str(skill["id"]), instructions="base")
        task = client.post("/v1/tasks", json={"title": "Ship"}).json()
        started = client.post(f"/v1/tasks/{task['id']}/runs")
        run_id = started.json()["run_id"]

        with app.state.uow_factory() as uow:
            uow.run_skill_bindings.add_all(
                [
                    RunSkillBinding(
                        RunId.parse(run_id),
                        SkillId.parse(str(skill["id"])),
                        SkillRevisionId.parse(str(revision["id"])),
                        position=1,
                    )
                ]
            )
            uow.commit()

        feedback = SkillRunFeedback(
            id=SkillRunFeedbackId.new(),
            run_id=RunId.parse(run_id),
            skill_id=SkillId.parse(str(skill["id"])),
            revision_id=SkillRevisionId.parse(str(revision["id"])),
            rating=SkillFeedbackRating.HELPFUL,
            note="worked",
            created_by="operator",
            created_at=NOW,
        )
        with app.state.uow_factory() as uow:
            uow.skill_run_feedback.add(feedback)
            uow.commit()

        listed = client.get(f"/v1/runs/{run_id}/skills/{skill['id']}/feedback")
        missing_run = client.get(f"/v1/runs/{_MISSING}/skills/{skill['id']}/feedback")
        missing_skill = client.get(f"/v1/runs/{run_id}/skills/{_MISSING}/feedback")

    assert listed.status_code == 200
    assert listed.json()[0]["rating"] == "helpful"
    assert missing_run.status_code == 404
    assert missing_run.json()["error"]["type"] == "run_not_found"
    assert missing_skill.status_code == 404
    assert missing_skill.json()["error"]["type"] == "skill_not_found"
