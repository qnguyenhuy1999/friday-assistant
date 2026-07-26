"""Worker composition root: settings, infrastructure, use cases, processor,
and loop. Construction is fail-closed: a missing Claude executable, an
unverifiable brain-only CLI, or an invalid workspace root raises before any
Worker exists — no claim can ever happen without a real processor."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import Engine

from apps.worker.computer_settings import ComputerSettings
from apps.worker.memory_settings import MemorySettings
from apps.worker.runtime_settings import RuntimeSettings
from apps.worker.settings import WorkerSettings
from apps.worker.worker_loop import WorkerLoop
from friday.application.agent_run_processor import AgentRunProcessor, RuntimeLimits
from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.memory.index_coordination import (
    BuildMemoryIndex,
    InspectMemoryIndex,
    RefreshMemoryIndexIfStale,
)
from friday.application.memory.models import (
    IndexBuildRequest,
    IndexSnapshot,
    IndexState,
    IndexStatus,
    MemoryCandidate,
    MemoryContext,
    MemoryExcerpt,
    MemoryQuery,
    MemoryVaultPolicy,
    MemoryWriteCandidate,
    MemoryWriteResult,
    RetrievalMode,
)
from friday.application.memory.retrieval import MemoryRetrievalSettings, MemoryRetriever
from friday.application.ports import UnitOfWorkFactory
from friday.application.retry_policy import RetryPolicy
from friday.application.tool_authorization import RequestToolApproval
from friday.application.tool_gateway import ToolGateway
from friday.application.worker_coordination import (
    ApplyFailedOutcome,
    ApplySucceededOutcome,
    ApplyWaitingOutcome,
    ClaimNextRun,
    RenewRunLease,
    RequeueClaimedRun,
    VerifyRunClaim,
)
from friday.application.worker_maintenance import ExpireDueApprovals, RecoverExpiredLeases
from friday.infrastructure.brain.claude_cli import (
    ClaudeCliBrainRuntime,
    ClaudeCliSettings,
    verify_brain_only_support,
)
from friday.infrastructure.clock import SystemClock
from friday.infrastructure.memory.graphify_cli import GraphifyCliIndexBuilder, GraphifyCliSettings
from friday.infrastructure.memory.graphify_json import GraphifyJsonIndex, GraphifyJsonIndexSettings
from friday.infrastructure.memory.lexical_index import LexicalIndexStore
from friday.infrastructure.memory.obsidian_vault import ObsidianVaultStore
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from friday.infrastructure.tools.composite import CompositeToolGateway
from friday.infrastructure.tools.computer_composition import (
    ComputerGatewayConfig,
    build_computer_gateway,
)
from friday.infrastructure.tools.computer_gateway import ComputerToolGateway
from friday.infrastructure.tools.gateway import (
    WorkspaceToolGateway,
    WorkspaceToolGatewaySettings,
)
from friday.infrastructure.tools.memory_tools import MemoryToolSettings


@dataclass(slots=True)
class Worker:
    engine: Engine
    settings: WorkerSettings
    loop: WorkerLoop
    processor: AgentRunProcessor
    computer_gateway: ComputerToolGateway | None = None

    def close(self) -> None:
        """Release everything the worker owns outside this process.

        The database engine is not the only such resource once computer use is
        enabled: the driver is a child process holding a stdio pipe, and a
        worker that exits without closing it leaves it orphaned. Both are
        released here so `main` has one shutdown call rather than a list that
        silently falls behind.
        """
        if self.computer_gateway is not None:
            self.computer_gateway.close()
        self.engine.dispose()


class _DisabledMemoryRetriever:
    """Explicit no-I/O retriever used whenever memory is not safely configured."""

    def retrieve(self, *, query: MemoryQuery) -> MemoryContext:
        del query
        return MemoryContext(RetrievalMode.DISABLED, (), (), None, IndexState.DISABLED, 0)


class _DisabledStructuralIndex:
    """Structural index used whenever Graphify is disabled: always reports
    MISSING so a stale on-disk graph.json from a previous run can never
    influence retrieval. The flag must guarantee zero structural influence,
    not just zero rebuilds. MISSING (not DISABLED) keeps retrieval in its
    normal lexical-only mode -- lexical search is unaffected by this flag,
    only structural search is."""

    def status(self) -> IndexStatus:
        return IndexStatus(IndexState.MISSING, None, None, None, 0, 0, None, None)

    def search(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        del query, limit
        return ()

    def neighbors(self, path: str, *, depth: int, max_nodes: int) -> tuple[MemoryCandidate, ...]:
        del path, depth, max_nodes
        return ()


class _LexicalMemoryStore:
    """Use the efficient lexical index while retaining canonical vault reads."""

    def __init__(self, vault: ObsidianVaultStore, lexical: LexicalIndexStore) -> None:
        self._vault = vault
        self._lexical = lexical

    def search_lexical(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        return self._lexical.search(query, limit=limit)

    def read_excerpt(self, candidate: MemoryCandidate, *, max_chars: int) -> MemoryExcerpt:
        return self._vault.read_excerpt(candidate, max_chars=max_chars)

    def source_snapshot_hash(self) -> str:
        return self._vault.source_snapshot_hash()

    def write_candidate(self, candidate: MemoryWriteCandidate) -> MemoryWriteResult:
        return self._vault.write_candidate(candidate)


class _UowBackedIndexSnapshotRepository:
    """Adapts the durable snapshot repository to BuildMemoryIndex's
    UnitOfWork-free call site: index builds happen outside any Run's
    transaction, so each call here opens its own short transaction and
    commits immediately rather than relying on a caller to do so."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def add(self, snapshot: IndexSnapshot) -> None:
        with self._uow_factory() as uow:
            uow.memory_index_snapshots.add(snapshot)
            uow.commit()

    def latest(self) -> IndexSnapshot | None:
        with self._uow_factory() as uow:
            return uow.memory_index_snapshots.latest()

    def mark_stale(self, snapshot_id: str) -> None:
        with self._uow_factory() as uow:
            uow.memory_index_snapshots.mark_stale(snapshot_id)
            uow.commit()


