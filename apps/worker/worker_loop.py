"""Delivery loop for claimed runs, lease heartbeats, and maintenance."""

from __future__ import annotations

import logging
import threading
import time
from typing import Protocol

from apps.worker.operational_logging import lifecycle_log
from friday.application.errors import ClaimLost
from friday.application.materialize_due_schedule import MaterializeDueSchedules
from friday.application.ports import Clock
from friday.application.run_processor import ClaimContext, ProcessingOutcome, RunProcessor
from friday.application.worker_coordination import (
    ApplyFailedOutcome,
    ApplySucceededOutcome,
    ApplyWaitingOutcome,
    ClaimNextRun,
    RenewRunLease,
    RequeueClaimedRun,
)
from friday.application.worker_maintenance import (
    ExpireDueApprovals,
    MaterializeScheduledAnswerDeliveries,
    RecoverExpiredLeases,
)
from friday.domain.failure import Failure, FailureCause

logger = logging.getLogger(__name__)


class MemoryIndexRefresh(Protocol):
    def execute(self) -> object: ...


class OutboundDeliveryWorker(Protocol):
    def run_once(self) -> bool: ...


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
        clock: Clock,
        heartbeat_interval_seconds: float,
        maintenance_interval_seconds: float,
        poll_interval_seconds: float,
        refresh_memory_index: MemoryIndexRefresh | None = None,
        memory_index_maintenance_interval_seconds: float | None = None,
        materialize_due_schedules: MaterializeDueSchedules | None = None,
        materialize_scheduled_answers: MaterializeScheduledAnswerDeliveries | None = None,
        delivery_worker: OutboundDeliveryWorker | None = None,
    ) -> None:
        self._claim_next_run = claim_next_run
        self._renew_lease = renew_lease
        self._requeue_claimed_run = requeue_claimed_run
        self._apply_failed = apply_failed
        self._apply_succeeded = apply_succeeded
        self._apply_waiting = apply_waiting
        self._recover_expired_leases = recover_expired_leases
        self._expire_due_approvals = expire_due_approvals
        self._materialize_due_schedules = materialize_due_schedules
        self._materialize_scheduled_answers = materialize_scheduled_answers
        self._delivery_worker = delivery_worker
        self._clock = clock
        self._refresh_memory_index = refresh_memory_index
        self._memory_index_maintenance_interval_seconds = memory_index_maintenance_interval_seconds
        self._last_memory_index_maintenance = time.monotonic()
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._maintenance_interval_seconds = maintenance_interval_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def run_once(
        self,
        processor: RunProcessor | None,
        shutdown_event: threading.Event | None = None,
    ) -> bool:
        if shutdown_event is not None and shutdown_event.is_set():
            return False
        if processor is None:
            return False
        claim = self._claim_next_run.execute()
        if claim is None:
            return False
        fields = {
            "task_id": claim.task_id,
            "run_id": claim.run_id,
            "worker_id": claim.worker_id,
            "claim_generation": claim.claim_generation,
        }
        lifecycle_log(logger, logging.INFO, "worker.claimed_run", **fields)

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
            is_lease_lost=lambda: (
                lease_lost.is_set() or (shutdown_event is not None and shutdown_event.is_set())
            ),
        )
        processor_error: Exception | None = None
        outcome: ProcessingOutcome | None = None
        try:
            outcome = processor.process(context)
        except Exception as exc:
            processor_error = exc
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join()

        if heartbeat_errors:
            lifecycle_log(logger, logging.ERROR, "worker.heartbeat_failed", **fields)
            return True

        if shutdown_event is not None and shutdown_event.is_set():
            try:
                self._requeue_claimed_run.execute(
                    claim.run_id,
                    claim.worker_id,
                    claim.claim_token,
                    claim.claim_generation,
                    self._clock.now(),
                )
                lifecycle_log(logger, logging.INFO, "worker.shutdown_requeued_claim", **fields)
            except ClaimLost:
                lifecycle_log(logger, logging.INFO, "worker.shutdown_claim_fenced", **fields)
            return True

        if lease_lost.is_set():
            if processor_error is not None:
                lifecycle_log(logger, logging.ERROR, "worker.processor_error_after_fence", **fields)
            else:
                lifecycle_log(logger, logging.INFO, "worker.outcome_fenced", **fields)
            return True

        if processor_error is not None:
            failure = Failure(
                code="processor_exception",
                message="Run processor failed unexpectedly.",
                retryable=True,
                cause=FailureCause.RUNTIME,
            )
            lifecycle_log(logger, logging.ERROR, "worker.processor_exception", **fields)
            try:
                self._apply_failed.execute(
                    claim.run_id,
                    claim.worker_id,
                    claim.claim_token,
                    claim.claim_generation,
                    failure,
                )
            except ClaimLost:
                lifecycle_log(logger, logging.INFO, "worker.failure_outcome_fenced", **fields)
            return True

        assert outcome is not None
        try:
            if outcome.kind == "succeeded":
                self._apply_succeeded.execute(
                    claim.run_id,
                    claim.worker_id,
                    claim.claim_token,
                    claim.claim_generation,
                    outcome.final_response,
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
                assert outcome.approval_request_id is not None
                self._apply_waiting.execute(
                    claim.run_id,
                    claim.worker_id,
                    claim.claim_token,
                    claim.claim_generation,
                    outcome.approval_request_id,
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
            else:
                lifecycle_log(logger, logging.ERROR, "worker.unknown_processing_outcome", **fields)
                self._apply_failed.execute(
                    claim.run_id,
                    claim.worker_id,
                    claim.claim_token,
                    claim.claim_generation,
                    Failure(
                        code="unknown_processing_outcome",
                        message="Run processor returned an unrecognized outcome kind.",
                        retryable=False,
                        cause=FailureCause.RUNTIME,
                    ),
                )
        except ClaimLost:
            lifecycle_log(logger, logging.INFO, "worker.outcome_fenced", **fields)
        else:
            lifecycle_log(logger, logging.INFO, f"worker.outcome_{outcome.kind}", **fields)
        return True

    def run_maintenance_tick(self) -> None:
        try:
            materialized = (
                self._materialize_due_schedules.execute() if self._materialize_due_schedules else 0
            )
        except Exception:  # noqa: BLE001 - one malformed schedule must not stop delivery
            materialized = 0
            lifecycle_log(logger, logging.WARNING, "scheduler.materialization_failed")
        try:
            scheduled_answers = (
                self._materialize_scheduled_answers.execute()
                if self._materialize_scheduled_answers
                else 0
            )
        except Exception:  # noqa: BLE001 - scheduled answers must not stop maintenance
            scheduled_answers = 0
            lifecycle_log(logger, logging.WARNING, "scheduler.answer_materialization_failed")
        try:
            recovered = self._recover_expired_leases.execute()
        except Exception:  # noqa: BLE001 - maintenance jobs are isolated from one another
            recovered = 0
            lifecycle_log(logger, logging.WARNING, "worker.lease_recovery_failed")
        try:
            approvals = self._expire_due_approvals.execute()
        except Exception:  # noqa: BLE001 - maintenance jobs are isolated from one another
            approvals = []
            lifecycle_log(logger, logging.WARNING, "worker.approval_expiry_failed")
        lifecycle_log(
            logger, logging.INFO, "worker.expired_leases_recovered", recovered_count=recovered
        )
        lifecycle_log(logger, logging.INFO, "scheduler.materialized", run_count=materialized)
        lifecycle_log(
            logger,
            logging.INFO,
            "scheduler.answers_materialized",
            delivery_count=scheduled_answers,
        )
        lifecycle_log(
            logger,
            logging.INFO,
            "worker.approvals_expired",
            expired_approval_count=len(approvals),
        )
        self._refresh_memory_index_if_due()

    def _refresh_memory_index_if_due(self) -> None:
        refresh = self._refresh_memory_index
        interval = self._memory_index_maintenance_interval_seconds
        if refresh is None or interval is None:
            return
        now = time.monotonic()
        if now - self._last_memory_index_maintenance < interval:
            return
        try:
            refresh.execute()
        except Exception:  # noqa: BLE001 - maintenance must not stop the worker
            lifecycle_log(logger, logging.WARNING, "worker.memory_index_refresh_failed")
        self._last_memory_index_maintenance = now

    def serve_forever(
        self, shutdown_event: threading.Event, processor: RunProcessor | None = None
    ) -> None:
        stop_deliveries = threading.Event()
        delivery_thread: threading.Thread | None = None
        if self._delivery_worker is not None:
            delivery_thread = threading.Thread(
                target=self._serve_deliveries,
                args=(shutdown_event, stop_deliveries),
                name="delivery-dispatcher",
            )
            delivery_thread.start()
        last_maintenance = time.monotonic()
        try:
            while not shutdown_event.is_set():
                if time.monotonic() - last_maintenance >= self._maintenance_interval_seconds:
                    self.run_maintenance_tick()
                    last_maintenance = time.monotonic()

                if not self.run_once(processor, shutdown_event):
                    shutdown_event.wait(timeout=self._poll_interval_seconds)
        finally:
            stop_deliveries.set()
            if delivery_thread is not None:
                delivery_thread.join()

    def _serve_deliveries(
        self, shutdown_event: threading.Event, stop_deliveries: threading.Event
    ) -> None:
        """Keep delivery failures isolated from the agent-run worker loop."""
        assert self._delivery_worker is not None
        while not shutdown_event.is_set() and not stop_deliveries.is_set():
            try:
                worked = self._delivery_worker.run_once()
            except Exception:  # noqa: BLE001 - delivery must not stop run processing
                lifecycle_log(logger, logging.ERROR, "worker.delivery_dispatch_failed")
                worked = False
            if not worked:
                stop_deliveries.wait(timeout=self._poll_interval_seconds)
