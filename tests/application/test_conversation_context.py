from friday.application.conversation_context import (
    ConversationContext,
    ConversationContextAssembler,
    ConversationMessage,
    ConversationRole,
    build_conversation_section,
)
from friday.domain.conversation import ConversationInputMode
from friday.domain.conversation_turn import ConversationTurn
from friday.domain.event import RunEvent, RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import (
    ConversationId,
    ConversationTurnId,
    RunEventId,
    RunId,
    TaskId,
)
from friday.domain.run import Run
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeUnitOfWork


def test_conversation_section_drops_oldest_messages_and_keeps_newest() -> None:
    context = ConversationContext(
        (
            ConversationMessage(ConversationRole.USER, "oldest message"),
            ConversationMessage(ConversationRole.FRIDAY, "middle response"),
            ConversationMessage(ConversationRole.USER, "newest message must remain"),
        ),
        0,
    )

    document = build_conversation_section(context, max_chars=90)

    assert "newest message must remain" in document
    assert "oldest message" not in document


def test_retry_descendant_uses_root_turn_and_prior_retry_answer() -> None:
    uow = FakeUnitOfWork()
    conversation_id = ConversationId.new()
    first = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    first.start(T0)
    first.fail(T0, Failure("retry", "retry", True, FailureCause.RUNTIME))
    retry = Run.new(
        id=RunId.new(),
        task_id=first.task_id,
        created_at=T0.replace(second=1),
        execution_id=first.execution_id,
    )
    retry.start(T0)
    retry.succeed(T0)
    current = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0.replace(second=2))
    current.start(T0.replace(second=2))
    current.fail(T0.replace(second=2), Failure("retry", "retry", True, FailureCause.RUNTIME))
    current_retry = Run.new(
        id=RunId.new(),
        task_id=current.task_id,
        created_at=T0.replace(second=3),
        execution_id=current.execution_id,
    )
    for run in (first, retry, current, current_retry):
        uow.run_repo.add(run)
    uow.conversation_turn_repo.add(
        ConversationTurn.new(
            id=ConversationTurnId.new(),
            conversation_id=conversation_id,
            client_turn_id="first",
            input_text="first question",
            input_mode=ConversationInputMode.TYPED,
            recognition_language=None,
            task_id=first.task_id,
            run_id=first.id,
            created_at=T0,
        )
    )
    uow.conversation_turn_repo.add(
        ConversationTurn.new(
            id=ConversationTurnId.new(),
            conversation_id=conversation_id,
            client_turn_id="current",
            input_text="current question",
            input_mode=ConversationInputMode.TYPED,
            recognition_language=None,
            task_id=current.task_id,
            run_id=current.id,
            created_at=T0.replace(second=2),
        )
    )
    uow.event_store.append(
        RunEvent(
            id=RunEventId.new(),
            run_id=retry.id,
            type=RunEventType.AGENT_FINISHED,
            sequence=1,
            occurred_at=T0,
            payload={"summary": "retry answer"},
        )
    )

    context = ConversationContextAssembler(CountingUnitOfWorkFactory(uow)).assemble(
        current_retry.id
    )

    assert [(message.role, message.text) for message in context.messages] == [
        (ConversationRole.USER, "first question"),
        (ConversationRole.FRIDAY, "retry answer"),
    ]
