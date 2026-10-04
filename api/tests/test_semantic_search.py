"""Natural-language search: semantic matches, related diseases, and graceful fallback.

No network: the index is built in-memory from hand-made vectors and the embedder is
a fake. Labels are placeholders, not real identifiers.
"""
from __future__ import annotations

import math

from api import semantic
from api.graph_store import GraphStore
from pipeline.search_index import pack_int8, truncate_normalise

BUILT_AT = "2026-01-01T00:00:00+00:00"

# 4-dim toy embedding space: axis 0 = fungal infection, 1 = thyroid, 2 = blood, 3 = drug/JAK
VECS = {
    "HP:FUNGAL": [1, 0, 0, 0],
    "HP:ORAL": [0.9, 0, 0, 0.1],  # child of HP:FUNGAL
    "HP:THYROID": [0, 1, 0, 0],
    "HP:BLOOD": [0, 0, 1, 0],
    # diseases sit off the symptom axes, so they can only be reached through their symptoms
    "D:BOTH": [0.4, 0.4, 0.6, 0.6],
    "D:FUNGAL_ONLY": [0.3, 0, 0.9, 0],
    "D:BLOOD": [0, 0, 1, 0],
    "DRUG:A": [0, 0, 0, 1],
}
QUERIES = {
    "keeps getting thrush and thyroid trouble": [0.7, 0.7, 0, 0],
    "too many red cells": [0, 0, 1, 0],
    "medicine for jak": [0, 0, 0, 1],
}


def _node(nid, ntype, label, ic=None):
    props = {"ic": ic} if ic is not None else {}
    return {"id": nid, "type": ntype, "label": label, "synonyms": [], "props": props}


def _edge(i, s, t, rel):
    return {"id": f"e_{i:06d}", "source": s, "target": t, "relation": rel, "evidence_class": "observed",
            "confidence": 0.5, "confidence_basis": {}, "status": "supported", "sources": [],
            "supporting": [], "contradicting": [], "attributes": {}}


def make_store(with_index=True, embedder="fake", built_at=BUILT_AT):
    nodes = [
        _node("HP:FUNGAL", "Phenotype", "Placeholder recurrent fungal infections", ic=3.0),
        _node("HP:ORAL", "Phenotype", "Placeholder oral candidiasis", ic=5.0),
        _node("HP:THYROID", "Phenotype", "Placeholder hypothyroidism", ic=3.0),
        _node("HP:BLOOD", "Phenotype", "Placeholder increased red cell mass", ic=4.0),
        _node("D:BOTH", "Disease", "Placeholder disease with both"),
        _node("D:FUNGAL_ONLY", "Disease", "Placeholder disease fungal only"),
        _node("D:BLOOD", "Disease", "Placeholder blood disease"),
        _node("DRUG:A", "Drug", "Placeholdrinib"),
    ]
    edges = [
        _edge(1, "HP:ORAL", "HP:FUNGAL", "is_a"),          # oral candidiasis is a fungal infection
        _edge(2, "D:BOTH", "HP:ORAL", "has_phenotype"),     # reached through the child term
        _edge(3, "D:BOTH", "HP:THYROID", "has_phenotype"),
        _edge(4, "D:FUNGAL_ONLY", "HP:FUNGAL", "has_phenotype"),
        _edge(5, "D:BLOOD", "HP:BLOOD", "has_phenotype"),
    ]
    store = GraphStore({"meta": {"built_at": BUILT_AT}, "nodes": nodes, "edges": edges, "claims": [],
                        "clusters": []}, "test-graph.json")
    if with_index:
        ids = list(VECS)
        store.semantic = semantic.SemanticIndex({
            "graph_built_at": built_at, "model": "fake-embed", "dims": 4, "ids": ids,
            "vectors_int8_b64": pack_int8([truncate_normalise(VECS[i], 4) for i in ids]),
        })
    calls = []

    def fake(text, model):
        calls.append(text)
        return QUERIES.get(text.strip().lower())

    store.embedder = {"fake": fake, "none": None, "failing": lambda t, m: None}[embedder]
    store.calls = calls
    return store


def by_label(results):
    return {r["label"]: r for r in results}


def test_index_roundtrip_preserves_direction():
    idx = semantic.SemanticIndex({"graph_built_at": BUILT_AT, "model": "m", "dims": 4, "ids": ["a"],
                                  "vectors_int8_b64": pack_int8([truncate_normalise([3, 4, 0, 0], 4)])})
    (nid, sim), = idx.query([3, 4, 0, 0])
    assert nid == "a" and math.isclose(sim, 1.0, abs_tol=0.02)


def test_natural_language_query_reaches_symptoms_and_related_diseases():
    res = make_store().search("keeps getting thrush and thyroid trouble")
    labels = [r["label"] for r in res]
    got = by_label(res)
    assert got["Placeholder hypothyroidism"]["match_kind"] == "semantic"
    both = got["Placeholder disease with both"]
    assert both["match_kind"] == "related"
    # thyroid matched directly; fungal infections reached through the more specific child term
    assert "hypothyroidism" in both["resolution"] and "recurrent fungal infections" in both["resolution"]
    # the disease with both symptoms (one via the child term) outranks the one with a single symptom
    if "Placeholder disease fungal only" in got:
        assert labels.index("Placeholder disease with both") < labels.index("Placeholder disease fungal only")


def test_intent_words_favour_drugs():
    res = make_store().search("medicine for jak")
    assert res[0]["label"] == "Placeholdrinib" and res[0]["match_kind"] == "semantic"


def test_exact_name_match_outranks_semantic_lookalikes():
    store = make_store()
    QUERIES["placeholdrinib"] = [0.6, 0, 0, 0.8]
    try:
        res = store.search("Placeholdrinib")
    finally:
        del QUERIES["placeholdrinib"]
    assert res[0]["label"] == "Placeholdrinib" and res[0]["match_kind"] == "label"
    assert all(r["score"] <= 0.84 for r in res if r["match_kind"] == "semantic")


def test_falls_back_to_lexical_without_index_embedder_or_on_failure():
    for store in (make_store(with_index=False), make_store(embedder="none"), make_store(embedder="failing")):
        res = store.search("Placeholder hypothyroidism")
        assert res[0]["label"] == "Placeholder hypothyroidism" and res[0]["match_kind"] == "label"
        assert not any(r["match_kind"] == "semantic" for r in res)


def test_index_built_for_another_graph_is_not_used():
    store = make_store(built_at="1999-01-01T00:00:00+00:00")
    store.search("too many red cells")
    assert store.calls == []  # no embedding call at all


def test_embed_query_offline_makes_no_network_call(monkeypatch):
    monkeypatch.setenv("ATLAS_OFFLINE", "1")
    monkeypatch.setattr(semantic.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert semantic.embed_query("anything", "m") is None
