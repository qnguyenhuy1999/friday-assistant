"""Deterministic MemoryQuery builder with a strict allowlist of safe inputs.

Consumes a RunSnapshot — a pure, vendor-neutral data structure carrying only
the fields the application explicitly allows. Produces a MemoryQuery whose
terms, phrases, tags, and titles are capped, deduplicated, stopword-filtered,
and deterministically ordered. Returns None for degenerate snapshots so the
caller can skip retrieval entirely."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from friday.application.memory.models import MemoryQuery

_DEFAULT_MAX_TERMS = 20
_DEFAULT_MAX_PHRASES = 5
_DEFAULT_MAX_TAGS = 10
_DEFAULT_MAX_TITLES = 10
_DEFAULT_MAX_TERM_LENGTH = 100
_DEFAULT_MIN_TOKEN_LENGTH = 2

_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "all",
        "also",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "done",
        "down",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "here",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "may",
        "me",
        "might",
        "more",
        "most",
        "my",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "out",
        "over",
        "own",
        "per",
        "said",
        "same",
        "shall",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "up",
        "upon",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
    }
)

_TOKEN_SPLIT = re.compile(r"[^\w]+")


def _normalize_text(text: str) -> str:
    """Normalize a single text value: strip whitespace, casefold."""
    return text.strip().casefold()


def _is_valid_term(token: str, min_length: int, max_length: int) -> bool:
    return len(token) >= min_length and len(token) <= max_length and token not in _STOPWORDS


def _split_into_tokens(text: str) -> tuple[str, ...]:
    """Split free text into word tokens, preserving first-seen order after
    deduplication."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in _TOKEN_SPLIT.split(text):
        if not raw:
            continue
        token = raw.casefold()
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return tuple(result)


def _dedupe_preserve_order(items: Sequence[str]) -> tuple[str, ...]:
    """Deduplicate while preserving first-seen order, casefolded."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return tuple(result)


def _normalize_titles(items: Sequence[str]) -> tuple[str, ...]:
    """Normalize titles: strip, casefold, dedupe, cap."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip().casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def _cap(items: tuple[str, ...], limit: int) -> tuple[str, ...]:
    if len(items) <= limit:
        return items
    return items[:limit]


def _strip_graphify(term: str) -> str:
    """Strip Graphify/Cypher-looking syntax from a term: anything that looks
    like a label, query fragment, or node-link syntax is reduced to plain
    identifier-like tokens."""
    return re.sub(r"[{}():;`]", "", term)


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """Durable, application-owned execution-context snapshot consumed by
    MemoryQueryBuilder.

    Every field is plain text. The builder applies caps, stopword filtering,
    deduplication, and deterministic ordering. Forbdden content (credentials,
    full prompts, raw responses, environment values) is never part of this
    shape — the producer above (the run orchestrator) is responsible for
    extracting only the allowed sources before building this snapshot.
    """

    task_title: str = ""
    task_description: str = ""
    objective: str = ""
    step_names: tuple[str, ...] = ()
    failure_codes: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    memory_search_term: str | None = None


class MemoryQueryBuilder:
    """Pure, deterministic builder that constructs a MemoryQuery from a
    RunSnapshot. No side effects, no I/O, no vendor types, no Graphify
    dependency.

    Configurable caps and thresholds via constructor kwargs; every parameter
    has a documented _DEFAULT_* constant for auditability.
    """

    def __init__(
        self,
        *,
        max_terms: int = _DEFAULT_MAX_TERMS,
        max_phrases: int = _DEFAULT_MAX_PHRASES,
        max_tags: int = _DEFAULT_MAX_TAGS,
        max_titles: int = _DEFAULT_MAX_TITLES,
        max_term_length: int = _DEFAULT_MAX_TERM_LENGTH,
        min_token_length: int = _DEFAULT_MIN_TOKEN_LENGTH,
    ) -> None:
        if max_terms < 1:
            raise ValueError("max_terms must be >= 1")
        if max_phrases < 1:
            raise ValueError("max_phrases must be >= 1")
        if max_tags < 1:
            raise ValueError("max_tags must be >= 1")
        if max_titles < 1:
            raise ValueError("max_titles must be >= 1")
        if max_term_length < 1:
            raise ValueError("max_term_length must be >= 1")
        if min_token_length < 1:
            raise ValueError("min_token_length must be >= 1")
        self._max_terms = max_terms
        self._max_phrases = max_phrases
        self._max_tags = max_tags
        self._max_titles = max_titles
        self._max_term_length = max_term_length
        self._min_token_length = min_token_length

    def build(self, snapshot: RunSnapshot) -> MemoryQuery | None:
        terms: list[str] = []
        phrases: list[str] = []
        tags: list[str] = []
        titles: list[str] = []

        # Task title → titles
        tt = _normalize_text(snapshot.task_title)
        if tt:
            titles.append(tt)

        # Task description → terms (split into tokens)
        if snapshot.task_description:
            for token in _split_into_tokens(snapshot.task_description):
                token = _strip_graphify(token)
                if _is_valid_term(token, self._min_token_length, self._max_term_length):
                    terms.append(token)

        # Run objective → phrases (whole objective as a phrase)
        obj = _normalize_text(snapshot.objective)
        if obj:
            phrases.append(obj)

        # Step names → titles
        for name in snapshot.step_names:
            n = _normalize_text(name)
            if n:
                titles.append(n)

        # Failure codes → tags
        for code in snapshot.failure_codes:
            c = _normalize_text(code)
            if c:
                tags.append(c)

        # Tool names → terms
        for name in snapshot.tool_names:
            n = _normalize_text(name)
            if n:
                terms.append(_strip_graphify(n))

        # Memory search term → phrases (exact)
        if snapshot.memory_search_term:
            phr = _normalize_text(snapshot.memory_search_term)
            if phr:
                phrases.append(phr)

        # Deduplicate preserving first-seen order and apply caps
        terms_tuple = _cap(_dedupe_preserve_order(terms), self._max_terms)
        phrases_tuple = _cap(_dedupe_preserve_order(phrases), self._max_phrases)
        tags_tuple = _cap(_dedupe_preserve_order(tags), self._max_tags)
        titles_tuple = _cap(_dedupe_preserve_order(titles), self._max_titles)

        if not (terms_tuple or phrases_tuple or tags_tuple or titles_tuple):
            return None

        return MemoryQuery(
            terms=terms_tuple,
            phrases=phrases_tuple,
            tags=tags_tuple,
            titles=titles_tuple,
        )

    @property
    def max_terms(self) -> int:
        return self._max_terms

    @property
    def max_phrases(self) -> int:
        return self._max_phrases

    @property
    def max_tags(self) -> int:
        return self._max_tags

    @property
    def max_titles(self) -> int:
        return self._max_titles

    @property
    def max_term_length(self) -> int:
        return self._max_term_length

    @property
    def min_token_length(self) -> int:
        return self._min_token_length
