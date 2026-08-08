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

from collections.abc import Callable

from friday.application.brain_runtime import BrainRuntime
from friday.application.errors import UnknownBrainRuntimeKind

DEFAULT_RUNTIME_KIND = "claude_cli"
"""The runtime_kind every existing Agent-less Run continues to use; Step 1
introduces no other adapter."""


class BrainRuntimeRegistry:
    """A fixed, code-owned mapping from `runtime_kind` to a `BrainRuntime`
    factory. Never constructed from persisted data — only from calls made
    directly in application/infrastructure code."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], BrainRuntime]] = {}

    def register(self, runtime_kind: str, factory: Callable[[], BrainRuntime]) -> None:
        if not runtime_kind:
            raise ValueError("runtime_kind must not be empty")
        self._factories[runtime_kind] = factory

    def is_registered(self, runtime_kind: str) -> bool:
        return runtime_kind in self._factories

    def create(self, runtime_kind: str) -> BrainRuntime:
        try:
            factory = self._factories[runtime_kind]
        except KeyError:
            raise UnknownBrainRuntimeKind(runtime_kind) from None
        return factory()
