"""Screenshot persistence: where image bytes stop being bytes.

Friday's existing artifact flow is reused wholesale — `ArtifactKind.IMAGE`,
`ArtifactCandidate`, and `ExecuteToolAction`'s Txn B all already exist, so
there is no second artifact system here. This module's only job is to get the
bytes onto disk safely and describe them.

The hard rule the layout enforces: **image bytes never enter JSON.** No base64
payload, no inline data URI, no absolute path. Claude receives a
workspace-relative location, a size, a checksum, and the dimensions. It can
reason about the capture; it cannot inhale a megabyte of pixels into its
context, and nothing downstream has to remember to strip them.

Validation order matters. The byte ceiling and the magic-number check happen
*before* the file is created, so an oversized or mislabelled capture leaves
nothing behind to clean up. Publication is atomic (temp file + `os.replace`)
so a reader never observes a half-written PNG under a name that implies a
complete one.

Containment is not assumed from the shape of the relative path. `computer.capture`
is read-only from Claude's side and needs no approval, which makes this the one
desktop-triggered filesystem write reachable unattended — so it goes through the
same choke point as every other workspace write (`resolve_workspace_path`):
reject `..`, resolve symlinks, require the result to stay inside the resolved
root. Composing a safe-looking relative path onto the root is not sufficient,
because any component of it may already be a symlink out of the workspace, and
the reported `.friday/artifacts/computer/...` location would then describe a
file that is somewhere else entirely. Same documented TOCTOU limitation as
Phase 11: a symlink introduced between this check and `os.replace` can still
redirect the write.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from friday.application.errors import WorkspaceAccessDenied
from friday.application.tool_gateway import ArtifactCandidate
from friday.domain.artifact import ArtifactKind
from friday.infrastructure.computer.errors import ComputerActionRejected
from friday.infrastructure.computer.models import CaptureId, Screenshot
from friday.infrastructure.workspace_paths import (
    resolve_workspace_path,
    resolve_workspace_root,
)

DEFAULT_ARTIFACT_ROOT = ".friday/artifacts/computer"
DEFAULT_MAX_CAPTURE_BYTES = 8_000_000

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MEDIA_TYPE_SUFFIX = {"image/png": ".png"}
_MEDIA_TYPE_MAGIC = {"image/png": _PNG_MAGIC}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ScreenshotTooLarge(ComputerActionRejected):
    """The driver produced more bytes than the configured capture ceiling.

    Reported as a driver failure rather than invalid input: Claude asked for a
    screenshot, which is a legitimate request — it was the desktop that turned
    out to be too big to bring back under the configured budget.
    """

    code = "computer_use_failed"


class ScreenshotNotConfined(ComputerActionRejected):
    """The artifact path did not resolve to somewhere inside the workspace, so
    nothing was written.

    Carries the same code as ScreenshotTooLarge for the same reason: Claude
    supplied no path — the artifact layout is Friday's own — so a containment
    failure describes a misconfigured or tampered workspace, not a bad request,
    and the distinction is not Claude's to act on.
    """

    code = "computer_use_failed"


@dataclass(frozen=True, slots=True)
class ScreenshotStoreSettings:
    workspace_root: Path
    max_capture_bytes: int = DEFAULT_MAX_CAPTURE_BYTES
    artifact_root: str = DEFAULT_ARTIFACT_ROOT

    def __post_init__(self) -> None:
        if self.max_capture_bytes < 1:
            raise ValueError("ScreenshotStoreSettings.max_capture_bytes must be positive")
        relative = PurePosixPath(self.artifact_root)
        if not self.artifact_root or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(
                "ScreenshotStoreSettings.artifact_root must be a workspace-relative path"
            )


class ScreenshotStore:
    """Writes one capture into the workspace and describes it as an artifact."""

    def __init__(self, settings: ScreenshotStoreSettings) -> None:
        self._settings = settings

    def _confined_target(self, relative: str) -> Path:
        """Map the artifact location onto a real path provably inside the
        workspace, before any directory is created.

        Ordering matters: `mkdir(parents=True)` on an unvalidated path follows
        an escaping symlink and materializes directories outside the workspace
        even when the write itself is refused afterwards.
        """
        try:
            root = resolve_workspace_root(self._settings.workspace_root)
            return resolve_workspace_path(root, relative)
        except WorkspaceAccessDenied as exc:
            raise ScreenshotNotConfined(
                "the capture artifact path does not resolve inside the workspace"
            ) from exc

    def persist(
        self, screenshot: Screenshot, *, invocation_id: str, capture_id: CaptureId
    ) -> ArtifactCandidate:
        """Validate, confine, write atomically, and return the artifact candidate."""
        self._reject_unusable(screenshot)
        relative = self._relative_location(invocation_id, capture_id, screenshot.media_type)
        target = self._confined_target(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(target, screenshot.data)
        return ArtifactCandidate(
            kind=ArtifactKind.IMAGE,
            name=target.name,
            media_type=screenshot.media_type,
            location=relative,
            size=len(screenshot.data),
            checksum=hashlib.sha256(screenshot.data).hexdigest(),
        )

    def _reject_unusable(self, screenshot: Screenshot) -> None:
        """Everything that must be true before a file exists.

        `Screenshot` already bounds all of this at construction; re-checking
        the configured ceiling here is the point — the model ceiling is a
        sanity limit, this one is the operator's actual budget.
        """
        if len(screenshot.data) > self._settings.max_capture_bytes:
            raise ScreenshotTooLarge("the captured image exceeds the configured capture ceiling")
        # `Screenshot` already enforces the media-type allowlist at construction,
        # so this lookup is not a second allowlist check — it fails closed if the
        # allowlist ever grows without a matching signature to verify against.
        magic = _MEDIA_TYPE_MAGIC.get(screenshot.media_type)
        if magic is None:
            raise ScreenshotTooLarge("the captured image media type is not supported")
        if not screenshot.data.startswith(magic):
            # a driver's declared media type is a claim, not evidence
            raise ScreenshotTooLarge("the captured image does not match its declared media type")

    def _relative_location(self, invocation_id: str, capture_id: CaptureId, media_type: str) -> str:
        """`<artifact_root>/<invocation-id>/<capture-id>.png`.

        Keyed by invocation rather than by run so a replayed or re-approved
        capture can never overwrite the image an earlier invocation's artifact
        row already points at.
        """
        if not _SAFE_COMPONENT.match(invocation_id):
            raise ValueError(f"invocation_id is not a safe path component: {invocation_id!r}")
        suffix = _MEDIA_TYPE_SUFFIX[media_type]
        root = self._settings.artifact_root.strip("/")
        return f"{root}/{invocation_id}/{capture_id.value}{suffix}"


def _write_atomically(target: Path, data: bytes) -> None:
    """Publish under a temporary sibling name, then rename into place."""
    descriptor, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".friday-capture-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temp_name, target)
    except OSError:
        os.unlink(temp_name)
        raise
