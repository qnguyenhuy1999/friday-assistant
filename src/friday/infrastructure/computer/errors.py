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
