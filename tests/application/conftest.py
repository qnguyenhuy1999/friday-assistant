"""Fixtures wiring the shared fakes into application use-case tests."""

from __future__ import annotations

import pytest

from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


@pytest.fixture
def fake_uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def uow_factory(fake_uow: FakeUnitOfWork) -> CountingUnitOfWorkFactory:
    return CountingUnitOfWorkFactory(fake_uow)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
