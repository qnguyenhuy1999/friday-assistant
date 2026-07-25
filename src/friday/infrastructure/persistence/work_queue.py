from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.engine import CursorResult
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

    def try_claim(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        stmt = (
            update(RunWorkItemRow)
            .where(
                RunWorkItemRow.run_id == str(run_id),
                RunWorkItemRow.available_at <= now,
                or_(
                    RunWorkItemRow.claimed_by.is_(None),
                    RunWorkItemRow.lease_expires_at <= now,
                ),
            )
            .values(
                claimed_by=worker_id,
                claim_token=claim_token,
                claim_generation=RunWorkItemRow.claim_generation + 1,
                claimed_at=now,
                heartbeat_at=now,
                lease_expires_at=lease_expires_at,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def renew_lease(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        stmt = (
            update(RunWorkItemRow)
            .where(
                RunWorkItemRow.run_id == str(run_id),
                RunWorkItemRow.claimed_by == worker_id,
                RunWorkItemRow.claim_token == claim_token,
                RunWorkItemRow.claim_generation == claim_generation,
                RunWorkItemRow.lease_expires_at > now,
            )
            .values(heartbeat_at=now, lease_expires_at=lease_expires_at)
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def release_claim(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int, now: datetime
    ) -> bool:
        stmt = (
            update(RunWorkItemRow)
            .where(
                RunWorkItemRow.run_id == str(run_id),
                RunWorkItemRow.claimed_by == worker_id,
                RunWorkItemRow.claim_token == claim_token,
                RunWorkItemRow.claim_generation == claim_generation,
                RunWorkItemRow.lease_expires_at.is_not(None),
                RunWorkItemRow.lease_expires_at > now,
            )
            .values(
                claimed_by=None,
                claim_token=None,
                claimed_at=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def requeue_claimed(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        available_at: datetime,
        enqueued_at: datetime,
        now: datetime,
    ) -> bool:
        stmt = (
            update(RunWorkItemRow)
            .where(
                RunWorkItemRow.run_id == str(run_id),
                RunWorkItemRow.claimed_by == worker_id,
                RunWorkItemRow.claim_token == claim_token,
                RunWorkItemRow.claim_generation == claim_generation,
                RunWorkItemRow.lease_expires_at.is_not(None),
                RunWorkItemRow.lease_expires_at > now,
            )
            .values(
                available_at=available_at,
                enqueued_at=enqueued_at,
                claimed_by=None,
                claim_token=None,
                claimed_at=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def remove_if_claimed(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int, now: datetime
    ) -> bool:
        stmt = (
            delete(RunWorkItemRow)
            .where(
                RunWorkItemRow.run_id == str(run_id),
                RunWorkItemRow.claimed_by == worker_id,
                RunWorkItemRow.claim_token == claim_token,
                RunWorkItemRow.claim_generation == claim_generation,
                RunWorkItemRow.lease_expires_at.is_not(None),
                RunWorkItemRow.lease_expires_at > now,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def clear_expired_claim(self, run_id: RunId, now: datetime) -> bool:
        stmt = (
            update(RunWorkItemRow)
            .where(
                RunWorkItemRow.run_id == str(run_id),
                RunWorkItemRow.claimed_by.is_not(None),
                RunWorkItemRow.lease_expires_at.is_not(None),
                RunWorkItemRow.lease_expires_at <= now,
            )
            .values(
                claimed_by=None,
                claim_token=None,
                claimed_at=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def remove_if_lease_expired(self, run_id: RunId, now: datetime) -> bool:
        stmt = (
            delete(RunWorkItemRow)
            .where(
                RunWorkItemRow.run_id == str(run_id),
                RunWorkItemRow.claimed_by.is_not(None),
                RunWorkItemRow.lease_expires_at.is_not(None),
                RunWorkItemRow.lease_expires_at <= now,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def is_claim_active(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        row = self._session.get(RunWorkItemRow, str(run_id))
        if (
            row is None
            or row.claimed_by != worker_id
            or row.claim_token != claim_token
            or row.claim_generation != claim_generation
            or row.lease_expires_at is None
        ):
            return False
        # SQLite drops tzinfo on read-back; reattach UTC before the only
        # Python-side datetime comparison in this repository (every other
        # method compares inside a SQL WHERE clause).
        lease_expires_at = row.lease_expires_at
        if lease_expires_at.tzinfo is None:
            lease_expires_at = lease_expires_at.replace(tzinfo=UTC)
        return lease_expires_at > now
