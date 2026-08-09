from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from friday.domain.failure import Failure
from friday.domain.identifiers import ApprovalRequestId, DelegationRequestId, RunId, TaskId
from friday.domain.json_value import JsonValue


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
    kind: Literal[
        "succeeded",
        "failed",
        "waiting_for_approval",
        "waiting_for_delegation",
        "yielded",
    ]
    failure: Failure | None = None
    available_at: datetime | None = None
    approval_request_id: ApprovalRequestId | None = None
    delegation_request_id: DelegationRequestId | None = None
    final_response: tuple[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        if self.kind not in {
            "succeeded",
            "failed",
            "waiting_for_approval",
            "waiting_for_delegation",
            "yielded",
        }:
            raise ValueError(f"unsupported processing outcome kind: {self.kind}")
        if self.kind == "failed" and self.failure is None:
            raise ValueError("a 'failed' outcome requires a failure")
        if self.kind != "failed" and self.failure is not None:
            raise ValueError("only a 'failed' outcome may carry a failure")
        if self.kind == "waiting_for_approval" and self.approval_request_id is None:
            raise ValueError("a 'waiting_for_approval' outcome requires approval_request_id")
        if self.kind != "waiting_for_approval" and self.approval_request_id is not None:
            raise ValueError("only a 'waiting_for_approval' outcome may carry approval_request_id")
        if self.kind == "waiting_for_delegation" and self.delegation_request_id is None:
            raise ValueError("a 'waiting_for_delegation' outcome requires delegation_request_id")
        if self.kind != "waiting_for_delegation" and self.delegation_request_id is not None:
            raise ValueError(
                "only a 'waiting_for_delegation' outcome may carry delegation_request_id"
            )
        if self.kind != "succeeded" and self.final_response is not None:
            raise ValueError("only a 'succeeded' outcome may carry final_response")
        if self.kind == "yielded" and self.available_at is None:
            raise ValueError("a 'yielded' outcome requires available_at")
        if self.kind != "yielded" and self.available_at is not None:
            raise ValueError("only a 'yielded' outcome may carry available_at")

    @classmethod
    def succeeded(cls, summary: str = "", details: JsonValue = None) -> ProcessingOutcome:
        return cls(kind="succeeded", final_response=(summary, details))

    @classmethod
    def failed(cls, failure: Failure) -> ProcessingOutcome:
        return cls(kind="failed", failure=failure)

    @classmethod
    def waiting_for_approval(cls, approval_request_id: ApprovalRequestId) -> ProcessingOutcome:
        return cls(kind="waiting_for_approval", approval_request_id=approval_request_id)

    @classmethod
    def waiting_for_delegation(
        cls, delegation_request_id: DelegationRequestId
    ) -> ProcessingOutcome:
        return cls(kind="waiting_for_delegation", delegation_request_id=delegation_request_id)

    @classmethod
    def yielded(cls, available_at: datetime) -> ProcessingOutcome:
        return cls(kind="yielded", available_at=available_at)


class RunProcessor(Protocol):
    def process(self, context: ClaimContext) -> ProcessingOutcome: ...
