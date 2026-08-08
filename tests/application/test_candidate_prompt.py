"""Proves the candidate prompt is byte-for-byte reproducible from persistence.

The persisted evidence snapshot is the sole candidate-input authority: there
is no caller-controlled summary or evidence-id channel, and the exact prompt
sent to the brain adapter can always be rebuilt from persisted rows alone."""

from __future__ import annotations

import hashlib

import pytest

from friday.application.errors import EntityConflict
from friday.application.skill_improvement import (
    CandidateGenerationRequest,
    CreateSkillEvidenceSnapshot,
    CreateSkillImprovementProposal,
    GenerateSkillImprovementProposal,
    build_candidate_prompt,
    candidate_prompt_sha256,
)
from friday.application.skill_registry import CreateSkill, CreateSkillRevision
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import SkillEvidenceSnapshotId, SkillId, SkillRevisionId
from friday.domain.skill import SkillRevisionSourceKind
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

GENERATOR_VERSION = "brain-candidate-generator-v2"


class _CapturingGenerator:
    """Records the request it was called with; never invents a summary."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.requests: list[CandidateGenerationRequest] = []
        self.called = False

    def generate_candidate(self, request: CandidateGenerationRequest) -> str:
        self.called = True
        self.requests.append(request)
        return self._response


def _candidate(instructions: str, evidence_ids: list[str]) -> str:
    import json

    return json.dumps(
        {
            "version": 1,
            "proposed_instructions": instructions,
            "rationale": "addresses the selected evidence",
            "addressed_evidence_ids": evidence_ids,
        }
    )


def test_prompt_is_reproducible_byte_for_byte_from_persisted_rows() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="candidate.reproduce", display_name="P", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="base instructions",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={"items": [{"id": "usage-1", "summary": "frozen"}]},
    )
    generator = _CapturingGenerator(_candidate("improved instructions", ["usage-1"]))
    proposal = GenerateSkillImprovementProposal(
        generator, CreateSkillImprovementProposal(factory, clock)
    ).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        trigger_kind="manual",
        evidence_snapshot_id=snapshot.id,
        evidence_snapshot_hash=snapshot.content_sha256,
        generator_version=GENERATOR_VERSION,
        base_instructions="base instructions",
    )
    assert generator.called
    sent_prompt = build_candidate_prompt(
        base_revision_id=generator.requests[0].base_revision_id,
        base_instructions=generator.requests[0].base_instructions,
        base_content_sha256=generator.requests[0].base_content_sha256,
        snapshot_id=generator.requests[0].snapshot_id,
        snapshot_payload=generator.requests[0].snapshot_payload,
        evidence_snapshot_hash=generator.requests[0].evidence_snapshot_hash,
        generator_config_fingerprint=generator.requests[0].generator_config_fingerprint,
    )

    # Reload only the persisted rows -- no in-memory reference to the
    # original request objects -- and rebuild the prompt independently.
    reloaded_snapshot = uow.skill_evidence_snapshots.get(snapshot.id)
    reloaded_base = uow.skill_revisions.get(base.id)
    reloaded_proposal = uow.skill_improvement_proposals.get(proposal.id)
    assert reloaded_snapshot is not None
    assert reloaded_base is not None
    assert reloaded_proposal is not None
    reconstructed = build_candidate_prompt(
        base_revision_id=reloaded_base.id,
        base_instructions=reloaded_base.instructions,
        base_content_sha256=reloaded_base.content_sha256,
        snapshot_id=reloaded_snapshot.id,
        snapshot_payload=reloaded_snapshot.evidence,
        evidence_snapshot_hash=reloaded_snapshot.content_sha256,
        generator_config_fingerprint=reloaded_proposal.generator_version,
    )
    assert reconstructed == sent_prompt

    # The reconstructed prompt's hash equals the persisted provenance hash.
    assert candidate_prompt_sha256(reconstructed) == reloaded_proposal.candidate_prompt_sha256
    assert reloaded_proposal.candidate_prompt_version == "candidate-prompt-v1"


def test_candidate_generation_request_rejects_unknown_summary_kwargs() -> None:
    with pytest.raises(TypeError):
        CandidateGenerationRequest(  # type: ignore[call-arg]
            base_instructions="x",
            evidence_snapshot_hash="a" * 64,
            snapshot_id=SkillEvidenceSnapshotId.new(),
            snapshot_payload={"version": 1, "entries": []},
            base_revision_id=SkillRevisionId.new(),
            base_content_sha256=hashlib.sha256(b"x").hexdigest(),
            feedback_summaries=(),
        )


def test_candidate_generation_request_rejects_caller_supplied_evidence_ids() -> None:
    with pytest.raises(TypeError):
        CandidateGenerationRequest(  # type: ignore[call-arg]
            base_instructions="x",
            evidence_snapshot_hash="a" * 64,
            snapshot_id=SkillEvidenceSnapshotId.new(),
            snapshot_payload={"version": 1, "entries": []},
            base_revision_id=SkillRevisionId.new(),
            base_content_sha256=hashlib.sha256(b"x").hexdigest(),
            evidence_ids=("x",),
        )


def test_generate_skill_improvement_proposal_rejects_unknown_summary_kwargs() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    generator = _CapturingGenerator(_candidate("does not matter", []))
    with pytest.raises(TypeError):
        GenerateSkillImprovementProposal(
            generator, CreateSkillImprovementProposal(factory, clock)
        ).execute(  # type: ignore[call-arg]
            skill_id=SkillId.new(),
            base_revision_id=SkillRevisionId.new(),
            trigger_kind="manual",
            evidence_snapshot_id=SkillEvidenceSnapshotId.new(),
            evidence_snapshot_hash="a" * 64,
            generator_version=GENERATOR_VERSION,
            base_instructions="x",
            feedback_summaries=(),
            evaluator_summaries=(),
        )
    assert not generator.called


def test_canonical_snapshot_and_prompt_insensitive_to_input_ordering() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="candidate.order", display_name="P", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="base instructions",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    ordered = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={
            "version": 1,
            "entries": [
                {"id": "usage-1", "kind": "manual", "payload": {"a": 1, "b": 2}},
                {"id": "usage-2", "kind": "manual", "payload": {"c": 3}},
            ],
        },
    )
    reordered = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={
            "entries": [
                {"payload": {"b": 2, "a": 1}, "id": "usage-1", "kind": "manual"},
                {"kind": "manual", "id": "usage-2", "payload": {"c": 3}},
            ],
            "version": 1,
        },
    )
    assert ordered.content_sha256 == reordered.content_sha256
    prompt_a = build_candidate_prompt(
        base_revision_id=base.id,
        base_instructions=base.instructions,
        base_content_sha256=base.content_sha256,
        snapshot_id=ordered.id,
        snapshot_payload=ordered.evidence,
        evidence_snapshot_hash=ordered.content_sha256,
        generator_config_fingerprint=GENERATOR_VERSION,
    )
    prompt_b = build_candidate_prompt(
        base_revision_id=base.id,
        base_instructions=base.instructions,
        base_content_sha256=base.content_sha256,
        snapshot_id=ordered.id,
        snapshot_payload=reordered.evidence,
        evidence_snapshot_hash=reordered.content_sha256,
        generator_config_fingerprint=GENERATOR_VERSION,
    )
    assert prompt_a == prompt_b


def test_prompt_hash_changes_when_evidence_or_base_changes() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="candidate.mutate", display_name="P", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="base instructions",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    snapshot_one = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={"items": [{"id": "usage-1"}]},
    )
    snapshot_two = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={"items": [{"id": "usage-1"}, {"id": "usage-2"}]},
    )
    prompt_one = build_candidate_prompt(
        base_revision_id=base.id,
        base_instructions=base.instructions,
        base_content_sha256=base.content_sha256,
        snapshot_id=snapshot_one.id,
        snapshot_payload=snapshot_one.evidence,
        evidence_snapshot_hash=snapshot_one.content_sha256,
        generator_config_fingerprint=GENERATOR_VERSION,
    )
    prompt_two = build_candidate_prompt(
        base_revision_id=base.id,
        base_instructions=base.instructions,
        base_content_sha256=base.content_sha256,
        snapshot_id=snapshot_two.id,
        snapshot_payload=snapshot_two.evidence,
        evidence_snapshot_hash=snapshot_two.content_sha256,
        generator_config_fingerprint=GENERATOR_VERSION,
    )
    assert prompt_one != prompt_two
    assert candidate_prompt_sha256(prompt_one) != candidate_prompt_sha256(prompt_two)

    other_base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="different base instructions",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    prompt_other_base = build_candidate_prompt(
        base_revision_id=other_base.id,
        base_instructions=other_base.instructions,
        base_content_sha256=other_base.content_sha256,
        snapshot_id=snapshot_one.id,
        snapshot_payload=snapshot_one.evidence,
        evidence_snapshot_hash=snapshot_one.content_sha256,
        generator_config_fingerprint=GENERATOR_VERSION,
    )
    assert prompt_other_base != prompt_one


def test_addressed_evidence_ids_restricted_to_exact_persisted_snapshot() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="candidate.restricted", display_name="P", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="base instructions",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={"items": [{"id": "usage-1"}]},
    )
    generator = _CapturingGenerator(_candidate("improved", ["not-in-snapshot"]))
    with pytest.raises(DomainValidationError, match="outside the frozen snapshot"):
        GenerateSkillImprovementProposal(
            generator, CreateSkillImprovementProposal(factory, clock)
        ).execute(
            skill_id=skill.id,
            base_revision_id=base.id,
            trigger_kind="manual",
            evidence_snapshot_id=snapshot.id,
            evidence_snapshot_hash=snapshot.content_sha256,
            generator_version=GENERATOR_VERSION,
            base_instructions="base instructions",
        )
    assert generator.called  # the adapter ran; validation happens on its output


def test_tampered_snapshot_payload_fails_before_the_brain_adapter_is_called() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="candidate.tampered", display_name="P", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="base instructions",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={"items": [{"id": "usage-1"}]},
    )
    generator = _CapturingGenerator(_candidate("improved", ["usage-1"]))
    with pytest.raises(EntityConflict, match="do not match the frozen snapshot"):
        GenerateSkillImprovementProposal(
            generator, CreateSkillImprovementProposal(factory, clock)
        ).execute(
            skill_id=skill.id,
            base_revision_id=base.id,
            trigger_kind="manual",
            evidence_snapshot_id=snapshot.id,
            evidence_snapshot_hash="f" * 64,
            generator_version=GENERATOR_VERSION,
            base_instructions="base instructions",
        )
    assert not generator.called
