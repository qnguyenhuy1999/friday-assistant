"""The one JSON-RPC 2.0 stdio session: one child, one framing implementation.

Both MCP consumers sit on this module — Phase 13's computer-use driver adapter
and Phase 18's Friday-owned MCP tool gateway. A second framing/process
implementation would mean two places to get line bounds, queue bounds, stderr
draining, handshake cleanup, non-blocking writes, and process-group shutdown
right, and only one of them would stay correct.

The session is vendor-free and protocol-agnostic: it spawns an argv (never a
shell), frames newline-delimited JSON, serializes requests under one lock, and
raises four neutral failures each caller maps onto its own error vocabulary. It
knows nothing about `initialize`, tools, or capabilities.

Three properties are load-bearing, and all three are about an untrusted child:

*Bounded input.* Every read is `readline(limit)`; a line that does not
terminate within the limit is a protocol violation, not something to keep
buffering. The pending-message queue is bounded too — line size alone does not
bound a peer that emits unsolicited notifications forever, so an overflow
invalidates the session and reaps the child rather than growing memory.

*Transactional start.* `start(handshake=...)` either returns with a live,
handshaken child or leaves no child at all. A caller that treats a failed
connect as "unavailable, carry on" — discovery does exactly that — must not
thereby strand a process for the lifetime of the worker.

*Bounded writes.* stdin is written non-blocking against the same deadline as
the read. A child that never drains its stdin would otherwise block a worker
thread forever inside what the caller believes is a bounded call.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import select
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import IO, cast

from friday.domain.json_value import JsonValue

DEFAULT_MAX_PENDING_MESSAGES = 128
MAX_IGNORED_MESSAGES_PER_REQUEST = 128
_STDERR_CHUNK_BYTES = 8_000
_SHUTDOWN_GRACE_SECONDS = 2.0
_POLL_SECONDS = 0.05
_MAX_JSON_DEPTH = 64
_MALFORMED_MESSAGE = "the child sent a malformed message"


class StdioSessionError(Exception):
    """Base for every stdio-session failure. Never carries peer text: a
    JSON-RPC error body or a stderr line can quote absolute paths, usernames,
    window contents, and tokens."""


class StdioSessionUnavailable(StdioSessionError):
    """The child could not be started, exited, or is not connected."""


class StdioSessionTimeout(StdioSessionError):
    """No answer within the caller's budget, or the caller cancelled."""


class StdioSessionProtocolError(StdioSessionError):
    """The peer violated framing, JSON-RPC shape, or its message budget."""


class StdioSessionRemoteError(StdioSessionError):
    """The peer answered with a well-formed JSON-RPC error object."""


@dataclass(frozen=True, slots=True)
class StdioSessionSettings:
    argv: tuple[str, ...]
    environment: Mapping[str, str]
    max_line_bytes: int
    max_pending_messages: int = DEFAULT_MAX_PENDING_MESSAGES

    def __post_init__(self) -> None:
        if not self.argv or not all(part.strip() for part in self.argv):
            raise ValueError("argv must be a non-empty argument list")
        if self.max_line_bytes < 1:
            raise ValueError("max_line_bytes must be positive")
        if self.max_pending_messages < 1:
            raise ValueError("max_pending_messages must be positive")


