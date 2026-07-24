"""Delivery loop for claimed runs, lease heartbeats, and maintenance."""

from __future__ import annotations

import logging
import threading
import time

from friday.application.errors import ClaimLost
from friday.application.run_processor import ClaimContext, RunProcessor
from friday.application.worker_coordination import (
    ApplyFailedOutcome,
    ApplySucceededOutcome,
    ApplyWaitingOutcome,
    ClaimNextRun,
    RenewRunLease,
    RequeueClaimedRun,
)
from friday.application.worker_maintenance import ExpireDueApprovals, RecoverExpiredLeases
from friday.domain.failure import Failure, FailureCause

logger = logging.getLogger(__name__)


class WorkerLoop:
    def __init__(
        self,
        *,
        claim_next_run: ClaimNextRun,
        renew_lease: RenewRunLease,
        requeue_claimed_run: RequeueClaimedRun,
        apply_failed: ApplyFailedOutcome,
        apply_succeeded: ApplySucceededOutcome,
        apply_waiting: ApplyWaitingOutcome,
        recover_expired_leases: RecoverExpiredLeases,
        expire_due_approvals: ExpireDueApprovals,
        heartbeat_interval_seconds: float,
        maintenance_interval_seconds: float,
        poll_interval_seconds: float,
    ) -> None:
        self._claim_next_run = claim_next_run
        self._renew_lease = renew_lease
        self._requeue_claimed_run = requeue_claimed_run
        self._apply_failed = apply_failed
        self._apply_succeeded = apply_succeeded
        self._apply_waiting = apply_waiting
        self._recover_expired_leases = recover_expired_leases
        self._expire_due_approvals = expire_due_approvals
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._maintenance_interval_seconds = maintenance_interval_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def run_once(self, processor: RunProcessor | None) -> bool:
        if processor is None:
            return False
        claim = self._claim_next_run.execute()
        if claim is None:
            return False

        lease_lost = threading.Event()
        stop_heartbeat = threading.Event()
        heartbeat_errors: list[BaseException] = []

        def heartbeat() -> None:
            while not stop_heartbeat.wait(self._heartbeat_interval_seconds):
                try:
                    self._renew_lease.execute(
                        claim.run_id,
                        claim.worker_id,
                        claim.claim_token,
                        claim.claim_generation,
                    )
                except ClaimLost:
                    lease_lost.set()
                    return
                except Exception as exc:  # noqa: BLE001 - recorded, thread must not die silently
                    heartbeat_errors.append(exc)
                    lease_lost.set()
                    return

        heartbeat_thread = threading.Thread(target=heartbeat, name="worker-heartbeat")
        heartbeat_thread.start()
        context = ClaimContext(
            run_id=claim.run_id,
            task_id=claim.task_id,
            worker_id=claim.worker_id,
            claim_token=claim.claim_token,
            claim_generation=claim.claim_generation,
            attempt_number=claim.attempt_number,
            is_lease_lost=lease_lost.is_set,
        )
        try:
            outcome = processor.process(context)
        except Exception as exc:
            if context.is_lease_lost():
                logger.error(
                    "Processor raised %s for run %s after its claim was already lost; "
                    "discarding outcome",
                    type(exc).__name__,
                    claim.run_id,
                )
                return True
            failure = Failure(
                code="processor_exception",
                message="Run processor failed unexpectedly.",
                retryable=True,
                cause=FailureCause.RUNTIME,
            )
            logger.exception(
                "Processor raised %s for run %s; recording as failure code %s",
                type(exc).__name__,
                claim.run_id,
                failure.code,
            )
            try:
                self._apply_failed.execute(
                    claim.run_id,
                    claim.worker_id,
                    claim.claim_token,
                    claim.claim_generation,
                    failure,
                )
            except ClaimLost:
                logger.info(
                    "Claim lost while applying synthetic failure outcome for run %s", claim.run_id
                )
            return True
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join()

        if heartbeat_errors:
            logger.error(
                "Heartbeat thread failed with %s for run %s; lease state unknown, "
                "skipping outcome application",
                type(heartbeat_errors[0]).__name__,
                claim.run_id,
            )
            return True

        try:
            if outcome.kind == "succeeded":
                self._apply_succeeded.execute(
                    claim.run_id, claim.worker_id, claim.claim_token, claim.claim_generation
                )
            elif outcome.kind == "failed":
                assert outcome.failure is not None
                self._apply_failed.execute(
                    claim.run_id,
                    claim.worker_id,
                    claim.claim_token,
                    claim.claim_generation,
                    outcome.failure,
                )
            elif outcome.kind == "waiting_for_approval":
                self._apply_waiting.execute(
                    claim.run_id, claim.worker_id, claim.claim_token, claim.claim_generation
                )
            elif outcome.kind == "yielded":
                assert outcome.available_at is not None
                self._requeue_claimed_run.execute(
                    claim.run_id,
                    claim.worker_id,
                    claim.claim_token,
                    claim.claim_generation,
                    outcome.available_at,
                )
        except ClaimLost:
            logger.info("Claim lost while applying outcome for run %s", claim.run_id)
        return True

    def run_maintenance_tick(self) -> None:
        recovered = self._recover_expired_leases.execute()
        approvals = self._expire_due_approvals.execute()
        logger.info("Recovered %d expired leases", recovered)
        logger.info("Expired %d due approvals", len(approvals))

    def serve_forever(
        self, shutdown_event: threading.Event, processor: RunProcessor | None = None
    ) -> None:
        last_maintenance = time.monotonic()
        while not shutdown_event.is_set():
            if time.monotonic() - last_maintenance >= self._maintenance_interval_seconds:
                self.run_maintenance_tick()
                last_maintenance = time.monotonic()

            if processor is None or not self.run_once(processor):
                shutdown_event.wait(timeout=self._poll_interval_seconds)
