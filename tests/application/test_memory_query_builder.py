"""Deterministic MemoryQueryBuilder with a strict allowlist of safe inputs."""

from __future__ import annotations

import pytest

from friday.application.memory.query_builder import (
    _DEFAULT_MAX_PHRASES,
    _DEFAULT_MAX_TAGS,
    _DEFAULT_MAX_TERM_LENGTH,
    _DEFAULT_MAX_TERMS,
    _DEFAULT_MAX_TITLES,
    _DEFAULT_MIN_TOKEN_LENGTH,
    MemoryQueryBuilder,
    RunSnapshot,
)

# ---- Builder configuration validation -------------------------------------


def test_builder_validates_max_terms() -> None:
    with pytest.raises(ValueError, match="max_terms"):
        MemoryQueryBuilder(max_terms=0)


def test_builder_validates_max_phrases() -> None:
    with pytest.raises(ValueError, match="max_phrases"):
        MemoryQueryBuilder(max_phrases=0)


def test_builder_validates_max_tags() -> None:
    with pytest.raises(ValueError, match="max_tags"):
        MemoryQueryBuilder(max_tags=0)


def test_builder_validates_max_titles() -> None:
    with pytest.raises(ValueError, match="max_titles"):
        MemoryQueryBuilder(max_titles=0)


def test_builder_validates_max_term_length() -> None:
    with pytest.raises(ValueError, match="max_term_length"):
        MemoryQueryBuilder(max_term_length=0)


def test_builder_validates_min_token_length() -> None:
    with pytest.raises(ValueError, match="min_token_length"):
        MemoryQueryBuilder(min_token_length=0)


# ---- Degenerate / empty snapshots -----------------------------------------


def test_empty_snapshot_returns_none() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot()
    assert builder.build(snapshot) is None


def test_snapshot_with_only_stopwords_returns_none() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(task_description="the and for but")
    assert builder.build(snapshot) is None


def test_snapshot_with_very_short_tokens_returns_none() -> None:
    builder = MemoryQueryBuilder(min_token_length=5)
    snapshot = RunSnapshot(task_description="hi ok go")
    assert builder.build(snapshot) is None


def test_snapshot_with_blank_fields_returns_none() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(task_title="  ", task_description="   ", objective="   ")
    assert builder.build(snapshot) is None


# ---- Allowed sources contribute to the correct fields ---------------------


def test_task_title_goes_to_titles() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(task_title="Database Migration Plan")
    query = builder.build(snapshot)
    assert query is not None
    assert "database migration plan" in query.titles
    # Verify no cross-contamination
    assert not query.terms
    assert not query.phrases


def test_task_description_goes_to_terms() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(task_description="Fix login timeout bug in auth module")
    query = builder.build(snapshot)
    assert query is not None
    assert "fix" in query.terms
    assert "login" in query.terms
    assert "timeout" in query.terms
    assert "bug" in query.terms
    assert "auth" in query.terms
    assert "module" in query.terms
    # Stopwords filtered
    assert "in" not in query.terms
    # Not in other fields
    assert not query.phrases
    assert not query.tags


def test_objective_goes_to_phrases() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(objective="Fix the production database timeout")
    query = builder.build(snapshot)
    assert query is not None
    assert "fix the production database timeout" in query.phrases


def test_step_names_go_to_titles() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(step_names=("Analyze", "Implement", "Review"))
    query = builder.build(snapshot)
    assert query is not None
    assert "analyze" in query.titles
    assert "implement" in query.titles
    assert "review" in query.titles


def test_failure_codes_go_to_tags() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(failure_codes=("timeout", "auth_denied", "rate_limited"))
    query = builder.build(snapshot)
    assert query is not None
    assert "timeout" in query.tags
    assert "auth_denied" in query.tags
    assert "rate_limited" in query.tags


def test_tool_names_go_to_terms() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(tool_names=("read_file", "search_code", "list_directory"))
    query = builder.build(snapshot)
    assert query is not None
    assert "read_file" in query.terms
    assert "search_code" in query.terms
    assert "list_directory" in query.terms


def test_memory_search_term_goes_to_phrases() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(memory_search_term="previous incident with database failover")
    query = builder.build(snapshot)
    assert query is not None
    assert "previous incident with database failover" in query.phrases


# ---- Multiple sources combine correctly -----------------------------------


