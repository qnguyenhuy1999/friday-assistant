"""Worker delivery settings sourced from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

_DEFAULT_DATABASE_URL = "sqlite:///./friday.db"
_DEFAULT_LEASE_SECONDS = 60.0
_DEFAULT_CANDIDATE_LIMIT = 10
_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 20.0
_DEFAULT_MAINTENANCE_INTERVAL_SECONDS = 30.0
_DEFAULT_MAINTENANCE_BATCH_SIZE = 100
_DEFAULT_RETRY_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_BASE_DELAY_SECONDS = 5.0
_DEFAULT_RETRY_MULTIPLIER = 2.0
_DEFAULT_RETRY_MAX_DELAY_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    database_url: str
    worker_id: str
    lease_duration: timedelta
    candidate_limit: int
    poll_interval_seconds: float
    heartbeat_interval_seconds: float
    maintenance_interval_seconds: float
    maintenance_batch_size: int
    retry_max_attempts: int
    retry_base_delay: timedelta
    retry_multiplier: float
    retry_max_delay: timedelta
    maintenance_only: bool

    @classmethod
    def from_env(cls) -> WorkerSettings:
        maintenance_only = os.environ.get("FRIDAY_WORKER_MAINTENANCE_ONLY", "false").lower()
        return cls(
            database_url=os.environ.get("FRIDAY_WORKER_DATABASE_URL", _DEFAULT_DATABASE_URL),
            worker_id=os.environ.get("FRIDAY_WORKER_ID", f"worker-{os.getpid()}"),
            lease_duration=timedelta(
                seconds=float(os.environ.get("FRIDAY_WORKER_LEASE_SECONDS", _DEFAULT_LEASE_SECONDS))
            ),
            candidate_limit=int(
                os.environ.get("FRIDAY_WORKER_CANDIDATE_LIMIT", _DEFAULT_CANDIDATE_LIMIT)
            ),
            poll_interval_seconds=float(
                os.environ.get(
                    "FRIDAY_WORKER_POLL_INTERVAL_SECONDS", _DEFAULT_POLL_INTERVAL_SECONDS
                )
            ),
            heartbeat_interval_seconds=float(
                os.environ.get(
                    "FRIDAY_WORKER_HEARTBEAT_INTERVAL_SECONDS",
                    _DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
                )
            ),
            maintenance_interval_seconds=float(
                os.environ.get(
                    "FRIDAY_WORKER_MAINTENANCE_INTERVAL_SECONDS",
                    _DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
                )
            ),
            maintenance_batch_size=int(
                os.environ.get(
                    "FRIDAY_WORKER_MAINTENANCE_BATCH_SIZE", _DEFAULT_MAINTENANCE_BATCH_SIZE
                )
            ),
            retry_max_attempts=int(
                os.environ.get("FRIDAY_WORKER_RETRY_MAX_ATTEMPTS", _DEFAULT_RETRY_MAX_ATTEMPTS)
            ),
            retry_base_delay=timedelta(
                seconds=float(
                    os.environ.get(
                        "FRIDAY_WORKER_RETRY_BASE_DELAY_SECONDS",
                        _DEFAULT_RETRY_BASE_DELAY_SECONDS,
                    )
                )
            ),
            retry_multiplier=float(
                os.environ.get("FRIDAY_WORKER_RETRY_MULTIPLIER", _DEFAULT_RETRY_MULTIPLIER)
            ),
            retry_max_delay=timedelta(
                seconds=float(
                    os.environ.get(
                        "FRIDAY_WORKER_RETRY_MAX_DELAY_SECONDS",
                        _DEFAULT_RETRY_MAX_DELAY_SECONDS,
                    )
                )
            ),
            maintenance_only=maintenance_only in {"1", "true"},
        )
