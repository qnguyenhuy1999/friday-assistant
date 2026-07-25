from __future__ import annotations

# mypy: disable-error-code=no-untyped-def
import json
import stat
import time
from pathlib import Path

import pytest

from friday.application.memory.models import IndexBuildRequest, IndexState
from friday.infrastructure.memory.graphify_cli import (
    GraphifyCliIndexBuilder,
    GraphifyCliSettings,
    _run_bounded,
)

_VALID_GRAPH = (
    '{"directed":false,"multigraph":false,"graph":{},"nodes":[],"links":[],'
    '"hyperedges":[],"built_at_commit":"x"}'
)


def _executable(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "graphify"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --version ]; then echo fake; exit 0; fi\n'
        "out=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --out ]; then out="$2"; break; fi\n'
        "  shift\n"
        "done\n" + body,
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def _request() -> IndexBuildRequest:
    return IndexBuildRequest("vault", "snapshot", ("note.md",), 5, 1_000)


def _builder(
    tmp_path: Path,
    executable: Path,
    *,
    timeout_seconds: float = 1,
    max_stdout_bytes: int = 100,
    max_stderr_bytes: int = 100,
    max_graph_bytes: int = 1_000,
    lock_ttl_seconds: float = 60,
    previous_retention: int = 1,
) -> GraphifyCliIndexBuilder:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "note.md").write_text("note", encoding="utf-8")
    return GraphifyCliIndexBuilder(
        GraphifyCliSettings(
            vault,
            tmp_path / "indexes",
            str(executable),
            timeout_seconds,
            max_stdout_bytes,
            max_stderr_bytes,
            max_graph_bytes,
            lock_ttl_seconds,
            previous_retention,
        )
    )


def _valid_body() -> str:
    return (
        'mkdir -p "$out/graphify-out"\n'
        f"printf '%s\\n' '{_VALID_GRAPH}' > \"$out/graphify-out/graph.json\"\n"
    )


def _active_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_build_promotes_valid_graph(tmp_path):
    executable = _executable(tmp_path, _valid_body())
    builder = _builder(tmp_path, executable)

    result = builder.build(_request())

    assert result.state is IndexState.FRESH
    assert (
        json.loads((tmp_path / "indexes" / "vault" / "active" / "graph.json").read_text())["nodes"]
        == []
    )


def test_missing_executable_disables_build(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    builder = GraphifyCliIndexBuilder(
        GraphifyCliSettings(vault, tmp_path / "indexes", "missing-graphify", 1, 100, 100, 1_000)
    )

    result = builder.build(IndexBuildRequest("vault", "snapshot", (), 1, 1_000))

    assert result.state is IndexState.DISABLED
    assert result.failure_code == "executable_missing"


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"executable": ""}, "empty"),
        ({"timeout_seconds": 0}, "positive"),
        ({"previous_retention": -1}, "negative"),
    ],
)
def test_invalid_graphify_settings_are_rejected(tmp_path, settings, message):
    if settings == {"executable": ""}:
        with pytest.raises(ValueError, match=message):
            GraphifyCliSettings(tmp_path / "vault", tmp_path / "indexes", "", 1, 1, 1, 1)
    elif settings == {"timeout_seconds": 0}:
        with pytest.raises(ValueError, match=message):
            GraphifyCliSettings(tmp_path / "vault", tmp_path / "indexes", "fake", 0, 1, 1, 1)
    else:
        with pytest.raises(ValueError, match=message):
            GraphifyCliSettings(
                tmp_path / "vault",
                tmp_path / "indexes",
                "fake",
                1,
                1,
                1,
                1,
                previous_retention=-1,
            )


def test_graphify_settings_reject_an_index_inside_the_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()

    with pytest.raises(ValueError, match="outside"):
        GraphifyCliSettings(vault, vault / "indexes", "fake", 1, 1, 1, 1)


def test_timeout_kills_the_process_group(tmp_path):
    marker = tmp_path / "orphaned-child"
    executable = _executable(tmp_path, f'(sleep 0.3; touch "{marker}") &\nsleep 2\n')
    result = _builder(tmp_path, executable, timeout_seconds=0.05).build(_request())

    time.sleep(0.5)

    assert result.failure_code == "timeout"
    assert not marker.exists()


