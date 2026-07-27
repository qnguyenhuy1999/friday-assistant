"""Deterministic worker entry point used only by browser E2E tests.

It composes the real worker, persistence, queue, processor and loop, while
substituting the BrainRuntime at its application boundary. No browser, SDK,
API, gateway, lifecycle, or persistence code is mocked.
"""

from __future__ import annotations

import os
import signal
import threading

from apps.worker.app import create_worker
from apps.worker.runtime_settings import RuntimeSettings
from apps.worker.settings import WorkerSettings
from friday.application.brain_runtime import BrainRequest, BrainResponse
from friday.application.runtime_actions import FinishAction, InvokeToolAction


class E2eFinishBrain:
    def next_action(self, request: BrainRequest) -> BrainResponse:
        del request
        return BrainResponse(
            action=FinishAction(
                summary="E2E task completed",
                details={"source": "deterministic-e2e-brain", "verified": True},
            )
        )


class E2eApprovalBrain:
    """Requests one real protected write for the approval browser proof."""

    def __init__(self) -> None:
        self._requested_runs: set[str] = set()

    def next_action(self, request: BrainRequest) -> BrainResponse:
        run_id = str(request.run_id)
        # Keep the ordinary conversation spec deterministic while allowing the
        # approval spec to opt in through its submitted text.
        if ": E2E approval proof\n" not in request.context:
            return E2eFinishBrain().next_action(request)
        if run_id not in self._requested_runs:
            self._requested_runs.add(run_id)
            return BrainResponse(
                action=InvokeToolAction(
                    tool="workspace.write_text",
                    tool_input={
                        "path": "approval-proof.txt",
                        "content": "approved E2E write",
                    },
                    reason="E2E approval proof requires a protected workspace write.",
                )
            )
        return BrainResponse(
            action=FinishAction(
                summary="E2E approval task completed",
                details={"source": "deterministic-e2e-approval-brain", "verified": True},
            )
        )


def main() -> None:
    brain = (
        E2eApprovalBrain() if os.environ.get("FRIDAY_E2E_BRAIN") == "approval" else E2eFinishBrain()
    )
    worker = create_worker(WorkerSettings.from_env(), RuntimeSettings.from_env(), brain=brain)
    shutdown_event = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        del signum, frame
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        worker.loop.serve_forever(shutdown_event, worker.processor)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
