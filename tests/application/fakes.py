"""In-memory fakes for application use-case tests.

`FakeUnitOfWork` implements the full `UnitOfWork` protocol; every
repository holds real in-memory state as of Phase 8."""

from __future__ import annotations

import builtins
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

from friday.application.memory.models import IndexSnapshot, IndexState, MemoryRetrievalRecord
from friday.application.memory.ports import (
    MemoryIndexSnapshotRepository,
    MemoryRetrievalRecordRepository,
)
from friday.application.ports import (
    ApprovalRepository,
    ArtifactRepository,
    ConversationRepository,
    ConversationTurnRepository,
    DeliveryAttemptRepository,
    OutboundDeliveryRepository,
    RunEventStore,
    RunRepository,
    RunStepRepository,
    RunWorkItemView,
    RunWorkQueue,
    ScheduleDeliveryPolicyRepository,
    ScheduleFireDeliveryPlanRepository,
    ScheduleFireRepository,
    ScheduleRepository,
    TaskEventStore,
    TaskRepository,
    ToolInvocationRepository,
    validate_delivery_attempt_history_limit,
)
from friday.domain.approval import ApprovalRequest, ApprovalStatus
from friday.domain.artifact import Artifact
from friday.domain.conversation import Conversation
from friday.domain.conversation_turn import ConversationTurn
from friday.domain.delivery_attempt import (
    DeliveryAttempt,
    DeliveryAttemptOutcome,
    validate_delivery_attempt_shape,
)
from friday.domain.event import RunEvent, RunEventType
from friday.domain.identifiers import (
    ApprovalRequestId,
    ArtifactId,
    ConversationId,
    ConversationTurnId,
    DeliveryAttemptId,
    DeliveryId,
    RunId,
    RunStepId,
    ScheduleFireId,
    ScheduleId,
    TaskId,
    ToolInvocationId,
)
from friday.domain.outbound_delivery import DeliveryStatus, OutboundDelivery
from friday.domain.run import Run
from friday.domain.schedule import Schedule, ScheduleStatus
from friday.domain.schedule_delivery_policy import ScheduleDeliveryPolicy
from friday.domain.schedule_fire import ScheduleFire
from friday.domain.schedule_fire_delivery_plan import ScheduleFireDeliveryPlan
from friday.domain.step import TERMINAL_RUN_STEP_STATUSES, RunStep
from friday.domain.task import Task
from friday.domain.task_event import TaskEvent
from friday.domain.tool import TERMINAL_TOOL_INVOCATION_STATUSES, ToolInvocation

