"""Worker composition-root smoke tests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from apps.worker.app import Worker, create_worker
from apps.worker.delivery_worker import DeliveryWorker
from apps.worker.settings import WorkerSettings
from friday.application.delivery_lifecycle import ClaimNextDelivery
from tests.worker.fake_claude import make_fake_claude
from tests.worker.test_worker_composition import runtime_settings

RUN_LEASE = timedelta(seconds=60)
DELIVERY_LEASE = timedelta(seconds=90)


def _settings(tmp_path: Path) -> WorkerSettings:
    return WorkerSettings(
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
        worker_id="test-worker",
        lease_duration=RUN_LEASE,
        delivery_lease_duration=DELIVERY_LEASE,
        candidate_limit=10,
        poll_interval_seconds=0.01,
        heartbeat_interval_seconds=0.01,
        maintenance_interval_seconds=0.01,
        maintenance_batch_size=100,
        retry_max_attempts=3,
        retry_base_delay=timedelta(seconds=5),
        retry_multiplier=2.0,
        retry_max_delay=timedelta(seconds=300),
    )


def test_create_worker_wires_real_infrastructure(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    executable, _ = make_fake_claude(
        tmp_path, action_jsons=['{"version": 1, "action": "finish", "result": {"summary": "x"}}']
    )
    worker = create_worker(settings, runtime_settings(tmp_path, executable))
    try:
        with worker.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    finally:
        worker.close()


def test_delivery_and_run_claims_use_their_own_leases(tmp_path: Path) -> None:
    """Wiring proof: the run loop claims on the run lease, delivery on its own."""
    executable, _ = make_fake_claude(
        tmp_path, action_jsons=['{"version": 1, "action": "finish", "result": {"summary": "x"}}']
    )
    worker = create_worker(_settings(tmp_path), runtime_settings(tmp_path, executable))
    try:
        loop = worker.loop
        delivery_worker = loop._delivery_worker
        assert isinstance(delivery_worker, DeliveryWorker)
        claim_next_delivery = delivery_worker._dispatcher.claim_next
        assert isinstance(claim_next_delivery, ClaimNextDelivery)
        assert claim_next_delivery._lease_duration == DELIVERY_LEASE
        assert loop._claim_next_run._lease_duration == RUN_LEASE
    finally:
        worker.close()


def test_worker_disposes_engine_when_computer_close_fails() -> None:
    class Engine:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    class Gateway:
        def close(self) -> None:
            raise RuntimeError("driver close failed")

    engine = Engine()
    worker = Worker(
        engine=engine,  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
        loop=None,  # type: ignore[arg-type]
        processor=None,  # type: ignore[arg-type]
        computer_gateway=Gateway(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="driver close failed"):
        worker.close()

    assert engine.disposed is True
