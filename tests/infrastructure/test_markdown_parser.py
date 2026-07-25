"""Tests for the Obsidian Markdown parser: frontmatter, headings, links,
tags — including security constraints, degraded modes, and edge cases."""

from __future__ import annotations

import dataclasses

from friday.infrastructure.memory.markdown_parser import (
    Frontmatter,
    Heading,
    HeadingBody,
    MarkdownLink,
    ParsedMarkdown,
    Tag,
    Wikilink,
    map_heading_bodies,
    parse_frontmatter,
    parse_headings,
    parse_markdown,
    parse_markdown_links,
    parse_tags,
    parse_wikilinks,
)

# ── Frontmatter ────────────────────────────────────────────────────────


class TestParseFrontmatter:
    def test_with_title(self) -> None:
        text = "---\ntitle: My Note\n---\n\nBody content."
        fm = parse_frontmatter(text)
        assert fm.title == "My Note"
        assert fm.private is False

    def test_all_boolean_flags(self) -> None:
        text = "---\nfriday_index: true\nprivate: yes\nsensitive: 1\nfriday_managed: false\n---"
        fm = parse_frontmatter(text)
        assert fm.friday_index is True
        assert fm.private is True
        assert fm.sensitive is True
        assert fm.friday_managed is False

    def test_aliases_as_scalar(self) -> None:
        text = "---\naliases: AliasName\n---"
        fm = parse_frontmatter(text)
        assert fm.aliases == ("AliasName",)

    def test_aliases_as_bracket_list(self) -> None:
        text = "---\naliases: [A, B, C]\n---"
        fm = parse_frontmatter(text)
        assert fm.aliases == ("A", "B", "C")

    def test_aliases_as_yaml_list(self) -> None:
        text = "---\naliases:\n  - A\n  - B\n---"
        fm = parse_frontmatter(text)
        assert fm.aliases == ("A", "B")

    def test_tags_as_scalar(self) -> None:
        text = "---\ntags: project/active\n---"
        fm = parse_frontmatter(text)
        assert fm.tags == ("project/active",)

    def test_tags_as_comma_list(self) -> None:
        text = "---\ntags: tag1, tag2, tag3\n---"
        fm = parse_frontmatter(text)
        assert fm.tags == ("tag1", "tag2", "tag3")

    def test_tags_as_yaml_list(self) -> None:
        text = "---\ntags:\n  - tag1\n  - tag2\n---"
        fm = parse_frontmatter(text)
        assert fm.tags == ("tag1", "tag2")

    def test_tags_as_bracket_list(self) -> None:
        text = "---\ntags: [tag1, tag2]\n---"
        fm = parse_frontmatter(text)
        assert fm.tags == ("tag1", "tag2")

    def test_friday_memory_id(self) -> None:
        text = "---\nfriday_memory_id: abc-123\n---"
        fm = parse_frontmatter(text)
        assert fm.friday_memory_id == "abc-123"

    def test_unknown_keys_ignored(self) -> None:
        text = "---\ntitle: Known\nunknown_key: value\nanother_unknown: x\n---"
        fm = parse_frontmatter(text)
        assert fm.title == "Known"

    def test_absent_returns_defaults(self) -> None:
        text = "Just a regular note\n\nNo frontmatter here."
        fm = parse_frontmatter(text)
        assert fm.title == ""
        assert fm.friday_index is False
        assert fm.private is False

    def test_malformed_no_closing_fence_returns_defaults(self) -> None:
        text = "---\ntitle: No closing fence\n"
        fm = parse_frontmatter(text)
        assert fm.title == ""
        assert fm.friday_index is False

    def test_frontmatter_not_at_start_returns_defaults(self) -> None:
        text = "\n---\ntitle: Later\n---"
        fm = parse_frontmatter(text)
        assert fm.title == ""

    def test_empty_text_returns_defaults(self) -> None:
        fm = parse_frontmatter("")
        assert fm.title == ""

    def test_oversized_frontmatter_lookahead(self) -> None:
        prefix = "x\n" * 40_000
        text = prefix + "---\ntitle: Late\n---\n"
        fm = parse_frontmatter(text)
        assert fm.title == ""

    def test_defaults_not_opt_in(self) -> None:
        """Absent boolean flags must default to False, never to True."""
        text = "---\ntitle: Note\n---"
        fm = parse_frontmatter(text)
        assert fm.friday_index is False
        assert fm.private is False
        assert fm.sensitive is False
        assert fm.friday_managed is False

    def test_malformed_non_key_lines(self) -> None:
        """Non-key-value lines after keys should not fail."""
        text = "---\ntitle: Hello\n  - list item without key\n  - another\n---"
        fm = parse_frontmatter(text)
        assert fm.title == "Hello"

    def test_quoted_title(self) -> None:
        text = '---\ntitle: "My Title"\n---'
        fm = parse_frontmatter(text)
        assert fm.title == "My Title"

    def test_title_with_colon(self) -> None:
        text = "---\ntitle: A note about: colon\n---"
        fm = parse_frontmatter(text)
        assert fm.title == "A note about: colon"


