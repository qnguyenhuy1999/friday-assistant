"""Entry point for the worker delivery process."""

from __future__ import annotations

import signal
import threading

from apps.worker.app import create_worker
from apps.worker.settings import WorkerSettings

settings = WorkerSettings.from_env()
worker = create_worker(settings)


def main() -> None:
    shutdown_event = threading.Event()

    def _handle_signal(signum, frame):  # type: ignore[no-untyped-def]
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        processor = None  # RunProcessor implementation lands in Phase 11.
        worker.loop.serve_forever(shutdown_event, processor)
    finally:
        worker.engine.dispose()


if __name__ == "__main__":
    main()
