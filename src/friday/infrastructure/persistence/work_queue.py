from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from friday.application.ports import RunWorkItemView
from friday.domain import RunId
from friday.infrastructure.persistence.mappers import run_work_item_from_row
from friday.infrastructure.persistence.models import RunWorkItemRow


class SqlAlchemyRunWorkQueue:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, run_id: RunId, available_at: datetime, enqueued_at: datetime) -> None:
        row = self._session.get(RunWorkItemRow, str(run_id))
        if row is None:
            self._session.add(
                RunWorkItemRow(
                    run_id=str(run_id),
                    available_at=available_at,
                    enqueued_at=enqueued_at,
                )
            )
            return
        row.available_at = available_at
        row.enqueued_at = enqueued_at

    def get(self, run_id: RunId) -> RunWorkItemView | None:
        row = self._session.get(RunWorkItemRow, str(run_id))
        return run_work_item_from_row(row) if row is not None else None

    def find_due_candidates(self, now: datetime, limit: int) -> list[RunWorkItemView]:
        stmt = (
            select(RunWorkItemRow)
            .where(
                RunWorkItemRow.available_at <= now,
                or_(
                    RunWorkItemRow.claimed_by.is_(None),
                    RunWorkItemRow.lease_expires_at <= now,
                ),
            )
            .order_by(
                RunWorkItemRow.available_at,
                RunWorkItemRow.enqueued_at,
                RunWorkItemRow.run_id,
            )
            .limit(limit)
        )
        return [run_work_item_from_row(row) for row in self._session.execute(stmt).scalars()]

    def find_expired_claims(self, now: datetime, limit: int) -> list[RunWorkItemView]:
        stmt = (
            select(RunWorkItemRow)
            .where(
                and_(
                    RunWorkItemRow.claimed_by.is_not(None),
                    RunWorkItemRow.lease_expires_at.is_not(None),
                    RunWorkItemRow.lease_expires_at <= now,
                )
            )
            .order_by(
                RunWorkItemRow.available_at,
                RunWorkItemRow.enqueued_at,
                RunWorkItemRow.run_id,
            )
            .limit(limit)
        )
        return [run_work_item_from_row(row) for row in self._session.execute(stmt).scalars()]

    def remove(self, run_id: RunId) -> None:
        row = self._session.get(RunWorkItemRow, str(run_id))
        if row is not None:
            self._session.delete(row)
