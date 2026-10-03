"""Loader fallback chain (CONTRACT 'Graph loading') — pure functions, tmp dirs only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.graph_store import (
    GraphStore,
    load_explanations,
    resolve_explanations_path,
    resolve_graph_path,
)
from api.tests.conftest import FIXTURE_GRAPH, write_minimal_graph


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    """A tmp repo root with a clean env (no ATLAS_* overrides leaking in)."""
    monkeypatch.delenv("ATLAS_GRAPH_PATH", raising=False)
    monkeypatch.delenv("ATLAS_EXPLANATIONS_PATH", raising=False)
    return tmp_path


def test_env_override_wins(repo, monkeypatch):
    write_minimal_graph(repo / "data" / "build" / "graph.json")
    write_minimal_graph(repo / "data" / "build" / "graph.mock.json")
    override = write_minimal_graph(repo / "elsewhere" / "graph.custom.json")
    monkeypatch.setenv("ATLAS_GRAPH_PATH", str(override))
    assert resolve_graph_path(repo) == override


def test_real_graph_preferred_over_mocks(repo):
    real = write_minimal_graph(repo / "data" / "build" / "graph.json")
    write_minimal_graph(repo / "data" / "build" / "graph.mock.json")
    write_minimal_graph(repo / "api" / "fixtures" / "graph.mock.json")
    assert resolve_graph_path(repo) == real


def test_build_mock_preferred_over_api_fixture(repo):
    build_mock = write_minimal_graph(repo / "data" / "build" / "graph.mock.json")
    write_minimal_graph(repo / "api" / "fixtures" / "graph.mock.json")
    assert resolve_graph_path(repo) == build_mock


def test_api_fixture_is_last_resort(repo):
    fixture = write_minimal_graph(repo / "api" / "fixtures" / "graph.mock.json")
    assert resolve_graph_path(repo) == fixture


def test_missing_env_override_is_skipped(repo, monkeypatch):
    real = write_minimal_graph(repo / "data" / "build" / "graph.json")
    monkeypatch.setenv("ATLAS_GRAPH_PATH", str(repo / "does-not-exist.json"))
    assert resolve_graph_path(repo) == real


def test_empty_env_override_is_ignored(repo, monkeypatch):
    real = write_minimal_graph(repo / "data" / "build" / "graph.json")
    monkeypatch.setenv("ATLAS_GRAPH_PATH", "")
    assert resolve_graph_path(repo) == real


def test_no_graph_anywhere_raises(repo):
    with pytest.raises(FileNotFoundError):
        resolve_graph_path(repo)


def test_mode_is_mock_when_path_contains_mock(repo):
    path = write_minimal_graph(repo / "api" / "fixtures" / "graph.mock.json")
    assert GraphStore.load(path).mode == "mock"


def test_mode_is_real_otherwise(repo):
    path = write_minimal_graph(repo / "data" / "build" / "graph.json")
    assert GraphStore.load(path).mode == "real"


def test_explanations_chain(repo, monkeypatch):
    assert resolve_explanations_path(repo) is None

    fixture = repo / "api" / "fixtures" / "explanations.mock.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("{}")
    assert resolve_explanations_path(repo) == fixture

    build = repo / "data" / "build" / "explanations.json"
    build.parent.mkdir(parents=True, exist_ok=True)
    build.write_text("{}")
    assert resolve_explanations_path(repo) == build

    override = repo / "custom-explanations.json"
    override.write_text("{}")
    monkeypatch.setenv("ATLAS_EXPLANATIONS_PATH", str(override))
    assert resolve_explanations_path(repo) == override


def test_load_explanations_handles_none_and_comment_keys(tmp_path):
    assert load_explanations(None) == {}
    path = tmp_path / "expl.json"
    path.write_text(
        json.dumps(
            {"_comment": "ignore me", "mock:disease:gof": {"text": "t", "citations": []}}
        )
    )
    loaded = load_explanations(path)
    assert "_comment" not in loaded
    assert "mock:disease:gof" in loaded


def test_indexes_built_once_at_load():
    store = GraphStore.load(FIXTURE_GRAPH)
    assert store.nodes_by_id["mock:disease:gof"]["type"] == "Disease"
    assert store.edges_by_id["e_000011"]["status"] == "contested"
    assert store.claims_by_id["c_000002"]["polarity"] == "contradicts"
    # 9 edges touch mock:disease:gof in the dev fixture
    assert len(store.edges_by_endpoint["mock:disease:gof"]) == 9
    assert store.cluster_ids_of("mock:disease:gof") == ["cl_mock_gof"]