# ── Headings ───────────────────────────────────────────────────────────


class TestParseHeadings:
    def test_simple_headings(self) -> None:
        text = "# H1\n\n## H2\n\n### H3"
        result = parse_headings(text)
        assert len(result) == 3
        assert result[0] == Heading(level=1, text="H1", line_number=1)
        assert result[1] == Heading(level=2, text="H2", line_number=3)
        assert result[2] == Heading(level=3, text="H3", line_number=5)

    def test_closing_marks(self) -> None:
        text = "# H1 #\n"
        result = parse_headings(text)
        assert len(result) == 1
        assert result[0].text == "H1"

    def test_inside_fenced_code_block(self) -> None:
        text = "# Real Heading\n\n```\n# Fake Heading\n```\n\n# Another Real"
        result = parse_headings(text)
        assert len(result) == 2
        assert result[0].text == "Real Heading"
        assert result[1].text == "Another Real"

    def test_multiple_code_blocks(self) -> None:
        text = "# H1\n```\n# Inside 1\n```\n## H2\n```\n# Inside 2\n```"
        result = parse_headings(text)
        assert len(result) == 2
        assert result[0].text == "H1"
        assert result[1].text == "H2"

    def test_unclosed_code_block(self) -> None:
        text = "# H1\n```\n# H2\n# H3"
        result = parse_headings(text)
        assert len(result) == 1
        assert result[0].text == "H1"

    def test_no_headings(self) -> None:
        result = parse_headings("Plain text with no headings.")
        assert result == ()

    def test_max_level(self) -> None:
        text = "###### H6"
        result = parse_headings(text)
        assert len(result) == 1
        assert result[0].level == 6

    def test_with_trailing_space(self) -> None:
        text = "# Heading With Space   "
        result = parse_headings(text)
        assert result[0].text == "Heading With Space"


