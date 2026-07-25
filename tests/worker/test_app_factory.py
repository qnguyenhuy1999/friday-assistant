"""Worker composition-root smoke tests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from apps.worker.app import create_worker
from apps.worker.settings import WorkerSettings
from tests.worker.fake_claude import make_fake_claude
from tests.worker.test_worker_composition import runtime_settings


def test_create_worker_wires_real_infrastructure(tmp_path: Path) -> None:
    settings = WorkerSettings(
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
        worker_id="test-worker",
        lease_duration=timedelta(seconds=60),
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
    executable, _ = make_fake_claude(
        tmp_path, action_jsons=['{"version": 1, "action": "finish", "result": {"summary": "x"}}']
    )
    worker = create_worker(settings, runtime_settings(tmp_path, executable))
    try:
        with worker.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    finally:
        worker.engine.dispose()