T0 = datetime(2026, 1, 2, 3, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self.fixed_now = now

    def now(self) -> datetime:
        return self.fixed_now


class FakeTaskRepository:
    def __init__(self) -> None:
        self.items: dict[TaskId, Task] = {}
        self.saved: list[TaskId] = []

    def add(self, task: Task) -> None:
        self.items[task.id] = task

    def get(self, task_id: TaskId) -> Task | None:
        return self.items.get(task_id)

    def save(self, task: Task) -> None:
        self.items[task.id] = task
        self.saved.append(task.id)

    def list(self, limit: int) -> list[Task]:
        return sorted(self.items.values(), key=lambda task: (task.created_at, str(task.id)))[:limit]

    def list_page(
        self, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> builtins.list[Task]:
        tasks = self.list(len(self.items))
        if after_created_at is not None and after_id is not None:
            tasks = [
                task
                for task in tasks
                if (task.created_at, str(task.id)) > (after_created_at, after_id)
            ]
        return tasks[:limit]


class FakeRunRepository:
    def __init__(self) -> None:
        self.items: dict[RunId, Run] = {}

    def add(self, run: Run) -> None:
        self.items[run.id] = run

    def get(self, run_id: RunId) -> Run | None:
        return self.items.get(run_id)

    def save(self, run: Run) -> None:
        self.items[run.id] = run

    def list_for_task(self, task_id: TaskId) -> list[Run]:
        matching = [run for run in self.items.values() if run.task_id == task_id]
        return sorted(matching, key=lambda run: (run.created_at, str(run.id)))

    def list_for_task_page(
        self, task_id: TaskId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Run]:
        runs = self.list_for_task(task_id)
        if after_created_at is not None and after_id is not None:
            runs = [
                run for run in runs if (run.created_at, str(run.id)) > (after_created_at, after_id)
            ]
        return runs[:limit]

    def has_non_terminal_for_ids(self, run_ids: list[RunId]) -> bool:
        from friday.domain.run import TERMINAL_RUN_STATUSES

        return any(  # noqa: E501
            self.items[x].status not in TERMINAL_RUN_STATUSES for x in run_ids if x in self.items
        )

    def count_for_execution(self, execution_id: RunId) -> int:
        return sum(run.execution_id == execution_id for run in self.items.values())

    def list_for_execution(self, execution_id: RunId) -> list[Run]:
        runs = [run for run in self.items.values() if run.execution_id == execution_id]
        return sorted(runs, key=lambda run: (run.created_at, str(run.id)))

    def get_latest_for_execution(self, execution_id: RunId) -> Run | None:
        runs = self.list_for_execution(execution_id)
        return runs[-1] if runs else None


class FakeScheduleRepository:
    def __init__(self) -> None:
        self.items: dict[ScheduleId, Schedule] = {}

    def add(self, schedule: Schedule) -> None:
        self.items[schedule.id] = schedule

    def get(self, schedule_id: ScheduleId) -> Schedule | None:
        return self.items.get(schedule_id)

    def save(self, schedule: Schedule) -> None:
        self.items[schedule.id] = schedule

    def list_for_task(self, task_id: TaskId, limit: int) -> list[Schedule]:
        return sorted(  # noqa: E501
            (x for x in self.items.values() if x.task_id == task_id),
            key=lambda x: (x.created_at, str(x.id)),
        )[:limit]

    def list_for_task_page(
        self, task_id: TaskId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Schedule]:
        rows = self.list_for_task(task_id, len(self.items))
        if after_created_at is not None and after_id is not None:
            rows = [x for x in rows if (x.created_at, str(x.id)) > (after_created_at, after_id)]
        return rows[:limit]

    def list_due(self, now: datetime, limit: int) -> list[Schedule]:
        values = (  # noqa: E501
            x
            for x in self.items.values()
            if x.status is ScheduleStatus.ACTIVE
            and x.next_fire_at is not None
            and x.next_fire_at <= now
        )
        return sorted(values, key=lambda x: (x.next_fire_at, str(x.id)))[:limit]

    def complete_for_task(self, task_id: TaskId, at: datetime, *, cancelled: bool) -> None:
        for schedule in self.items.values():
            if schedule.task_id == task_id and schedule.status in (  # noqa: E501
                ScheduleStatus.ACTIVE,
                ScheduleStatus.PAUSED,
            ):
                schedule.cancel(at) if cancelled else schedule.complete(at)


class FakeConversationRepository:
    def __init__(self) -> None:
        self.items: dict[ConversationId, Conversation] = {}

    def add(self, conversation: Conversation) -> None:
        self.items[conversation.id] = conversation

    def get(self, conversation_id: ConversationId) -> Conversation | None:
        return self.items.get(conversation_id)

    def save(self, conversation: Conversation) -> None:
        self.items[conversation.id] = conversation


class FakeConversationTurnRepository:
    def __init__(self) -> None:
        self.items: dict[ConversationTurnId, ConversationTurn] = {}

    def add(self, turn: ConversationTurn) -> None:
        from friday.application.errors import EntityConflict

        if self.get_by_client_turn_id(turn.conversation_id, turn.client_turn_id) or self.get_by_run(
            turn.run_id
        ):
            raise EntityConflict("duplicate conversation turn")
        self.items[turn.id] = turn

    def get(self, turn_id: ConversationTurnId) -> ConversationTurn | None:
        return self.items.get(turn_id)

    def get_by_client_turn_id(
        self, conversation_id: ConversationId, client_turn_id: str
    ) -> ConversationTurn | None:
        return next(
            (
                turn
                for turn in self.items.values()
                if turn.conversation_id == conversation_id and turn.client_turn_id == client_turn_id
            ),
            None,
        )

    def get_by_run(self, run_id: RunId) -> ConversationTurn | None:
        return next((turn for turn in self.items.values() if turn.run_id == run_id), None)

    def list_for_conversation_page(
        self,
        conversation_id: ConversationId,
        limit: int,
        after_created_at: datetime | None,
        after_id: str | None,
    ) -> list[ConversationTurn]:
        turns = sorted(
            (turn for turn in self.items.values() if turn.conversation_id == conversation_id),
            key=lambda turn: (turn.created_at, str(turn.id)),
        )
        if after_created_at is not None and after_id is not None:
            turns = [
                turn
                for turn in turns
                if (turn.created_at, str(turn.id)) > (after_created_at, after_id)
            ]
        return turns[:limit]

    def list_recent_before(
        self,
        conversation_id: ConversationId,
        before_created_at: datetime | None,
        before_id: str | None,
        limit: int,
    ) -> list[ConversationTurn]:
        turns = self.list_for_conversation_page(conversation_id, len(self.items), None, None)
        if before_created_at is not None and before_id is not None:
            turns = [
                turn
                for turn in turns
                if (turn.created_at, str(turn.id)) < (before_created_at, before_id)
            ]
        return turns[-limit:]


class FakeScheduleFireRepository:
    def __init__(self) -> None:
        self.items: list[ScheduleFire] = []
        self._runs: FakeRunRepository | None = None

    def add(self, fire: ScheduleFire) -> None:
        if any(  # noqa: E501
            x.schedule_id == fire.schedule_id and x.scheduled_for == fire.scheduled_for
            for x in self.items
        ):
            from friday.application.errors import EntityConflict

            raise EntityConflict("duplicate schedule occurrence")
        self.items.append(fire)

    def list_for_schedule(self, schedule_id: ScheduleId, limit: int) -> list[ScheduleFire]:
        return sorted(  # noqa: E501
            (x for x in self.items if x.schedule_id == schedule_id),
            key=lambda x: (x.scheduled_for, str(x.id)),
        )[:limit]

    def list_for_schedule_page(  # noqa: E501
        self,
        schedule_id: ScheduleId,
        limit: int,
        after_scheduled_for: datetime | None,
        after_id: str | None,
    ) -> list[ScheduleFire]:
        rows = self.list_for_schedule(schedule_id, len(self.items))
        if after_scheduled_for is not None and after_id is not None:
            rows = [
                x for x in rows if (x.scheduled_for, str(x.id)) > (after_scheduled_for, after_id)
            ]
        return rows[:limit]

    def has_non_terminal_execution_for_schedule(self, schedule_id: ScheduleId) -> bool:
        from friday.domain.run import TERMINAL_RUN_STATUSES

        roots = {x.run_id for x in self.items if x.schedule_id == schedule_id}
        assert self._runs is not None
        return any(
            run.execution_id in roots and run.status not in TERMINAL_RUN_STATUSES
            for run in self._runs.items.values()
        )


class FakeScheduleDeliveryPolicyRepository:
    def __init__(self) -> None:
        self.items: dict[ScheduleId, ScheduleDeliveryPolicy] = {}

    def get_for_schedule(self, schedule_id: ScheduleId) -> ScheduleDeliveryPolicy | None:
        policy = self.items.get(schedule_id)
        return self._copy(policy) if policy is not None else None

    def save(self, policy: ScheduleDeliveryPolicy) -> None:
        # Compatibility seed helper for tests; production has no unrestricted
        # policy save surface. Store a detached value like a real mapper.
        self.items[policy.schedule_id] = self._copy(policy)

    def put_for_nonterminal_schedule(self, policy: ScheduleDeliveryPolicy) -> bool:
        self.items[policy.schedule_id] = self._copy(policy)
        return True

    @staticmethod
    def _copy(policy: ScheduleDeliveryPolicy) -> ScheduleDeliveryPolicy:
        return ScheduleDeliveryPolicy.reconstruct(
            schedule_id=policy.schedule_id,
            route_id=policy.route_id,
            enabled=policy.enabled,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
        )


class FakeScheduleFireDeliveryPlanRepository:
    def __init__(self) -> None:
        self.items: dict[ScheduleFireId, ScheduleFireDeliveryPlan] = {}

    def add(self, plan: ScheduleFireDeliveryPlan) -> None:
        if plan.schedule_fire_id in self.items:
            from friday.application.errors import EntityConflict

            raise EntityConflict("duplicate schedule fire delivery plan")
        self.items[plan.schedule_fire_id] = plan

    def add_for_fire(self, plan: ScheduleFireDeliveryPlan, fire: ScheduleFire) -> None:
        if (
            plan.schedule_fire_id != fire.id
            or plan.schedule_id != fire.schedule_id
            or plan.execution_id != fire.run_id
        ):
            from friday.application.errors import EntityConflict

            raise EntityConflict("delivery plan must bind exactly to its ScheduleFire")
        self.add(plan)

    def get_by_fire(self, schedule_fire_id: ScheduleFireId) -> ScheduleFireDeliveryPlan | None:
        return self.items.get(schedule_fire_id)


class FakeRunEventStore:
    def __init__(self) -> None:
        self.appended: list[RunEvent] = []
        self._next_sequences: dict[RunId, int] = {}

    def append(self, event: RunEvent) -> None:
        self.appended.append(event)

    def latest_of_type_for_run(self, run_id: RunId, event_type: RunEventType) -> RunEvent | None:
        events = [
            event for event in self.appended if event.run_id == run_id and event.type is event_type
        ]
        return max(events, key=lambda event: event.sequence) if events else None

    def list_for_run(self, run_id: RunId) -> list[RunEvent]:
        matching = [event for event in self.appended if event.run_id == run_id]
        return sorted(matching, key=lambda event: event.sequence)

    def list_after_sequence(self, run_id: RunId, after_sequence: int, limit: int) -> list[RunEvent]:
        return [event for event in self.list_for_run(run_id) if event.sequence > after_sequence][
            :limit
        ]

    def reserve_sequences(self, run_id: RunId, count: int) -> int:
        start = self._next_sequences.get(run_id, 1)
        self._next_sequences[run_id] = start + count
        return start


class FakeTaskEventStore:
    def __init__(self) -> None:
        self.appended: list[TaskEvent] = []
        self._next_sequences: dict[TaskId, int] = {}

    def append(self, event: TaskEvent) -> None:
        self.appended.append(event)

    def reserve_sequences(self, task_id: TaskId, count: int) -> int:
        start = self._next_sequences.get(task_id, 1)
        self._next_sequences[task_id] = start + count
        return start

    def list_for_task(self, task_id: TaskId) -> list[TaskEvent]:
        return sorted(
            (event for event in self.appended if event.task_id == task_id),
            key=lambda event: event.sequence,
        )

    def list_after_sequence(
        self, task_id: TaskId, after_sequence: int, limit: int
    ) -> list[TaskEvent]:
        return [event for event in self.list_for_task(task_id) if event.sequence > after_sequence][
            :limit
        ]


class FakeRunStepRepository:
    def __init__(self) -> None:
        self.items: dict[RunStepId, RunStep] = {}

    def add(self, step: RunStep) -> None:
        self.items[step.id] = step

    def get(self, step_id: RunStepId) -> RunStep | None:
        return self.items.get(step_id)

    def save(self, step: RunStep) -> None:
        self.items[step.id] = step

    def list_for_run(self, run_id: RunId) -> list[RunStep]:
        return sorted(
            (s for s in self.items.values() if s.run_id == run_id),
            key=lambda s: (s.position, str(s.id)),
        )

    def has_non_terminal_for_run(self, run_id: RunId) -> bool:
        return any(
            step.status not in TERMINAL_RUN_STEP_STATUSES
            for step in self.items.values()
            if step.run_id == run_id
        )

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_position: int | None, after_id: str | None
    ) -> list[RunStep]:
        steps = self.list_for_run(run_id)
        if after_position is not None and after_id is not None:
            steps = [
                step for step in steps if (step.position, str(step.id)) > (after_position, after_id)
            ]
        return steps[:limit]


class FakeToolInvocationRepository:
    def __init__(self) -> None:
        self.items: dict[ToolInvocationId, ToolInvocation] = {}

    def add(self, item: ToolInvocation) -> None:
        self.items[item.id] = item

    def get(self, item_id: ToolInvocationId) -> ToolInvocation | None:
        return self.items.get(item_id)

    def save(self, item: ToolInvocation) -> None:
        self.items[item.id] = item

    def list_for_run(self, run_id: RunId) -> list[ToolInvocation]:
        return sorted(
            (i for i in self.items.values() if i.run_id == run_id),
            key=lambda i: (i.requested_at, str(i.id)),
        )

    def has_non_terminal_for_run(self, run_id: RunId) -> bool:
        return any(
            invocation.status not in TERMINAL_TOOL_INVOCATION_STATUSES
            for invocation in self.items.values()
            if invocation.run_id == run_id
        )

    def list_for_step(self, step_id: RunStepId) -> list[ToolInvocation]:
        return sorted(
            (i for i in self.items.values() if i.step_id == step_id),
            key=lambda i: (i.requested_at, str(i.id)),
        )

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_requested_at: datetime | None, after_id: str | None
    ) -> list[ToolInvocation]:
        invocations = self.list_for_run(run_id)
        if after_requested_at is not None and after_id is not None:
            invocations = [
                invocation
                for invocation in invocations
                if (invocation.requested_at, str(invocation.id)) > (after_requested_at, after_id)
            ]
        return invocations[:limit]

    def list_for_step_page(
        self,
        step_id: RunStepId,
        limit: int,
        after_requested_at: datetime | None,
        after_id: str | None,
    ) -> list[ToolInvocation]:
        invocations = self.list_for_step(step_id)
        if after_requested_at is not None and after_id is not None:
            invocations = [
                invocation
                for invocation in invocations
                if (invocation.requested_at, str(invocation.id)) > (after_requested_at, after_id)
            ]
        return invocations[:limit]


