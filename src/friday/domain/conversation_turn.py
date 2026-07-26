"""Immutable user utterance and its materialized Task/Run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.domain.conversation import (
    MAX_CLIENT_TURN_ID_CHARS,
    MAX_CONVERSATION_INPUT_CHARS,
    MAX_RECOGNITION_LANGUAGE_CHARS,
    ConversationInputMode,
)
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import ConversationId, ConversationTurnId, RunId, TaskId
from friday.domain.time import ensure_utc


def _bounded(value: str, *, field: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise DomainValidationError(f"ConversationTurn.{field} must not be empty after trimming")
    if len(normalized) > limit:
        raise DomainValidationError(f"ConversationTurn.{field} must be at most {limit} characters")
    return normalized


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    id: ConversationTurnId
    conversation_id: ConversationId
    client_turn_id: str
    input_text: str
    input_mode: ConversationInputMode
    recognition_language: str | None
    task_id: TaskId
    run_id: RunId
    created_at: datetime

    @classmethod
    def new(
        cls,
        *,
        id: ConversationTurnId,
        conversation_id: ConversationId,
        client_turn_id: str,
        input_text: str,
        input_mode: ConversationInputMode,
        recognition_language: str | None,
        task_id: TaskId,
        run_id: RunId,
        created_at: datetime,
    ) -> ConversationTurn:
        language = (
            _bounded(
                recognition_language,
                field="recognition_language",
                limit=MAX_RECOGNITION_LANGUAGE_CHARS,
            )
            if recognition_language is not None
            else None
        )
        return cls(
            id=id,
            conversation_id=conversation_id,
            client_turn_id=_bounded(
                client_turn_id, field="client_turn_id", limit=MAX_CLIENT_TURN_ID_CHARS
            ),
            input_text=_bounded(input_text, field="input_text", limit=MAX_CONVERSATION_INPUT_CHARS),
            input_mode=input_mode,
            recognition_language=language,
            task_id=task_id,
            run_id=run_id,
            created_at=ensure_utc(created_at),
        )
