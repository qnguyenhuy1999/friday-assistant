"""Shared secret-shape detection.

One scanner, two callers: curated memory writes and desktop text entry. They
are refusing the same thing for the same reason, and two independently drifting
regex lists would mean a token shape blocked from a note is typeable into a
password field — or the reverse.

This is **defence in depth, not a guarantee**. It recognizes shapes that are
obviously credentials; it cannot recognize a secret that looks like ordinary
prose, and it is not a licence to route real credentials anywhere near it.
Friday's actual position is upstream of this function: it never deliberately
puts credentials where Claude can propose typing them, and there is no
secret-retrieval path in the system for it to reach.

Detection is deterministic — fixed patterns plus a Shannon-entropy test on long
tokens — so the same text is always judged the same way. A probabilistic
detector would make "why was this refused?" unanswerable.
"""

from __future__ import annotations

import base64
import math
import re
from collections import Counter

_SECRET_PATTERNS = (
    re.compile(r"(?im)^authorization\s*:\s*(?:bearer|basic)\s+\S+"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/_=.-]{16,}"),
    re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|access[_-]?token)\s*[:=]\s*\S+"),
)
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9+/_=-]{32,}\b")
_MIN_DECODED_BYTES = 24
_MIN_TOKEN_ENTROPY = 4.0


def contains_secret_shape(text: str) -> bool:
    """True when `text` looks like it carries a credential."""
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS) or _has_high_entropy_token(
        text
    )


def _has_high_entropy_token(text: str) -> bool:
    """Conservatively identify token-like high-entropy strings.

    Length alone would flag base64-encoded prose and long hex hashes; entropy
    alone would flag short random-looking words, so both are required.

    Known over-block, inherited from Phase 12 and deliberately left unchanged:
    the token character class includes `/` and `-`, so a long filesystem path or
    URL reads as one high-entropy token and is refused. For a memory write that
    is merely conservative. For desktop typing it means a long URL cannot be
    typed — an accepted limitation, because the alternative is loosening a
    detector that curated memory writes also depend on.
    """
    for token in _LONG_TOKEN.findall(text):
        try:
            decoded = base64.b64decode(token + "===", validate=False)
        except ValueError:
            decoded = token.encode("utf-8")
        if len(decoded) >= _MIN_DECODED_BYTES and _shannon_entropy(token) >= _MIN_TOKEN_ENTROPY:
            return True
    return False


def _shannon_entropy(token: str) -> float:
    length = len(token)
    frequencies = Counter(token)
    return -sum((count / length) * math.log2(count / length) for count in frequencies.values())
