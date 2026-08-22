"""Reconcile one terminal child attempt with its durable delegation."""

from __future__ import annotations

from datetime import datetime

from friday.application.lifecycle_events import LifecycleEvents
from friday.application.ports import UnitOfWork
from friday.domain.delegation import DelegationStatus
from friday.domain.event import RunEventType
from friday.domain.json_value import JsonValue
from friday.domain.run import Run, RunStatus


def reconcile_child_terminal_in_uow(uow: UnitOfWork, child_run: Run, now: datetime) -> None:
    """Close a dispatched delegation exactly once and wake its waiting parent.

    The lookup is by execution lineage, so an automatic retry is still part of
    the same delegation.  The execution write fence makes retry materialization
    and reconciliation serialize before the latest attempt is selected.  A
    conditional DISPATCHED -> terminal update is the idempotency fence.
    """

    if not uow.runs.lock_execution_lineage(child_run.execution_id):
        return

    request = uow.delegation_requests.get_for_child_execution(child_run.execution_id)
    if request is None or request.status is not DelegationStatus.DISPATCHED:
        return

    # Never let a historical terminal attempt settle the delegation while a
    # newer retry is queued, running, or otherwise non-terminal.
    latest = uow.runs.get_latest_for_execution(child_run.execution_id)
    if latest is None or latest.status not in {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        return

    if latest.status is RunStatus.SUCCEEDED:
        terminal_status = DelegationStatus.SUCCEEDED
        event_type = RunEventType.DELEGATION_SUCCEEDED
        failure_code = None
        payload: JsonValue = {
            "delegation_request_id": str(request.id),
            "child_run_id": str(latest.id),
            "status": terminal_status.value,
        }
    elif latest.status is RunStatus.FAILED:
        terminal_status = DelegationStatus.FAILED
        event_type = RunEventType.DELEGATION_FAILED
        failure_code = latest.failure.code if latest.failure is not None else "child_run_failed"
        payload = {
            "delegation_request_id": str(request.id),
            "child_run_id": str(latest.id),
            "status": terminal_status.value,
            "failure_code": failure_code,
        }
    else:
        terminal_status = DelegationStatus.CANCELLED
        event_type = RunEventType.DELEGATION_CANCELLED
        failure_code = None
        payload = {
            "delegation_request_id": str(request.id),
            "child_run_id": str(latest.id),
            "status": terminal_status.value,
        }

    if not uow.delegation_requests.finalize_if_dispatched(
        request.id, terminal_status, now, failure_code
    ):
        # A concurrent reconciler won the terminal transition.  In
        # particular, do not enqueue or resume the parent a second time.
        return

    # Update the in-memory snapshot only after the conditional durable fence
    # wins; no unconditional merge can overwrite a terminal request.
    if terminal_status is DelegationStatus.SUCCEEDED:
        request.succeed(now)
    elif terminal_status is DelegationStatus.FAILED:
        assert failure_code is not None
        request.fail(now, failure_code)
    else:
        request.cancel(now)

    parent = uow.runs.get(request.parent_run_id)
    if (
        parent is None
        or parent.status is not RunStatus.WAITING_FOR_DELEGATION
        or parent.delegation_request_id != request.id
    ):
        return

    parent.resume_from_delegation(now)
    uow.runs.save(parent)
    uow.work_queue.enqueue(parent.id, available_at=now, enqueued_at=now)
    LifecycleEvents.append_run_events(
        uow,
        parent,
        now,
        [
            (event_type, payload, None),
            (
                RunEventType.RUN_RESUMED,
                {
                    "run_id": str(parent.id),
                    "delegation_request_id": str(request.id),
                },
                None,
            ),
        ],
    )