def _expired_claim_order(delivery: OutboundDelivery) -> tuple[datetime, str]:
    assert delivery.claim_expires_at is not None
    return (delivery.claim_expires_at, str(delivery.id))


class FakeOutboundDeliveryRepository:
    def __init__(self) -> None:
        self.items: dict[DeliveryId, OutboundDelivery] = {}

    def add(self, delivery: OutboundDelivery) -> None:
        from friday.application.errors import EntityConflict

        if delivery.source_tool_invocation_id is not None and any(
            item.source_tool_invocation_id == delivery.source_tool_invocation_id
            for item in self.items.values()
        ):
            raise EntityConflict("write violated a uniqueness or state constraint")
        if delivery.source_schedule_fire_id is not None and any(
            item.source_schedule_fire_id == delivery.source_schedule_fire_id
            for item in self.items.values()
        ):
            raise EntityConflict("write violated a uniqueness or state constraint")
        self.items[delivery.id] = delivery

    def get(self, delivery_id: DeliveryId) -> OutboundDelivery | None:
        stored = self.items.get(delivery_id)
        # Detached copy, like a real read: a caller mutating the aggregate must
        # go through a fenced write for the change to become durable.
        return deepcopy(stored) if stored is not None else None

    def get_by_source_tool_invocation_id(
        self, invocation_id: ToolInvocationId
    ) -> OutboundDelivery | None:
        for delivery in self.items.values():
            if delivery.source_tool_invocation_id == invocation_id:
                return deepcopy(delivery)
        return None

    def list_due(self, now: datetime, limit: int) -> list[OutboundDelivery]:
        deliveries = [
            delivery
            for delivery in self.items.values()
            if delivery.status is DeliveryStatus.QUEUED and delivery.available_at <= now
        ]
        deliveries.sort(key=lambda delivery: (delivery.available_at, str(delivery.id)))
        return [deepcopy(delivery) for delivery in deliveries[:limit]]

    def find_expired_claims(self, now: datetime, limit: int) -> list[OutboundDelivery]:
        expired = [
            delivery
            for delivery in self.items.values()
            if delivery.status is DeliveryStatus.SENDING
            and delivery.claim_expires_at is not None
            and delivery.claim_expires_at <= now
        ]
        expired.sort(key=_expired_claim_order)
        return [deepcopy(delivery) for delivery in expired[:limit]]

    def try_claim(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> int | None:
        delivery = self.items.get(delivery_id)
        if (
            delivery is None
            or delivery.status is not DeliveryStatus.QUEUED
            or delivery.available_at > now
        ):
            return None
        delivery.mark_sending(
            at=now,
            claim_owner=worker_id,
            claim_token=claim_token,
            claim_expires_at=lease_expires_at,
        )
        return delivery.claim_generation

    def _active_claim(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> OutboundDelivery | None:
        delivery = self.items.get(delivery_id)
        if (
            delivery is None
            or delivery.status is not DeliveryStatus.SENDING
            or delivery.claim_owner != worker_id
            or delivery.claim_token != claim_token
            or delivery.claim_generation != claim_generation
            or delivery.claim_expires_at is None
            or delivery.claim_expires_at <= now
        ):
            return None
        return delivery

    def is_claim_active(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        return (
            self._active_claim(delivery_id, worker_id, claim_token, claim_generation, now)
            is not None
        )

    def mark_dispatch_started(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        delivery = self._active_claim(delivery_id, worker_id, claim_token, claim_generation, now)
        if delivery is None or delivery.dispatch_started_at is not None:
            return False
        delivery.mark_dispatch_started(at=now)
        return True

    def save_claimed_lifecycle(
        self,
        delivery: OutboundDelivery,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        stored = self._active_claim(delivery.id, worker_id, claim_token, claim_generation, now)
        if stored is None:
            return False
        if delivery.status is DeliveryStatus.QUEUED and stored.dispatch_started_at is not None:
            return False
        # Mirror the SQL SET list: lifecycle columns only. Authority, content,
        # attempt_count, claim_generation and dispatch_started_at keep the
        # stored values, so a fenced write can never retarget a delivery.
        stored.status = delivery.status
        stored.available_at = delivery.available_at
        stored.claim_owner = delivery.claim_owner
        stored.claim_token = delivery.claim_token
        stored.claim_expires_at = delivery.claim_expires_at
        stored.provider_message_id = delivery.provider_message_id
        stored.failure_code = delivery.failure_code
        stored.failure_message = delivery.failure_message
        stored.delivered_at = delivery.delivered_at
        stored.updated_at = delivery.updated_at
        return True

    def requeue_expired_pre_dispatch(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: datetime,
        available_at: datetime,
    ) -> bool:
        delivery = self._expired_claim(delivery_id, claim_generation, now)
        if delivery is None or delivery.dispatch_started_at is not None:
            return False
        delivery.release_for_retry(at=now, available_at=available_at)
        return True

    def fail_expired_pre_dispatch(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: datetime,
        failure_code: str,
        failure_message: str,
    ) -> bool:
        delivery = self._expired_claim(delivery_id, claim_generation, now)
        if delivery is None or delivery.dispatch_started_at is not None:
            return False
        delivery.fail(at=now, failure_code=failure_code, failure_message=failure_message)
        delivery.claim_owner = None
        delivery.claim_token = None
        delivery.claim_expires_at = None
        return True

    def mark_expired_post_dispatch_ambiguous(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: datetime,
        failure_code: str,
        failure_message: str,
    ) -> bool:
        delivery = self._expired_claim(delivery_id, claim_generation, now)
        if delivery is None or delivery.dispatch_started_at is None:
            return False
        delivery.mark_ambiguous(at=now, failure_code=failure_code, failure_message=failure_message)
        delivery.claim_owner = None
        delivery.claim_token = None
        delivery.claim_expires_at = None
        return True

    def _expired_claim(
        self, delivery_id: DeliveryId, claim_generation: int, now: datetime
    ) -> OutboundDelivery | None:
        delivery = self.items.get(delivery_id)
        if (
            delivery is None
            or delivery.status is not DeliveryStatus.SENDING
            or delivery.claim_generation != claim_generation
            or delivery.claim_expires_at is None
            or delivery.claim_expires_at > now
        ):
            return None
        return delivery


class FakeDeliveryAttemptRepository:
    """In-memory mirror of the claim-fenced ledger, with no generic insert.

    Deliberately exposes the same surface as the real repository — including
    the absence of `add`/`save` — so a test cannot manufacture a durable
    attempt the production code path could not have produced.
    """

    def __init__(self, deliveries: FakeOutboundDeliveryRepository) -> None:
        self._deliveries = deliveries
        self.items: dict[tuple[DeliveryId, int], DeliveryAttempt] = {}

    def begin_for_claim(  # noqa: PLR0913 — mirrors the port signature exactly
        self,
        attempt_id: DeliveryAttemptId,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        started_at: datetime,
        now: datetime,
    ) -> bool:
        delivery = self._deliveries._active_claim(
            delivery_id, worker_id, claim_token, claim_generation, now
        )
        if (
            delivery is None
            or delivery.dispatch_started_at is None
            or delivery.dispatch_started_at != started_at
        ):
            return False
        key = (delivery_id, claim_generation)
        if key in self.items:
            # Mirrors UNIQUE(delivery_id, claim_generation) in SQLite.
            from friday.application.errors import EntityConflict

            raise EntityConflict("write violated a uniqueness or state constraint")
        self.items[key] = DeliveryAttempt.begin(
            id=attempt_id,
            delivery_id=delivery_id,
            claim_generation=claim_generation,
            started_at=started_at,
        )
        return True

    def get_for_generation(
        self, delivery_id: DeliveryId, claim_generation: int
    ) -> DeliveryAttempt | None:
        found = self.items.get((delivery_id, claim_generation))
        return deepcopy(found) if found else None

    def list_for_delivery(self, delivery_id: DeliveryId, limit: int) -> list[DeliveryAttempt]:
        validate_delivery_attempt_history_limit(limit)
        return [
            deepcopy(item)
            for item in sorted(
                (a for a in self.items.values() if a.delivery_id == delivery_id),
                key=lambda a: (a.started_at, str(a.id)),
                reverse=True,
            )[:limit]
        ]

    def _validated_terminal_write(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: datetime,
        outcome: DeliveryAttemptOutcome,
        failure_code: str | None,
    ) -> None:
        """Reject an invalid terminal shape before any state changes.

        Matches the real repository: validation runs first and raises, so an
        invalid outcome/failure-code pair produces zero durable mutation
        whether or not a closeable row exists.
        """
        attempt = self.items.get((delivery_id, claim_generation))
        closeable = attempt is not None and attempt.outcome is DeliveryAttemptOutcome.IN_PROGRESS
        validate_delivery_attempt_shape(
            outcome=outcome,
            started_at=attempt.started_at if closeable and attempt is not None else now,
            finished_at=now,
            failure_code=failure_code,
        )

    def complete_for_claim(  # noqa: PLR0913 — mirrors the port signature exactly
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
        outcome: DeliveryAttemptOutcome,
        failure_code: str | None,
    ) -> bool:
        self._validated_terminal_write(delivery_id, claim_generation, now, outcome, failure_code)
        if not self._deliveries.is_claim_active(
            delivery_id, worker_id, claim_token, claim_generation, now
        ):
            return False
        attempt = self.items.get((delivery_id, claim_generation))
        if attempt is None or attempt.outcome is not DeliveryAttemptOutcome.IN_PROGRESS:
            return False
        attempt.complete(outcome=outcome, finished_at=now, failure_code=failure_code)
        return True

    def close_expired_as_ambiguous(
        self, delivery_id: DeliveryId, claim_generation: int, now: datetime, failure_code: str
    ) -> bool:
        self._validated_terminal_write(
            delivery_id, claim_generation, now, DeliveryAttemptOutcome.AMBIGUOUS, failure_code
        )
        delivery = self._deliveries._expired_claim(delivery_id, claim_generation, now)
        attempt = self.items.get((delivery_id, claim_generation))
        if (
            delivery is None
            or delivery.dispatch_started_at is None
            or attempt is None
            or attempt.outcome is not DeliveryAttemptOutcome.IN_PROGRESS
        ):
            return False
        attempt.complete(
            outcome=DeliveryAttemptOutcome.AMBIGUOUS, finished_at=now, failure_code=failure_code
        )
        return True


class FakeApprovalRepository:
    def __init__(self) -> None:
        self.items: dict[ApprovalRequestId, ApprovalRequest] = {}

    def add(self, approval: ApprovalRequest) -> None:
        self.items[approval.id] = approval

    def get(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None:
        return self.items.get(approval_id)

    def save(self, approval: ApprovalRequest) -> None:
        self.items[approval.id] = approval

    def list_pending_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        matching = [
            a
            for a in self.items.values()
            if a.run_id == run_id and a.status is ApprovalStatus.PENDING
        ]
        return sorted(matching, key=lambda a: (a.requested_at, str(a.id)))

    def has_pending_for_run(self, run_id: RunId) -> bool:
        return any(
            approval.run_id == run_id and approval.status is ApprovalStatus.PENDING
            for approval in self.items.values()
        )

    def list_due_for_expiry(self, now: datetime, limit: int) -> list[ApprovalRequest]:
        matching = [
            approval
            for approval in self.items.values()
            if approval.status is ApprovalStatus.PENDING
            and approval.expires_at is not None
            and approval.expires_at <= now
        ]
        matching.sort(key=lambda approval: (approval.requested_at, str(approval.id)))
        return matching[:limit]

    def list_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        return sorted(
            (approval for approval in self.items.values() if approval.run_id == run_id),
            key=lambda approval: (approval.requested_at, str(approval.id)),
        )

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_requested_at: datetime | None, after_id: str | None
    ) -> list[ApprovalRequest]:
        approvals = self.list_for_run(run_id)
        if after_requested_at is not None and after_id is not None:
            approvals = [
                approval
                for approval in approvals
                if (approval.requested_at, str(approval.id)) > (after_requested_at, after_id)
            ]
        return approvals[:limit]


class FakeArtifactRepository:
    def __init__(self) -> None:
        self.items: dict[ArtifactId, Artifact] = {}

    def add(self, artifact: Artifact) -> None:
        self.items[artifact.id] = artifact

    def get(self, artifact_id: ArtifactId) -> Artifact | None:
        return self.items.get(artifact_id)

    def list_for_run(self, run_id: RunId) -> list[Artifact]:
        return sorted(
            (a for a in self.items.values() if a.run_id == run_id),
            key=lambda a: (a.created_at, str(a.id)),
        )

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Artifact]:
        artifacts = self.list_for_run(run_id)
        if after_created_at is not None and after_id is not None:
            artifacts = [
                artifact
                for artifact in artifacts
                if (artifact.created_at, str(artifact.id)) > (after_created_at, after_id)
            ]
        return artifacts[:limit]


class FakeRunWorkQueue:
    def __init__(self) -> None:
        self.items: dict[RunId, RunWorkItemView] = {}

    def enqueue(self, run_id: RunId, available_at: datetime, enqueued_at: datetime) -> None:
        existing = self.items.get(run_id)
        if existing is None:
            self.items[run_id] = RunWorkItemView(
                run_id=run_id,
                available_at=available_at,
                enqueued_at=enqueued_at,
                claimed_by=None,
                claim_token=None,
                claim_generation=0,
                claimed_at=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
            return
        self.items[run_id] = RunWorkItemView(
            run_id=run_id,
            available_at=available_at,
            enqueued_at=enqueued_at,
            claimed_by=existing.claimed_by,
            claim_token=existing.claim_token,
            claim_generation=existing.claim_generation,
            claimed_at=existing.claimed_at,
            heartbeat_at=existing.heartbeat_at,
            lease_expires_at=existing.lease_expires_at,
        )

    def get(self, run_id: RunId) -> RunWorkItemView | None:
        return self.items.get(run_id)

    def find_due_candidates(self, now: datetime, limit: int) -> builtins.list[RunWorkItemView]:
        candidates = [
            item
            for item in self.items.values()
            if item.available_at <= now
            and (
                item.claimed_by is None
                or (item.lease_expires_at is not None and item.lease_expires_at <= now)
            )
        ]
        candidates.sort(key=lambda item: (item.available_at, item.enqueued_at, str(item.run_id)))
        return candidates[:limit]

    def find_expired_claims(self, now: datetime, limit: int) -> builtins.list[RunWorkItemView]:
        candidates = [
            item
            for item in self.items.values()
            if item.claimed_by is not None
            and item.lease_expires_at is not None
            and item.lease_expires_at <= now
        ]
        candidates.sort(key=lambda item: (item.available_at, item.enqueued_at, str(item.run_id)))
        return candidates[:limit]

    def remove(self, run_id: RunId) -> None:
        self.items.pop(run_id, None)

    def try_claim(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        item = self.items.get(run_id)
        if item is None or item.available_at > now:
            return False
        if item.claimed_by is not None and not (
            item.lease_expires_at is not None and item.lease_expires_at <= now
        ):
            return False
        self.items[run_id] = RunWorkItemView(
            run_id=run_id,
            available_at=item.available_at,
            enqueued_at=item.enqueued_at,
            claimed_by=worker_id,
            claim_token=claim_token,
            claim_generation=item.claim_generation + 1,
            claimed_at=now,
            heartbeat_at=now,
            lease_expires_at=lease_expires_at,
        )
        return True

    def renew_lease(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        item = self.items.get(run_id)
        if not self._owns(item, worker_id, claim_token, claim_generation):
            return False
        assert item is not None
        if item.lease_expires_at is None or item.lease_expires_at <= now:
            return False
        self.items[run_id] = RunWorkItemView(
            run_id=run_id,
            available_at=item.available_at,
            enqueued_at=item.enqueued_at,
            claimed_by=item.claimed_by,
            claim_token=item.claim_token,
            claim_generation=item.claim_generation,
            claimed_at=item.claimed_at,
            heartbeat_at=now,
            lease_expires_at=lease_expires_at,
        )
        return True

    def release_claim(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int, now: datetime
    ) -> bool:
        item = self.items.get(run_id)
        if not self._owns_active(item, worker_id, claim_token, claim_generation, now):
            return False
        assert item is not None
        self.items[run_id] = RunWorkItemView(
            run_id=run_id,
            available_at=item.available_at,
            enqueued_at=item.enqueued_at,
            claimed_by=None,
            claim_token=None,
            claim_generation=item.claim_generation,
            claimed_at=None,
            heartbeat_at=None,
            lease_expires_at=None,
        )
        return True

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
        item = self.items.get(run_id)
        if not self._owns_active(item, worker_id, claim_token, claim_generation, now):
            return False
        assert item is not None
        self.items[run_id] = RunWorkItemView(
            run_id=run_id,
            available_at=available_at,
            enqueued_at=enqueued_at,
            claimed_by=None,
            claim_token=None,
            claim_generation=item.claim_generation,
            claimed_at=None,
            heartbeat_at=None,
            lease_expires_at=None,
        )
        return True

    def remove_if_claimed(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int, now: datetime
    ) -> bool:
        item = self.items.get(run_id)
        if not self._owns_active(item, worker_id, claim_token, claim_generation, now):
            return False
        del self.items[run_id]
        return True

    def clear_expired_claim(self, run_id: RunId, now: datetime) -> bool:
        item = self.items.get(run_id)
        if not self._is_expired(item, now):
            return False
        assert item is not None
        self.items[run_id] = RunWorkItemView(
            run_id=item.run_id,
            available_at=item.available_at,
            enqueued_at=item.enqueued_at,
            claimed_by=None,
            claim_token=None,
            claim_generation=item.claim_generation,
            claimed_at=None,
            heartbeat_at=None,
            lease_expires_at=None,
        )
        return True

    def remove_if_lease_expired(self, run_id: RunId, now: datetime) -> bool:
        item = self.items.get(run_id)
        if not self._is_expired(item, now):
            return False
        del self.items[run_id]
        return True

    def is_claim_active(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        item = self.items.get(run_id)
        return (
            self._owns(item, worker_id, claim_token, claim_generation)
            and item is not None
            and item.lease_expires_at is not None
            and item.lease_expires_at > now
        )

    @staticmethod
    def _owns(
        item: RunWorkItemView | None, worker_id: str, claim_token: str, claim_generation: int
    ) -> bool:
        return (
            item is not None
            and item.claimed_by == worker_id
            and item.claim_token == claim_token
            and item.claim_generation == claim_generation
        )

    @staticmethod
    def _owns_active(
        item: RunWorkItemView | None,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        return (
            FakeRunWorkQueue._owns(item, worker_id, claim_token, claim_generation)
            and item is not None
            and item.lease_expires_at is not None
            and item.lease_expires_at > now
        )

    @staticmethod
    def _is_expired(item: RunWorkItemView | None, now: datetime) -> bool:
        return (
            item is not None
            and item.claimed_by is not None
            and item.lease_expires_at is not None
            and item.lease_expires_at <= now
        )


class FakeMemoryIndexSnapshotRepository:
    def __init__(self) -> None:
        self.items: list[IndexSnapshot] = []

    def add(self, snapshot: IndexSnapshot) -> None:
        self.items.append(snapshot)

    def latest(self) -> IndexSnapshot | None:
        if not self.items:
            return None
        return max(self.items, key=lambda snapshot: snapshot.built_at)

    def mark_stale(self, snapshot_id: str) -> None:
        for index, snapshot in enumerate(self.items):
            if snapshot.id == snapshot_id:
                self.items[index] = replace(snapshot, state=IndexState.STALE)


class FakeMemoryRetrievalRecordRepository:
    def __init__(self) -> None:
        self.added: list[MemoryRetrievalRecord] = []

    def add(self, record: MemoryRetrievalRecord) -> None:
        self.added.append(record)


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.task_repo = FakeTaskRepository()
        self.run_repo = FakeRunRepository()
        self.schedule_repo = FakeScheduleRepository()
        self.conversation_repo = FakeConversationRepository()
        self.conversation_turn_repo = FakeConversationTurnRepository()
        self.schedule_fire_repo = FakeScheduleFireRepository()
        self.schedule_fire_repo._runs = self.run_repo
        self.schedule_delivery_policy_repo = FakeScheduleDeliveryPolicyRepository()
        self.schedule_fire_delivery_plan_repo = FakeScheduleFireDeliveryPlanRepository()
        self.event_store = FakeRunEventStore()
        self.task_event_store = FakeTaskEventStore()
        self.step_repo = FakeRunStepRepository()
        self.tool_repo = FakeToolInvocationRepository()
        self.delivery_repo = FakeOutboundDeliveryRepository()
        self.delivery_attempt_repo = FakeDeliveryAttemptRepository(self.delivery_repo)
        self.approval_repo = FakeApprovalRepository()
        self.artifact_repo = FakeArtifactRepository()
        self.work_queue_repo = FakeRunWorkQueue()
        self.memory_snapshot_repo = FakeMemoryIndexSnapshotRepository()
        self.memory_retrieval_repo = FakeMemoryRetrievalRecordRepository()
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    @property
    def tasks(self) -> TaskRepository:
        return self.task_repo

    @property
    def runs(self) -> RunRepository:
        return self.run_repo

    @property
    def schedules(self) -> ScheduleRepository:
        return self.schedule_repo

    @property
    def conversations(self) -> ConversationRepository:
        return self.conversation_repo

    @property
    def conversation_turns(self) -> ConversationTurnRepository:
        return self.conversation_turn_repo

    @property
    def schedule_fires(self) -> ScheduleFireRepository:
        return self.schedule_fire_repo

    @property
    def schedule_delivery_policies(self) -> ScheduleDeliveryPolicyRepository:
        return self.schedule_delivery_policy_repo

    @property
    def schedule_fire_delivery_plans(self) -> ScheduleFireDeliveryPlanRepository:
        return self.schedule_fire_delivery_plan_repo

    @property
    def steps(self) -> RunStepRepository:
        return self.step_repo

    @property
    def approvals(self) -> ApprovalRepository:
        return self.approval_repo

    @property
    def artifacts(self) -> ArtifactRepository:
        return self.artifact_repo

    @property
    def tool_invocations(self) -> ToolInvocationRepository:
        return self.tool_repo

    @property
    def deliveries(self) -> OutboundDeliveryRepository:
        return self.delivery_repo

    @property
    def delivery_attempts(self) -> DeliveryAttemptRepository:
        return self.delivery_attempt_repo

    @property
    def events(self) -> RunEventStore:
        return self.event_store

    @property
    def task_events(self) -> TaskEventStore:
        return self.task_event_store

    @property
    def work_queue(self) -> RunWorkQueue:
        return self.work_queue_repo

    @property
    def memory_index_snapshots(self) -> MemoryIndexSnapshotRepository:
        return self.memory_snapshot_repo

    @property
    def memory_retrieval_records(self) -> MemoryRetrievalRecordRepository:
        return self.memory_retrieval_repo

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        self.closed = True

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class CountingUnitOfWorkFactory:
    """Returns one prepared FakeUnitOfWork and counts invocations."""

    def __init__(self, uow: FakeUnitOfWork) -> None:
        self.uow = uow
        self.calls = 0

    def __call__(self) -> FakeUnitOfWork:
        self.calls += 1
        return self.uow
