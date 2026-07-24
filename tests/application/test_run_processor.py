from __future__ import annotations

from datetime import timedelta

import pytest

from friday.application.run_processor import ProcessingOutcome
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import ApprovalRequestId
from tests.application.fakes import T0

FAILURE = Failure("test", "failed", retryable=False, cause=FailureCause.RUNTIME)


def test_processing_outcome_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        ProcessingOutcome(kind="bogus")  # type: ignore[arg-type]


def test_processing_outcome_rejects_missing_failed_failure() -> None:
    with pytest.raises(ValueError):
        ProcessingOutcome(kind="failed")


@pytest.mark.parametrize("kind", ["succeeded", "waiting_for_approval", "yielded"])
def test_processing_outcome_rejects_failure_on_non_failed_outcome(kind: str) -> None:
    with pytest.raises(ValueError):
        ProcessingOutcome(kind=kind, failure=FAILURE)  # type: ignore[arg-type]


def test_processing_outcome_rejects_missing_yielded_time() -> None:
    with pytest.raises(ValueError):
        ProcessingOutcome(kind="yielded")


def test_processing_outcome_convenience_constructors() -> None:
    succeeded = ProcessingOutcome.succeeded()
    failed = ProcessingOutcome.failed(FAILURE)
    waiting = ProcessingOutcome.waiting_for_approval(ApprovalRequestId.new())
    yielded = ProcessingOutcome.yielded(T0 + timedelta(minutes=1))

    assert succeeded.kind == "succeeded" and succeeded.failure is None
    assert failed.kind == "failed" and failed.failure == FAILURE
    assert waiting.kind == "waiting_for_approval" and waiting.failure is None
    assert yielded.kind == "yielded" and yielded.available_at == T0 + timedelta(minutes=1)