@dataclass(frozen=True, slots=True)
class _MemoryStack:
    retriever: MemoryRetriever | _DisabledMemoryRetriever
    refresh_index: RefreshMemoryIndexIfStale | None
    maintenance_interval_seconds: float | None
    tool_settings: MemoryToolSettings | None


def _memory_stack(uow_factory: UnitOfWorkFactory) -> _MemoryStack:
    """Construct opt-in memory dependencies without ever scanning an invalid vault."""
    try:
        settings = MemorySettings.from_env()
    except ValueError:
        return _disabled_memory_stack()
    if not settings.memory_enabled or not settings.include_globs:
        return _disabled_memory_stack()
    if not settings.vault_root.is_dir():
        return _disabled_memory_stack()

    policy = MemoryVaultPolicy(
        include_globs=settings.effective_include_globs,
        exclude_globs=settings.exclude_globs,
        max_files=settings.max_files,
        max_note_bytes=settings.max_note_bytes,
    )
    vault = ObsidianVaultStore(settings.vault_root, policy, managed_root=settings.managed_root)
    lexical = LexicalIndexStore(
        settings.vault_root, policy, max_files_scanned=settings.index_max_files_per_scan
    )
    store = _LexicalMemoryStore(vault, lexical)
    tool_settings = MemoryToolSettings(
        vault_root=settings.vault_root,
        policy=policy,
        max_search_limit=settings.max_candidates,
        max_excerpt_chars=settings.max_excerpt_chars,
        managed_root=settings.managed_root,
    )
    retrieval_settings = MemoryRetrievalSettings(
        max_candidates=settings.max_candidates,
        max_excerpts=settings.max_excerpts,
        max_excerpt_chars=settings.max_excerpt_chars,
        max_total_context_chars=settings.max_total_context_chars,
        max_graph_depth=settings.max_graph_depth,
        max_graph_nodes_visited=settings.max_graph_nodes_visited,
    )

    if not settings.graphify_enabled:
        # Fail-closed: no GraphifyJsonIndex is even constructed, so a stale
        # active/graph.json left on disk from a previous run cannot leak
        # structural results back into retrieval.
        retriever = MemoryRetriever(store, _DisabledStructuralIndex(), settings=retrieval_settings)
        return _MemoryStack(retriever, None, None, tool_settings)

    vault_identity_hash = hashlib.sha256(str(vault.root).encode("utf-8")).hexdigest()
    index = GraphifyJsonIndex(
        GraphifyJsonIndexSettings(
            vault_root=vault.root,
            index_root=settings.graphify_index_root,
            vault_identity_hash=vault_identity_hash,
            max_graph_bytes=settings.graphify_max_graph_bytes,
        ),
        store,
    )
    retriever = MemoryRetriever(store, index, settings=retrieval_settings)

    builder = GraphifyCliIndexBuilder(
        GraphifyCliSettings(
            vault_root=vault.root,
            index_root=settings.graphify_index_root,
            executable=settings.graphify_executable,
            timeout_seconds=settings.graphify_build_timeout_seconds,
            max_stdout_bytes=settings.graphify_max_stdout_bytes,
            max_stderr_bytes=settings.graphify_max_stderr_bytes,
            max_graph_bytes=settings.graphify_max_graph_bytes,
        )
    )

    def request_factory(source_snapshot_hash: str) -> IndexBuildRequest:
        paths = vault.included_paths()[: settings.index_max_files_per_scan]
        return IndexBuildRequest(
            vault_identity_hash,
            source_snapshot_hash,
            paths,
            settings.graphify_build_timeout_seconds,
            settings.graphify_max_graph_bytes,
        )

    snapshots = _UowBackedIndexSnapshotRepository(uow_factory)
    refresh = RefreshMemoryIndexIfStale(
        InspectMemoryIndex(index, store),
        BuildMemoryIndex(builder, store, request_factory, snapshots),
    )
    return _MemoryStack(retriever, refresh, settings.index_maintenance_seconds, tool_settings)


