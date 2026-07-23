"""Immutable use-case output results. Expose typed domain identifiers only —
never ORM row instances or raw dicts."""

from __future__ import annotations

from dataclasses import dataclass

from friday.domain.identifiers import RunId, TaskId


@dataclass(frozen=True, slots=True)
class CreateTaskResult:
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class StartRunResult:
    task_id: TaskId
    run_id: RunId
