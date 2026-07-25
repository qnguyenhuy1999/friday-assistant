"""Computer-use error hierarchy, local to infrastructure.

These never escape ComputerToolGateway: it maps each one onto a stable
Failure code, exactly as WorkspaceToolGateway does for filesystem and process
policy errors. Keeping them out of friday.application.errors keeps the
application hierarchy free of desktop concepts it must never reason about.

Driver messages are assumed to be unsafe for propagation — an OS-level error
can embed absolute paths, usernames, or window contents — so the gateway
sanitizes on the way out rather than forwarding text from here.
"""

from __future__ import annotations


class ComputerUseError(Exception):
    """Base class for every computer-use failure."""


class ComputerDriverUnavailable(ComputerUseError):
    """The driver could not be started, or reported itself unhealthy. Always
    fail closed: a computer-use tool must never fall back to some other input
    mechanism when the configured driver is unavailable."""


class ComputerDriverTimeout(ComputerUseError):
    """The driver did not answer within its configured budget."""


class ComputerDriverFailed(ComputerUseError):
    """The driver answered, but the requested operation did not succeed."""


class ComputerActionRejected(ComputerUseError):
    """Friday refused the action before any driver call happened.

    Unlike a driver failure, the message here is Friday's own constant text
    about its own policy, so it is safe to forward: it describes what was
    requested and which fence refused it, never what was observed on screen.
    Every subclass carries a stable `code` the gateway reports verbatim, so
    "why was my click refused?" is answerable without parsing prose.

    A rejection is proof of a *non*-event: raising this instead of returning a
    failed result would be equivalent, but the exception form makes it
    structurally impossible for a handler to reject and then keep going.
    """

    code = "computer_use_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SnapshotNotFound(ComputerActionRejected):
    code = "computer_snapshot_not_found"


class SnapshotExpired(ComputerActionRejected):
    code = "computer_snapshot_expired"


class SnapshotMismatch(ComputerActionRejected):
    """The cited snapshot exists, but does not describe what the action claims:
    another window, another run, or an element it never captured."""

    code = "computer_snapshot_mismatch"


class TargetInvalid(ComputerActionRejected):
    code = "computer_target_invalid"


class TargetOutOfBounds(ComputerActionRejected):
    code = "computer_target_out_of_bounds"


class TextRejected(ComputerActionRejected):
    code = "computer_text_rejected"


class HotkeyRejected(ComputerActionRejected):
    code = "computer_hotkey_rejected"
