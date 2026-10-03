"""Demo-dataset loader chain + content checks (full-product model, demo-labeled).

Chain (CONTRACT.md): ATLAS_GRAPH_PATH > data/build/graph.json >
api/fixtures/graph.demo.mock.json > data/build/graph.mock.json >
api/fixtures/graph.mock.json. Real data always beats the demo; the demo file
name contains "mock" so /health keeps mode=mock and the UI ribbon stays on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.graph_store import GraphStore, resolve_graph_path
from api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_GRAPH = REPO_ROOT / "api" / "fixtures" / "graph.demo.mock.json"


def _tree(tmp_path, with_real=False, with_m0_mock=True):
    (tmp_path / "data" / "build").mkdir(parents=True)
    (tmp_path / "api" / "fixtures").mkdir(parents=True)
    empty = json.dumps({"meta": {}, "nodes": [], "edges": [], "claims": [], "clusters": []})
    if with_real:
        (tmp_path / "data" / "build" / "graph.json").write_text(empty)
    if with_m0_mock:
        (tmp_path / "data" / "build" / "graph.mock.json").write_text(empty)
    (tmp_path / "api" / "fixtures" / "graph.demo.mock.json").write_text(empty)
    (tmp_path / "api" / "fixtures" / "graph.mock.json").write_text(empty)
    return tmp_path


def test_demo_beats_m0_mock(tmp_path):
    root = _tree(tmp_path)
    assert resolve_graph_path(root, env={}) == root / "api" / "fixtures" / "graph.demo.mock.json"


def test_real_graph_beats_demo(tmp_path):
    root = _tree(tmp_path, with_real=True)
    assert resolve_graph_path(root, env={}) == root / "data" / "build" / "graph.json"


def test_demo_mode_stays_mock(tmp_path):
    root = _tree(tmp_path)
    store = GraphStore.load(resolve_graph_path(root, env={}))
    assert store.mode == "mock"


# ---- content checks on the committed demo dataset ---------------------------


@pytest.fixture(scope="module")
def demo_client():
    explanations = REPO_ROOT / "api" / "fixtures" / "explanations.demo.mock.json"
    return TestClient(create_app(graph_path=DEMO_GRAPH, explanations_path=explanations))


def test_demo_dataset_is_fully_demo_labeled():
    """Hard rule 1/10: every source id and claim pmid/quote is a DEMO placeholder,
    except asset URLs verified in pipeline/seeds/patient_groups.yaml."""
    data = json.loads(DEMO_GRAPH.read_text())
    seeds_text = (REPO_ROOT / "pipeline" / "seeds" / "patient_groups.yaml").read_text()
    for edge in data["edges"]:
        for src in edge["sources"]:
            if src["kind"] == "url":
                assert src["id"] in seeds_text or "example.invalid" in src["id"], (
                    f"edge {edge['id']} cites url not verified in seeds: {src['id']}"
                )
            else:
                assert src["id"].startswith("DEMO:"), f"edge {edge['id']}: {src['id']}"
    for claim in data["claims"]:
        assert claim["pmid"].startswith("DEMO"), claim["id"]
        assert claim["quote"].startswith("Demo placeholder"), claim["id"]


def test_demo_maria_search_resolves_r274w(demo_client):
    res = demo_client.get("/search", params={"q": "STAT1 R274W"}).json()
    assert res["results"], "R274W must resolve"
    assert res["results"][0]["id"] == "demo:variant:stat1-r274w"


def test_demo_ruxolitinib_edge_is_contested_small_n(demo_client):
    res = demo_client.get("/edge/e_1020").json()
    assert set(res["labels"]) == {"promising", "contested", "small_n"}
    assert res["supporting_claims"] and res["contradicting_claims"]


def test_demo_rux_to_scid_is_inferred_unsupported(demo_client):
    res = demo_client.get("/edge/e_1021").json()
    assert res["edge"]["status"] == "inferred_unsupported"
    assert "direction mismatch" in res["edge"]["confidence_basis"]["reason"]


def test_demo_clusters_model_ground_truth_shapes():
    data = json.loads(DEMO_GRAPH.read_text())
    clusters = {c["id"]: set(c["members"]) for c in data["clusters"]}
    gof = clusters["cl_demo_gof_dysreg"]
    assert {"demo:disease:stat1-gof", "demo:disease:stat3-gof"} <= gof  # test-1/2 shape
    assert "demo:disease:stat1-lof" not in gof  # test 1
    assert {"demo:disease:jak3-scid", "demo:disease:xscid"} <= clusters["cl_demo_scid"]  # test 2
    assert clusters["cl_demo_somatic_mpn"] == {"demo:disease:pv-mpn"}  # test 3


def test_demo_mechanism_dampen_ranks_gof_excludes_lof(demo_client):
    res = demo_client.get("/mechanism-search", params={"direction": "dampen"}).json()
    ranked = [r["node"]["id"] for r in res["results"]]
    assert "demo:disease:stat1-gof" in ranked and "demo:disease:stat3-gof" in ranked
    excluded = {x["node"]["id"]: x["reason"] for x in res["excluded"]}
    assert "demo:disease:jak3-scid" in excluded
    assert "wrong direction" in excluded["demo:disease:jak3-scid"]


def test_demo_honest_gap_entity(demo_client):
    res = demo_client.get("/entity/demo:gene:orphan").json()
    assert res["no_supported_route"] is True
    assert res["coverage"]["searched"]
    assert res["next_question"]


def test_demo_explanations_precomputed(demo_client):
    res = demo_client.post("/explain", json={"ids": ["demo:disease:stat1-gof"]}).json()
    assert res["source"] == "precomputed"
    assert res["citations"]
