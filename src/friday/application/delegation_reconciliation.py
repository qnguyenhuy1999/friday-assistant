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
    the same delegation.  A terminal request is the idempotency fence.
    """

    request = uow.delegation_requests.get_for_child_execution(child_run.execution_id)
    if request is None or request.status is not DelegationStatus.DISPATCHED:
        return

    if child_run.status is RunStatus.SUCCEEDED:
        request.succeed(now)
        event_type = RunEventType.DELEGATION_SUCCEEDED
        payload: JsonValue = {
            "delegation_request_id": str(request.id),
            "child_run_id": str(child_run.id),
            "status": request.status.value,
        }
    elif child_run.status is RunStatus.FAILED:
        request.fail(
            now, child_run.failure.code if child_run.failure is not None else "child_run_failed"
        )
        event_type = RunEventType.DELEGATION_FAILED
        payload = {
            "delegation_request_id": str(request.id),
            "child_run_id": str(child_run.id),
            "status": request.status.value,
            "failure_code": request.failure_code,
        }
    elif child_run.status is RunStatus.CANCELLED:
        request.cancel(now)
        event_type = RunEventType.DELEGATION_CANCELLED
        payload = {
            "delegation_request_id": str(request.id),
            "child_run_id": str(child_run.id),
            "status": request.status.value,
        }
    else:
        return

    uow.delegation_requests.save(request)
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
