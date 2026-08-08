"""Agent/AgentRevision domain invariants: immutable revision integrity,
activation ownership, lifecycle transitions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from friday.domain.agent import (
    Agent,
    AgentRevision,
    AgentRevisionSourceKind,
    AgentStatus,
    RunAgentResolution,
    TaskAgentBinding,
)
from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import (
    AgentId,
    AgentRevisionId,
    RunAgentResolutionId,
    RunId,
    TaskId,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 1, tzinfo=UTC)


def _new_agent() -> Agent:
    return Agent.new(
        id=AgentId.new(), key="research.coder", display_name="Coder", description="", created_at=T0
    )


def _new_revision(agent: Agent, *, version: int = 1) -> AgentRevision:
    return AgentRevision.new(
        id=AgentRevisionId.new(),
        agent_id=agent.id,
        version=version,
        instructions="be helpful",
        runtime_kind="claude_cli",
        runtime_config={"max_turns": 4},
        source_kind=AgentRevisionSourceKind.OPERATOR,
        created_at=T0,
    )


def test_agent_new_normalizes_and_defaults_to_active() -> None:
    agent = Agent.new(
        id=AgentId.new(), key="coder", display_name="  Coder  ", description="  d  ", created_at=T0
    )
    assert agent.display_name == "Coder"
    assert agent.description == "d"
    assert agent.status is AgentStatus.ACTIVE
    assert agent.active_revision_id is None


def test_agent_key_must_be_lowercase_dot_or_hyphen_identity() -> None:
    with pytest.raises(DomainValidationError):
        Agent.new(id=AgentId.new(), key="Bad Key", display_name="x", description="", created_at=T0)


def test_agent_display_name_must_not_be_blank() -> None:
    with pytest.raises(DomainValidationError):
        Agent.new(id=AgentId.new(), key="coder", display_name="   ", description="", created_at=T0)


def test_revision_content_sha256_is_recomputed_and_checked() -> None:
    agent = _new_agent()
    revision = _new_revision(agent)
    expected = hashlib.sha256(
        json.dumps(
            {
                "instructions": "be helpful",
                "runtime_kind": "claude_cli",
                "runtime_config": {"max_turns": 4},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert revision.content_sha256 == expected


def test_revision_with_tampered_digest_fails_closed() -> None:
    agent = _new_agent()
    revision = _new_revision(agent)
    with pytest.raises(DomainValidationError, match="agent_integrity_failed"):
        AgentRevision(
            revision.id,
            revision.agent_id,
            revision.version,
            revision.instructions,
            revision.runtime_kind,
            revision.runtime_config,
            "0" * 64,
            revision.source_kind,
            revision.created_at,
        )


@pytest.mark.parametrize("version", [0, -1])
def test_revision_version_must_be_positive(version: int) -> None:
    agent = _new_agent()
    with pytest.raises(DomainValidationError):
        AgentRevision.new(
            id=AgentRevisionId.new(),
            agent_id=agent.id,
            version=version,
            instructions="x",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
            created_at=T0,
        )


@pytest.mark.parametrize("content", ["", "bad\x00", "\ud800"])
def test_revision_instructions_are_validated(content: str) -> None:
    agent = _new_agent()
    with pytest.raises(DomainValidationError):
        AgentRevision.new(
            id=AgentRevisionId.new(),
            agent_id=agent.id,
            version=1,
            instructions=content,
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
            created_at=T0,
        )


@pytest.mark.parametrize("runtime_kind", ["", "Claude", "claude cli", "claude/cli"])
def test_revision_runtime_kind_must_be_a_machine_identity(runtime_kind: str) -> None:
    agent = _new_agent()
    with pytest.raises(DomainValidationError):
        AgentRevision.new(
            id=AgentRevisionId.new(),
            agent_id=agent.id,
            version=1,
            instructions="x",
            runtime_kind=runtime_kind,
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
            created_at=T0,
        )


def test_activate_requires_revision_to_belong_to_agent() -> None:
    a = _new_agent()
    b = Agent.new(id=AgentId.new(), key="other", display_name="B", description="", created_at=T0)
    revision_of_b = _new_revision(b)
    with pytest.raises(DomainValidationError):
        a.activate(revision_of_b, T1)


def test_activate_sets_pointer_and_updated_at() -> None:
    agent = _new_agent()
    revision = _new_revision(agent)
    agent.activate(revision, T1)
    assert agent.active_revision_id == revision.id
    assert agent.updated_at == T1


def test_archived_agent_cannot_be_reactivated_or_disabled() -> None:
    agent = _new_agent()
    agent.archive(T1)
    assert agent.status is AgentStatus.ARCHIVED
    revision = _new_revision(agent)
    with pytest.raises(InvalidStateTransition):
        agent.activate(revision, T1)
    with pytest.raises(InvalidStateTransition):
        agent.disable(T1)


def test_archive_is_idempotent() -> None:
    agent = _new_agent()
    agent.archive(T1)
    agent.archive(T1)
    assert agent.status is AgentStatus.ARCHIVED


def test_task_agent_binding_requires_aware_timestamp() -> None:
    with pytest.raises(DomainValidationError):
        TaskAgentBinding(TaskId.new(), AgentId.new(), datetime(2026, 1, 1))


def test_run_agent_resolution_requires_aware_timestamp() -> None:
    with pytest.raises(DomainValidationError):
        RunAgentResolution(
            RunAgentResolutionId.new(),
            RunId.new(),
            AgentId.new(),
            AgentRevisionId.new(),
            datetime(2026, 1, 1),
        )