def test_all_allowed_sources_contribute() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(
        task_title="Deploy v2.1",
        task_description="Roll out the new caching layer",
        objective="Complete production rollout of caching",
        step_names=("Build", "Test", "Deploy"),
        failure_codes=("oom", "timeout"),
        tool_names=("kubectl", "helm"),
        memory_search_term="caching rollout procedure",
    )
    query = builder.build(snapshot)
    assert query is not None
    assert "deploy v2.1" in query.titles
    assert "roll" in query.terms
    assert "caching" in query.terms
    assert "layer" in query.terms
    assert "complete production rollout of caching" in query.phrases
    assert "build" in query.titles
    assert "test" in query.titles
    assert "deploy" in query.titles
    assert "oom" in query.tags
    assert "timeout" in query.tags
    assert "kubectl" in query.terms
    assert "helm" in query.terms
    assert "caching rollout procedure" in query.phrases


# ---- Forbidden / dangerous content filtering ------------------------------


def test_graphify_syntax_in_description_stripped() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(task_description="Match (n:Node {id: '123'}) RETURN n")
    query = builder.build(snapshot)
    assert query is not None
    for term in query.terms:
        assert "{" not in term
        assert "}" not in term
        assert "(" not in term
        assert ")" not in term
        assert ":" not in term
        assert "'" not in term
    assert "n" not in query.terms  # too short (min_token_length=2)


def test_graphify_syntax_in_tool_names_stripped() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(tool_names=("cypher:`MATCH (n)`",))
    query = builder.build(snapshot)
    assert query is not None
    for term in query.terms:
        assert "`" not in term
        assert "(" not in term
        assert ")" not in term


# ---- Determinism ----------------------------------------------------------


def test_identical_snapshots_yield_identical_query() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(
        task_title="Fix Auth",
        task_description="resolve token expiry edge case",
        objective="fix the auth token flow",
        step_names=("Debug", "Fix", "Verify"),
        failure_codes=("token_expired",),
        tool_names=("read", "write"),
        memory_search_term="auth token fix",
    )
    q1 = builder.build(snapshot)
    q2 = builder.build(snapshot)
    assert q1 is not None
    assert q2 is not None
    assert q1.terms == q2.terms
    assert q1.phrases == q2.phrases
    assert q1.tags == q2.tags
    assert q1.titles == q2.titles
    assert q1.query_hash == q2.query_hash


def test_equivalent_step_order_yields_same_hash() -> None:
    builder = MemoryQueryBuilder()
    a = builder.build(RunSnapshot(step_names=("A", "B", "C")))
    b = builder.build(RunSnapshot(step_names=("A", "B", "C")))
    assert a is not None and b is not None
    assert a.query_hash == b.query_hash


# ---- Term caps ------------------------------------------------------------


def test_terms_capped_at_max() -> None:
    builder = MemoryQueryBuilder(max_terms=3)
    snapshot = RunSnapshot(
        task_description="one two three four five six seven eight nine ten",
        tool_names=("extra1", "extra2", "extra3"),
    )
    query = builder.build(snapshot)
    assert query is not None
    assert len(query.terms) <= 3
    # First-seen order: "one" (from desc), then "two".."ten", then "extra1"..
    # After dedup the first 3 should be one, two, three
    assert query.terms[0] == "one"
    assert query.terms[1] == "two"
    assert query.terms[2] == "three"


def test_phrases_capped_at_max() -> None:
    builder = MemoryQueryBuilder(max_phrases=1)
    snapshot = RunSnapshot(
        objective="first objective",
        memory_search_term="second search term",
    )
    query = builder.build(snapshot)
    assert query is not None
    assert len(query.phrases) <= 1


def test_tags_capped_at_max() -> None:
    builder = MemoryQueryBuilder(max_tags=2)
    snapshot = RunSnapshot(failure_codes=("a", "b", "c", "d"))
    query = builder.build(snapshot)
    assert query is not None
    assert len(query.tags) <= 2
    assert query.tags == ("a", "b")


def test_titles_capped_at_max() -> None:
    builder = MemoryQueryBuilder(max_titles=2)
    snapshot = RunSnapshot(
        task_title="Title",
        step_names=("s1", "s2", "s3"),
    )
    query = builder.build(snapshot)
    assert query is not None
    assert len(query.titles) <= 2


# ---- Stopword removal -----------------------------------------------------


def test_stopwords_removed_from_terms() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(task_description="the quick brown fox jumps over the lazy dog")
    query = builder.build(snapshot)
    assert query is not None
    assert "the" not in query.terms
    assert "over" not in query.terms
    assert "quick" in query.terms
    assert "brown" in query.terms
    assert "fox" in query.terms
    assert "jumps" in query.terms
    assert "lazy" in query.terms
    assert "dog" in query.terms


def test_stopwords_not_filtered_from_phrases() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(objective="the quick brown fox")
    query = builder.build(snapshot)
    assert query is not None
    assert "the quick brown fox" in query.phrases