class TestMapHeadingBodies:
    def test_simple_ranges(self) -> None:
        text = "# H1\n\npara1\n\n## H2\n\npara2\n\n### H3\npara3"
        headings = parse_headings(text)
        bodies = map_heading_bodies(text, headings)
        assert len(bodies) == 3
        # H1 at line 1, next same/higher (H1) none → end of doc
        assert bodies[0].start_line == 1
        assert bodies[0].end_line == 10  # last line of doc
        # H2 at line 5, next same/higher (H2 or H1) none → end of doc
        assert bodies[1].start_line == 5
        assert bodies[1].end_line == 10
        # H3 at line 9, last heading → end of doc
        assert bodies[2].start_line == 9
        assert bodies[2].end_line == 10

    def test_multilevel_ranges(self) -> None:
        """H1 body spans until next H1. H2 body spans until next H2 or H1."""
        text = "# A\n\n## B\n\n### C\n\n# D\n\n## E"
        bodies = map_heading_bodies(text, parse_headings(text))
        assert len(bodies) == 5
        # A's body: line 1 → before D at line 7 (next H1)
        assert bodies[0].start_line == 1
        assert bodies[0].end_line == 6
        # B's body: line 3 → before D at line 7 (same/higher = H2 or H1)
        assert bodies[1].start_line == 3
        assert bodies[1].end_line == 6
        # C's body: line 5 → before D at line 7 (next H1 or H2 is same/higher)
        assert bodies[2].start_line == 5
        assert bodies[2].end_line == 6
        # D's body: line 7 → before E at line 9 (next H1 or H2 is same/higher)
        assert bodies[3].start_line == 7
        assert bodies[3].end_line == 9
        # E's body: line 9 → end of doc
        assert bodies[4].start_line == 9
        assert bodies[4].end_line == 9

    def test_single_heading_covers_document(self) -> None:
        text = "# Only Heading\n\nBody text.\nMore text."
        headings = parse_headings(text)
        bodies = map_heading_bodies(text, headings)
        assert len(bodies) == 1
        assert bodies[0].start_line == 1
        assert bodies[0].end_line == 4

    def test_empty_headings_returns_empty(self) -> None:
        bodies = map_heading_bodies("# text", ())
        assert bodies == ()

    def test_adjacent_headings(self) -> None:
        text = "# H1\n## H2\n### H3"
        headings = parse_headings(text)
        bodies = map_heading_bodies(text, headings)
        # H1 at line 1, no next H1 → end
        assert bodies[0].start_line == 1
        assert bodies[0].end_line == 3
        # H2 at line 2, no next H2 or H1 → end
        assert bodies[1].start_line == 2
        assert bodies[1].end_line == 3
        # H3 at line 3, last
        assert bodies[2].start_line == 3
        assert bodies[2].end_line == 3

    def test_includes_heading_object(self) -> None:
        text = "# H1\n\ncontent"
        headings = parse_headings(text)
        bodies = map_heading_bodies(text, headings)
        assert bodies[0].heading == Heading(level=1, text="H1", line_number=1)


# ── Wikilinks ──────────────────────────────────────────────────────────


class TestParseWikilinks:
    def test_basic(self) -> None:
        text = "[[Note]]"
        result = parse_wikilinks(text)
        assert len(result) == 1
        assert result[0] == Wikilink(target="Note", alias="", heading="", is_embed=False)

    def test_folder(self) -> None:
        text = "[[Folder/Note]]"
        result = parse_wikilinks(text)
        assert result[0].target == "Folder/Note"

    def test_with_alias(self) -> None:
        text = "[[Note|Display Text]]"
        result = parse_wikilinks(text)
        assert result[0].target == "Note"
        assert result[0].alias == "Display Text"

    def test_with_heading(self) -> None:
        text = "[[Note#Section]]"
        result = parse_wikilinks(text)
        assert result[0].target == "Note"
        assert result[0].heading == "Section"

    def test_with_heading_and_alias(self) -> None:
        text = "[[Note#Section|See Here]]"
        result = parse_wikilinks(text)
        assert result[0].target == "Note"
        assert result[0].heading == "Section"
        assert result[0].alias == "See Here"

    def test_embed(self) -> None:
        text = "![[Embedded Note]]"
        result = parse_wikilinks(text)
        assert len(result) == 1
        assert result[0].target == "Embedded Note"
        assert result[0].is_embed is True

    def test_multiple_on_same_line(self) -> None:
        text = "[[A]] and [[B|bee]] and ![[C#head]]"
        result = parse_wikilinks(text)
        assert len(result) == 3
        assert result[0].target == "A"
        assert result[1].target == "B"
        assert result[1].alias == "bee"
        assert result[2].target == "C"
        assert result[2].heading == "head"
        assert result[2].is_embed is True

    def test_none(self) -> None:
        assert parse_wikilinks("plain text") == ()

    def test_empty_target_skipped(self) -> None:
        result = parse_wikilinks("[[]]")
        assert result == ()

    def test_with_spaces(self) -> None:
        text = "[[A Note With Spaces]]"
        result = parse_wikilinks(text)
        assert result[0].target == "A Note With Spaces"


