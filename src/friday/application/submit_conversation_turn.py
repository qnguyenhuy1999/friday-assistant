"""Atomically materialize one conversation turn into one queued Run."""

from __future__ import annotations

from friday.application.commands import SubmitConversationTurnCommand
from friday.application.errors import ConversationNotFound, EntityConflict
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import SubmitConversationTurnResult
from friday.application.start_run import StartRun
from friday.domain.conversation_turn import ConversationTurn
from friday.domain.identifiers import ConversationTurnId, TaskId
from friday.domain.task import Task

CONVERSATION_TASK_TITLE_CHARS = 120


def _canonical_client_turn_id(value: str) -> str:
    return value.strip()


def _title_for(input_text: str) -> str:
    collapsed = " ".join(input_text.split())
    return (
        collapsed
        if len(collapsed) <= CONVERSATION_TASK_TITLE_CHARS
        else collapsed[: CONVERSATION_TASK_TITLE_CHARS - 1].rstrip() + "…"
    )


class SubmitConversationTurn:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, command: SubmitConversationTurnCommand) -> SubmitConversationTurnResult:
        try:
            return self._materialize(command)
        except EntityConflict:
            replay = self._existing(command)
            if replay is None:
                raise
            return replay

    def _materialize(self, command: SubmitConversationTurnCommand) -> SubmitConversationTurnResult:
        client_turn_id = _canonical_client_turn_id(command.client_turn_id)
        with self._uow_factory() as uow:
            conversation = uow.conversations.get(command.conversation_id)
            if conversation is None:
                raise ConversationNotFound(command.conversation_id)
            existing = uow.conversation_turns.get_by_client_turn_id(
                command.conversation_id, client_turn_id
            )
            if existing is not None:
                self._assert_same_payload(existing, command)
                return _result(existing, deduplicated=True)
            now = self._clock.now()
            task = Task.new(
                id=TaskId.new(),
                title=_title_for(command.input_text),
                description=command.input_text,
                created_at=now,
            )
            uow.tasks.add(task)
            started = StartRun.execute_in_uow(uow, task, now)
            turn = ConversationTurn.new(
                id=ConversationTurnId.new(),
                conversation_id=command.conversation_id,
                client_turn_id=client_turn_id,
                input_text=command.input_text,
                input_mode=command.input_mode,
                recognition_language=command.recognition_language,
                task_id=started.task_id,
                run_id=started.run_id,
                created_at=now,
            )
            uow.conversation_turns.add(turn)
            conversation.touch(now)
            uow.conversations.save(conversation)
            uow.commit()
        return _result(turn, deduplicated=False)

    def _existing(
        self, command: SubmitConversationTurnCommand
    ) -> SubmitConversationTurnResult | None:
        client_turn_id = _canonical_client_turn_id(command.client_turn_id)
        with self._uow_factory() as uow:
            turn = uow.conversation_turns.get_by_client_turn_id(
                command.conversation_id, client_turn_id
            )
        if turn is None:
            return None
        self._assert_same_payload(turn, command)
        return _result(turn, deduplicated=True)

    @staticmethod
    def _assert_same_payload(
        turn: ConversationTurn, command: SubmitConversationTurnCommand
    ) -> None:
        canonical = ConversationTurn.new(
            id=turn.id,
            conversation_id=command.conversation_id,
            client_turn_id=_canonical_client_turn_id(command.client_turn_id),
            input_text=command.input_text,
            input_mode=command.input_mode,
            recognition_language=command.recognition_language,
            task_id=turn.task_id,
            run_id=turn.run_id,
            created_at=turn.created_at,
        )
        if (
            turn.input_text,
            turn.input_mode,
            turn.recognition_language,
        ) != (
            canonical.input_text,
            canonical.input_mode,
            canonical.recognition_language,
        ):
            raise EntityConflict("client_turn_id is already bound to a different payload")


def _result(turn: ConversationTurn, *, deduplicated: bool) -> SubmitConversationTurnResult:
    return SubmitConversationTurnResult(
        turn.conversation_id, turn.id, turn.task_id, turn.run_id, deduplicated
    )
