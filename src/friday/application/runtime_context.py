"""Deterministic, bounded context construction for brain turns.

Renders one Run's durable state (task objective, run/step lifecycle,
approvals, tool invocations with bounded outputs, artifacts, recent events)
plus the allowed tool manifest into a plain-text document under an explicit
character budget. Ordering and truncation are fully deterministic: same
snapshot + same budget -> same document. Truncation drops oldest, least
relevant items first (events, then invocations, then approvals, then
artifacts, then previous turns) and always tells the brain what was omitted.
No hidden summarization — token-aware semantic compression is Phase 12."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace

from friday.application.tool_gateway import ToolDescriptor
from friday.domain.approval import ApprovalRequest
from friday.domain.artifact import Artifact
from friday.domain.event import RunEvent
from friday.domain.failure import Failure
from friday.domain.json_value import JsonValue
from friday.domain.run import Run
from friday.domain.step import RunStep
from friday.domain.task import Task
from friday.domain.tool import ToolInvocation

MIN_CONTEXT_CHARS = 1000
MAX_ITEM_CHARS = 2000
_TRUNCATION_SUFFIX = "…[truncated]"


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """Everything the context builder may draw from — nothing else (no
    unrelated runs, no global history, no environment, no secrets)."""

    task: Task
    run: Run
    steps: tuple[RunStep, ...]
    approvals: tuple[ApprovalRequest, ...]
    invocations: tuple[ToolInvocation, ...]
    artifacts: tuple[Artifact, ...]
    events: tuple[RunEvent, ...]
    previous_turns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Omitted:
    events: int = 0
    invocations: int = 0
    approvals: int = 0
    artifacts: int = 0
    turns: int = 0
    description: bool = False


def _clip(text: str, limit: int = MAX_ITEM_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def _compact_json(value: JsonValue) -> str:
    return _clip(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _failure_line(failure: Failure) -> str:
    return _clip(f"failure={failure.code}: {failure.message}")


def _objective_lines(task: Task, *, description_truncated: bool) -> list[str]:
    description = task.description.strip()
    if description_truncated:
        description = _clip(description, MAX_ITEM_CHARS // 2)
    lines = ["# OBJECTIVE", f"Task {task.id}: {_clip(task.title)}"]
    if description:
        lines.append(description)
    return lines


def _run_lines(run: Run, attempt_number: int, turn_number: int) -> list[str]:
    lines = [
        "# RUN",
        f"Run {run.id} status={run.status.value} attempt={attempt_number} turn={turn_number}",
    ]
    if run.failure is not None:
        lines.append(_failure_line(run.failure))
    return lines


def _step_lines(steps: tuple[RunStep, ...]) -> list[str]:
    if not steps:
        return []
    lines = ["# STEPS"]
    for step in sorted(steps, key=lambda s: (s.position, str(s.id))):
        line = f"- [{step.position}] {_clip(step.name, 200)} status={step.status.value}"
        if step.failure is not None:
            line += f" {_failure_line(step.failure)}"
        lines.append(line)
    return lines


def _approval_lines(approvals: Sequence[ApprovalRequest], omitted: int) -> list[str]:
    if not approvals and omitted == 0:
        return []
    lines = ["# APPROVALS"]
    if omitted:
        lines.append(f"[{omitted} older approval(s) omitted]")
    for approval in approvals:
        lines.append(
            f"- {approval.id} status={approval.status.value}"
            f" category={approval.category.value}"
            f" action={_clip(approval.requested_action, 200)}"
        )
        if approval.resolution_note:
            lines.append(f"  note: {_clip(approval.resolution_note, 500)}")
    return lines


def _invocation_lines(invocations: Sequence[ToolInvocation], omitted: int) -> list[str]:
    if not invocations and omitted == 0:
        return []
    lines = ["# TOOL INVOCATIONS"]
    if omitted:
        lines.append(f"[{omitted} older invocation(s) omitted]")
    for invocation in invocations:
        lines.append(
            f"- {invocation.id} tool={invocation.tool_name} status={invocation.status.value}"
        )
        lines.append(f"  input: {_compact_json(invocation.requested_input)}")
        if invocation.output_set:
            lines.append(f"  output: {_compact_json(invocation.output)}")
        if invocation.failure is not None:
            lines.append(f"  {_failure_line(invocation.failure)}")
    return lines


def _artifact_lines(artifacts: Sequence[Artifact], omitted: int) -> list[str]:
    if not artifacts and omitted == 0:
        return []
    lines = ["# ARTIFACTS"]
    if omitted:
        lines.append(f"[{omitted} older artifact(s) omitted]")
    for artifact in artifacts:
        lines.append(
            f"- {_clip(artifact.name, 200)} kind={artifact.kind.value}"
            f" location={_clip(artifact.location, 500)}"
        )
    return lines


def _tool_lines(manifest: tuple[ToolDescriptor, ...]) -> list[str]:
    lines = ["# TOOLS"]
    for descriptor in sorted(manifest, key=lambda d: d.name):
        mode = "read-only" if descriptor.read_only else "mutating"
        approval = "approval required" if descriptor.approval_required else "no approval"
        lines.append(f"- {descriptor.name} ({mode}, {approval}): {_clip(descriptor.description)}")
    return lines


def _turn_lines(turns: tuple[str, ...], omitted: int) -> list[str]:
    if not turns and omitted == 0:
        return []
    lines = ["# PREVIOUS TURNS (this claim)"]
    if omitted:
        lines.append(f"[{omitted} older turn(s) omitted]")
    for index, turn in enumerate(turns, start=1 + omitted):
        lines.append(f"- turn {index}: {_clip(turn)}")
    return lines


def _event_lines(events: Sequence[RunEvent], omitted: int) -> list[str]:
    if not events and omitted == 0:
        return []
    lines = ["# RECENT EVENTS"]
    if omitted:
        lines.append(f"[{omitted} older event(s) omitted]")
    for event in events:
        line = f"- [{event.sequence}] {event.type.value}"
        if event.step_id is not None:
            line += f" step={event.step_id}"
        lines.append(line)
    return lines


def _render(
    snapshot: RunSnapshot,
    manifest: tuple[ToolDescriptor, ...],
    attempt_number: int,
    turn_number: int,
    omitted: _Omitted,
) -> str:
    approvals = _sorted_approvals(snapshot.approvals)[omitted.approvals :]
    invocations = _sorted_invocations(snapshot.invocations)[omitted.invocations :]
    artifacts = _sorted_artifacts(snapshot.artifacts)[omitted.artifacts :]
    events = _sorted_events(snapshot.events)[omitted.events :]
    turns = snapshot.previous_turns[omitted.turns :]

    sections = [
        _objective_lines(snapshot.task, description_truncated=omitted.description),
        _run_lines(snapshot.run, attempt_number, turn_number),
        _step_lines(snapshot.steps),
        _approval_lines(approvals, omitted.approvals),
        _invocation_lines(invocations, omitted.invocations),
        _artifact_lines(artifacts, omitted.artifacts),
        _tool_lines(manifest),
        _turn_lines(turns, omitted.turns),
        _event_lines(events, omitted.events),
    ]
    return "\n\n".join("\n".join(section) for section in sections if section)


def _sorted_approvals(approvals: tuple[ApprovalRequest, ...]) -> list[ApprovalRequest]:
    return sorted(approvals, key=lambda a: (a.requested_at, str(a.id)))


def _sorted_invocations(invocations: tuple[ToolInvocation, ...]) -> list[ToolInvocation]:
    return sorted(invocations, key=lambda i: (i.requested_at, str(i.id)))


def _sorted_artifacts(artifacts: tuple[Artifact, ...]) -> list[Artifact]:
    return sorted(artifacts, key=lambda a: (a.created_at, str(a.id)))


def _sorted_events(events: tuple[RunEvent, ...]) -> list[RunEvent]:
    return sorted(events, key=lambda e: e.sequence)


def _next_drop(snapshot: RunSnapshot, omitted: _Omitted) -> _Omitted | None:
    """Deterministic drop priority: oldest event, then oldest invocation,
    then oldest approval, then oldest artifact, then oldest previous turn,
    finally the task description. Returns None when nothing is left."""
    if omitted.events < len(snapshot.events):
        return replace(omitted, events=omitted.events + 1)
    if omitted.invocations < len(snapshot.invocations):
        return replace(omitted, invocations=omitted.invocations + 1)
    if omitted.approvals < len(snapshot.approvals):
        return replace(omitted, approvals=omitted.approvals + 1)
    if omitted.artifacts < len(snapshot.artifacts):
        return replace(omitted, artifacts=omitted.artifacts + 1)
    if omitted.turns < len(snapshot.previous_turns):
        return replace(omitted, turns=omitted.turns + 1)
    if not omitted.description:
        return replace(omitted, description=True)
    return None


def build_runtime_context(
    snapshot: RunSnapshot,
    *,
    tool_manifest: tuple[ToolDescriptor, ...],
    attempt_number: int,
    turn_number: int,
    max_chars: int,
) -> str:
    """Render the bounded context document. Deterministic for a given
    snapshot and budget; never exceeds `max_chars`."""
    if max_chars < MIN_CONTEXT_CHARS:
        raise ValueError(f"max_chars must be >= {MIN_CONTEXT_CHARS}")

    omitted = _Omitted()
    document = _render(snapshot, tool_manifest, attempt_number, turn_number, omitted)
    while len(document) > max_chars:
        next_omitted = _next_drop(snapshot, omitted)
        if next_omitted is None:
            marker = "\n[context truncated to budget]"
            return document[: max_chars - len(marker)] + marker
        omitted = next_omitted
        document = _render(snapshot, tool_manifest, attempt_number, turn_number, omitted)
    return document
