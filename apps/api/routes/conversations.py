from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    cursor_datetime,
    decode_cursor,
    encode_cursor,
    page_from_query,
)
from apps.api.schemas.conversations import (
    ConversationResponse,
    ConversationTurnPageResponse,
    ConversationTurnResponse,
    SubmitConversationTurnBody,
)
from friday.application.commands import SubmitConversationTurnCommand
from friday.application.conversation_lifecycle import (
    CreateConversation,
    GetConversation,
    GetConversationTurn,
    ListConversationTurns,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import ConversationResult, ConversationTurnResult
from friday.application.submit_conversation_turn import SubmitConversationTurn
from friday.domain.conversation import ConversationInputMode
from friday.domain.identifiers import ConversationId

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
UowDependency = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDependency = Annotated[Clock, Depends(get_clock)]


def _conversation(value: ConversationResult) -> ConversationResponse:
    return ConversationResponse(
        id=str(value.conversation_id), created_at=value.created_at, updated_at=value.updated_at
    )


def _turn(value: ConversationTurnResult) -> ConversationTurnResponse:
    return ConversationTurnResponse(
        id=str(value.turn_id),
        conversation_id=str(value.conversation_id),
        client_turn_id=value.client_turn_id,
        input_text=value.input_text,
        input_mode=value.input_mode.value,
        recognition_language=value.recognition_language,
        task_id=str(value.task_id),
        run_id=str(value.run_id),
        created_at=value.created_at,
    )


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createConversation",
)
def create_conversation(uow_factory: UowDependency, clock: ClockDependency) -> ConversationResponse:
    return _conversation(CreateConversation(uow_factory, clock).execute())


@router.get(
    "/{conversation_id}", response_model=ConversationResponse, operation_id="getConversation"
)
def get_conversation(conversation_id: UUID, uow_factory: UowDependency) -> ConversationResponse:
    return _conversation(
        GetConversation(uow_factory).execute(ConversationId.parse(str(conversation_id)))
    )


@router.post(
    "/{conversation_id}/turns",
    response_model=ConversationTurnResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="submitConversationTurn",
)
def submit_turn(
    conversation_id: UUID,
    body: SubmitConversationTurnBody,
    uow_factory: UowDependency,
    clock: ClockDependency,
) -> ConversationTurnResponse:
    result = SubmitConversationTurn(uow_factory, clock).execute(
        SubmitConversationTurnCommand(
            conversation_id=ConversationId.parse(str(conversation_id)),
            client_turn_id=body.client_turn_id,
            input_text=body.input_text,
            input_mode=ConversationInputMode(body.input_mode),
            recognition_language=body.recognition_language,
        )
    )
    return _turn(GetConversationTurn(uow_factory).execute(result.turn_id))


def _recent_turns(
    conversation_id: ConversationId,
    parent_id: str,
    uow_factory: UnitOfWorkFactory,
    limit: int,
    cursor: str | None,
) -> ConversationTurnPageResponse:
    """Newest turns first page, then backwards through older ones.

    Items stay oldest-first within a page so a transcript renders as-is, while
    `next_cursor` walks *backwards* in time. That is what lets a long-lived
    conversation open on its tail instead of downloading its whole history.
    """
    before = decode_cursor(
        cursor,
        collection="conversation_turns",
        parent_id=parent_id,
        order="recent_desc",
        parts=2,
    )
    items = ListConversationTurns(uow_factory).recent_page(
        conversation_id,
        limit + 1,
        cursor_datetime(before.after[0]) if before else None,
        before.after[1] if before else None,
    )
    has_older = len(items) > limit
    page = items[1:] if has_older else items
    next_cursor = (
        encode_cursor(
            collection="conversation_turns",
            parent_id=parent_id,
            order="recent_desc",
            after=(page[0].created_at.isoformat(), str(page[0].turn_id)),
        )
        if has_older and page
        else None
    )
    return ConversationTurnPageResponse(
        items=[_turn(item) for item in page], next_cursor=next_cursor
    )


@router.get(
    "/{conversation_id}/turns",
    response_model=ConversationTurnPageResponse,
    operation_id="listConversationTurns",
)
def list_turns(
    conversation_id: UUID,
    uow_factory: UowDependency,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    order: Literal["created_at_id_asc", "recent_desc"] = "created_at_id_asc",
) -> ConversationTurnPageResponse:
    parsed = ConversationId.parse(str(conversation_id))
    if order == "recent_desc":
        return _recent_turns(parsed, str(conversation_id), uow_factory, limit, cursor)
    after = decode_cursor(
        cursor,
        collection="conversation_turns",
        parent_id=str(conversation_id),
        order="created_at_id_asc",
        parts=2,
    )
    items = ListConversationTurns(uow_factory).page(
        parsed,
        limit + 1,
        cursor_datetime(after.after[0]) if after else None,
        after.after[1] if after else None,
    )
    page, next_cursor = page_from_query(
        items,
        limit=limit,
        collection="conversation_turns",
        parent_id=str(conversation_id),
        order="created_at_id_asc",
        key=lambda item: (item.created_at.isoformat(), str(item.turn_id)),
    )
    return ConversationTurnPageResponse(
        items=[_turn(item) for item in page], next_cursor=next_cursor
    )
