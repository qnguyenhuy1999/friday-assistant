"""Bounded, Run-scoped conversational context for the brain."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from friday.application.ports import UnitOfWork, UnitOfWorkFactory
from friday.domain.conversation_turn import ConversationTurn
from friday.domain.event import RunEventType
from friday.domain.identifiers import RunId

CONVERSATION_MAX_TURNS = 12
CONVERSATION_MAX_CHARS = 8_000
CONVERSATION_MAX_MESSAGE_CHARS = 2_000
_TRUNCATION_SUFFIX = "…[truncated]"


class ConversationRole(StrEnum):
    USER = "User"
    FRIDAY = "Friday"


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    role: ConversationRole
    text: str


@dataclass(frozen=True, slots=True)
class ConversationContext:
    messages: tuple[ConversationMessage, ...]
    omitted_turns: int


EMPTY_CONVERSATION_CONTEXT = ConversationContext((), 0)


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    return (
        collapsed
        if len(collapsed) <= limit
        else collapsed[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX
    )


def build_conversation_section(context: ConversationContext, *, max_chars: int) -> str:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if not context.messages and not context.omitted_turns:
        return ""
    header = "# CONVERSATION"
    omitted = context.omitted_turns
    lines = [f"{message.role.value}: {message.text}" for message in context.messages]
    # Window from the newest message backwards: continuity is more valuable
    # than the beginning of a stale transcript.
    kept: list[str] = []
    for line in reversed(lines):
        candidate = "\n".join(
            [header, *([f"[{omitted} older turn(s) omitted]"] if omitted else []), line, *kept]
        )
        if len(candidate) > max_chars:
            if not kept:
                # A single long newest message still carries more continuity
                # than an empty section; retain as much of it as fits.
                prefix = [header, f"[{omitted + 1} older turn(s) omitted]"]
                room = max_chars - len("\n".join(prefix)) - 1
                if room > len(_TRUNCATION_SUFFIX):
                    kept.insert(0, _clip(line, room))
            omitted += 1
            continue
        kept.insert(0, line)
    prefix = [header]
    if omitted:
        prefix.append(f"[{omitted} older turn(s) omitted]")
    document = "\n".join([*prefix, *kept])
    while len(document) > max_chars and len(kept) > 1:
        kept.pop(0)
        omitted += 1
        prefix = [header, f"[{omitted} older turn(s) omitted]"]
        document = "\n".join([*prefix, *kept])
    if len(document) > max_chars and kept:
        room = max_chars - len("\n".join(prefix)) - 1
        if room > len(_TRUNCATION_SUFFIX):
            document = "\n".join([*prefix, _clip(kept[-1], room)])
    return document if len(document) <= max_chars else header[:max_chars]


class ConversationContextAssembler:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        max_turns: int = CONVERSATION_MAX_TURNS,
        max_chars: int = CONVERSATION_MAX_CHARS,
        max_message_chars: int = CONVERSATION_MAX_MESSAGE_CHARS,
    ) -> None:
        if min(max_turns, max_chars, max_message_chars) < 1:
            raise ValueError("conversation context bounds must be positive")
        self._uow_factory, self._max_turns, self._max_chars, self._max_message_chars = (
            uow_factory,
            max_turns,
            max_chars,
            max_message_chars,
        )

    def assemble(self, run_id: RunId) -> ConversationContext:
        with self._uow_factory() as uow:
            current = uow.conversation_turns.get_by_run(run_id)
            if current is None:
                return EMPTY_CONVERSATION_CONTEXT
            window = uow.conversation_turns.list_recent_before(
                current.conversation_id, current.created_at, str(current.id), self._max_turns + 1
            )
            dropped = max(0, len(window) - self._max_turns)
            messages = self._messages_for(uow, window[dropped:])
        while sum(len(message.text) for message in messages) > self._max_chars and messages:
            messages.pop(0)
            if messages and messages[0].role is ConversationRole.FRIDAY:
                messages.pop(0)
            dropped += 1
        return ConversationContext(tuple(messages), dropped)

    def _messages_for(
        self, uow: UnitOfWork, turns: Sequence[ConversationTurn]
    ) -> list[ConversationMessage]:
        messages: list[ConversationMessage] = []
        for turn in turns:
            messages.append(
                ConversationMessage(
                    ConversationRole.USER, _clip(turn.input_text, self._max_message_chars)
                )
            )
            event = uow.events.latest_of_type_for_run(turn.run_id, RunEventType.AGENT_FINISHED)
            summary = (
                event.payload.get("summary") if event and isinstance(event.payload, dict) else None
            )
            if isinstance(summary, str) and summary.strip():
                messages.append(
                    ConversationMessage(
                        ConversationRole.FRIDAY, _clip(summary, self._max_message_chars)
                    )
                )
        return messages
