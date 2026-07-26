"""Structured, secret-safe diagnostics for worker lifecycle transitions.

Durable domain events remain lifecycle authority. These records only make an
operator's investigation joinable across a worker process and its claims.
"""

from __future__ import annotations

import json
import logging
from typing import Final

_CORRELATION_FIELDS: Final = (
    "task_id",
    "run_id",
    "step_id",
    "approval_id",
    "invocation_id",
    "worker_id",
    "claim_generation",
)
_SAFE_FIELDS: Final = (
    *_CORRELATION_FIELDS,
    "recovered_count",
    "expired_approval_count",
    "run_count",
    "schedule_id",
    "scheduled_for",
)


class JsonOperationalFormatter(logging.Formatter):
    """Render worker logs as one JSON object without exception payloads."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname.lower(),
            "event": getattr(record, "event", record.getMessage()),
            "logger": record.name,
        }
        for field in _SAFE_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def configure_operational_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonOperationalFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def lifecycle_log(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: object,
) -> None:
    """Emit a fixed event name and whitelisted correlation fields only."""
    safe = {name: str(value) for name, value in fields.items() if name in _SAFE_FIELDS}
    logger.log(level, event, extra={"event": event, **safe})
