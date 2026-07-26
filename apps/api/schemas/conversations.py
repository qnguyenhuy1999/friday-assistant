from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from friday.domain.conversation import (
    MAX_CLIENT_TURN_ID_CHARS,
    MAX_CONVERSATION_INPUT_CHARS,
    MAX_RECOGNITION_LANGUAGE_CHARS,
)


class SubmitConversationTurnBody(BaseModel):
    client_turn_id: str = Field(min_length=1, max_length=MAX_CLIENT_TURN_ID_CHARS)
    input_text: str = Field(min_length=1, max_length=MAX_CONVERSATION_INPUT_CHARS)
    input_mode: Literal["typed", "push_to_talk", "hands_free"]
    recognition_language: str | None = Field(
        default=None, min_length=1, max_length=MAX_RECOGNITION_LANGUAGE_CHARS
    )


class ConversationResponse(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime


class ConversationTurnResponse(BaseModel):
    id: str
    conversation_id: str
    client_turn_id: str
    input_text: str
    input_mode: str
    recognition_language: str | None
    task_id: str
    run_id: str
    created_at: datetime


class ConversationTurnPageResponse(BaseModel):
    items: list[ConversationTurnResponse]
    next_cursor: str | None