def _disabled_memory_stack() -> _MemoryStack:
    return _MemoryStack(_DisabledMemoryRetriever(), None, None, None)


def _computer_gateway(runtime: RuntimeSettings) -> ComputerToolGateway | None:
    """Build the opt-in computer-use gateway, or None when it is disabled.

    This composition root deliberately knows only three things: that computer
    use is another ToolGateway, that it is off by default, and that a broken
    enabled configuration must stop startup. Drivers, MCP framing, target
    resolution, and screenshot storage all stay behind build_computer_gateway —
    see friday.infrastructure.tools.computer_composition.

    The concrete type is returned rather than the ToolGateway protocol for one
    reason: the worker has to be able to shut the driver process down, and
    `close()` is not part of what a tool gateway is.

    Unlike memory, an invalid configuration is not silently downgraded: memory
    degrades to "no relevant memory found", which is a truthful answer, whereas
    a computer gateway that cannot reach a desktop has no truthful degraded
    mode. Enabled-and-broken raises here, before any Run is claimed.
    """
    settings = ComputerSettings.from_env()
    if not settings.computer_use_enabled:
        return None
    return build_computer_gateway(
        ComputerGatewayConfig(
            enabled=True,
            workspace_root=runtime.workspace_root,
            driver_command=settings.driver_command,
            timeout_seconds=settings.timeout_seconds,
            max_capture_bytes=settings.max_capture_bytes,
            max_type_chars=settings.max_type_chars,
            max_scroll_amount=settings.max_scroll_amount,
            max_elements=settings.max_elements,
            telemetry_enabled=settings.telemetry_enabled,
        )
    )


