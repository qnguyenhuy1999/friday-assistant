from friday.application.conversation_context import (
    ConversationContext,
    ConversationMessage,
    ConversationRole,
    build_conversation_section,
)


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