def test_stopwords_not_filtered_from_titles() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(task_title="The Art of War")
    query = builder.build(snapshot)
    assert query is not None
    assert "the art of war" in query.titles


def test_stopwords_not_filtered_from_tags() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(failure_codes=("the_issue",))
    query = builder.build(snapshot)
    assert query is not None
    assert "the_issue" in query.tags


# ---- Deduplication preserving first-seen order ----------------------------


def test_dedup_preserves_first_seen_order_for_terms() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(task_description="alpha beta gamma alpha beta delta")
    query = builder.build(snapshot)
    assert query is not None
    assert query.terms == ("alpha", "beta", "gamma", "delta")


def test_dedup_case_variants_in_titles() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(step_names=("Fix Bug", "fix bug", "FIX BUG"))
    query = builder.build(snapshot)
    assert query is not None
    # "fix bug" appears only once (casefold dedup)
    assert query.titles == ("fix bug",)


def test_dedup_case_variants_in_failure_codes() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(failure_codes=("TIMEOUT", "Timeout", "timeout"))
    query = builder.build(snapshot)
    assert query is not None
    assert query.tags == ("timeout",)


# ---- Min / max token length -----------------------------------------------


def test_terms_below_min_length_dropped() -> None:
    builder = MemoryQueryBuilder(min_token_length=4)
    snapshot = RunSnapshot(task_description="hi hello bye greetings")
    query = builder.build(snapshot)
    assert query is not None
    assert "hi" not in query.terms
    assert "bye" not in query.terms
    assert "hello" in query.terms
    assert "greetings" in query.terms


def test_terms_above_max_length_truncated() -> None:
    builder = MemoryQueryBuilder(max_term_length=5)
    snapshot = RunSnapshot(task_description="short toolongcut")
    query = builder.build(snapshot)
    assert query is not None
    assert "short" in query.terms
    assert "toolongcut" not in query.terms


# ---- Edge cases -----------------------------------------------------------


def test_default_config_values_matched() -> None:
    assert _DEFAULT_MAX_TERMS == 20
    assert _DEFAULT_MAX_PHRASES == 5
    assert _DEFAULT_MAX_TAGS == 10
    assert _DEFAULT_MAX_TITLES == 10
    assert _DEFAULT_MAX_TERM_LENGTH == 100
    assert _DEFAULT_MIN_TOKEN_LENGTH == 2


def test_builder_properties_match_config() -> None:
    builder = MemoryQueryBuilder(
        max_terms=5,
        max_phrases=3,
        max_tags=4,
        max_titles=6,
        max_term_length=50,
        min_token_length=3,
    )
    assert builder.max_terms == 5
    assert builder.max_phrases == 3
    assert builder.max_tags == 4
    assert builder.max_titles == 6
    assert builder.max_term_length == 50
    assert builder.min_token_length == 3


# ---- Branch coverage: blank/whitespace entries are skipped -----------------


def test_blank_step_name_is_skipped() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(
        step_names=("Valid Step", "   ", ""),
    )
    query = builder.build(snapshot)
    assert query is not None
    assert "valid step" in query.titles
    assert len(query.titles) == 1


def test_blank_failure_code_is_skipped() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(
        failure_codes=("real_error", "   ", ""),
    )
    query = builder.build(snapshot)
    assert query is not None
    assert "real_error" in query.tags
    assert len(query.tags) == 1


def test_blank_tool_name_is_skipped() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(
        tool_names=("valid_tool", "   ", ""),
    )
    query = builder.build(snapshot)
    assert query is not None
    assert "valid_tool" in query.terms
    assert len(query.terms) == 1


def test_whitespace_memory_search_term_adds_no_phrase() -> None:
    builder = MemoryQueryBuilder()
    snapshot = RunSnapshot(
        objective="real objective",
        memory_search_term="   ",
    )
    query = builder.build(snapshot)
    assert query is not None
    assert "real objective" in query.phrases
    assert len(query.phrases) == 1


# ---- Structural regression guard (security) --------------------------------


def test_runsnapshot_field_names_are_explicit_allowlist() -> None:
    """RunSnapshot fields are the *only* data path from Run into memory
    retrieval. Adding a field is a security decision requiring review —
    a future `tool_stdout` field must fail this test loudly instead of
    silently leaking."""
    import dataclasses

    names = {f.name for f in dataclasses.fields(RunSnapshot)}
    assert names == {
        "task_title",
        "task_description",
        "objective",
        "step_names",
        "failure_codes",
        "tool_names",
        "memory_search_term",
    }
