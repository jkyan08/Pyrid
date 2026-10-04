"""POST /explain and POST /draft — LLM seam: live -> precomputed -> fallback."""

from __future__ import annotations

import sys
import types

DISCLAIMER = "Draft only — never sent. Research navigation, not medical advice."


def _all_citable_ids(fixture_graph):
    return {e["id"] for e in fixture_graph["edges"]} | {
        c["id"] for c in fixture_graph["claims"]
    }


def test_explain_precomputed(client, fixture_explanations):
    resp = client.post("/explain", json={"ids": ["mock:disease:gof"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "precomputed"
    assert body["text"] == fixture_explanations["mock:disease:gof"]["text"]
    assert body["citations"] == fixture_explanations["mock:disease:gof"]["citations"]


def test_explain_fallback_cites_real_edge_ids(client, fixture_graph):
    # id in the graph but not in the precomputed explanations file
    resp = client.post("/explain", json={"ids": ["mock:gene:alpha"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback"
    assert body["citations"], "fallback must cite real ids"
    assert set(body["citations"]) <= _all_citable_ids(fixture_graph)
    assert "e_000008" in body["citations"]
    assert "[" in body["text"] and "]" in body["text"]


def test_explain_unknown_id_never_500(client):
    resp = client.post("/explain", json={"ids": ["not:a:node"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback"
    assert body["citations"] == []


def test_explain_live_when_pipeline_llm_importable(client, monkeypatch):
    mod = types.ModuleType("pipeline.llm")
    mod.explain = lambda ids, graph: {
        "text": "live explanation [e_000001].",
        "citations": ["e_000001"],
    }
    monkeypatch.setitem(sys.modules, "pipeline.llm", mod)
    body = client.post("/explain", json={"ids": ["mock:disease:gof"]}).json()
    assert body["source"] == "live"
    assert body["citations"] == ["e_000001"]


def test_explain_broken_pipeline_llm_never_500(client, monkeypatch):
    def boom(ids, graph):
        raise RuntimeError("explain backend exploded")

    mod = types.ModuleType("pipeline.llm")
    mod.explain = boom
    monkeypatch.setitem(sys.modules, "pipeline.llm", mod)
    resp = client.post("/explain", json={"ids": ["mock:disease:gof"]})
    assert resp.status_code == 200
    assert resp.json()["source"] == "precomputed"  # falls through the chain


def test_draft_fallback(client, fixture_graph):
    resp = client.post("/draft", json={"entity_id": "mock:disease:gof"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback"
    assert body["disclaimer"] == DISCLAIMER
    assert body["citations"], "draft must cite edge/claim ids"
    assert set(body["citations"]) <= _all_citable_ids(fixture_graph)
    # cites both an edge and the treatment claims from the fixture
    assert "e_000011" in body["citations"]
    assert {"c_000001", "c_000002"} <= set(body["citations"])
    assert "[" in body["text"]


def test_draft_accepts_optional_focus(client):
    resp = client.post(
        "/draft", json={"entity_id": "mock:disease:gof", "focus": "registry_pooling"}
    )
    assert resp.status_code == 200
    assert resp.json()["disclaimer"] == DISCLAIMER


def test_draft_unknown_entity_404(client):
    assert client.post("/draft", json={"entity_id": "not:a:node"}).status_code == 404


def test_draft_live_when_pipeline_llm_importable(client, monkeypatch):
    mod = types.ModuleType("pipeline.llm")
    mod.draft = lambda entity_id, graph, focus=None: {
        "text": "live draft [e_000014].",
        "citations": ["e_000014"],
    }
    monkeypatch.setitem(sys.modules, "pipeline.llm", mod)
    body = client.post("/draft", json={"entity_id": "mock:disease:gof"}).json()
    assert body["source"] == "live"
    assert body["disclaimer"] == DISCLAIMER
