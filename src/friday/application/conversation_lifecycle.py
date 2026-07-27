"""Conversation read/create use cases; no execution authority lives here."""

from __future__ import annotations

from datetime import datetime

from friday.application.errors import ConversationNotFound, ConversationTurnNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import ConversationResult, ConversationTurnResult
from friday.domain.conversation import Conversation
from friday.domain.conversation_turn import ConversationTurn
from friday.domain.identifiers import ConversationId, ConversationTurnId


def conversation_result(conversation: Conversation) -> ConversationResult:
    return ConversationResult(conversation.id, conversation.created_at, conversation.updated_at)


def conversation_turn_result(turn: ConversationTurn) -> ConversationTurnResult:
    return ConversationTurnResult(
        turn.id,
        turn.conversation_id,
        turn.client_turn_id,
        turn.input_text,
        turn.input_mode,
        turn.recognition_language,
        turn.task_id,
        turn.run_id,
        turn.created_at,
    )


class CreateConversation:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self) -> ConversationResult:
        conversation = Conversation.new(id=ConversationId.new(), created_at=self._clock.now())
        with self._uow_factory() as uow:
            uow.conversations.add(conversation)
            uow.commit()
        return conversation_result(conversation)


class GetConversation:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, conversation_id: ConversationId) -> ConversationResult:
        with self._uow_factory() as uow:
            conversation = uow.conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        return conversation_result(conversation)


class ListConversationTurns:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def page(
        self,
        conversation_id: ConversationId,
        limit: int,
        after_created_at: datetime | None,
        after_id: str | None,
    ) -> list[ConversationTurnResult]:
        with self._uow_factory() as uow:
            if uow.conversations.get(conversation_id) is None:
                raise ConversationNotFound(conversation_id)
            turns = uow.conversation_turns.list_for_conversation_page(
                conversation_id, limit, after_created_at, after_id
            )
        return [conversation_turn_result(turn) for turn in turns]

    def recent_page(
        self,
        conversation_id: ConversationId,
        limit: int,
        before_created_at: datetime | None,
        before_id: str | None,
    ) -> list[ConversationTurnResult]:
        """The newest turns, oldest-first within the page.

        A conversation only grows, so a client that wants the current state
        needs the tail — walking forward from the beginning to reach it means
        downloading the whole history every time it opens.
        """
        with self._uow_factory() as uow:
            if uow.conversations.get(conversation_id) is None:
                raise ConversationNotFound(conversation_id)
            turns = uow.conversation_turns.list_recent_before(
                conversation_id, before_created_at, before_id, limit
            )
        return [conversation_turn_result(turn) for turn in turns]


class GetConversationTurn:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, turn_id: ConversationTurnId) -> ConversationTurnResult:
        with self._uow_factory() as uow:
            turn = uow.conversation_turns.get(turn_id)
        if turn is None:
            raise ConversationTurnNotFound(turn_id)
        return conversation_turn_result(turn)
