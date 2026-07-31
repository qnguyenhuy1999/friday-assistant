from __future__ import annotations

import json
import logging

from apps.worker.operational_logging import JsonOperationalFormatter, lifecycle_log


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.record: logging.LogRecord | None = None

    def emit(self, record: logging.LogRecord) -> None:
        self.record = record


def test_lifecycle_log_keeps_only_event_and_safe_correlation_fields() -> None:
    logger = logging.getLogger("test.operational")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        lifecycle_log(
            logger,
            logging.INFO,
            "worker.claimed_run",
            run_id="run-1",
            worker_id="worker-1",
            secret="must-not-log",
        )
    finally:
        logger.removeHandler(handler)

    assert handler.record is not None
    rendered = json.loads(JsonOperationalFormatter().format(handler.record))
    assert rendered["event"] == "worker.claimed_run"
    assert rendered["run_id"] == "run-1"
    assert rendered["worker_id"] == "worker-1"
    assert "secret" not in rendered


def test_lifecycle_log_keeps_scheduled_answer_delivery_count() -> None:
    logger = logging.getLogger("test.operational.delivery-count")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        lifecycle_log(logger, logging.INFO, "scheduler.answers_materialized", delivery_count=3)
    finally:
        logger.removeHandler(handler)

    assert handler.record is not None
    rendered = json.loads(JsonOperationalFormatter().format(handler.record))
    assert rendered["delivery_count"] == "3"