# ── Markdown Links ─────────────────────────────────────────────────────


class TestParseMarkdownLinks:
    def test_basic(self) -> None:
        text = "[text](https://example.com)"
        result = parse_markdown_links(text)
        assert len(result) == 1
        assert result[0] == MarkdownLink(text="text", url="https://example.com")

    def test_image_excluded(self) -> None:
        text = "![image](img.png)"
        result = parse_markdown_links(text)
        assert result == ()

    def test_multiple(self) -> None:
        text = "[a](url1) and [b](url2)"
        result = parse_markdown_links(text)
        assert len(result) == 2
        assert result[0].text == "a"
        assert result[1].text == "b"

    def test_none(self) -> None:
        assert parse_markdown_links("plain text") == ()

    def test_nested_parentheses(self) -> None:
        text = "[wiki](https://en.wikipedia.org/w/index.php?title=Example_(disambiguation))"
        result = parse_markdown_links(text)
        assert len(result) == 1
        assert "disambiguation)" in result[0].url

    def test_empty_link_text(self) -> None:
        text = "[](/empty)"
        result = parse_markdown_links(text)
        assert len(result) == 1
        assert result[0].text == ""


# ── Tags ───────────────────────────────────────────────────────────────


class TestParseTags:
    def test_inline(self) -> None:
        text = "a #tag here"
        result = parse_tags(text)
        assert len(result) == 1
        assert result[0] == Tag(tag="tag", line_number=1)

    def test_with_slash(self) -> None:
        text = "a #project/active tag"
        result = parse_tags(text)
        assert result[0].tag == "project/active"

    def test_multiple(self) -> None:
        text = "#tag1 and #tag2/subl here"
        result = parse_tags(text)
        assert len(result) == 2
        assert result[0].tag == "tag1"
        assert result[1].tag == "tag2/subl"

    def test_inside_fenced_code_block_skipped(self) -> None:
        text = "a #RealTag here\n\n```\n# FakeTag\n```"
        result = parse_tags(text)
        assert len(result) == 1
        assert result[0].tag == "RealTag"

    def test_hash_in_url_not_a_tag(self) -> None:
        text = "see [link](#section)"
        result = parse_tags(text)
        assert result == ()

    def test_none(self) -> None:
        assert parse_tags("plain text") == ()

    def test_at_line_start(self) -> None:
        text = "#startoftag"
        result = parse_tags(text)
        assert len(result) == 1
        assert result[0].tag == "startoftag"
        assert result[0].line_number == 1

    def test_hash_only_not_tag(self) -> None:
        text = "this is # not a tag"
        result = parse_tags(text)
        assert result == ()


# ── Integration / parse_markdown ───────────────────────────────────────