class StdioJsonRpcSession:
    """One child process, one reader thread, one stderr drain, serialized ids."""

    def __init__(self, settings: StdioSessionSettings) -> None:
        self._settings = settings
        self._process: subprocess.Popen[bytes] | None = None
        # `start_new_session=True` makes the child pid the process-group id on
        # POSIX.  Keep that value while we own the session: asking the kernel
        # for it later fails precisely in the parent-exits-first race.
        self._process_group_id: int | None = None
        self._lines: queue.Queue[bytes | None] = queue.Queue(maxsize=settings.max_pending_messages)
        self._next_id = 0
        self._flooded = False
        self._request_lock = threading.Lock()

    # --- lifecycle --------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._process is not None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def start(self, *, handshake: Callable[[], None] | None = None) -> None:
        """Spawn the child, begin draining it, and run the caller's handshake.

        Transactional: a handshake that times out, is malformed, or is refused
        closes the child before the exception leaves this method.
        """
        if self._process is not None:
            return
        self._flooded = False
        self._lines = queue.Queue(maxsize=self._settings.max_pending_messages)
        try:
            self._process = subprocess.Popen(  # noqa: S603 - argv list, never a shell
                list(self._settings.argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=dict(self._settings.environment),
                bufsize=0,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self._process = None
            raise StdioSessionUnavailable("the child process could not be started") from exc
        process = self._process
        self._process_group_id = process.pid
        _spawn_daemon(lambda: self._pump_stdout(process), name="friday-stdio-out")
        _spawn_daemon(lambda: self._drain_stderr(process), name="friday-stdio-err")
        if handshake is None:
            return
        try:
            handshake()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        process, self._process = self._process, None
        group_id, self._process_group_id = self._process_group_id, None
        if process is None:
            return
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()
        # Signal our *recorded* group even when poll() already reaped the
        # leader.  Descendants can otherwise survive stdin EOF indefinitely.
        _signal_group(process, group_id, signal.SIGTERM)
        try:
            process.wait(timeout=_SHUTDOWN_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(process, group_id, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=_SHUTDOWN_GRACE_SECONDS)

    # --- JSON-RPC ---------------------------------------------------------

    def request(
        self,
        method: str,
        params: Mapping[str, JsonValue],
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
        cancellation_notification: Callable[[int], Mapping[str, JsonValue]] | None = None,
    ) -> JsonValue:
        with self._request_lock:
            self._next_id += 1
            request_id = self._next_id
            deadline = time.monotonic() + timeout_seconds
            self._write(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)},
                deadline,
            )
            return self._await_response(request_id, deadline, cancelled, cancellation_notification)

    def notify(
        self, method: str, params: Mapping[str, JsonValue], *, timeout_seconds: float
    ) -> None:
        with self._request_lock:
            self._write(
                {"jsonrpc": "2.0", "method": method, "params": dict(params)},
                time.monotonic() + timeout_seconds,
            )

    def _await_response(
        self,
        request_id: int,
        deadline: float,
        cancelled: Callable[[], bool] | None,
        cancellation_notification: Callable[[int], Mapping[str, JsonValue]] | None,
    ) -> JsonValue:
        """Read until the matching response arrives, the budget expires, or the
        child dies. Notifications and server-initiated requests are skipped
        rather than treated as answers."""
        ignored = 0
        while True:
            if self._flooded:
                raise StdioSessionProtocolError("the child exceeded its pending-message budget")
            if cancelled is not None and cancelled():
                self._best_effort_cancel(request_id, cancellation_notification)
                raise StdioSessionTimeout("the request was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._best_effort_cancel(request_id, cancellation_notification)
                raise StdioSessionTimeout("the child did not respond in time")
            try:
                line = self._lines.get(timeout=min(remaining, _POLL_SECONDS))
            except queue.Empty:
                continue
            if line is None:
                raise StdioSessionUnavailable("the child exited unexpectedly")
            message = _decode(line)
            if "id" not in message:
                ignored += 1
                if ignored > MAX_IGNORED_MESSAGES_PER_REQUEST:
                    raise StdioSessionProtocolError("the child flooded unsolicited messages")
                continue  # a notification, not an answer
            identifier = message["id"]
            if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
                raise StdioSessionProtocolError("the child sent a malformed response id")
            if type(identifier) is not type(request_id) or identifier != request_id:
                ignored += 1
                if ignored > MAX_IGNORED_MESSAGES_PER_REQUEST:
                    raise StdioSessionProtocolError("the child flooded stale responses")
                continue  # someone else's answer
            if ("result" in message) == ("error" in message):
                raise StdioSessionProtocolError("the child sent a malformed response")
            if "error" in message:
                _require_error_object(message["error"])
                raise StdioSessionRemoteError("the child returned an error response")
            return message["result"]

    def _write(self, message: Mapping[str, JsonValue], deadline: float) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise StdioSessionUnavailable("the child is not running")
        try:
            payload = json.dumps(message, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        except (TypeError, ValueError, RecursionError) as exc:
            raise StdioSessionProtocolError("the local message was not JSON-safe") from exc
        try:
            descriptor = process.stdin.fileno()
            os.set_blocking(descriptor, False)
            sent = 0
            while sent < len(payload):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StdioSessionTimeout("the child did not accept input in time")
                _, writable, _ = select.select([], [descriptor], [], remaining)
                if not writable:
                    raise StdioSessionTimeout("the child did not accept input in time")
                sent += os.write(descriptor, payload[sent:])
        except (BrokenPipeError, BlockingIOError, OSError) as exc:
            raise StdioSessionUnavailable("the child stopped accepting input") from exc

    # --- background readers ----------------------------------------------

    def _pump_stdout(self, process: subprocess.Popen[bytes]) -> None:
        stream = process.stdout
        if stream is not None:
            limit = self._settings.max_line_bytes
            try:
                while line := stream.readline(limit):
                    if not line.endswith(b"\n"):
                        # either the stream ended mid-line or the line exceeded
                        # the ceiling; both mean this connection is unusable
                        break
                    if not (stripped := line.strip()):
                        continue
                    try:
                        self._lines.put_nowait(stripped)
                    except queue.Full:
                        self._on_flood(process)
                        return
            except (OSError, ValueError):
                pass
        with contextlib.suppress(queue.Full):
            self._lines.put_nowait(None)

    def _on_flood(self, process: subprocess.Popen[bytes]) -> None:
        """A peer that outruns its pending-message budget is disconnected, not
        throttled: buffering more of it is the exact failure this bound exists
        to prevent, and silently dropping messages would let a flood hide the
        real answer."""
        self._flooded = True
        _signal_group(process, self._process_group_id, signal.SIGKILL)

    def _best_effort_cancel(
        self,
        request_id: int,
        factory: Callable[[int], Mapping[str, JsonValue]] | None,
    ) -> None:
        """An optional protocol-owned cancellation notification.

        The shared transport never knows which methods are cancellable; the
        caller supplies the fully formed notification.  A blocked peer cannot
        turn this courtesy into an unbounded second timeout.
        """
        if factory is None:
            return
        with contextlib.suppress(StdioSessionError):
            self._write(factory(request_id), time.monotonic() + 0.1)

    def _drain_stderr(self, process: subprocess.Popen[bytes]) -> None:
        """Consume and discard stderr so the child never blocks on a full pipe.

        Discarded rather than captured: peer diagnostics are exactly the text
        that must not reach the brain, and Friday has no use for them that
        would justify holding them in memory.
        """
        stream = process.stderr
        if stream is not None:
            _drain(stream)


def allowlisted_environment(
    names: tuple[str, ...],
    *,
    source: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Project a parent environment onto an explicit allow-list.

    A child must never inherit API keys or nested session variables merely
    because this process has them.
    """
    origin = os.environ if source is None else source
    environment = {name: origin[name] for name in names if origin.get(name) is not None}
    environment.update(extra or {})
    return environment


def _decode(line: bytes) -> dict[str, JsonValue]:
    try:
        message = json.loads(
            line,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_no_duplicate_keys,
        )
        _require_json_depth(message, 1)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise StdioSessionProtocolError(_MALFORMED_MESSAGE) from exc
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise StdioSessionProtocolError(_MALFORMED_MESSAGE)
    return cast(dict[str, JsonValue], message)


def _reject_json_constant(_: str) -> None:
    raise ValueError("non-standard JSON constant")


def _no_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _require_json_depth(value: object, depth: int) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("JSON nesting exceeded")
    if isinstance(value, dict):
        for item in value.values():
            _require_json_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _require_json_depth(item, depth + 1)


def _require_error_object(error: JsonValue) -> None:
    if (
        not isinstance(error, dict)
        or isinstance(error.get("code"), bool)
        or not isinstance(error.get("code"), int)
        or not isinstance(error.get("message"), str)
    ):
        raise StdioSessionProtocolError("the child sent a malformed error")


def _signal_group(process: subprocess.Popen[bytes], group_id: int | None, number: int) -> None:
    """Signal the child's own process group, falling back to the child alone
    when the group is already gone or the platform has no process groups."""
    try:
        if group_id is None:
            raise OSError("no owned process group")
        os.killpg(group_id, number)
        return
    except (OSError, AttributeError, PermissionError):
        pass
    with contextlib.suppress(OSError):
        if number == signal.SIGKILL:
            process.kill()
        else:
            process.terminate()


def _drain(stream: IO[bytes]) -> None:
    try:
        while stream.read(_STDERR_CHUNK_BYTES):
            pass
    except (OSError, ValueError):
        pass


def _spawn_daemon(target: Callable[[], None], *, name: str) -> None:
    threading.Thread(target=target, name=name, daemon=True).start()
