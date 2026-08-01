"""Claude CLI adapter tests against a fake executable. No real Claude
subscription, network, or API key is ever required: each test bakes the
fake's behavior (stdout per call, stderr, exit code, sleep) into a small
python script and asserts the adapter's argv, environment allowlist,
stdin-based prompting, timeout handling, output caps, envelope parsing,
and the single bounded repair attempt."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import time
from pathlib import Path

import pytest

from friday.application.brain_runtime import BrainRequest
from friday.application.errors import (
    BrainProtocolError,
    BrainResponseInvalid,
    BrainTimeout,
    BrainUnavailable,
)
from friday.application.runtime_actions import FinishAction, InvokeToolAction
from friday.application.skill_evaluation import BrainOnlyEvaluationRequest
from friday.application.skill_improvement import CandidateGenerationRequest
from friday.application.tool_gateway import ToolDescriptor
from friday.domain.identifiers import RunId, SkillEvidenceSnapshotId, SkillRevisionId, TaskId
from friday.domain.json_value import JsonValue
from friday.domain.skill_evidence_snapshot import evidence_payload_hash
from friday.infrastructure.brain.claude_cli import (
    _SYSTEM_PROMPT,
    CANDIDATE_SYSTEM_PROMPT,
    ENVIRONMENT_ALLOWLIST,
    EVALUATION_SYSTEM_PROMPT,
    ClaudeCliBrainRuntime,
    ClaudeCliSettings,
)
from friday.infrastructure.brain.claude_cli_protocol import parse_cli_envelope

FINISH_ACTION = '{"version": 1, "action": "finish", "result": {"summary": "done"}}'
INVOKE_ACTION = '{"version": 1, "action": "invoke_tool", "tool": "workspace.list", "input": {}}'

_FAKE_TEMPLATE = """#!/usr/bin/env python3
import json, os, sys, time

RECORD = {record!r}
STDOUTS = {stdouts!r}
STDERR = {stderr!r}
SLEEP = {sleep!r}
EXIT = {exit_code!r}