def test_non_zero_exit_code_disables_build(tmp_path):
    result = _builder(tmp_path, _executable(tmp_path, "exit 7\n")).build(_request())

    assert result.state is IndexState.DISABLED
    assert result.failure_code == "command_failed"


def test_oversized_stdout_is_truncated(tmp_path):
    result = _run_bounded(
        [str(_executable(tmp_path, "printf 'abcdefghij'\n")), "extract"], tmp_path, 1, 4, 100
    )

    assert result.stdout == "abcd"


def test_oversized_stderr_is_truncated(tmp_path):
    result = _run_bounded(
        [str(_executable(tmp_path, "printf 'abcdefghij' >&2\n")), "extract"], tmp_path, 1, 100, 4
    )

    assert result.stderr == "abcd"


def test_failed_process_spawn_is_reported(tmp_path):
    result = _run_bounded([str(tmp_path / "does-not-exist")], tmp_path, 1, 1, 1)

    assert result.reason == "spawn_failed"


def test_malformed_graph_json_is_rejected(tmp_path):
    result = _builder(
        tmp_path,
        _executable(
            tmp_path,
            'mkdir -p "$out/graphify-out"\nprintf "not json" > "$out/graphify-out/graph.json"\n',
        ),
    ).build(_request())

    assert result.state is IndexState.CORRUPT
    assert result.failure_code == "build_failed"


def test_oversized_graph_is_rejected(tmp_path):
    result = _builder(
        tmp_path,
        _executable(tmp_path, _valid_body() + 'printf "more" >> "$out/graphify-out/graph.json"\n'),
        max_graph_bytes=10,
    ).build(_request())

    assert result.failure_code == "invalid_graph"


def test_missing_top_level_graph_schema_key_is_rejected(tmp_path):
    body = (
        'mkdir -p "$out/graphify-out"\n'
        'printf \'{"directed":false,"multigraph":false,"graph":{},"nodes":[],'
        '"links":[],"hyperedges":[]}\' > "$out/graphify-out/graph.json"\n'
    )
    result = _builder(tmp_path, _executable(tmp_path, body)).build(_request())

    assert result.failure_code == "build_failed"


def test_unknown_top_level_graph_schema_key_is_rejected(tmp_path):
    body = (
        'mkdir -p "$out/graphify-out"\n'
        'printf \'{"directed":false,"multigraph":false,"graph":{},"nodes":[],'
        '"links":[],"hyperedges":[],"unexpected":true,"built_at_commit":"x"}\' '
        '> "$out/graphify-out/graph.json"\n'
    )
    result = _builder(tmp_path, _executable(tmp_path, body)).build(_request())

    assert result.state is IndexState.CORRUPT
    assert result.failure_code == "build_failed"