def create_worker(settings: WorkerSettings, runtime: RuntimeSettings) -> Worker:
    # --- fail-closed environment verification (before anything else) ------
    claude_settings = ClaudeCliSettings(
        executable=runtime.claude_executable,
        model=runtime.claude_model,
        timeout_seconds=runtime.claude_timeout_seconds,
        max_output_bytes=runtime.claude_max_output_bytes,
        max_stderr_bytes=runtime.claude_max_stderr_bytes,
    )
    verify_brain_only_support(claude_settings)  # raises BrainUnavailable

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    uow_factory = create_unit_of_work_factory(session_factory)
    clock = SystemClock()
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
        multiplier=settings.retry_multiplier,
        max_delay=settings.retry_max_delay,
    )
    memory = _memory_stack(uow_factory)
    workspace_gateway = WorkspaceToolGateway(  # raises WorkspaceAccessDenied
        WorkspaceToolGatewaySettings(
            workspace_root=runtime.workspace_root,
            max_file_bytes=runtime.tool_max_file_bytes,
            max_list_entries=runtime.tool_max_list_entries,
            process_timeout_seconds=runtime.tool_timeout_seconds,
            process_max_timeout_seconds=runtime.tool_max_timeout_seconds,
            max_stdout_bytes=runtime.tool_max_stdout_bytes,
            max_stderr_bytes=runtime.tool_max_stderr_bytes,
            memory=memory.tool_settings,
        )
    )
    gateways: list[ToolGateway] = [workspace_gateway]
    computer_gateway = _computer_gateway(runtime)  # raises ComputerUseUnavailable
    if computer_gateway is not None:
        gateways.append(computer_gateway)
    # ONE composite instance for both the brain manifest and execution: two
    # registries could disagree about which tools exist or what they cost.
    gateway = CompositeToolGateway(*gateways)

    brain = ClaudeCliBrainRuntime(claude_settings)
    processor = AgentRunProcessor(
        uow_factory=uow_factory,
        clock=clock,
        brain=brain,
        gateway=gateway,
        verify_claim=VerifyRunClaim(uow_factory, clock),
        request_tool_approval=RequestToolApproval(uow_factory, clock),
        execute_tool_action=ExecuteToolAction(uow_factory, clock, gateway),
        limits=RuntimeLimits(
            max_turns_per_claim=runtime.max_turns_per_claim,
            max_tool_calls_per_claim=runtime.max_tool_calls_per_claim,
            max_context_chars=runtime.max_context_chars,
            max_response_bytes=runtime.max_response_bytes,
            max_yield_seconds=runtime.max_yield_seconds,
            max_processing_seconds=runtime.max_processing_seconds,
        ),
        memory_retriever=memory.retriever,
    )

    loop = WorkerLoop(
        claim_next_run=ClaimNextRun(
            uow_factory,
            clock,
            worker_id=settings.worker_id,
            lease_duration=settings.lease_duration,
            candidate_limit=settings.candidate_limit,
        ),
        renew_lease=RenewRunLease(uow_factory, clock, lease_duration=settings.lease_duration),
        requeue_claimed_run=RequeueClaimedRun(uow_factory, clock),
        apply_failed=ApplyFailedOutcome(uow_factory, clock, retry_policy=retry_policy),
        apply_succeeded=ApplySucceededOutcome(uow_factory, clock),
        apply_waiting=ApplyWaitingOutcome(uow_factory, clock),
        recover_expired_leases=RecoverExpiredLeases(
            uow_factory, clock, batch_size=settings.maintenance_batch_size
        ),
        expire_due_approvals=ExpireDueApprovals(
            uow_factory, clock, batch_size=settings.maintenance_batch_size
        ),
        refresh_memory_index=memory.refresh_index,
        memory_index_maintenance_interval_seconds=memory.maintenance_interval_seconds,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        maintenance_interval_seconds=settings.maintenance_interval_seconds,
        poll_interval_seconds=settings.poll_interval_seconds,
    )
    return Worker(
        engine=engine,
        settings=settings,
        loop=loop,
        processor=processor,
        computer_gateway=computer_gateway,
    )
