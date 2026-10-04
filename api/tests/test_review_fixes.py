"""Tests for the eng-review fixes (R2 approved D4, R1 parity, R3 seam logging).

RED first: each test here was written before its implementation and watched fail.
"""

from __future__ import annotations

import json
import sys
import types

import pytest
from fastapi.testclient import TestClient

from api import llm_seam
from api.graph_store import GraphStore
from api.main import create_app

from .conftest import FIXTURE_GRAPH


def _graph(tmp_path, **overrides):
    base = {
        "meta": {"built_at": "2026-10-03T12:00:00Z", "sources": [], "coverage": {}},
        "nodes": [],
        "edges": [],
        "claims": [],
        "clusters": [],
    }
    base.update(overrides)
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(base))
    return path


def _client(graph_path):
    return TestClient(create_app(graph_path=graph_path))


# ---- R2 (D4 approved): /entity for coverage-only ids -----------------------


def test_entity_coverage_only_id_returns_honest_gap(tmp_path):
    coverage = {
        "mock:gene:ghost": {
            "searched": [
                {"source": "MOCK-DB", "query": "ghost gene", "date": "2026-10-03", "hits": 0}
            ]
        }
    }
    client = _client(_graph(tmp_path, meta={"built_at": "x", "sources": [], "coverage": coverage}))
    res = client.get("/entity/mock:gene:ghost")
    assert res.status_code == 200
    body = res.json()
    assert body["no_supported_route"] is True
    assert body["coverage"] == coverage["mock:gene:ghost"]
    assert isinstance(body["next_question"], str) and "mock:gene:ghost" in body["next_question"]
    assert body["node"]["id"] == "mock:gene:ghost"
    assert body["node"]["type"] == "Unknown"
    assert body["neighbors"] == [] and body["assets"] == []


def test_entity_unknown_id_still_404(tmp_path):
    client = _client(_graph(tmp_path))
    assert client.get("/entity/not:anywhere").status_code == 404


# ---- R1 parity: mechanism-search orientation + dedup ------------------------


def _mech_nodes():
    return [
        {"id": "mech:X:GoF", "type": "Mechanism", "label": "X GoF", "synonyms": [], "props": {"direction": "GoF"}},
        {"id": "d:1", "type": "Disease", "label": "Disease One", "synonyms": [], "props": {}},
    ]


def _edge(eid, source, target, relation="causes"):
    return {
        "id": eid, "source": source, "target": target, "relation": relation,
        "evidence_class": "observed", "confidence": 0.8, "confidence_basis": {},
        "status": "supported", "sources": [], "supporting": [], "contradicting": [],
        "attributes": {},
    }


def test_mechanism_search_accepts_disease_to_mechanism_orientation(tmp_path):
    client = _client(
        _graph(tmp_path, nodes=_mech_nodes(), edges=[_edge("e_1", "d:1", "mech:X:GoF")])
    )
    body = client.get("/mechanism-search", params={"direction": "dampen"}).json()
    assert [r["node"]["id"] for r in body["results"]] == ["d:1"]


def test_mechanism_search_dedups_disease_across_edges(tmp_path):
    client = _client(
        _graph(
            tmp_path,
            nodes=_mech_nodes(),
            edges=[_edge("e_1", "mech:X:GoF", "d:1"), _edge("e_2", "d:1", "mech:X:GoF")],
        )
    )
    body = client.get("/mechanism-search", params={"direction": "dampen"}).json()
    ids = [r["node"]["id"] for r in body["results"]] + [x["node"]["id"] for x in body["excluded"]]
    assert ids == ["d:1"]


def test_mechanism_search_accepts_has_mechanism_relation(tmp_path):
    # M0's frozen mock links Disease --has_mechanism--> Mechanism (no "causes").
    client = _client(
        _graph(tmp_path, nodes=_mech_nodes(), edges=[_edge("e_1", "d:1", "mech:X:GoF", "has_mechanism")])
    )
    body = client.get("/mechanism-search", params={"direction": "dampen"}).json()
    assert [r["node"]["id"] for r in body["results"]] == ["d:1"]


def test_mechanism_search_ignores_non_causes_relations(tmp_path):
    client = _client(
        _graph(tmp_path, nodes=_mech_nodes(), edges=[_edge("e_1", "mech:X:GoF", "d:1", "similar_to")])
    )
    body = client.get("/mechanism-search", params={"direction": "dampen"}).json()
    assert body["results"] == [] and body["excluded"] == []


# ---- R1 parity: search substring guard (len >= 3, matching web) -------------


def test_search_single_char_does_not_substring_flood():
    store = GraphStore.load(FIXTURE_GRAPH)
    assert store.search("a") == []  # stopword token + sub-3-char substring


def test_search_two_chars_no_substring_but_exact_ok():
    store = GraphStore.load(FIXTURE_GRAPH)
    assert all(r["score"] < 0.9 for r in store.search("ck"))  # no 0.9 substring hits


# ---- R3: seam logs when a present pipeline.llm is broken ---------------------


def test_llm_seam_logs_warning_when_module_broken(caplog, monkeypatch):
    broken = types.ModuleType("pipeline.llm")

    def explain(ids, graph):  # pragma: no cover - raises immediately
        raise RuntimeError("boom from lane B")

    broken.explain = explain
    pipeline_pkg = types.ModuleType("pipeline")
    monkeypatch.setitem(sys.modules, "pipeline", pipeline_pkg)
    monkeypatch.setitem(sys.modules, "pipeline.llm", broken)

    with caplog.at_level("WARNING"):
        assert llm_seam.try_live_explain(["x"], {}) is None
    assert any("pipeline.llm" in r.message for r in caplog.records)


def test_llm_seam_quiet_when_live_returns_none(caplog, monkeypatch):
    """Lane B's contract: explain/draft return None unless the model produced
    validated text — None is the documented quiet 'no live answer', not an error."""
    quiet = types.ModuleType("pipeline.llm")
    quiet.explain = lambda ids, graph: None
    pipeline_pkg = types.ModuleType("pipeline")
    monkeypatch.setitem(sys.modules, "pipeline", pipeline_pkg)
    monkeypatch.setitem(sys.modules, "pipeline.llm", quiet)

    with caplog.at_level("WARNING"):
        assert llm_seam.try_live_explain(["x"], {}) is None
    assert not [r for r in caplog.records if "pipeline.llm" in r.message]
