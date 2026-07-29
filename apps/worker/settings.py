"""Worker delivery settings sourced from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from math import isfinite

_DEFAULT_DATABASE_URL = "sqlite:///./friday.db"
_DEFAULT_LEASE_SECONDS = 60.0
#: Outbound delivery holds its own lease, sized for a webhook round trip rather
#: than for an agent run. Keeping it separate means a webhook timeout can never
#: silently stretch the run lease (and its heartbeat margin) underneath the
#: agent-run loop. The default clears the longest route timeout the messaging
#: config will accept (`MAX_TIMEOUT_SECONDS`, 60s) plus the safety margin, so
#: any valid route configuration starts up without operator tuning.
_DEFAULT_DELIVERY_LEASE_SECONDS = 90.0
_DELIVERY_LEASE_SAFETY_MARGIN_SECONDS = 5.0
_DEFAULT_CANDIDATE_LIMIT = 10
_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 20.0
_DEFAULT_MAINTENANCE_INTERVAL_SECONDS = 30.0
_DEFAULT_MAINTENANCE_BATCH_SIZE = 100
_DEFAULT_RETRY_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_BASE_DELAY_SECONDS = 5.0
_DEFAULT_RETRY_MULTIPLIER = 2.0
_DEFAULT_RETRY_MAX_DELAY_SECONDS = 300.0
_DEFAULT_SCHEDULER_ENABLED = True


def _positive_finite_seconds(name: str, default: float) -> timedelta:
    """Parse a duration whose non-finite values must fail as a config error.

    `timedelta` rejects NaN and infinity with its own `ValueError`/`OverflowError`,
    so the check happens here while the value is still a float and the message can
    name the variable the operator actually set.
    """
    seconds = float(os.environ.get(name, default))
    if not isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return timedelta(seconds=seconds)


def _strict_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    database_url: str
    worker_id: str
    lease_duration: timedelta
    delivery_lease_duration: timedelta
    candidate_limit: int
    poll_interval_seconds: float
    heartbeat_interval_seconds: float
    maintenance_interval_seconds: float
    maintenance_batch_size: int
    retry_max_attempts: int
    retry_base_delay: timedelta
    retry_multiplier: float
    retry_max_delay: timedelta
    scheduler_enabled: bool = _DEFAULT_SCHEDULER_ENABLED

    def __post_init__(self) -> None:
        if not self.worker_id.strip():
            raise ValueError("worker_id must not be empty or whitespace-only")
        if self.lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if self.delivery_lease_duration <= timedelta(0):
            raise ValueError("delivery_lease_duration must be positive")
        if not isfinite(self.heartbeat_interval_seconds) or self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive and finite")
        if self.heartbeat_interval_seconds >= self.lease_duration.total_seconds():
            raise ValueError(
                "heartbeat_interval_seconds must be less than lease_duration "
                "so the heartbeat leaves real margin under the lease"
            )
        if not isfinite(self.poll_interval_seconds) or self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive and finite")
        if (
            not isfinite(self.maintenance_interval_seconds)
            or self.maintenance_interval_seconds <= 0
        ):
            raise ValueError("maintenance_interval_seconds must be positive and finite")
        if self.candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        if self.maintenance_batch_size <= 0:
            raise ValueError("maintenance_batch_size must be positive")
        if self.retry_max_attempts <= 0:
            raise ValueError("retry_max_attempts must be positive")
        if self.retry_base_delay <= timedelta(0):
            raise ValueError("retry_base_delay must be positive")
        if not isfinite(self.retry_multiplier) or self.retry_multiplier <= 0:
            raise ValueError("retry_multiplier must be positive and finite")
        if self.retry_max_delay <= timedelta(0):
            raise ValueError("retry_max_delay must be positive")
        if self.retry_max_delay < self.retry_base_delay:
            raise ValueError("retry_max_delay must be at least retry_base_delay")

    def validate_delivery_timeouts(self, timeout_seconds: tuple[float, ...]) -> None:
        """Require enough delivery-lease time for a webhook timeout and persistence.

        Only the delivery lease is checked. A webhook timeout says nothing about
        how long an agent run may hold the run lease, so messaging configuration
        must never force unrelated worker retuning — pass only the enabled
        routes' timeouts, and passing none is a no-op.
        """
        max_timeout = max(timeout_seconds, default=0.0)
        if not max_timeout:
            return
        required = max_timeout + _DELIVERY_LEASE_SAFETY_MARGIN_SECONDS
        if self.delivery_lease_duration.total_seconds() <= required:
            raise ValueError(
                "delivery_lease_duration must exceed the longest webhook timeout by "
                f"at least {_DELIVERY_LEASE_SAFETY_MARGIN_SECONDS:g} seconds"
            )

    @classmethod
    def from_env(cls) -> WorkerSettings:
        return cls(
            database_url=os.environ.get("FRIDAY_WORKER_DATABASE_URL", _DEFAULT_DATABASE_URL),
            worker_id=os.environ.get("FRIDAY_WORKER_ID", f"worker-{os.getpid()}"),
            lease_duration=_positive_finite_seconds(
                "FRIDAY_WORKER_LEASE_SECONDS", _DEFAULT_LEASE_SECONDS
            ),
            delivery_lease_duration=_positive_finite_seconds(
                "FRIDAY_DELIVERY_LEASE_SECONDS", _DEFAULT_DELIVERY_LEASE_SECONDS
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
            scheduler_enabled=_strict_bool("FRIDAY_SCHEDULER_ENABLED", _DEFAULT_SCHEDULER_ENABLED),
        )
