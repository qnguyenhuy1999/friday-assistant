"""Worker coordination use cases: atomic claiming, lease renewal, release,
requeue, and completion, all fenced by (worker_id, claim_token,
claim_generation). No processor execution lives here — see Phase 11.

Losing a claim race during `ClaimNextRun` is an ordinary outcome (try the
next candidate). Losing a claim during renewal/release/requeue/completion
means a stale worker is being fenced out: it raises `ClaimLost` rather than
silently succeeding or leaking a persistence exception.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from friday.application.errors import ClaimLost
from friday.application.lifecycle_events import LifecycleEvents
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import RunClaimResult
from friday.domain.event import RunEventType
from friday.domain.identifiers import RunId
from friday.domain.run import TERMINAL_RUN_STATUSES, RunStatus


class ClaimNextRun:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        *,
        worker_id: str,
        lease_duration: timedelta,
        candidate_limit: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._candidate_limit = candidate_limit

    def execute(self) -> RunClaimResult | None:
        with self._uow_factory() as uow:
            now = self._clock.now()
            candidates = uow.work_queue.find_due_candidates(now, self._candidate_limit)
            for candidate in candidates:
                claim_token = uuid.uuid4().hex
                lease_expires_at = now + self._lease_duration
                claimed = uow.work_queue.try_claim(
                    candidate.run_id, self._worker_id, claim_token, now, lease_expires_at
                )
                if not claimed:
                    continue

                run = uow.runs.get(candidate.run_id)
                if (
                    run is None
                    or run.status in TERMINAL_RUN_STATUSES
                    or run.status is RunStatus.WAITING_FOR_APPROVAL
                ):
                    # Stale work item left behind by a state change that
                    # predates this claim; there is nothing to run.
                    uow.work_queue.remove(candidate.run_id)
                    continue

                if run.status is RunStatus.QUEUED:
                    run.start(now)
                    uow.runs.save(run)
                    LifecycleEvents.append_run_events(
                        uow, run, now, [(RunEventType.RUN_STARTED, {"run_id": str(run.id)}, None)]
                    )
                # RunStatus.RUNNING here means resumable running work
                # (e.g. after an expired-lease reclaim); run_started must
                # not be emitted again.

                item = uow.work_queue.get(candidate.run_id)
                assert item is not None
                uow.commit()
                return RunClaimResult(
                    run_id=run.id,
                    task_id=run.task_id,
                    worker_id=self._worker_id,
                    claim_token=claim_token,
                    claim_generation=item.claim_generation,
                    acquired_at=now,
                    lease_expires_at=lease_expires_at,
                )

            uow.commit()
            return None


class RenewRunLease:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, clock: Clock, *, lease_duration: timedelta
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._lease_duration = lease_duration

    def execute(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int
    ) -> datetime:
        with self._uow_factory() as uow:
            now = self._clock.now()
            new_lease_expires_at = now + self._lease_duration
            renewed = uow.work_queue.renew_lease(
                run_id, worker_id, claim_token, claim_generation, now, new_lease_expires_at
            )
            uow.commit()
        if not renewed:
            raise ClaimLost(f"lease renewal lost for run {run_id}")
        return new_lease_expires_at


class ReleaseRunClaim:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int
    ) -> None:
        with self._uow_factory() as uow:
            released = uow.work_queue.release_claim(
                run_id, worker_id, claim_token, claim_generation
            )
            uow.commit()
        if not released:
            raise ClaimLost(f"claim release lost for run {run_id}")


class RequeueClaimedRun:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        available_at: datetime,
    ) -> None:
        with self._uow_factory() as uow:
            now = self._clock.now()
            requeued = uow.work_queue.requeue_claimed(
                run_id, worker_id, claim_token, claim_generation, available_at, now
            )
            uow.commit()
        if not requeued:
            raise ClaimLost(f"requeue lost claim for run {run_id}")


class CompleteRunWorkItem:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int
    ) -> None:
        with self._uow_factory() as uow:
            removed = uow.work_queue.remove_if_claimed(
                run_id, worker_id, claim_token, claim_generation
            )
            uow.commit()
        if not removed:
            raise ClaimLost(f"work item completion lost claim for run {run_id}")


class VerifyRunClaim:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int
    ) -> bool:
        with self._uow_factory() as uow:
            now = self._clock.now()
            active = uow.work_queue.is_claim_active(
                run_id, worker_id, claim_token, claim_generation, now
            )
            uow.commit()
        return active
