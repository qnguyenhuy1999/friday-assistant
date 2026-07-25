"""Safe, opt-in Graphify CLI adapter for replaceable derived indexes."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from friday.application.memory.models import IndexBuildRequest, IndexSnapshot, IndexState
from friday.infrastructure.memory.file_lock import FileLock
from friday.infrastructure.memory.index_metadata import IndexMetadata

_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
_TRUNCATION_MARKER = "…[truncated]"


@dataclass(frozen=True, slots=True)
class GraphifyCliSettings:
    vault_root: Path
    index_root: Path
    executable: str
    timeout_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_graph_bytes: int
    lock_ttl_seconds: float = 1_800.0
    previous_retention: int = 1

    def __post_init__(self) -> None:
        if not self.executable:
            raise ValueError("executable must not be empty")
        if (
            min(
                self.timeout_seconds,
                self.max_stdout_bytes,
                self.max_stderr_bytes,
                self.max_graph_bytes,
                self.lock_ttl_seconds,
            )
            <= 0
        ):
            raise ValueError("Graphify CLI limits must be positive")
        if self.previous_retention < 0:
            raise ValueError("previous_retention must not be negative")
        if self.index_root.resolve(strict=False).is_relative_to(
            self.vault_root.resolve(strict=False)
        ):
            raise ValueError("index_root must be outside vault_root")


class GraphifyCliIndexBuilder:
    def __init__(self, settings: GraphifyCliSettings) -> None:
        self._settings = settings

    def build(self, request: IndexBuildRequest) -> IndexSnapshot:
        started = time.monotonic()
        slug = request.vault_identity_hash[:32]
        vault_dir = self._settings.index_root / slug
        vault_dir.mkdir(parents=True, exist_ok=True)
        lock = FileLock(vault_dir / ".build.lock", self._settings.lock_ttl_seconds, "graphify")
        if not lock.acquire():
            return self._snapshot(
                request, started, IndexState.DISABLED, None, None, 0, 0, "build_locked"
            )
        try:
            return self._build_locked(request, started, vault_dir)
        finally:
            lock.release()

    def _build_locked(
        self, request: IndexBuildRequest, started: float, vault_dir: Path
    ) -> IndexSnapshot:
        executable = shutil.which(self._settings.executable)
        if executable is None and not Path(self._settings.executable).is_file():
            return self._snapshot(
                request, started, IndexState.DISABLED, None, None, 0, 0, "executable_missing"
            )
        staging = vault_dir / "staging"
        source_corpus = vault_dir / "source"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        shutil.rmtree(source_corpus, ignore_errors=True)
        source_corpus.mkdir()
        try:
            self._stage_source_corpus(request, source_corpus)
            version = self._version(executable or self._settings.executable)
            result = _run_bounded(
                [
                    executable or self._settings.executable,
                    "extract",
                    str(source_corpus),
                    "--out",
                    str(staging),
                ],
                source_corpus,
                self._settings.timeout_seconds,
                self._settings.max_stdout_bytes,
                self._settings.max_stderr_bytes,
            )
            if result.reason is not None or result.returncode != 0:
                return self._snapshot(
                    request,
                    started,
                    IndexState.DISABLED,
                    None,
                    version,
                    0,
                    0,
                    result.reason or "command_failed",
                )
            graph = staging / "graphify-out" / "graph.json"
            if not graph.is_file() or graph.stat().st_size > min(
                request.max_graph_bytes, self._settings.max_graph_bytes
            ):
                return self._snapshot(
                    request, started, IndexState.CORRUPT, None, version, 0, 0, "invalid_graph"
                )
            node_count, edge_count = _validate_graph(graph)
            target = staging / "graph.json"
            os.replace(graph, target)
            shutil.rmtree(staging / "graphify-out", ignore_errors=True)
            checksum = _checksum(target)
            file_count, source_bytes = self._source_stats(request)
            metadata = IndexMetadata(
                1,
                version,
                request.vault_identity_hash,
                request.source_snapshot_hash,
                file_count,
                source_bytes,
                datetime.now(UTC),
                time.monotonic() - started,
                checksum,
                node_count,
                edge_count,
            )
            metadata.write(staging)
            self._promote(vault_dir, staging)
            return self._snapshot(
                request,
                started,
                IndexState.FRESH,
                checksum,
                version,
                node_count,
                edge_count,
                None,
                file_count,
                source_bytes,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return self._snapshot(
                request, started, IndexState.CORRUPT, None, None, 0, 0, "build_failed"
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(source_corpus, ignore_errors=True)

    def _stage_source_corpus(self, request: IndexBuildRequest, destination: Path) -> None:
        """Copy only the caller-approved included paths into a staging
        corpus outside the vault -- Graphify must never see excluded,
        private, or otherwise ineligible notes."""
        for relative in request.included_paths:
            source = self._resolve_within_vault(relative)
            if source is None:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, target)
            except OSError:
                continue

    def _resolve_within_vault(self, relative: str) -> Path | None:
        candidate = (self._settings.vault_root / relative).resolve(strict=False)
        root = self._settings.vault_root.resolve(strict=False)
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
        return None

    def _version(self, executable: str) -> str:
        result = _run_bounded(
            [executable, "--version"],
            self._settings.vault_root,
            self._settings.timeout_seconds,
            4096,
            4096,
        )
        if result.returncode != 0 or result.reason is not None:
            return "unknown"
        return result.stdout.strip()[:256] or "unknown"

    def _source_stats(self, request: IndexBuildRequest) -> tuple[int, int]:
        paths = request.included_paths
        total = 0
        for path in paths:
            candidate = (self._settings.vault_root / path).resolve(strict=False)
            if (
                candidate.is_relative_to(self._settings.vault_root.resolve(strict=False))
                and candidate.is_file()
            ):
                total += candidate.stat().st_size
        return len(paths), total

    def _promote(self, vault_dir: Path, staging: Path) -> None:
        active, previous = vault_dir / "active", vault_dir / "previous"
        if previous.exists():
            shutil.rmtree(previous)
        if active.exists():
            os.replace(active, previous)
        os.replace(staging, active)

    def _snapshot(
        self,
        request: IndexBuildRequest,
        started: float,
        state: IndexState,
        checksum: str | None,
        version: str | None,
        nodes: int,
        edges: int,
        failure: str | None,
        file_count: int = 0,
        source_bytes: int = 0,
    ) -> IndexSnapshot:
        return IndexSnapshot(
            id=hashlib.sha256(
                f"{request.source_snapshot_hash}:{time.monotonic_ns()}".encode()
            ).hexdigest(),
            vault_identity_hash=request.vault_identity_hash,
            source_snapshot_hash=request.source_snapshot_hash,
            graph_checksum=checksum,
            graphify_version=version,
            state=state,
            built_at=datetime.now(UTC),
            build_duration_seconds=time.monotonic() - started,
            file_count=file_count,
            source_total_bytes=source_bytes,
            node_count=nodes,
            edge_count=edges,
            failure_code=failure,
        )


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    returncode: int | None
    stdout: str
    stderr: str
    reason: str | None


def _run_bounded(
    argv: list[str], cwd: Path, timeout: float, stdout_cap: int, stderr_cap: int
) -> _ProcessResult:
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env={
                key: value for key in _ENV_ALLOWLIST if (value := os.environ.get(key)) is not None
            },
            start_new_session=True,
        )
    except OSError:
        return _ProcessResult(None, "", "", "spawn_failed")
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            assert stream is not None
            selector.register(stream, selectors.EVENT_READ, name)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(process)
                return _result(process, buffers, "timeout")
            for key, _ in selector.select(min(remaining, 0.1)):
                data = os.read(key.fd, 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                bucket = buffers[key.data]
                cap = stdout_cap if key.data == "stdout" else stderr_cap
                bucket.extend(data)
                if len(bucket) > cap:
                    del bucket[cap:]
        process.wait()
        return _result(process, buffers, None)
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _result(
    process: subprocess.Popen[bytes], buffers: dict[str, bytearray], reason: str | None
) -> _ProcessResult:
    return _ProcessResult(
        process.returncode,
        bytes(buffers["stdout"]).decode(errors="replace")[:],
        bytes(buffers["stderr"]).decode(errors="replace")[:],
        reason,
    )


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()
    process.wait(timeout=2)


def _validate_graph(path: Path) -> tuple[int, int]:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "directed",
        "multigraph",
        "graph",
        "nodes",
        "links",
        "hyperedges",
        "built_at_commit",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or not isinstance(value["nodes"], list)
        or not isinstance(value["links"], list)
    ):
        raise ValueError("invalid Graphify graph schema")
    return len(value["nodes"]), len(value["links"])


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
