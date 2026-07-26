# Conversational voice interface

Phase 17 places `Conversation` and immutable `ConversationTurn` records in
front of the unchanged Task and Run execution layer. A submitted turn atomically
materializes one Task and one queued Run, fenced by `(conversation_id,
client_turn_id)`; a Run can belong to only one turn.

Voice is browser-native delivery only. Speech recognition produces text, which
uses the same turn-submission path as typing. It cannot approve, authorize, or
invoke tools. Browser TTS speaks only bounded final summaries. Recognition is
suspended during playback; hands-free collection uses the 1,500 ms silence
threshold and remains limited to one in-flight Run.

The brain resolves history from `run_id` through its own turn and conversation.
It receives at most 12 prior turns, 8,000 characters total, and 2,000 characters
per message. Only `agent_finished.summary` is included; tool output, details,
audio, approval fingerprints, and artifacts remain outside conversational
context. A Run with no turn gets no conversation section.

Escape stops local listening/speech; cancellation of an active Run remains the
durable Run cancellation use case. That cancellation also terminalizes pending
approvals in the same transaction.

Non-goals include wake words, always-on microphones, native/mobile clients,
messaging platforms, cloud STT/TTS, voice biometrics, and voice approval.
