"""Conversation interaction aggregate, deliberately separate from execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.identifiers import ConversationId
from friday.domain.time import ensure_utc

MAX_CONVERSATION_INPUT_CHARS = 8_000
MAX_CLIENT_TURN_ID_CHARS = 200
MAX_RECOGNITION_LANGUAGE_CHARS = 35


class ConversationInputMode(StrEnum):
    """Input delivery metadata. It never confers execution authority."""

    TYPED = "typed"
    PUSH_TO_TALK = "push_to_talk"
    HANDS_FREE = "hands_free"


@dataclass(slots=True)
class Conversation:
    _id: ConversationId
    _created_at: datetime
    _updated_at: datetime

    @classmethod
    def new(cls, *, id: ConversationId, created_at: datetime) -> Conversation:
        normalized = ensure_utc(created_at)
        return cls(id, normalized, normalized)

    @property
    def id(self) -> ConversationId:
        return self._id

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    def touch(self, at: datetime) -> None:
        self._updated_at = ensure_utc(at)
