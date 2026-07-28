"""Remote output is bounded before it becomes durable or reaches the brain.

Everything a server returns is attacker-controllable in size and shape. The
normalizer's job is to turn that into a small, JSON-safe envelope with a
truthful `truncated` flag — truthful because a silently trimmed result reads
downstream as the whole answer, and Friday would act on a fragment believing
it had the rest.

Where a bound cannot be honoured by trimming — depth, total bytes, item count —
the result is refused outright rather than reshaped into something misleading.
"""

from __future__ import annotations

import pytest

from friday.domain.json_value import JsonValue
from friday.infrastructure.mcp.client import McpCallResult
from friday.infrastructure.mcp.errors import McpInvalidOutput
from friday.infrastructure.mcp.output import MAX_OUTPUT_DEPTH, OutputBounds, normalize_call_result

GENEROUS = OutputBounds(max_content_items=16, max_text_chars=1_000, max_result_bytes=100_000)


def _envelope(result: McpCallResult, bounds: OutputBounds = GENEROUS) -> dict[str, JsonValue]:
    envelope = normalize_call_result(result, bounds=bounds)
    assert isinstance(envelope, dict)
    return envelope


def _nested(depth: int) -> JsonValue:
    value: JsonValue = "leaf"
    for _ in range(depth):
        value = {"child": value}
    return value


def test_an_ordinary_result_passes_through_untruncated() -> None:
    envelope = _envelope(
        McpCallResult(structured_content={"ok": True}, text_blocks=("done",), total_content_items=1)
    )

    assert envelope["structured"] == {"ok": True}
    assert envelope["text"] == ["done"]
    assert envelope["truncated"] is False


def test_an_oversized_text_block_is_trimmed_and_says_so() -> None:
    envelope = _envelope(
        McpCallResult(text_blocks=("x" * 5_000,), total_content_items=1),
        OutputBounds(max_content_items=4, max_text_chars=100, max_result_bytes=100_000),
    )

    text = envelope["text"]
    assert isinstance(text, list)
    assert text == ["x" * 100]
    assert envelope["truncated"] is True


def test_more_blocks_than_the_bound_allows_is_refused() -> None:
    """Refused rather than trimmed: dropping blocks would hand the brain a
    partial answer that looks complete."""
    with pytest.raises(McpInvalidOutput):
        normalize_call_result(
            McpCallResult(text_blocks=("a", "b", "c"), total_content_items=3),
            bounds=OutputBounds(max_content_items=2, max_text_chars=100, max_result_bytes=100_000),
        )


def test_non_text_blocks_are_named_but_never_carried() -> None:
    """An image or blob must not ride along into a durable record or the
    brain's context; the operator still gets to see that one was there."""
    envelope = _envelope(
        McpCallResult(
            text_blocks=("summary",),
            omitted_block_kinds=("image", "resource"),
            total_content_items=3,
        )
    )

    assert envelope["omitted_block_kinds"] == ["image", "resource"]
    assert envelope["truncated"] is True  # a block was present and is not in the envelope


def test_a_result_over_its_byte_budget_is_refused_not_trimmed() -> None:
    """Trimming structured JSON would hand the brain a plausible-looking object
    with silently missing members."""
    huge: JsonValue = {f"k{index}": "v" * 100 for index in range(200)}

    with pytest.raises(McpInvalidOutput):
        normalize_call_result(
            McpCallResult(structured_content=huge, total_content_items=0),
            bounds=OutputBounds(max_content_items=4, max_text_chars=100, max_result_bytes=1_000),
        )


def test_a_result_deeper_than_the_ceiling_is_refused() -> None:
    with pytest.raises(McpInvalidOutput):
        normalize_call_result(
            McpCallResult(structured_content=_nested(MAX_OUTPUT_DEPTH + 4), total_content_items=0),
            bounds=GENEROUS,
        )


def test_more_content_items_than_allowed_is_refused() -> None:
    """The count is the server's own claim about what it sent; a reply that
    exceeds the bound is refused rather than partially believed."""
    with pytest.raises(McpInvalidOutput):
        normalize_call_result(
            McpCallResult(text_blocks=("a",), total_content_items=99),
            bounds=OutputBounds(max_content_items=4, max_text_chars=100, max_result_bytes=100_000),
        )


def test_bounds_come_from_the_server_configuration() -> None:
    from friday.domain.approval import ApprovalCategory
    from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding

    server = McpServerConfig(
        "fixture",
        True,
        ("server",),
        (
            McpToolBinding(
                "fixture.read", "read", "Read.", True, False, ApprovalCategory.NETWORK_ACCESS
            ),
        ),
        max_content_items=3,
        max_text_chars=7,
        max_output_bytes=9_000,
    )

    bounds = OutputBounds.from_server(server)

    assert (bounds.max_content_items, bounds.max_text_chars, bounds.max_result_bytes) == (
        3,
        7,
        9_000,
    )
