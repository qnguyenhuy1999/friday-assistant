"""Immutable use-case input commands. Stdlib dataclasses only — no
Pydantic/FastAPI/ORM/dict/vendor types, no generic command bus."""

from __future__ import annotations

from dataclasses import dataclass

from friday.domain.identifiers import TaskId


@dataclass(frozen=True, slots=True)
class CreateTaskCommand:
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class StartRunCommand:
    task_id: TaskId
