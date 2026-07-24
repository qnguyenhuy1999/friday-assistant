"""Worker composition root: settings, infrastructure, use cases, and loop."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from apps.worker.settings import WorkerSettings
from apps.worker.worker_loop import WorkerLoop
from friday.application.retry_policy import RetryPolicy
from friday.application.worker_coordination import (
    ApplyFailedOutcome,
    ApplySucceededOutcome,
    ApplyWaitingOutcome,
    ClaimNextRun,
    RenewRunLease,
    RequeueClaimedRun,
)
from friday.application.worker_maintenance import ExpireDueApprovals, RecoverExpiredLeases
from friday.infrastructure.clock import SystemClock
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory


@dataclass(slots=True)
class Worker:
    engine: Engine
    settings: WorkerSettings
    loop: WorkerLoop


def create_worker(settings: WorkerSettings) -> Worker:
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    uow_factory = create_unit_of_work_factory(session_factory)
    clock = SystemClock()
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
        multiplier=settings.retry_multiplier,
        max_delay=settings.retry_max_delay,
    )
    loop = WorkerLoop(
        claim_next_run=ClaimNextRun(
            uow_factory,
            clock,
            worker_id=settings.worker_id,
            lease_duration=settings.lease_duration,
            candidate_limit=settings.candidate_limit,
        ),
        renew_lease=RenewRunLease(uow_factory, clock, lease_duration=settings.lease_duration),
        requeue_claimed_run=RequeueClaimedRun(uow_factory, clock),
        apply_failed=ApplyFailedOutcome(uow_factory, clock, retry_policy=retry_policy),
        apply_succeeded=ApplySucceededOutcome(uow_factory, clock),
        apply_waiting=ApplyWaitingOutcome(uow_factory, clock),
        recover_expired_leases=RecoverExpiredLeases(
            uow_factory, clock, batch_size=settings.maintenance_batch_size
        ),
        expire_due_approvals=ExpireDueApprovals(
            uow_factory, clock, batch_size=settings.maintenance_batch_size
        ),
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        maintenance_interval_seconds=settings.maintenance_interval_seconds,
        poll_interval_seconds=settings.poll_interval_seconds,
    )
    return Worker(engine=engine, settings=settings, loop=loop)
