from __future__ import annotations

from friday.application.tool_authorization import FINGERPRINT_VERSION


def test_mcp_approvals_use_the_fail_closed_v2_fingerprint() -> None:
    assert FINGERPRINT_VERSION == 2
