from __future__ import annotations

import json
import stat

from friday.application.memory.models import IndexBuildRequest, IndexState
from friday.infrastructure.memory.graphify_cli import GraphifyCliIndexBuilder, GraphifyCliSettings


def test_build_promotes_valid_graph(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("note")
    executable = tmp_path / "graphify"
    executable.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --version ]; then echo fake; exit 0; fi\n"
        "mkdir -p \"$5/graphify-out\"\n"
        "echo '{\"directed\":false,\"multigraph\":false,\"graph\":{},\"nodes\":[],\"links\":[],\"hyperedges\":[],\"built_at_commit\":\"x\"}' > \"$5/graphify-out/graph.json\"\n"
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    builder = GraphifyCliIndexBuilder(
        GraphifyCliSettings(vault, tmp_path / "indexes", str(executable), 5, 100, 100, 1_000)
    )
    request = IndexBuildRequest("vault", "snapshot", ("note.md",), 5, 1_000)

    result = builder.build(request)

    assert result.state is IndexState.FRESH
    assert json.loads((tmp_path / "indexes" / "vault" / "active" / "graph.json").read_text())["nodes"] == []


def test_missing_executable_disables_build(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    builder = GraphifyCliIndexBuilder(
        GraphifyCliSettings(vault, tmp_path / "indexes", "missing-graphify", 1, 100, 100, 1_000)
    )

    result = builder.build(IndexBuildRequest("vault", "snapshot", (), 1, 1_000))

    assert result.state is IndexState.DISABLED
    assert result.failure_code == "executable_missing"
