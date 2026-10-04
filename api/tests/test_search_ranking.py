"""Search relevance: entities before papers, bounded result counts.

User feedback: searching a gene floods the dropdown with papers whose titles
contain the gene name, and the list is too long to sift. Entities (genes,
diseases, drugs...) must outrank Paper/Investigator matches of equal string
quality, free-text noise needs a real threshold, and results are capped.
"""

from __future__ import annotations

import json

from api.graph_store import GraphStore


def _store(nodes):
    return GraphStore(
        {"meta": {}, "nodes": nodes, "edges": [], "claims": [], "clusters": []},
        "test.mock.json",
    )


def _node(i, type_, label, synonyms=()):
    return {"id": f"n:{i}", "type": type_, "label": label, "synonyms": list(synonyms), "props": {}}


def test_entities_rank_before_papers_of_equal_match_quality():
    store = _store(
        [
            _node(1, "Paper", "STAT1 mutations in chronic disease: a review"),
            _node(2, "Paper", "Another STAT1 paper title"),
            _node(3, "Gene", "STAT1"),
            _node(4, "Variant", "NM_007315.4(STAT1):c.820C>T"),
            _node(5, "Disease", "STAT1 deficiency"),
        ]
    )
    results = store.search("STAT1")
    types = [r["type"] for r in results]
    # gene exact match first; all entity types before any Paper
    assert results[0]["id"] == "n:3"
    assert types.index("Disease") < types.index("Paper")
    assert types.index("Variant") < types.index("Paper")


def test_paper_and_investigator_results_are_capped():
    nodes = [_node(i, "Paper", f"STAT1 paper number {i}") for i in range(1, 12)]
    nodes += [_node(100 + i, "Investigator", f"STAT1 Lab Member {i}") for i in range(1, 8)]
    nodes.append(_node(200, "Gene", "STAT1"))
    store = _store(nodes)
    results = store.search("STAT1")
    assert sum(1 for r in results if r["type"] == "Paper") <= 3
    assert sum(1 for r in results if r["type"] == "Investigator") <= 3


def test_total_results_are_capped():
    nodes = [_node(i, "Phenotype", f"Recurrent infection type {i}") for i in range(1, 60)]
    store = _store(nodes)
    assert len(store.search("recurrent infection")) <= 24


def test_one_entity_class_cannot_flood_out_the_rest():
    # 30 ClinVar-style variants must not crowd out the disease and drug rows
    nodes = [_node(i, "Variant", f"NM_007315.4(STAT1):c.{i}G>T") for i in range(1, 31)]
    nodes.append(_node(100, "Gene", "STAT1"))
    nodes.append(_node(101, "Disease", "STAT1 deficiency"))
    nodes.append(_node(102, "Drug", "STAT1-pathway inhibitor"))
    results = _store(nodes).search("STAT1")
    types = {r["type"] for r in results}
    assert {"Gene", "Disease", "Drug"} <= types
    assert sum(1 for r in results if r["type"] == "Variant") <= 5


def test_free_text_needs_meaningful_overlap():
    # one shared generic token out of three should not match
    store = _store([_node(1, "Disease", "immune syndrome of the skin")])
    assert store.search("bone marrow syndrome") == []
