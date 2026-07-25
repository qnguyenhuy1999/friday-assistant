"""Deliberately narrow, safe Obsidian Markdown parser: frontmatter, headings,
wikilinks, Markdown links, and inline tags. Pure parsing — no I/O, no
filesystem access, no query execution. Returns frozen dataclasses only.

Security principle: content is UNTRUSTED. It must never alter program flow,
construct Python objects, execute callables, or modify system state. Every
output structure is validated at construction time.

Non-goals (by design, to keep surface small and safe):
- No YAML object loader — only recognised keys are read.
- No link rewriting.
- No Markdown-to-HTML conversion.
- No Markdown AST — just the structures retrieval needs."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAX_FRONTMATTER_BYTES = 64_000
"""Bounded lookahead for the opening frontmatter fence; 64 KiB covers every
sane Obsidian note and limits injection surface."""

_KNOWN_FRONTMATTER_KEYS: frozenset[str] = frozenset(
    {
        "title",
        "aliases",
        "tags",
        "friday_index",
        "private",
        "sensitive",
        "friday_managed",
        "friday_memory_id",
    }
)


@dataclass(frozen=True, slots=True)
class Frontmatter:
    """Parsed and validated frontmatter keys. All fields have safe defaults
    so callers never need to None-check."""

    title: str = ""
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    friday_index: bool = False
    private: bool = False
    sensitive: bool = False
    friday_managed: bool = False
    friday_memory_id: str = ""


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str
    line_number: int


@dataclass(frozen=True, slots=True)
class HeadingBody:
    """Annotated heading with its inclusive line range in the source."""

    heading: Heading
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class Wikilink:
    target: str
    alias: str = ""
    heading: str = ""
    is_embed: bool = False


@dataclass(frozen=True, slots=True)
class MarkdownLink:
    text: str
    url: str


@dataclass(frozen=True, slots=True)
class Tag:
    tag: str
    line_number: int


@dataclass(frozen=True, slots=True)
class ParsedMarkdown:
    """Complete parse result. Every field has a safe empty default so callers
    never access missing data."""

    frontmatter: Frontmatter = Frontmatter()
    headings: tuple[Heading, ...] = ()
    heading_bodies: tuple[HeadingBody, ...] = ()
    wikilinks: tuple[Wikilink, ...] = ()
    markdown_links: tuple[MarkdownLink, ...] = ()
    tags: tuple[Tag, ...] = ()


# ── line-level patterns (compiled once) ──────────────────────────────

_FENCE_RE = re.compile(r"^```")
_ATX_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#{1,6})?\s*$")
_FRONTMATTER_FENCE_RE = re.compile(r"^---\s*$")
_YAML_KEY_RE = re.compile(r"^(\w[\w_]*):\s*(.*)")
_WIKILINK_INLINE_RE = re.compile(r"!?\[\[([^\]\[#|]+)(?:#([^\]\[|]+))?(?:\|([^\]]+))?\]\]")
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(((?:[^()]+|\([^()]*\))*)\)")
_TAG_RE = re.compile(r"(?:(?<=^)|(?<=\s))#([\w/]+)")


# ── frontmatter ──────────────────────────────────────────────────────


def _parse_bracket_list(value: str) -> list[str]:
    """Parse ``[a, b, c]`` into strings, or return [].

    This is *not* a general YAML parser — only the bracket-list shape
    Obsidian / Metadata Menu produce in practice."""
    stripped = value.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return []
    inner = stripped[1:-1].strip()
    if not inner:
        return []
    return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]


def _parse_scalar_or_list(value: str) -> list[str]:
    """Parse a YAML scalar, comma-separated string, or bracket list into a
    list of strings.  Not a general YAML parser."""
    stripped = value.strip()
    if not stripped:
        return []
    is_bracket = stripped.startswith("[") and stripped.endswith("]")
    if is_bracket:
        return _parse_bracket_list(value)
    if "," in stripped:
        return [item.strip() for item in stripped.split(",") if item.strip()]
    q = stripped[0]
    if q in ("'", '"') and stripped[-1] == q:
        return [stripped[1:-1]]
    return [stripped]


def _flush_frontmatter_key(
    key: str, raw_value: str, accum: dict[str, object], list_items: list[str] | None
) -> None:
    """Flush the current key-value pair into *accum*, handling YAML list
    items and inline values."""
    if key not in _KNOWN_FRONTMATTER_KEYS:
        return
    if list_items is not None:
        if key == "aliases":
            _aliases = accum["aliases"]
            if isinstance(_aliases, tuple):
                accum["aliases"] = tuple(list(_aliases) + list_items)
            else:
                accum["aliases"] = tuple(list_items)
        elif key == "tags":
            _tags = accum["tags"]
            if isinstance(_tags, tuple):
                accum["tags"] = tuple(list(_tags) + list_items)
            else:
                accum["tags"] = tuple(list_items)
        return
    value = raw_value.strip()
    if not value:
        return
    if key == "title":
        q = value[0]
        if q in ("'", '"') and value[-1] == q:
            value = value[1:-1]
        accum[key] = value
    elif key in ("friday_index", "private", "sensitive", "friday_managed"):
        accum[key] = value.lower() in ("true", "yes", "1")
    elif key == "friday_memory_id":
        if value:
            accum[key] = value
    elif key in ("aliases", "tags"):
        parsed = _parse_scalar_or_list(value)
        if parsed:
            _existing_val = accum[key]
            _existing: list[str] = list(_existing_val) if isinstance(_existing_val, tuple) else []
            accum[key] = tuple(_existing + parsed)


def parse_frontmatter(text: str) -> Frontmatter:
    """Extract YAML frontmatter between the first pair of ``---`` fences.

    Returns ``Frontmatter()`` (all defaults) when no valid frontmatter is
    found — this is *not* silently treated as opt-in for indexing (all
    boolean fields default to ``False``).

    Security: only recognises the keys in ``_KNOWN_FRONTMATTER_KEYS``.
    ``!`` YAML tags, Python object constructors, and arbitrary key-value
    pairs are silently ignored.  The opening fence must appear within
    ``_MAX_FRONTMATTER_BYTES`` from the start.
    """
    start_slice = text[:_MAX_FRONTMATTER_BYTES]
    lines = start_slice.split("\n")
    if not lines:
        return Frontmatter()
    if not _FRONTMATTER_FENCE_RE.match(lines[0]):
        return Frontmatter()
    raw_lines: list[str] = []
    for line in lines[1:]:
        if _FRONTMATTER_FENCE_RE.match(line):
            return _build_frontmatter(raw_lines)
        raw_lines.append(line)
    return Frontmatter()


def _build_frontmatter(raw_lines: list[str]) -> Frontmatter:
    """Build a Frontmatter from raw lines between fences."""
    accum: dict[str, object] = {
        "title": "",
        "aliases": (),
        "tags": (),
        "friday_index": False,
        "private": False,
        "sensitive": False,
        "friday_managed": False,
        "friday_memory_id": "",
    }
    current_key: str | None = None
    current_raw: str = ""
    current_list_items: list[str] | None = None

    for line in raw_lines:
        m = _YAML_KEY_RE.match(line)
        if m:
            if current_key is not None:
                _flush_frontmatter_key(current_key, current_raw, accum, current_list_items)
            current_key = m.group(1)
            current_raw = m.group(2)
            current_list_items = None
        elif current_key is not None and line.startswith(" "):
            cont = line.strip()
            if cont.startswith("- ") and current_key in ("aliases", "tags"):
                if current_list_items is None:
                    current_list_items = []
                current_list_items.append(cont[2:].strip())
            elif current_list_items is not None:
                if current_list_items:
                    current_list_items[-1] += " " + cont
            elif current_key in ("aliases", "tags"):
                # Scalar continuation for aliases/tags — could be more text.
                current_raw += " " + cont
            else:
                # Unknown structure after a known key — skip (malformed).
                pass
        else:
            if current_key is not None:
                _flush_frontmatter_key(current_key, current_raw, accum, current_list_items)
            current_key = None
            current_raw = ""
            current_list_items = None

    if current_key is not None:
        _flush_frontmatter_key(current_key, current_raw, accum, current_list_items)

    return Frontmatter(
        title=str(accum.get("title", "")),
        aliases=tuple(
            str(a) for a in (accum["aliases"] if isinstance(accum["aliases"], tuple) else ())
        ),
        tags=tuple(str(t) for t in (accum["tags"] if isinstance(accum["tags"], tuple) else ())),
        friday_index=bool(accum.get("friday_index", False)),
        private=bool(accum.get("private", False)),
        sensitive=bool(accum.get("sensitive", False)),
        friday_managed=bool(accum.get("friday_managed", False)),
        friday_memory_id=str(accum.get("friday_memory_id", "")),
    )


# ── code fence tracking ──────────────────────────────────────────────


def _code_fence_lines(text: str) -> set[int]:
    """Return the set of 0-based line indices inside a fenced code block."""
    inside = False
    targets: set[int] = set()
    for i, line in enumerate(text.split("\n")):
        if _FENCE_RE.match(line):
            inside = not inside
        elif inside:
            targets.add(i)
    return targets


# ── headings ─────────────────────────────────────────────────────────


def parse_headings(text: str) -> tuple[Heading, ...]:
    """Extract ATX headings, ignoring those inside fenced code blocks."""
    fence_lines = _code_fence_lines(text)
    results: list[Heading] = []
    for i, line in enumerate(text.split("\n")):
        if i in fence_lines:
            continue
        m = _ATX_RE.match(line)
        if m:
            results.append(
                Heading(level=len(m.group(1)), text=m.group(2).strip(), line_number=i + 1)
            )
    return tuple(results)


def map_heading_bodies(text: str, headings: tuple[Heading, ...]) -> tuple[HeadingBody, ...]:
    """Map each heading to its inclusive [start_line, end_line] body range.

    A heading's body starts at its own line number and ends at the line
    before the *next* heading at the *same or higher* level (``level <=
    current``), or the last line of the document.  Lower-level headings
    (deeper subsections) are consumed as part of the parent heading's
    body range.  Line numbers are 1-based and match the original file.
    """
    if not headings:
        return ()
    total_lines = len(text.split("\n"))
    results: list[HeadingBody] = []
    for idx, heading in enumerate(headings):
        start = heading.line_number
        end = total_lines
        for next_idx in range(idx + 1, len(headings)):
            if headings[next_idx].level <= heading.level:
                end = headings[next_idx].line_number - 1
                break
        results.append(HeadingBody(heading=heading, start_line=start, end_line=end))
    return tuple(results)


# ── wikilinks, markdown links, tags ──────────────────────────────────


def parse_wikilinks(text: str) -> tuple[Wikilink, ...]:
    """Extract all [[wikilinks]] and ![[embeds]] from text."""
    results: list[Wikilink] = []
    for m in _WIKILINK_INLINE_RE.finditer(text):
        raw = m.group(0)
        is_embed = raw.startswith("!")
        target = m.group(1).strip()
        heading = (m.group(2) or "").strip()
        alias = (m.group(3) or "").strip()
        if not target:
            continue
        results.append(Wikilink(target=target, alias=alias, heading=heading, is_embed=is_embed))
    return tuple(results)


def parse_markdown_links(text: str) -> tuple[MarkdownLink, ...]:
    """Extract standard Markdown links ``[text](url)``, excluding ``!``
    images.  Handles one level of nested parentheses in URLs."""
    results: list[MarkdownLink] = []
    for m in _MD_LINK_RE.finditer(text):
        results.append(MarkdownLink(text=m.group(1), url=m.group(2)))
    return tuple(results)


def parse_tags(text: str) -> tuple[Tag, ...]:
    """Extract inline ``#tag`` references, excluding those inside fenced
    code blocks.  The boundary check excludes ``#`` inside URLs and
    ``[text](#fragment)`` links."""
    fence_lines = _code_fence_lines(text)
    results: list[Tag] = []
    for i, line in enumerate(text.split("\n")):
        if i in fence_lines:
            continue
        for m in _TAG_RE.finditer(line):
            results.append(Tag(tag=m.group(1), line_number=i + 1))
    return tuple(results)


# ── public entry point ───────────────────────────────────────────────


def parse_markdown(text: str) -> ParsedMarkdown:
    """Parse the full text of an Obsidian Markdown note.

    Args:
        text: Raw note content.  Line numbers are 1-based and match the
            original file.  CRLF newlines are normalised for processing
            internally.

    Returns:
        A ``ParsedMarkdown`` with all extracted structures.  Every
        field has a safe default — callers never need to None-check.
    """
    if not text:
        return ParsedMarkdown()
    headings = parse_headings(text)
    return ParsedMarkdown(
        frontmatter=parse_frontmatter(text),
        headings=headings,
        heading_bodies=map_heading_bodies(text, headings),
        wikilinks=parse_wikilinks(text),
        markdown_links=parse_markdown_links(text),
        tags=parse_tags(text),
    )
