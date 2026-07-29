"""Startup validation tests for WorkerSettings."""

from __future__ import annotations

from datetime import timedelta
from typing import TypedDict

import pytest

from apps.worker.settings import (
    _DEFAULT_DELIVERY_LEASE_SECONDS,
    _DEFAULT_LEASE_SECONDS,
    WorkerSettings,
)


class _WorkerSettingsKwargs(TypedDict):
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


def _valid_kwargs() -> _WorkerSettingsKwargs:
    return {
        "database_url": "sqlite:///./friday.db",
        "worker_id": "worker-1",
        "lease_duration": timedelta(seconds=60),
        "delivery_lease_duration": timedelta(seconds=90),
        "candidate_limit": 10,
        "poll_interval_seconds": 1.0,
        "heartbeat_interval_seconds": 20.0,
        "maintenance_interval_seconds": 30.0,
        "maintenance_batch_size": 100,
        "retry_max_attempts": 3,
        "retry_base_delay": timedelta(seconds=5),
        "retry_multiplier": 2.0,
        "retry_max_delay": timedelta(seconds=300),
    }


def test_valid_settings_construct_without_error() -> None:
    settings = WorkerSettings(**_valid_kwargs())
    assert settings.worker_id == "worker-1"


def _unset_lease_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the compiled-in defaults, not whatever the shell happens to export."""
    monkeypatch.delenv("FRIDAY_WORKER_LEASE_SECONDS", raising=False)
    monkeypatch.delenv("FRIDAY_DELIVERY_LEASE_SECONDS", raising=False)


def test_run_lease_default_is_unchanged_by_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """The global run lease default is 60s and delivery must never move it."""
    assert _DEFAULT_LEASE_SECONDS == 60.0
    _unset_lease_env(monkeypatch)
    assert WorkerSettings.from_env().lease_duration == timedelta(seconds=60)


def test_delivery_lease_has_its_own_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _unset_lease_env(monkeypatch)
    settings = WorkerSettings.from_env()
    assert settings.delivery_lease_duration == timedelta(seconds=_DEFAULT_DELIVERY_LEASE_SECONDS)
    assert settings.delivery_lease_duration != settings.lease_duration


def test_custom_run_lease_does_not_alter_delivery_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    _unset_lease_env(monkeypatch)
    monkeypatch.setenv("FRIDAY_WORKER_LEASE_SECONDS", "300")
    settings = WorkerSettings.from_env()
    assert settings.lease_duration == timedelta(seconds=300)
    assert settings.delivery_lease_duration == timedelta(seconds=_DEFAULT_DELIVERY_LEASE_SECONDS)


def test_custom_delivery_lease_does_not_alter_run_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    _unset_lease_env(monkeypatch)
    monkeypatch.setenv("FRIDAY_DELIVERY_LEASE_SECONDS", "120")
    settings = WorkerSettings.from_env()
    assert settings.delivery_lease_duration == timedelta(seconds=120)
    assert settings.lease_duration == timedelta(seconds=_DEFAULT_LEASE_SECONDS)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_invalid_delivery_lease_fails_startup(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _unset_lease_env(monkeypatch)
    monkeypatch.setenv("FRIDAY_DELIVERY_LEASE_SECONDS", value)
    with pytest.raises(ValueError, match="FRIDAY_DELIVERY_LEASE_SECONDS"):
        WorkerSettings.from_env()


def test_non_positive_delivery_lease_duration_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["delivery_lease_duration"] = timedelta(0)
    with pytest.raises(ValueError, match="delivery_lease_duration"):
        WorkerSettings(**kwargs)


def test_delivery_timeout_validation_uses_delivery_lease_only() -> None:
    """A too-short delivery lease is the error; the run lease is never implicated."""
    kwargs = _valid_kwargs()
    kwargs["delivery_lease_duration"] = timedelta(seconds=60)
    kwargs["lease_duration"] = timedelta(seconds=600)
    settings = WorkerSettings(**kwargs)
    with pytest.raises(ValueError, match="delivery_lease_duration"):
        settings.validate_delivery_timeouts((56.0,))
    settings.validate_delivery_timeouts((54.0,))


def test_delivery_timeout_validation_ignores_run_lease_headroom() -> None:
    """A generous run lease cannot excuse a delivery lease that is too short."""
    kwargs = _valid_kwargs()
    kwargs["lease_duration"] = timedelta(seconds=600)
    kwargs["delivery_lease_duration"] = timedelta(seconds=20)
    with pytest.raises(ValueError, match="longest webhook timeout"):
        WorkerSettings(**kwargs).validate_delivery_timeouts((30.0,))


def test_no_enabled_route_timeouts_never_constrain_worker_settings() -> None:
    """Messaging being off must not force unrelated worker reconfiguration."""
    kwargs = _valid_kwargs()
    kwargs["delivery_lease_duration"] = timedelta(seconds=1)
    WorkerSettings(**kwargs).validate_delivery_timeouts(())


def test_empty_worker_id_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["worker_id"] = ""
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_whitespace_worker_id_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["worker_id"] = "   "
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_non_positive_lease_duration_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["lease_duration"] = timedelta(0)
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_non_positive_heartbeat_interval_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["heartbeat_interval_seconds"] = 0
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_heartbeat_interval_at_or_above_lease_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["heartbeat_interval_seconds"] = kwargs["lease_duration"].total_seconds()
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_non_positive_poll_interval_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["poll_interval_seconds"] = 0
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_non_positive_maintenance_interval_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["maintenance_interval_seconds"] = 0
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_non_positive_candidate_limit_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["candidate_limit"] = 0
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_non_positive_maintenance_batch_size_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["maintenance_batch_size"] = 0
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_non_positive_retry_max_attempts_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["retry_max_attempts"] = 0
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_non_positive_retry_base_delay_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["retry_base_delay"] = timedelta(0)
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_non_positive_retry_multiplier_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["retry_multiplier"] = 0
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_non_positive_retry_max_delay_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["retry_max_delay"] = timedelta(0)
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_retry_max_delay_below_base_delay_raises() -> None:
    kwargs = _valid_kwargs()
    kwargs["retry_base_delay"] = timedelta(seconds=10)
    kwargs["retry_max_delay"] = timedelta(seconds=5)
    with pytest.raises(ValueError):
        WorkerSettings(**kwargs)


def test_invalid_scheduler_boolean_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRIDAY_SCHEDULER_ENABLED", "treu")
    with pytest.raises(ValueError, match="FRIDAY_SCHEDULER_ENABLED"):
        WorkerSettings.from_env()


@pytest.mark.parametrize(
    "field",
    (
        "poll_interval_seconds",
        "heartbeat_interval_seconds",
        "maintenance_interval_seconds",
        "retry_multiplier",
    ),
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_float_settings_must_be_finite(field: str, value: float) -> None:
    kwargs = _valid_kwargs()
    kwargs[field] = value  # type: ignore[literal-required]
    with pytest.raises(ValueError, match="finite"):
        WorkerSettings(**kwargs)
