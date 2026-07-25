"""Gateway coverage for the four deliberately narrow memory tools."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from friday.application.errors import ToolInputInvalid, ToolNotFound
from friday.application.memory.errors import (
    MemoryAccessDenied,
    MemoryWriteConflict,
    MemoryWriteDenied,
)
from friday.application.memory.models import MemoryVaultPolicy
from friday.application.memory.write_policy import MemoryCategory
from friday.application.tool_gateway import ToolCall, ToolExecutionRequest, ToolExecutionResult
from friday.domain.approval import ApprovalCategory
from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import RunId, ToolInvocationId
from friday.domain.json_value import JsonValue
from friday.domain.tool import ToolInvocation, ToolInvocationStatus
from friday.infrastructure.tools.gateway import WorkspaceToolGateway, WorkspaceToolGatewaySettings
from friday.infrastructure.tools.memory_tools import (
    MemoryToolSettings,
    _bounded_integer,
    _frontmatter,
    _object,
    _optional_string,
    _string,
    memory_failure,
)


@pytest.fixture
def gateway(tmp_path: Path) -> tuple[WorkspaceToolGateway, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    vault = tmp_path / "vault"
    (vault / "Notes").mkdir(parents=True)
    (vault / "Notes" / "visible.md").write_text("---\ntitle: Visible\n---\nneedle words\n")
    (vault / "Notes" / "private.md").write_text("---\nprivate: true\n---\nsecret note\n")
    settings = WorkspaceToolGatewaySettings(
        workspace_root=workspace,
        max_file_bytes=10_000,
        max_list_entries=100,
        process_timeout_seconds=10.0,
        process_max_timeout_seconds=30.0,
        max_stdout_bytes=10_000,
        max_stderr_bytes=10_000,
        memory=MemoryToolSettings(
            vault_root=vault,
            policy=MemoryVaultPolicy(("**/*.md",), (), 100, 10_000),
        ),
    )
    return WorkspaceToolGateway(settings), vault


def call(gateway: WorkspaceToolGateway, tool: str, value: JsonValue) -> ToolExecutionResult:
    return gateway.execute(
        ToolExecutionRequest(
            invocation_id=ToolInvocationId.new(),
            run_id=RunId.new(),
            step_id=None,
            call=ToolCall(tool, value),
        )
    )


def test_memory_search_returns_short_excerpt_and_provenance(
    gateway: tuple[WorkspaceToolGateway, Path],
) -> None:
    result = call(gateway[0], "memory.search", {"query": "needle", "limit": 1})
    assert result.status == "succeeded"
    assert isinstance(result.output, dict)
    results = result.output["results"]
    assert isinstance(results, list)
    item = results[0]
    assert isinstance(item, dict)
    assert item["path"] == "Notes/visible.md"
    provenance = item["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["content_hash"]


def test_exact_read_denies_private_note(gateway: tuple[WorkspaceToolGateway, Path]) -> None:
    result = call(gateway[0], "memory.read_note", {"path": "Notes/private.md"})
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "memory_access_denied"


def test_exact_read_returns_bounded_content(gateway: tuple[WorkspaceToolGateway, Path]) -> None:
    result = call(gateway[0], "memory.read_note", {"path": "Notes/visible.md", "max_chars": 8})
    assert result.status == "succeeded"
    assert isinstance(result.output, dict)
    assert result.output["path"] == "Notes/visible.md"
    assert result.output["truncated"] is True


def test_mutating_memory_tools_are_approval_gated(
    gateway: tuple[WorkspaceToolGateway, Path],
) -> None:
    for name in ("memory.create_note", "memory.append_managed_note"):
        assessment = gateway[0].assess(ToolCall(name, {}))
        assert assessment.approval_required is True
        assert assessment.category is ApprovalCategory.FILESYSTEM_WRITE
    assert gateway[0].assess(ToolCall("memory.search", {})).approval_required is False


def test_create_is_managed_and_never_overwrites(gateway: tuple[WorkspaceToolGateway, Path]) -> None:
    value: dict[str, JsonValue] = {
        "path": "Friday/Inbox/new.md",
        "payload": "remember this\n",
        "memory_category": MemoryCategory.EXPLICIT_USER_REQUEST_TO_REMEMBER.value,
        "frontmatter": {
            "friday_managed": "true",
            "friday_memory_id": "m1",
            "source_run_id": "r1",
            "created_at": "now",
            "updated_at": "now",
        },
    }
    result = call(gateway[0], "memory.create_note", value)
    assert result.status == "succeeded"
    created = gateway[1] / "Friday/Inbox/new.md"
    original = created.read_bytes()
    duplicate = call(gateway[0], "memory.create_note", value)
    assert duplicate.status == "failed"
    assert created.read_bytes() == original


def test_append_compares_hash_and_preserves_conflicting_file(
    gateway: tuple[WorkspaceToolGateway, Path],
) -> None:
    path = gateway[1] / "Friday/Inbox/managed.md"
    path.parent.mkdir(parents=True)
    before = "---\nfriday_managed: true\n---\ninitial\n"
    path.write_text(before)
    changed = before + "outside change\n"
    path.write_text(changed)
    result = call(
        gateway[0],
        "memory.append_managed_note",
        {
            "path": "Friday/Inbox/managed.md",
            "payload": "append\n",
            "expected_content_hash": hashlib.sha256(before.encode()).hexdigest(),
            "memory_category": MemoryCategory.EXPLICIT_DECISION.value,
        },
    )
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "memory_write_conflict"
    assert path.read_text() == changed


def test_registry_has_only_the_four_memory_operations(
    gateway: tuple[WorkspaceToolGateway, Path],
) -> None:
    names = {descriptor.name for descriptor in gateway[0].list_tools()}
    assert {
        "memory.search",
        "memory.read_note",
        "memory.create_note",
        "memory.append_managed_note",
    } <= names
    assert not {"memory.delete_note", "memory.rename_note", "memory.overwrite_any_note"} & names


def test_tool_invocation_lifecycle_and_all_metadata_properties() -> None:
    now = datetime.now(UTC)
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=RunId.new(),
        tool_name="memory.create_note",
        requested_input={"path": "Friday/Inbox/note.md"},
        requested_at=now,
    )
    assert invocation.id and invocation.run_id and invocation.tool_name
    assert invocation.requested_input == {"path": "Friday/Inbox/note.md"}
    assert invocation.step_id is None and invocation.approval_request_id is None
    assert invocation.status is ToolInvocationStatus.REQUESTED
    assert invocation.requested_at == now and invocation.started_at is None
    assert invocation.completed_at is None and invocation.output is None
    assert invocation.output_set is False and invocation.failure is None
    invocation.start(now)
    invocation.succeed(now, {"created": True})
    assert invocation.status.value == "succeeded"
    assert invocation.output == {"created": True}
    assert invocation.output_set is True and invocation.failure is None


def test_tool_invocation_rejects_invalid_transitions_and_times() -> None:
    now = datetime.now(UTC)
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=RunId.new(),
        tool_name="memory.search",
        requested_input={},
        requested_at=now,
    )
    with pytest.raises(InvalidStateTransition):
        invocation.succeed(now, {})
    invocation.start(now)
    with pytest.raises(DomainValidationError):
        invocation.succeed(now - timedelta(seconds=1), {})
    with pytest.raises(DomainValidationError):
        invocation.succeed(now)
    invocation.cancel(now)
    with pytest.raises(InvalidStateTransition):
        invocation.start(now)


def test_tool_invocation_failure_and_validation() -> None:
    now = datetime.now(UTC)
    with pytest.raises(DomainValidationError):
        ToolInvocation.new(
            id=ToolInvocationId.new(),
            run_id=RunId.new(),
            tool_name=" ",
            requested_input={},
            requested_at=now,
        )
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=RunId.new(),
        tool_name="memory.read_note",
        requested_input={},
        requested_at=now,
    )
    invocation.start(now)
    failure = Failure("failed", "failed", False, FailureCause.TOOL)
    invocation.fail(now, failure)
    assert invocation.status is ToolInvocationStatus.FAILED
    assert invocation.failure is failure


def test_memory_tool_settings_and_input_helpers_reject_invalid_values(tmp_path: Path) -> None:
    policy = MemoryVaultPolicy(("**/*.md",), (), 1, 1)
    with pytest.raises(ValueError):
        MemoryToolSettings(tmp_path, policy, max_search_limit=0)
    with pytest.raises(ValueError):
        MemoryToolSettings(tmp_path, policy, max_excerpt_chars=0)
    with pytest.raises(ToolInputInvalid):
        _object("not an object")
    with pytest.raises(ToolInputInvalid):
        _string({}, "missing")
    with pytest.raises(ToolInputInvalid):
        _optional_string({"heading": 2}, "heading")
    assert _optional_string({}, "heading") is None
    for invalid in (True, 0, 2):
        with pytest.raises(ToolInputInvalid):
            _bounded_integer({"limit": invalid}, "limit", 1)
    with pytest.raises(ToolInputInvalid):
        _frontmatter({})
    with pytest.raises(ToolInputInvalid):
        _frontmatter({"frontmatter": {"key": 2}})


def test_memory_failure_maps_every_stable_failure_code() -> None:
    for exc, expected in (
        (MemoryWriteConflict("x"), "memory_write_conflict"),
        (MemoryAccessDenied("x"), "memory_access_denied"),
        (MemoryWriteDenied("x"), "memory_access_denied"),
        (ToolInputInvalid("x"), "tool_invalid_input"),
        (Exception("x"), "tool_execution_failed"),
    ):
        result = memory_failure(exc)
        assert result.failure is not None
        assert result.failure.code == expected


def test_gateway_maps_existing_error_paths_and_cancellation(
    gateway: tuple[WorkspaceToolGateway, Path],
) -> None:
    with pytest.raises(ToolNotFound):
        gateway[0].assess(ToolCall("unknown.tool", {}))
    with pytest.raises(ToolNotFound):
        call(gateway[0], "unknown.tool", {})
    request = ToolExecutionRequest(
        invocation_id=ToolInvocationId.new(),
        run_id=RunId.new(),
        step_id=None,
        call=ToolCall("memory.search", {"query": "needle"}),
        cancellation_requested=lambda: True,
    )
    cancelled = gateway[0].execute(request)
    escaped = call(gateway[0], "workspace.read_text", {"path": "../escape"})
    invalid = call(gateway[0], "workspace.read_text", {"bad": "input"})
    process = call(gateway[0], "process.run", {"argv": []})
    os_failure = call(gateway[0], "workspace.read_text", {"path": "."})
    assert cancelled.failure is not None and cancelled.failure.code == "claim_lost"
    assert escaped.failure is not None and escaped.failure.code == "workspace_escape_rejected"
    assert invalid.failure is not None and invalid.failure.code == "tool_invalid_input"
    assert process.failure is not None and process.failure.code == "tool_invalid_input"
    assert os_failure.failure is not None and os_failure.failure.code == "tool_invalid_input"
