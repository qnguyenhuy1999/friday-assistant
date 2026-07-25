"""Single source of truth for note privacy: every code path that can return
vault text to a caller (lexical search, structural read, snapshot hashing)
must exclude the same private/sensitive/friday_index:false notes."""

from __future__ import annotations

import re

from friday.infrastructure.memory.markdown_parser import parse_markdown

_FRIDAY_INDEX_DISABLED_RE = re.compile(r"(?im)^friday_index:\s*(?:false|no|0)\s*$")


def is_note_private(text: str) -> bool:
    frontmatter = parse_markdown(text).frontmatter
    index_disabled = bool(_FRIDAY_INDEX_DISABLED_RE.search(text))
    return index_disabled or frontmatter.private or frontmatter.sensitive
