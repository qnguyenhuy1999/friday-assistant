"""Approval-resolution race (25.6), against real SQLite and independent
Units of Work through the real WorkerLoop:

    worker claims Run
      -> processor requests approval (parks the work item)
      -> ANOTHER transaction approves it (run resumes, fresh work item)
      -> the old worker still returns waiting_for_approval

The old worker's outcome application must fence cleanly: no exception
escapes the loop, the fresh work item survives, the old claim cannot remove
it, the run stays resumable, and the approval audit remains intact."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from apps.worker.app import Worker
from friday.application.approval_workflow import ApproveRequest
from friday.application.commands import ApproveRequestCommand, RequestApprovalCommand
from friday.application.run_processor import ClaimContext, ProcessingOutcome
from friday.application.tool_authorization import RequestToolApproval
from friday.domain.approval import ApprovalCategory, ApprovalStatus
from friday.domain.event import RunEventType
from friday.domain.identifiers import ApprovalRequestId, RunId
from friday.domain.run import RunStatus
from friday.infrastructure.clock import SystemClock
from friday.infrastructure.persistence.database import create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from tests.worker.test_worker_composition import build_worker, seed_queued_run

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FINISH = '{"version": 1, "action": "finish", "result": {"summary": "done"}}'


class RacingProcessor:
    """Requests an approval under its claim, then — before returning the
    waiting outcome — simulates a human approving it through an independent
    transaction. The waiting outcome the worker then applies is stale."""

    def __init__(self, worker: Worker) -> None:
        factory = create_unit_of_work_factory(create_session_factory(worker.engine))
        self._factory = factory
        self._clock = SystemClock()
        self.approval_id: ApprovalRequestId | None = None

    def process(self, context: ClaimContext) -> ProcessingOutcome:
        result = RequestToolApproval(self._factory, self._clock).execute(
            RequestApprovalCommand(
                run_id=context.run_id,
                category=ApprovalCategory.FILESYSTEM_WRITE,
                summary="write out.txt",
                reason="",
                requested_action="workspace.write_text",
                requested_input={"path": "out.txt", "content": "x"},
                authorization_fingerprint="a" * 64,
            ),
            worker_id=context.worker_id,
            claim_token=context.claim_token,
            claim_generation=context.claim_generation,
        )
        self.approval_id = result.approval_id
        # the race: an independent transaction resolves the approval before
        # this worker reports its waiting outcome
        ApproveRequest(self._factory, self._clock).execute(
            ApproveRequestCommand(approval_id=result.approval_id, resolver="patrick")
        )
        return ProcessingOutcome.waiting_for_approval(result.approval_id)


def test_stale_waiting_outcome_cannot_disturb_the_resumed_run(tmp_path: Path) -> None:
    worker = build_worker(tmp_path, [FINISH])
    try:
        run_id: RunId = seed_queued_run(worker)
        racing = RacingProcessor(worker)

        # must not raise: the loop treats the fenced stale outcome as ClaimLost
        assert worker.loop.run_once(racing) is True

        factory = create_unit_of_work_factory(create_session_factory(worker.engine))
        with factory() as uow:
            run = uow.runs.get(run_id)
            assert run is not None
            assert run.status is RunStatus.RUNNING  # resumed by the approval
            item = uow.work_queue.get(run_id)
            assert item is not None  # fresh work item survived
            assert item.claimed_by is None  # and is claimable by anyone
            approvals = uow.approvals.list_for_run(run_id)
            assert len(approvals) == 1
            assert approvals[0].status is ApprovalStatus.APPROVED
            event_types = [event.type for event in uow.events.list_for_run(run_id)]
            # audit intact and ordered: requested -> waiting -> resolved -> resumed
            assert RunEventType.APPROVAL_REQUESTED in event_types
            assert RunEventType.RUN_WAITING_FOR_APPROVAL in event_types
            assert RunEventType.APPROVAL_RESOLVED in event_types
            assert RunEventType.RUN_RESUMED in event_types

        # the run remains fully processable: the real processor finishes it
        assert worker.loop.run_once(worker.processor) is True
        with factory() as uow:
            run = uow.runs.get(run_id)
            assert run is not None
            assert run.status is RunStatus.SUCCEEDED
    finally:
        worker.engine.dispose()
