from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from friday.domain.failure import Failure
from friday.domain.identifiers import RunId, TaskId


@dataclass(frozen=True, slots=True)
class ClaimContext:
    run_id: RunId
    task_id: TaskId
    worker_id: str
    claim_token: str
    claim_generation: int
    attempt_number: int
    is_lease_lost: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    kind: Literal["succeeded", "failed", "waiting_for_approval", "yielded"]
    failure: Failure | None = None
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"succeeded", "failed", "waiting_for_approval", "yielded"}:
            raise ValueError(f"unsupported processing outcome kind: {self.kind}")
        if self.kind == "failed" and self.failure is None:
            raise ValueError("a 'failed' outcome requires a failure")
        if self.kind != "failed" and self.failure is not None:
            raise ValueError("only a 'failed' outcome may carry a failure")
        if self.kind == "yielded" and self.available_at is None:
            raise ValueError("a 'yielded' outcome requires available_at")
        if self.kind != "yielded" and self.available_at is not None:
            raise ValueError("only a 'yielded' outcome may carry available_at")

    @classmethod
    def succeeded(cls) -> ProcessingOutcome:
        return cls(kind="succeeded")

    @classmethod
    def failed(cls, failure: Failure) -> ProcessingOutcome:
        return cls(kind="failed", failure=failure)

    @classmethod
    def waiting_for_approval(cls) -> ProcessingOutcome:
        return cls(kind="waiting_for_approval")

    @classmethod
    def yielded(cls, available_at: datetime) -> ProcessingOutcome:
        return cls(kind="yielded", available_at=available_at)


class RunProcessor(Protocol):
    def process(self, context: ClaimContext) -> ProcessingOutcome: ...
