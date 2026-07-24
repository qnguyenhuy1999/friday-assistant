"""Bounded maintenance ticks for worker claims and approval deadlines."""

from __future__ import annotations

from friday.application.approval_workflow import ExpireApproval
from friday.application.commands import ExpireApprovalCommand
from friday.application.errors import EntityConflict
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import ApprovalRequestResult
from friday.domain.run import TERMINAL_RUN_STATUSES, RunStatus


class RecoverExpiredLeases:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, *, batch_size: int) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._batch_size = batch_size

    def execute(self) -> int:
        with self._uow_factory() as uow:
            now = self._clock.now()
            expired = uow.work_queue.find_expired_claims(now, self._batch_size)
            recovered = 0
            for item in expired:
                run = uow.runs.get(item.run_id)
                if (
                    run is None
                    or run.status in TERMINAL_RUN_STATUSES
                    or run.status is RunStatus.WAITING_FOR_APPROVAL
                ):
                    recovered += int(uow.work_queue.remove_if_lease_expired(item.run_id, now))
                else:
                    recovered += int(uow.work_queue.clear_expired_claim(item.run_id, now))
            uow.commit()
            return recovered


class ExpireDueApprovals:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, *, batch_size: int) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._batch_size = batch_size

    def execute(self) -> list[ApprovalRequestResult]:
        with self._uow_factory() as uow:
            now = self._clock.now()
            due = uow.approvals.list_due_for_expiry(now, self._batch_size)
            uow.commit()

        expire = ExpireApproval(self._uow_factory, self._clock)
        results: list[ApprovalRequestResult] = []
        for approval in due:
            try:
                results.append(expire.execute(ExpireApprovalCommand(approval.id)))
            except EntityConflict:
                continue
        return results
