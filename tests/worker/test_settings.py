"""Startup validation tests for WorkerSettings."""

from __future__ import annotations

from datetime import timedelta

import pytest

from apps.worker.settings import WorkerSettings


def _valid_kwargs() -> dict:
    return {
        "database_url": "sqlite:///./friday.db",
        "worker_id": "worker-1",
        "lease_duration": timedelta(seconds=60),
        "candidate_limit": 10,
        "poll_interval_seconds": 1.0,
        "heartbeat_interval_seconds": 20.0,
        "maintenance_interval_seconds": 30.0,
        "maintenance_batch_size": 100,
        "retry_max_attempts": 3,
        "retry_base_delay": timedelta(seconds=5),
        "retry_multiplier": 2.0,
        "retry_max_delay": timedelta(seconds=300),
        "maintenance_only": False,
    }


def test_valid_settings_construct_without_error() -> None:
    settings = WorkerSettings(**_valid_kwargs())
    assert settings.worker_id == "worker-1"


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
