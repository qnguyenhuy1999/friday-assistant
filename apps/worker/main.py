"""Entry point for the worker delivery process.

Construction is deliberately lazy and fail-closed: build() verifies the
Claude CLI's brain-only support and the workspace root before any Worker
exists, so a misconfigured environment can never claim a Run."""

from __future__ import annotations

import signal
import threading

from apps.worker.app import Worker, create_worker
from apps.worker.runtime_settings import RuntimeSettings
from apps.worker.settings import WorkerSettings


def build() -> Worker:
    return create_worker(WorkerSettings.from_env(), RuntimeSettings.from_env())


def main() -> None:
    worker = build()
    shutdown_event = threading.Event()

    def _handle_signal(signum, frame):  # type: ignore[no-untyped-def]
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        worker.loop.serve_forever(shutdown_event, worker.processor)
    finally:
        worker.engine.dispose()


if __name__ == "__main__":
    main()
