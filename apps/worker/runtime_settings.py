"""Phase 11 runtime settings: brain backend, Claude CLI, runtime budgets,
and workspace tool limits. Deliberately separate from WorkerSettings (queue
coordination) — one module per concern.

No secret-bearing setting exists: authentication is the locally
authenticated Claude CLI subscription, never an API key."""

from __future__ import annotations

import os
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

_DEFAULT_BRAIN_BACKEND = "claude_cli"
_DEFAULT_CLAUDE_EXECUTABLE = "claude"
_DEFAULT_CLAUDE_TIMEOUT_SECONDS = 180.0
_DEFAULT_CLAUDE_MAX_OUTPUT_BYTES = 1_000_000
_DEFAULT_CLAUDE_MAX_STDERR_BYTES = 200_000
_DEFAULT_MAX_TURNS_PER_CLAIM = 8
_DEFAULT_MAX_TOOL_CALLS_PER_CLAIM = 4
_DEFAULT_MAX_CONTEXT_CHARS = 60_000
_DEFAULT_MAX_SKILL_CONTEXT_CHARS = 24_000
_DEFAULT_MAX_AGENT_CONTEXT_CHARS = 24_000
_DEFAULT_MAX_DELEGATIONS_PER_RUN = 4
_DEFAULT_MAX_DELEGATION_TARGETS = 32
_DEFAULT_MAX_RESPONSE_BYTES = 65_536
_DEFAULT_MAX_YIELD_SECONDS = 3_600
_DEFAULT_MAX_PROCESSING_SECONDS = 600.0
_DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
_DEFAULT_TOOL_MAX_TIMEOUT_SECONDS = 120.0
_DEFAULT_TOOL_MAX_STDOUT_BYTES = 200_000
_DEFAULT_TOOL_MAX_STDERR_BYTES = 200_000
_DEFAULT_TOOL_MAX_FILE_BYTES = 1_000_000
_DEFAULT_TOOL_MAX_LIST_ENTRIES = 500


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    workspace_root: Path
    brain_backend: str
    claude_executable: str
    claude_model: str | None
    claude_timeout_seconds: float
    claude_max_output_bytes: int
    max_turns_per_claim: int
    max_tool_calls_per_claim: int
    max_context_chars: int
    max_response_bytes: int
    max_yield_seconds: int
    tool_timeout_seconds: float
    tool_max_timeout_seconds: float
    tool_max_stdout_bytes: int
    tool_max_stderr_bytes: int
    tool_max_file_bytes: int
    tool_max_list_entries: int
    max_processing_seconds: float = _DEFAULT_MAX_PROCESSING_SECONDS
    claude_max_stderr_bytes: int = _DEFAULT_CLAUDE_MAX_STDERR_BYTES
    max_skill_context_chars: int = _DEFAULT_MAX_SKILL_CONTEXT_CHARS
    max_agent_context_chars: int | None = None
    max_delegations_per_run: int = _DEFAULT_MAX_DELEGATIONS_PER_RUN
    max_delegation_targets: int = _DEFAULT_MAX_DELEGATION_TARGETS

    def __post_init__(self) -> None:
        if not str(self.workspace_root).strip():
            raise ValueError("workspace_root must not be empty")
        if self.brain_backend != _DEFAULT_BRAIN_BACKEND:
            raise ValueError(f"unsupported brain backend: {self.brain_backend!r}")
        if not self.claude_executable.strip():
            raise ValueError("claude_executable must not be empty")
        if self.claude_model is not None and not self.claude_model.strip():
            raise ValueError("claude_model must not be blank when set")
        positives = {
            "claude_timeout_seconds": self.claude_timeout_seconds,
            "claude_max_output_bytes": self.claude_max_output_bytes,
            "claude_max_stderr_bytes": self.claude_max_stderr_bytes,
            "max_turns_per_claim": self.max_turns_per_claim,
            "max_tool_calls_per_claim": self.max_tool_calls_per_claim,
            "max_context_chars": self.max_context_chars,
            "max_skill_context_chars": self.max_skill_context_chars,
            "max_delegations_per_run": self.max_delegations_per_run,
            "max_delegation_targets": self.max_delegation_targets,
            "max_response_bytes": self.max_response_bytes,
            "max_processing_seconds": self.max_processing_seconds,
            "tool_timeout_seconds": self.tool_timeout_seconds,
            "tool_max_timeout_seconds": self.tool_max_timeout_seconds,
            "tool_max_stdout_bytes": self.tool_max_stdout_bytes,
            "tool_max_stderr_bytes": self.tool_max_stderr_bytes,
            "tool_max_file_bytes": self.tool_max_file_bytes,
            "tool_max_list_entries": self.tool_max_list_entries,
        }
        for name, value in positives.items():
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.max_yield_seconds < 0:
            raise ValueError("max_yield_seconds must be >= 0")
        if self.max_skill_context_chars >= self.max_context_chars:
            raise ValueError("max_skill_context_chars must be below max_context_chars")
        if (
            self.max_agent_context_chars is not None
            and self.max_agent_context_chars > self.max_context_chars
        ):
            raise ValueError("max_agent_context_chars must be at most max_context_chars")
        if self.tool_max_timeout_seconds < self.tool_timeout_seconds:
            raise ValueError("tool_max_timeout_seconds must be >= tool_timeout_seconds")

    @classmethod
    def from_env(cls) -> RuntimeSettings:
        workspace_root = os.environ.get("FRIDAY_WORKER_WORKSPACE_ROOT", "").strip()
        if not workspace_root:
            raise ValueError(
                "FRIDAY_WORKER_WORKSPACE_ROOT is required: the workspace root "
                "must be explicit — tools are confined to it"
            )
        model = os.environ.get("FRIDAY_CLAUDE_MODEL", "").strip() or None
        return cls(
            workspace_root=Path(workspace_root),
            brain_backend=os.environ.get("FRIDAY_BRAIN_BACKEND", _DEFAULT_BRAIN_BACKEND),
            claude_executable=os.environ.get(
                "FRIDAY_CLAUDE_EXECUTABLE", _DEFAULT_CLAUDE_EXECUTABLE
            ),
            claude_model=model,
            claude_timeout_seconds=float(
                os.environ.get("FRIDAY_CLAUDE_TIMEOUT_SECONDS", _DEFAULT_CLAUDE_TIMEOUT_SECONDS)
            ),
            claude_max_output_bytes=int(
                os.environ.get("FRIDAY_CLAUDE_MAX_OUTPUT_BYTES", _DEFAULT_CLAUDE_MAX_OUTPUT_BYTES)
            ),
            claude_max_stderr_bytes=int(
                os.environ.get("FRIDAY_CLAUDE_MAX_STDERR_BYTES", _DEFAULT_CLAUDE_MAX_STDERR_BYTES)
            ),
            max_turns_per_claim=int(
                os.environ.get("FRIDAY_RUNTIME_MAX_TURNS_PER_CLAIM", _DEFAULT_MAX_TURNS_PER_CLAIM)
            ),
            max_tool_calls_per_claim=int(
                os.environ.get(
                    "FRIDAY_RUNTIME_MAX_TOOL_CALLS_PER_CLAIM", _DEFAULT_MAX_TOOL_CALLS_PER_CLAIM
                )
            ),
            max_context_chars=int(
                os.environ.get("FRIDAY_RUNTIME_MAX_CONTEXT_CHARS", _DEFAULT_MAX_CONTEXT_CHARS)
            ),
            max_skill_context_chars=int(
                os.environ.get(
                    "FRIDAY_RUNTIME_MAX_SKILL_CONTEXT_CHARS", _DEFAULT_MAX_SKILL_CONTEXT_CHARS
                )
            ),
            max_agent_context_chars=int(
                os.environ.get(
                    "FRIDAY_RUNTIME_MAX_AGENT_CONTEXT_CHARS", _DEFAULT_MAX_AGENT_CONTEXT_CHARS
                )
            ),
            max_delegations_per_run=int(
                os.environ.get(
                    "FRIDAY_RUNTIME_MAX_DELEGATIONS_PER_RUN", _DEFAULT_MAX_DELEGATIONS_PER_RUN
                )
            ),
            max_delegation_targets=int(
                os.environ.get(
                    "FRIDAY_RUNTIME_MAX_DELEGATION_TARGETS", _DEFAULT_MAX_DELEGATION_TARGETS
                )
            ),
            max_response_bytes=int(
                os.environ.get("FRIDAY_RUNTIME_MAX_RESPONSE_BYTES", _DEFAULT_MAX_RESPONSE_BYTES)
            ),
            max_yield_seconds=int(
                os.environ.get("FRIDAY_RUNTIME_MAX_YIELD_SECONDS", _DEFAULT_MAX_YIELD_SECONDS)
            ),
            max_processing_seconds=float(
                os.environ.get(
                    "FRIDAY_RUNTIME_MAX_PROCESSING_SECONDS", _DEFAULT_MAX_PROCESSING_SECONDS
                )
            ),
            tool_timeout_seconds=float(
                os.environ.get("FRIDAY_TOOL_TIMEOUT_SECONDS", _DEFAULT_TOOL_TIMEOUT_SECONDS)
            ),
            tool_max_timeout_seconds=float(
                os.environ.get("FRIDAY_TOOL_MAX_TIMEOUT_SECONDS", _DEFAULT_TOOL_MAX_TIMEOUT_SECONDS)
            ),
            tool_max_stdout_bytes=int(
                os.environ.get("FRIDAY_TOOL_MAX_STDOUT_BYTES", _DEFAULT_TOOL_MAX_STDOUT_BYTES)
            ),
            tool_max_stderr_bytes=int(
                os.environ.get("FRIDAY_TOOL_MAX_STDERR_BYTES", _DEFAULT_TOOL_MAX_STDERR_BYTES)
            ),
            tool_max_file_bytes=int(
                os.environ.get("FRIDAY_TOOL_MAX_FILE_BYTES", _DEFAULT_TOOL_MAX_FILE_BYTES)
            ),
            tool_max_list_entries=int(
                os.environ.get("FRIDAY_TOOL_MAX_LIST_ENTRIES", _DEFAULT_TOOL_MAX_LIST_ENTRIES)
            ),
        )