def test_failed_build_keeps_active_index_byte_identical(tmp_path):
    executable = _executable(tmp_path, _valid_body())
    builder = _builder(tmp_path, executable)
    assert builder.build(_request()).state is IndexState.FRESH
    active = tmp_path / "indexes" / "vault" / "active"
    before = _active_files(active)
    executable.write_text("#!/bin/sh\nexit 4\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    result = builder.build(_request())

    assert result.state is IndexState.DISABLED
    assert _active_files(active) == before


def test_source_stats_ignore_missing_and_outside_paths(tmp_path):
    executable = _executable(tmp_path, _valid_body())
    builder = _builder(tmp_path, executable)
    request = IndexBuildRequest(
        "vault", "snapshot", ("note.md", "missing.md", "../outside.md"), 5, 1_000
    )

    result = builder.build(request)

    assert result.file_count == 3
    assert result.source_total_bytes == 4


def test_previous_retention_bound_is_honoured(tmp_path):
    executable = _executable(tmp_path, _valid_body())
    builder = _builder(tmp_path, executable, previous_retention=1)
    assert builder.build(_request()).state is IndexState.FRESH
    assert builder.build(_request()).state is IndexState.FRESH

    assert (tmp_path / "indexes" / "vault" / "previous").is_dir()
    assert not (tmp_path / "indexes" / "vault" / "previous" / "previous").exists()


def test_concurrent_builder_is_blocked_by_the_lock(tmp_path):
    executable = _executable(tmp_path, _valid_body())
    builder = _builder(tmp_path, executable)
    lock = tmp_path / "indexes" / "vault" / ".build.lock"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(
        json.dumps({"created_at": time.time(), "token": "other"}), encoding="utf-8"
    )

    result = builder.build(_request())

    assert result.failure_code == "build_locked"


def test_environment_is_limited_to_the_allowlist(tmp_path, monkeypatch):
    environment = tmp_path / "environment"
    monkeypatch.setenv("FRIDAY_TEST_SENTINEL", "secret")
    executable = _executable(tmp_path, f'env > "{environment}"\n' + _valid_body())

    assert _builder(tmp_path, executable).build(_request()).state is IndexState.FRESH

    assert "FRIDAY_TEST_SENTINEL=secret" not in environment.read_text(encoding="utf-8")


def test_graphify_never_receives_canonical_vault_root(tmp_path):
    captured = tmp_path / "captured_source.txt"
    executable = tmp_path / "graphify"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --version ]; then echo fake; exit 0; fi\n'
        f'printf "%s" "$2" > "{captured}"\n'
        "out=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --out ]; then out="$2"; break; fi\n'
        "  shift\n"
        "done\n" + _valid_body(),
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    builder = _builder(tmp_path, executable)

    result = builder.build(_request())

    assert result.state is IndexState.FRESH
    source_passed = captured.read_text()
    canonical_vault_root = str((tmp_path / "vault").resolve())
    assert source_passed != canonical_vault_root
    assert not Path(source_passed).is_relative_to(Path(canonical_vault_root))


def test_graphify_input_contains_only_included_notes(tmp_path):
    listing = tmp_path / "listing.txt"
    executable = tmp_path / "graphify"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --version ]; then echo fake; exit 0; fi\n'
        f'(cd "$2" && find . -type f | sort) > "{listing}"\n'
        "out=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --out ]; then out="$2"; break; fi\n'
        "  shift\n"
        "done\n" + _valid_body(),
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "note.md").write_text("note", encoding="utf-8")
    (vault / "not-included.md").write_text("never scanned by graphify", encoding="utf-8")

    builder = GraphifyCliIndexBuilder(
        GraphifyCliSettings(vault, tmp_path / "indexes", str(executable), 1, 100, 100, 1_000, 60, 1)
    )
    result = builder.build(_request())

    assert result.state is IndexState.FRESH
    assert listing.read_text().split() == ["./note.md"]


def test_graphify_input_excludes_private_sensitive_notes(tmp_path):
    """The staging step only copies request.included_paths -- private and
    sensitive notes never reach it because ObsidianVaultStore.included_paths()
    already excludes them before the request is built."""
    listing = tmp_path / "listing.txt"
    executable = tmp_path / "graphify"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --version ]; then echo fake; exit 0; fi\n'
        f'(cd "$2" && find . -type f | sort) > "{listing}"\n'
        "out=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --out ]; then out="$2"; break; fi\n'
        "  shift\n"
        "done\n" + _valid_body(),
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "note.md").write_text("note", encoding="utf-8")
    (vault / "private.md").write_text(
        "---\nprivate: true\n---\nsecret project info", encoding="utf-8"
    )
    (vault / "sensitive.md").write_text("---\nsensitive: true\n---\nsecret", encoding="utf-8")

    builder = GraphifyCliIndexBuilder(
        GraphifyCliSettings(vault, tmp_path / "indexes", str(executable), 1, 100, 100, 1_000, 60, 1)
    )
    # request.included_paths mirrors what ObsidianVaultStore.included_paths()
    # would return -- private.md/sensitive.md are already filtered upstream.
    result = builder.build(_request())

    assert result.state is IndexState.FRESH
    assert listing.read_text().split() == ["./note.md"]