class TestParseMarkdown:
    def test_full_note(self) -> None:
        text = (
            "---\n"
            "title: My Note\n"
            "tags: [project, active]\n"
            "---\n"
            "\n"
            "# Heading 1\n"
            "\n"
            "[[Link1]] and [[Link2|Alias]] and ![[Embed]]\n"
            "\n"
            "A #inline tag here\n"
            "\n"
            "## Heading 2\n"
            "\n"
            "[click](https://example.com)\n"
        )
        result = parse_markdown(text)
        assert result.frontmatter.title == "My Note"
        assert result.frontmatter.tags == ("project", "active")
        assert len(result.headings) == 2
        assert result.headings[0].text == "Heading 1"
        assert len(result.wikilinks) == 3
        assert result.wikilinks[0].target == "Link1"
        assert result.wikilinks[1].alias == "Alias"
        assert result.wikilinks[2].is_embed is True
        assert len(result.tags) == 1
        assert result.tags[0].tag == "inline"
        assert len(result.markdown_links) == 1
        assert result.markdown_links[0].url == "https://example.com"

    def test_empty_document(self) -> None:
        result = parse_markdown("")
        assert result.frontmatter.title == ""
        assert result.headings == ()
        assert result.heading_bodies == ()
        assert result.wikilinks == ()
        assert result.markdown_links == ()
        assert result.tags == ()

    def test_line_numbers_one_based(self) -> None:
        text = "\n\n# Heading\n\n[[Link]]\n\n#tag"
        result = parse_markdown(text)
        # heading on line 3, tag on line 7
        assert result.headings[0].line_number == 3
        assert result.tags[0].line_number == 7

    def test_heading_bodies_in_integration(self) -> None:
        text = "# A\n\ncontent a\n\n## B\n\ncontent b"
        result = parse_markdown(text)
        assert len(result.heading_bodies) == 2
        # A at line 1, no next H1 → end of doc (line 5)
        assert result.heading_bodies[0].start_line == 1
        assert result.heading_bodies[0].end_line == 7
        # B at line 4, last heading → end
        assert result.heading_bodies[1].start_line == 5
        assert result.heading_bodies[1].end_line == 7


# ── Edge cases and security ────────────────────────────────────────────


class TestSecurityAndEdgeCases:
    def test_unicode(self) -> None:
        text = "# 日本語見出し\n\n[[ノート|表示]]\n\n#tag\n"
        result = parse_markdown(text)
        assert result.headings[0].text == "日本語見出し"
        assert result.wikilinks[0].target == "ノート"
        assert result.wikilinks[0].alias == "表示"
        assert result.tags[0].tag == "tag"

    def test_crlf(self) -> None:
        text = "# H1\r\n\r\n[[Link]]\r\n"
        result = parse_markdown(text)
        assert result.headings[0].text == "H1"
        assert result.wikilinks[0].target == "Link"

    def test_very_long_text(self) -> None:
        lines: list[str] = []
        for i in range(100):
            lines.append(f"[[Link{i}]]")
        text = "\n".join(lines)
        result = parse_markdown(text)
        assert len(result.wikilinks) == 100

    def test_many_headings(self) -> None:
        lines = [f"# Heading {i}" for i in range(1000)]
        text = "\n".join(lines)
        result = parse_markdown(text)
        assert len(result.headings) == 1000

    def test_frontmatter_value_not_parsed_as_heading(self) -> None:
        text = "---\ntitle: '# not a heading'\n---\n# Real Heading\n"
        result = parse_markdown(text)
        assert len(result.headings) == 1
        assert result.headings[0].text == "Real Heading"

    def test_interleaved_code_blocks(self) -> None:
        text = "# H1\n```\ninside\n```\n## H2\n```\ninside 2\n```\n### H3"
        result = parse_headings(text)
        assert len(result) == 3

    def test_all_dataclasses_are_frozen(self) -> None:
        for cls in (Frontmatter, Heading, HeadingBody, Wikilink, MarkdownLink, Tag, ParsedMarkdown):
            assert dataclasses.is_dataclass(cls)
            assert hasattr(cls, "__dataclass_params__")
            assert cls.__dataclass_params__.frozen

    def test_only_frontmatter(self) -> None:
        text = "---\ntitle: Only Frontmatter\n---"
        result = parse_markdown(text)
        assert result.frontmatter.title == "Only Frontmatter"
        assert result.headings == ()

    def test_wikilink_inside_code_block_extracted(self) -> None:
        text = "```\n[[InsideCode]]\n```"
        result = parse_wikilinks(text)
        assert len(result) == 1
        assert result[0].target == "InsideCode"