data = sys.stdin.read()
count_path = os.path.join(RECORD, "count")
count = int(open(count_path).read()) if os.path.exists(count_path) else 0
open(count_path, "w").write(str(count + 1))
open(os.path.join(RECORD, f"argv-{{count}}.json"), "w").write(json.dumps(sys.argv))
open(os.path.join(RECORD, f"env-{{count}}.json"), "w").write(json.dumps(dict(os.environ)))
open(os.path.join(RECORD, f"stdin-{{count}}.txt"), "w").write(data)
time.sleep(SLEEP)
sys.stderr.write(STDERR)
sys.stdout.write(STDOUTS[min(count, len(STDOUTS) - 1)])
sys.exit(EXIT)
"""


def envelope(result: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result,
            "usage": {"input_tokens": 5, "output_tokens": 7, "service_tier": "standard"},
            "modelUsage": {"claude-x": {"inputTokens": 5}},
        }
    )


def make_fake(
    tmp_path: Path,
    *,
    stdouts: list[str],
    stderr: str = "",
    sleep: float = 0.0,
    exit_code: int = 0,
) -> tuple[str, Path]:
    record = tmp_path / "record"
    record.mkdir(exist_ok=True)
    script = tmp_path / "fake-claude"
    script.write_text(
        _FAKE_TEMPLATE.format(
            record=str(record), stdouts=stdouts, stderr=stderr, sleep=sleep, exit_code=exit_code
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), record


def settings(executable: str, **overrides: object) -> ClaudeCliSettings:
    fields: dict[str, object] = {
        "executable": executable,
        "model": "haiku",
        "timeout_seconds": 10.0,
        "max_output_bytes": 1_000_000,
    }
    fields.update(overrides)
    return ClaudeCliSettings(**fields)  # type: ignore[arg-type]


def request(**overrides: object) -> BrainRequest:
    fields: dict[str, object] = {
        "run_id": RunId.new(),
        "task_id": TaskId.new(),
        "turn_number": 1,
        "attempt_number": 1,
        "context": "# OBJECTIVE\nsay hello\n\n# TOOLS\n- workspace.list",
        "tool_manifest": (
            ToolDescriptor(
                name="workspace.list",
                description="List entries.",
                read_only=True,
                approval_required=False,
            ),
        ),
        "max_response_bytes": 65536,
    }
    fields.update(overrides)
    return BrainRequest(**fields)  # type: ignore[arg-type]


def test_success_returns_parsed_action_with_safe_metadata(tmp_path: Path) -> None:
    executable, _ = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)])
    runtime = ClaudeCliBrainRuntime(settings(executable))
    response = runtime.next_action(request())
    assert response.action == FinishAction(summary="done")
    assert response.model == "claude-x"
    assert response.usage == {"input_tokens": 5, "output_tokens": 7}
    assert response.repaired is False


def test_argv_is_a_list_with_brain_only_flags(tmp_path: Path) -> None:
    executable, record = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)])
    ClaudeCliBrainRuntime(settings(executable)).next_action(request())
    argv = json.loads((record / "argv-0.json").read_text())
    assert argv[0] == executable
    for flag in ("-p", "--strict-mcp-config", "--no-session-persistence", "--safe-mode"):
        assert flag in argv
    # all built-in tools are disabled at process level
    tools_index = argv.index("--tools")
    assert argv[tools_index + 1] == ""
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "haiku"


def test_model_flag_omitted_when_not_configured(tmp_path: Path) -> None:
    executable, record = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)])
    ClaudeCliBrainRuntime(settings(executable, model=None)).next_action(request())
    argv = json.loads((record / "argv-0.json").read_text())
    assert "--model" not in argv


def test_brain_only_evaluation_requires_exact_frozen_case_output_map(tmp_path: Path) -> None:
    executable, record = make_fake(tmp_path, stdouts=[envelope('{"case-a":"answer"}')])
    runtime = ClaudeCliBrainRuntime(settings(executable))
    assert runtime.evaluate_skill_cases(
        BrainOnlyEvaluationRequest(instructions="be precise", cases=(("case-a", "question"),))
    ) == {"case-a": "answer"}
    prompt = json.loads((record / "stdin-0.txt").read_text())
    assert prompt["instructions"] == "be precise"
    assert prompt["cases"] == [{"id": "case-a", "input": "question"}]
    argv = json.loads((record / "argv-0.json").read_text())
    assert argv[argv.index("--system-prompt") + 1] == EVALUATION_SYSTEM_PROMPT


def test_brain_only_candidate_uses_its_dedicated_protocol_and_provenance_prompt(
    tmp_path: Path,
) -> None:
    snapshot: JsonValue = {
        "version": 1,
        "entries": [{"id": "manual:one", "kind": "manual", "payload": {"note": "x"}}],
    }
    base_instructions = "base instructions"
    executable, record = make_fake(
        tmp_path,
        stdouts=[
            envelope(
                json.dumps(
                    {
                        "version": 1,
                        "proposed_instructions": "better instructions",
                        "rationale": "r",
                        "addressed_evidence_ids": ["manual:one"],
                    }
                )
            )
        ],
    )
    request = CandidateGenerationRequest(
        base_instructions=base_instructions,
        evidence_snapshot_hash=evidence_payload_hash(snapshot),
        evidence_ids=("manual:one",),
        feedback_summaries=(),
        evaluator_summaries=(),
        snapshot_id=SkillEvidenceSnapshotId.new(),
        snapshot_payload=snapshot,
        base_revision_id=SkillRevisionId.new(),
        base_content_sha256=hashlib.sha256(base_instructions.encode("utf-8")).hexdigest(),
    )
    runtime = ClaudeCliBrainRuntime(settings(executable))
    raw = runtime.generate_candidate(request)
    assert json.loads(raw)["proposed_instructions"] == "better instructions"
    argv = json.loads((record / "argv-0.json").read_text())
    system_prompt = argv[argv.index("--system-prompt") + 1]
    assert system_prompt == CANDIDATE_SYSTEM_PROMPT
    assert system_prompt != EVALUATION_SYSTEM_PROMPT
    assert system_prompt != _SYSTEM_PROMPT
    prompt = json.loads((record / "stdin-0.txt").read_text())
    assert prompt["evidence_snapshot"] == snapshot
    assert prompt["evidence_snapshot_hash"] == request.evidence_snapshot_hash
    assert prompt["base_instructions"] == base_instructions


def test_brain_only_evaluation_rejects_missing_or_extra_case_outputs(tmp_path: Path) -> None:
    executable, _ = make_fake(tmp_path, stdouts=[envelope('{"wrong":"answer"}')])
    runtime = ClaudeCliBrainRuntime(settings(executable))
    with pytest.raises(BrainResponseInvalid, match="map each frozen case"):
        runtime.evaluate_skill_cases(
            BrainOnlyEvaluationRequest(instructions="be precise", cases=(("case-a", "question"),))
        )


def test_prompt_travels_via_stdin_not_argv(tmp_path: Path) -> None:
    executable, record = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)])
    ClaudeCliBrainRuntime(settings(executable)).next_action(request())
    stdin_text = (record / "stdin-0.txt").read_text()
    assert "# OBJECTIVE" in stdin_text
    argv = json.loads((record / "argv-0.json").read_text())
    assert not any("# OBJECTIVE" in arg for arg in argv)


def test_environment_is_allowlisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-not-for-the-brain")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "nested-session")
    monkeypatch.setenv("FRIDAY_WORKER_DATABASE_URL", "sqlite:///x.db")
    executable, record = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)])
    ClaudeCliBrainRuntime(settings(executable)).next_action(request())
    child_env = json.loads((record / "env-0.json").read_text())
    assert "ANTHROPIC_API_KEY" not in child_env
    assert "CLAUDE_CODE_SESSION_ID" not in child_env
    assert "FRIDAY_WORKER_DATABASE_URL" not in child_env
    # macOS injects __CF_USER_TEXT_ENCODING into every child process; it is
    # not part of the adapter's env dict.
    os_injected = {"__CF_USER_TEXT_ENCODING"}
    assert set(child_env) <= set(ENVIRONMENT_ALLOWLIST) | os_injected
    assert child_env.get("HOME") == os.environ.get("HOME")


def test_malformed_stdout_raises_protocol_error(tmp_path: Path) -> None:
    executable, _ = make_fake(tmp_path, stdouts=["this is not json"])
    with pytest.raises(BrainProtocolError):
        ClaudeCliBrainRuntime(settings(executable)).next_action(request())


def test_error_envelope_raises_protocol_error(tmp_path: Path) -> None:
    error_envelope = json.dumps({"type": "result", "is_error": True, "result": "boom"})
    executable, _ = make_fake(tmp_path, stdouts=[error_envelope])
    with pytest.raises(BrainProtocolError):
        ClaudeCliBrainRuntime(settings(executable)).next_action(request())


def test_nonzero_exit_raises_unavailable_without_leaking_stderr(tmp_path: Path) -> None:
    executable, _ = make_fake(
        tmp_path, stdouts=[""], stderr="credential path /home/x/.claude/secret", exit_code=3
    )
    with pytest.raises(BrainUnavailable) as excinfo:
        ClaudeCliBrainRuntime(settings(executable)).next_action(request())
    assert "secret" not in str(excinfo.value)
    assert ".claude" not in str(excinfo.value)


def test_missing_executable_raises_unavailable(tmp_path: Path) -> None:
    runtime = ClaudeCliBrainRuntime(settings(str(tmp_path / "does-not-exist")))
    with pytest.raises(BrainUnavailable):
        runtime.next_action(request())


def test_timeout_raises_brain_timeout(tmp_path: Path) -> None:
    executable, _ = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)], sleep=5.0)
    runtime = ClaudeCliBrainRuntime(settings(executable, timeout_seconds=0.3))
    with pytest.raises(BrainTimeout):
        runtime.next_action(request())


def test_configured_timeout_caps_a_larger_processing_budget(tmp_path: Path) -> None:
    """A caller-supplied processing budget must never widen the configured
    per-call timeout — min(configured, request) applies, not the request
    value alone."""
    executable, _ = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)], sleep=0.4)
    runtime = ClaudeCliBrainRuntime(settings(executable, timeout_seconds=0.2))
    with pytest.raises(BrainTimeout):
        runtime.next_action(request(timeout_seconds=10.0))


def test_repair_attempt_shares_the_original_deadline(tmp_path: Path) -> None:
    """The bounded repair retry must draw from the same overall deadline as
    the original call, not get a fresh full budget of its own."""
    executable, _ = make_fake(
        tmp_path,
        stdouts=[envelope("not json"), envelope(INVOKE_ACTION)],
        sleep=0.35,
    )
    runtime = ClaudeCliBrainRuntime(settings(executable, timeout_seconds=0.5))
    with pytest.raises(BrainTimeout):
        runtime.next_action(request())


def test_stdin_write_never_blocks_past_the_deadline_on_a_stalled_child(
    tmp_path: Path,
) -> None:
    """A child that never reads stdin must not be able to hang the call
    beyond its timeout via a blocking stdin.write of a large prompt."""
    script = tmp_path / "stalled-claude"
    script.write_text("#!/usr/bin/env python3\nimport sys, time\ntime.sleep(5)\nsys.stdin.read()\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    runtime = ClaudeCliBrainRuntime(settings(str(script), timeout_seconds=0.3))
    big_context = "x" * (2 * 1024 * 1024)
    start = time.monotonic()
    with pytest.raises(BrainProtocolError, match="prompt input exceeded"):
        runtime.next_action(request(context=big_context))
    assert time.monotonic() - start < 2.0


def test_oversized_stdout_raises_protocol_error(tmp_path: Path) -> None:
    executable, _ = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)])
    runtime = ClaudeCliBrainRuntime(settings(executable, max_output_bytes=10))
    with pytest.raises(BrainProtocolError):
        runtime.next_action(request())


def test_oversized_model_response_raises_protocol_error(tmp_path: Path) -> None:
    executable, _ = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)])
    runtime = ClaudeCliBrainRuntime(settings(executable))
    with pytest.raises(BrainProtocolError):
        runtime.next_action(request(max_response_bytes=5))


def test_partial_envelope_raises_protocol_error(tmp_path: Path) -> None:
    executable, _ = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)[:40]])
    with pytest.raises(BrainProtocolError):
        ClaudeCliBrainRuntime(settings(executable)).next_action(request())


def test_repair_recovers_from_invalid_action_json(tmp_path: Path) -> None:
    executable, record = make_fake(
        tmp_path,
        stdouts=[envelope("I think the answer is: not-json"), envelope(INVOKE_ACTION)],
    )
    response = ClaudeCliBrainRuntime(settings(executable)).next_action(request())
    assert response.action == InvokeToolAction(tool="workspace.list", tool_input={}, reason=None)
    assert response.repaired is True
    # the repair prompt carries the validation error and the prior response
    repair_stdin = (record / "stdin-1.txt").read_text()
    assert "not a valid action envelope" in repair_stdin
    assert "not-json" in repair_stdin


def test_repair_exhaustion_raises_response_invalid(tmp_path: Path) -> None:
    executable, record = make_fake(
        tmp_path,
        stdouts=[envelope("still not json"), envelope('{"version": 2, "action": "finish"}')],
    )
    with pytest.raises(BrainResponseInvalid):
        ClaudeCliBrainRuntime(settings(executable)).next_action(request())
    # exactly two invocations: the original and one bounded repair — never a loop
    assert (record / "count").read_text() == "2"


def test_nothing_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    executable, _ = make_fake(tmp_path, stdouts=[envelope(FINISH_ACTION)])
    with caplog.at_level(logging.DEBUG):
        ClaudeCliBrainRuntime(settings(executable)).next_action(request())
    assert caplog.records == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executable", "  "),
        ("model", "  "),
        ("timeout_seconds", 0),
        ("max_output_bytes", 0),
    ],
)
def test_settings_invariants(field: str, value: object) -> None:
    fields: dict[str, object] = {
        "executable": "/usr/bin/true",
        "model": "haiku",
        "timeout_seconds": 10.0,
        "max_output_bytes": 1_000_000,
    }
    fields[field] = value
    with pytest.raises(ValueError):
        ClaudeCliSettings(**fields)  # type: ignore[arg-type]


# --- envelope protocol parsing -------------------------------------------


def test_parse_envelope_extracts_safe_subset() -> None:
    parsed = parse_cli_envelope(envelope(FINISH_ACTION))
    assert parsed.result_text == FINISH_ACTION
    assert parsed.model == "claude-x"
    assert parsed.usage == {"input_tokens": 5, "output_tokens": 7}


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps(["a", "list"]),
        json.dumps({"type": "other", "is_error": False, "result": "x"}),
        json.dumps({"type": "result", "is_error": True, "result": "x"}),
        json.dumps({"type": "result", "is_error": False, "result": ""}),
        json.dumps({"type": "result", "is_error": False}),
    ],
)
def test_parse_envelope_rejects_deviations(raw: str) -> None:
    with pytest.raises(BrainProtocolError):
        parse_cli_envelope(raw)


def test_parse_envelope_tolerates_missing_metadata() -> None:
    raw = json.dumps({"type": "result", "is_error": False, "result": FINISH_ACTION})
    parsed = parse_cli_envelope(raw)
    assert parsed.model is None
    assert parsed.usage is None
