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

from friday.application.errors import ApprovalNotFound, ClaimLost, EntityConflict, RunNotFound
from friday.application.lifecycle_events import LifecycleEvents, run_result
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import RunClaimResult, RunResult
from friday.application.retry_policy import RetryPolicy
from friday.application.run_lifecycle import _fail_run_event_specs, _succeed_run_event_specs
from friday.domain.approval import TERMINAL_APPROVAL_STATUSES, ApprovalStatus
from friday.domain.event import RunEventType
from friday.domain.failure import Failure
from friday.domain.identifiers import ApprovalRequestId, RunId
from friday.domain.run import TERMINAL_RUN_STATUSES, Run, RunStatus
from friday.domain.step import TERMINAL_RUN_STEP_STATUSES
from friday.domain.tool import TERMINAL_TOOL_INVOCATION_STATUSES


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
                    attempt_number=len(uow.runs.list_for_task(run.task_id)),
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
            now = self._clock.now()
            released = uow.work_queue.release_claim(
                run_id, worker_id, claim_token, claim_generation, now
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
                run_id, worker_id, claim_token, claim_generation, available_at, now, now
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
            now = self._clock.now()
            removed = uow.work_queue.remove_if_claimed(
                run_id, worker_id, claim_token, claim_generation, now
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


class ApplyFailedOutcome:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, clock: Clock, *, retry_policy: RetryPolicy
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._retry_policy = retry_policy

    def execute(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        failure: Failure,
    ) -> RunResult:
        with self._uow_factory() as uow:
            now = self._clock.now()
            removed = uow.work_queue.remove_if_claimed(
                run_id, worker_id, claim_token, claim_generation, now
            )
            if not removed:
                uow.commit()
                raise ClaimLost(f"failed outcome lost claim for run {run_id}")

            run = uow.runs.get(run_id)
            if run is None:
                uow.commit()
                raise RunNotFound(run_id)

            specs = _fail_run_event_specs(uow, run, now, failure)
            LifecycleEvents.append_run_events(uow, run, now, specs)

            attempt_number = len(uow.runs.list_for_task(run.task_id))
            if self._retry_policy.is_retry_allowed(attempt_number, failure):
                retry = Run.new(id=RunId.new(), task_id=run.task_id, created_at=now)
                uow.runs.add(retry)
                delay = self._retry_policy.compute_delay(attempt_number + 1)
                uow.work_queue.enqueue(retry.id, available_at=now + delay, enqueued_at=now)
                LifecycleEvents.append_run_events(
                    uow,
                    retry,
                    now,
                    [
                        (
                            RunEventType.RUN_CREATED,
                            {"task_id": str(run.task_id), "retry_of": str(run.id)},
                            None,
                        )
                    ],
                )

            uow.commit()
            return run_result(run)


class ApplySucceededOutcome:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int
    ) -> RunResult:
        with self._uow_factory() as uow:
            now = self._clock.now()
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            if any(
                step.status not in TERMINAL_RUN_STEP_STATUSES
                for step in uow.steps.list_for_run(run.id)
            ):
                raise EntityConflict("run has non-terminal steps")
            if any(
                tool.status not in TERMINAL_TOOL_INVOCATION_STATUSES
                for tool in uow.tool_invocations.list_for_run(run.id)
            ):
                raise EntityConflict("run has non-terminal tool invocations")

            removed = uow.work_queue.remove_if_claimed(
                run_id, worker_id, claim_token, claim_generation, now
            )
            if not removed:
                raise ClaimLost(f"successful outcome lost claim for run {run_id}")

            specs = _succeed_run_event_specs(uow, run, now)
            LifecycleEvents.append_run_events(uow, run, now, specs)
            uow.commit()
            return run_result(run)


class ApplyWaitingOutcome:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        approval_request_id: ApprovalRequestId,
    ) -> RunResult:
        with self._uow_factory() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                uow.commit()
                raise RunNotFound(run_id)
            approval = uow.approvals.get(approval_request_id)
            if approval is None:
                uow.commit()
                raise ApprovalNotFound(approval_request_id)
            if approval.run_id != run.id:
                uow.commit()
                raise EntityConflict("approval request does not belong to run")

            item = uow.work_queue.get(run_id)
            if run.status is RunStatus.RUNNING and approval.status in TERMINAL_APPROVAL_STATUSES:
                # Approval resolution resumed the run and enqueued a fresh item
                # before the old processor returned. Never touch that item.
                if item is None:
                    uow.commit()
                    return run_result(run)
                if (
                    item.claimed_by != worker_id
                    or item.claim_token != claim_token
                    or item.claim_generation != claim_generation
                ):
                    uow.commit()
                    raise ClaimLost(f"stale waiting outcome for run {run_id}")
                uow.commit()
                raise EntityConflict(
                    "processor reported waiting_for_approval after approval resolution"
                )

            if run.status is not RunStatus.WAITING_FOR_APPROVAL:
                uow.commit()
                raise EntityConflict(
                    "processor reported waiting_for_approval but the run is not waiting"
                )
            if run.approval_request_id != approval.id:
                uow.commit()
                raise EntityConflict("run is waiting for a different approval request")
            if approval.status not in {
                ApprovalStatus.PENDING,
                *TERMINAL_APPROVAL_STATUSES,
            }:
                uow.commit()
                raise EntityConflict("approval request has an invalid status")
            # RequestApproval already parked the run and removed the work item in
            # the same transaction before the outcome was returned.
            if item is None:
                uow.commit()
                return run_result(run)
            # Item still present — race path (e.g. a non-approval parking happened
            # between RequestApproval and outcome dispatch). Remove it here.
            now = self._clock.now()
            removed = uow.work_queue.remove_if_claimed(
                run_id, worker_id, claim_token, claim_generation, now
            )
            if not removed:
                raise ClaimLost(f"waiting outcome lost claim for run {run_id}")
            uow.commit()
            return run_result(run)
