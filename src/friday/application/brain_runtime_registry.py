"""Code-owned brain runtime registry: `AgentRevision.runtime_kind` names one
of a fixed, code-registered set of adapter factories. A persisted string can
never import arbitrary Python or name an arbitrary executable path — an
unrecognized kind fails closed with `UnknownBrainRuntimeKind` rather than
falling back to a default runtime.

This module holds only the registry mechanism. The default registration
(mapping `"claude_cli"` to a `ClaudeCliBrainRuntime` factory) belongs to the
composition root (`apps/worker/app.py`), which already owns the concrete
`ClaudeCliSettings`; this layer must not import infrastructure adapters."""

from __future__ import annotations

import json
from collections.abc import Callable

from friday.application.brain_runtime import BrainRuntime
from friday.application.errors import InvalidBrainRuntimeConfig, UnknownBrainRuntimeKind
from friday.domain.errors import DomainValidationError
from friday.domain.json_value import JsonValue, ensure_json_value

DEFAULT_RUNTIME_KIND = "claude_cli"
"""The runtime_kind every existing Agent-less Run continues to use; Step 1
introduces no other adapter."""

MAX_RUNTIME_CONFIG_BYTES = 512
MAX_RUNTIME_CONFIG_NODES = 32
MAX_RUNTIME_CONFIG_DEPTH = 4


def _config_complexity(value: JsonValue, *, depth: int = 0) -> tuple[int, int]:
    """Return bounded structural node count and maximum nesting depth."""
    if isinstance(value, dict):
        nodes, max_depth = 1, depth
        for key, child in value.items():
            child_nodes, child_depth = _config_complexity(child, depth=depth + 1)
            nodes += 1 + len(key) + child_nodes
            max_depth = max(max_depth, child_depth)
        return nodes, max_depth
    if isinstance(value, list):
        nodes, max_depth = 1, depth
        for child in value:
            child_nodes, child_depth = _config_complexity(child, depth=depth + 1)
            nodes += 1 + child_nodes
            max_depth = max(max_depth, child_depth)
        return nodes, max_depth
    return 1, depth


def _require_empty_claude_cli_config(value: JsonValue, runtime_kind: str) -> JsonValue:
    """Step 1's only runtime policy: no persisted behavioral knobs yet."""
    if not isinstance(value, dict) or value:
        raise InvalidBrainRuntimeConfig(runtime_kind)
    return value


_RUNTIME_CONFIG_POLICIES: dict[str, Callable[[JsonValue, str], JsonValue]] = {
    DEFAULT_RUNTIME_KIND: _require_empty_claude_cli_config,
}


class BrainRuntimeRegistry:
    """A fixed, code-owned mapping from `runtime_kind` to a `BrainRuntime`
    factory. Never constructed from persisted data — only from calls made
    directly in application/infrastructure code."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], BrainRuntime]] = {}

    def register(self, runtime_kind: str, factory: Callable[[], BrainRuntime]) -> None:
        if not runtime_kind:
            raise ValueError("runtime_kind must not be empty")
        if runtime_kind not in _RUNTIME_CONFIG_POLICIES:
            raise ValueError(f"no code-owned runtime configuration policy: {runtime_kind}")
        self._factories[runtime_kind] = factory

    def is_registered(self, runtime_kind: str) -> bool:
        return runtime_kind in self._factories

    def create(self, runtime_kind: str) -> BrainRuntime:
        try:
            factory = self._factories[runtime_kind]
        except KeyError:
            raise UnknownBrainRuntimeKind(runtime_kind) from None
        return factory()

    def validate_runtime_config(self, runtime_kind: str, runtime_config: object) -> JsonValue:
        """Validate and return a bounded, JSON-canonicalizable config.

        Runtime configuration is behavioral metadata only.  It is never a
        source of executable paths, commands, environment, credentials,
        tools, MCP, computer-use, messaging, or approval authority.  The
        positive policy for `claude_cli` is therefore the empty object.
        """
        if not self.is_registered(runtime_kind):
            raise UnknownBrainRuntimeKind(runtime_kind)
        try:
            normalized = ensure_json_value(runtime_config, path="AgentRevision.runtime_config")
            canonical = json.dumps(
                normalized,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (DomainValidationError, TypeError, ValueError):
            raise InvalidBrainRuntimeConfig(runtime_kind) from None

        nodes, depth = _config_complexity(normalized)
        if (
            len(canonical.encode("utf-8")) > MAX_RUNTIME_CONFIG_BYTES
            or nodes > MAX_RUNTIME_CONFIG_NODES
            or depth > MAX_RUNTIME_CONFIG_DEPTH
        ):
            raise InvalidBrainRuntimeConfig(runtime_kind)
        return _RUNTIME_CONFIG_POLICIES[runtime_kind](normalized, runtime_kind)
