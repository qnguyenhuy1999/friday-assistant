"""Deterministic worker entry point used only by browser E2E tests.

It composes the real worker, persistence, queue, processor and loop, while
substituting the BrainRuntime at its application boundary. No browser, SDK,
API, gateway, lifecycle, or persistence code is mocked.
"""

from __future__ import annotations

import signal
import threading

from apps.worker.app import create_worker
from apps.worker.runtime_settings import RuntimeSettings
from apps.worker.settings import WorkerSettings
from friday.application.brain_runtime import BrainRequest, BrainResponse
from friday.application.runtime_actions import FinishAction


class E2eFinishBrain:
    def next_action(self, request: BrainRequest) -> BrainResponse:
        del request
        return BrainResponse(
            action=FinishAction(
                summary="E2E task completed",
                details={"source": "deterministic-e2e-brain", "verified": True},
            )
        )


def main() -> None:
    worker = create_worker(
        WorkerSettings.from_env(), RuntimeSettings.from_env(), brain=E2eFinishBrain()
    )
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
