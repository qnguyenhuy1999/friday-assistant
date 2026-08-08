"""Test-only no-claim skill resolution helper.

Production ``ResolveRunSkills`` requires an exact active worker claim and fails
closed with ``ClaimLost`` when the queue row is missing or mismatched.  Pure
application tests that only exercise domain resolution (freezing current task
bindings, run ownership of a freeze) must not drive a real claim cycle.

This module provides the only no-claim convenience path in the repository.  It
lives under ``tests/application`` so it is never importable from ``src/`` or
``apps/``.
"""

from __future__ import annotations

from datetime import datetime

from friday.application.agent_registry import ResolveRunAgent
from friday.application.ports import Clock
from friday.application.skill_registry import ResolveRunSkills
from friday.domain.agent import RunAgentResolution
from friday.domain.identifiers import RunId
from friday.domain.skill import RunSkillBinding
from tests.application.fakes import (
    CountingUnitOfWorkFactory,
    FakeRunWorkQueue,
    FakeUnitOfWork,
)

_TEST_WORKER = "test-worker"
_TEST_TOKEN = "test-token"
_TEST_GENERATION = 1


class _AlwaysClaimedWorkQueue(FakeRunWorkQueue):
    """Fake queue that treats every run as holding an exact active claim."""

    def is_claim_active(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        del run_id, worker_id, claim_token, claim_generation, now
        return True


def resolve_run_skills_without_claim(
    factory: CountingUnitOfWorkFactory, clock: Clock, run_id: RunId
) -> list[RunSkillBinding]:
    """Resolve a run in the in-memory fake without a real worker claim."""
    uow = factory.uow
    assert isinstance(uow, FakeUnitOfWork)
    uow.work_queue_repo = _AlwaysClaimedWorkQueue()
    return ResolveRunSkills(factory, clock).execute(
        run_id, _TEST_WORKER, _TEST_TOKEN, _TEST_GENERATION
    )


def resolve_run_agent_without_claim(
    factory: CountingUnitOfWorkFactory, clock: Clock, run_id: RunId
) -> RunAgentResolution | None:
    """Resolve a run's Agent identity in the in-memory fake without a real
    worker claim, mirroring `resolve_run_skills_without_claim`."""
    uow = factory.uow
    assert isinstance(uow, FakeUnitOfWork)
    uow.work_queue_repo = _AlwaysClaimedWorkQueue()
    return ResolveRunAgent(factory, clock).execute(
        run_id, _TEST_WORKER, _TEST_TOKEN, _TEST_GENERATION
    )
