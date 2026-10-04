"""pipeline/graph/similarity.py on a synthetic in-memory graph (``mock:`` ids, ``MOCKGENE<n>``
symbols, "Mock " labels). Offline; no external identifier appears here.

The fixture graph is built so each general rule is exercised by one pair of diseases:

===========  ===========================================================================
pair         what it shows
===========  ===========================================================================
1 - 2        same direction, same origin, identical pathways and phenotypes: top score
1 - 3        opposite directions on the SAME gene: same-gene + same-pathway penalties
2 - 3        opposite directions on different genes sharing a pathway: pathway penalty only
4 - 5        same mechanism direction, germline vs somatic: origin penalty
1 - 6        origin unknown on one side: same_origin == unknown_origin_score (0.5)
7 - 8        one shared high-IC phenotype beats ...
9 - 10       ... three shared low-IC phenotypes
11 - 12      disjoint annotated terms that share an ancestor (closure through is_a)
13, 14, 16   no IC phenotype / nothing / sub-threshold association
===========  ===========================================================================
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pipeline.config import load_seeds
from pipeline.graph import similarity as sim
from pipeline.models import Edge, Graph

TODAY = "2026-10-03"
MOCK_SOURCE = {"kind": "db", "id": "mock:source:1", "date": TODAY}

CFG: dict[str, Any] = {
    "weightings": [
        {"name": "balanced", "w_m": 0.5, "w_p": 0.4, "w_c": 0.6},
        {"name": "phenotype_heavy", "w_m": 0.35, "w_p": 0.55, "w_c": 0.6},
    ],
    "similarity": {
        "min_score": 0.15,
        "top_k_per_disease": 10,
        "mechanism_components": ["same_direction", "same_origin", "shared_pathway"],
        "unknown_origin_score": 0.5,
        "phenotype_closure": "ancestors",
        "ic_missing_weight": 0.0,
        "gain_directions": ["GoF"],
        "loss_directions": ["LoF", "DN"],
        "association_exclude_labels": ["below_score_threshold"],
        "top_shared_phenotypes": 5,
    },
    "conflict": {"opposite_direction_same_gene": 1.0, "opposite_direction_same_pathway": 0.5, "germline_vs_somatic": 1.0},
    "candidates": {"require_mechanism_or_phenotype": True, "max_diseases": 400},
}
BALANCED = CFG["weightings"][0]


def approx(x: float):
    return pytest.approx(x, abs=1e-6)


# --------------------------------------------------------------------------------------
# synthetic graph
# --------------------------------------------------------------------------------------


def _n(node_id: str, node_type: str, label: str, **props: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "label": label, "synonyms": [], "props": props}


class _Edges:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, source: str, target: str, relation: str, **attributes: Any) -> None:
        self.rows.append(
            {
                "id": f"e_{len(self.rows) + 1:06d}",
                "source": source,
                "target": target,
                "relation": relation,
                "evidence_class": "observed",
                "confidence": 0.9,
                "confidence_basis": {"mock": True},
                "status": "supported",
                "sources": [dict(MOCK_SOURCE)],
                "supporting": [],
                "contradicting": [],
                "attributes": attributes,
            }
        )


def D(n: int) -> str:
    return f"mock:disease:{n}"


def P(name: str) -> str:
    return f"mock:phenotype:{name}"


def graph_dict() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        _n("mock:gene:1", "Gene", "MOCKGENE1", symbol="MOCKGENE1"),
        _n("mock:gene:2", "Gene", "MOCKGENE2", symbol="MOCKGENE2"),
        _n("mock:gene:3", "Gene", "MOCKGENE3", symbol="MOCKGENE3"),
        _n("mock:gene:4", "Gene", "MOCKGENE4", symbol="MOCKGENE4"),
        _n("mock:pathway:1", "Pathway", "Mock pathway one"),
        _n("mock:pathway:2", "Pathway", "Mock pathway two"),
        _n("mock:pathway:3", "Pathway", "Mock pathway three"),
        _n("mech:MOCKGENE1:LoF", "Mechanism", "Mock gene one loss of function"),
        _n("mech:MOCKGENE1:GoF", "Mechanism", "Mock gene one gain of function"),
        _n("mech:MOCKGENE2:LoF", "Mechanism", "Mock gene two loss of function"),
        _n("mech:MOCKGENE3:GoF:somatic", "Mechanism", "Mock gene three somatic gain of function"),
        _n("mech:MOCKGENE3:GoF", "Mechanism", "Mock gene three gain of function"),
        _n(P("root"), "Phenotype", "Mock phenotype root", ic=0.0),
        _n(P("mid"), "Phenotype", "Mock phenotype mid", ic=1.0),
        _n(P("leaf1"), "Phenotype", "Mock phenotype leaf one", ic=5.0),
        _n(P("leaf2"), "Phenotype", "Mock phenotype leaf two", ic=5.0),
        _n(P("low1"), "Phenotype", "Mock phenotype low one", ic=0.5),
        _n(P("low2"), "Phenotype", "Mock phenotype low two", ic=0.5),
        _n(P("low3"), "Phenotype", "Mock phenotype low three", ic=0.5),
        _n(P("high"), "Phenotype", "Mock phenotype high", ic=8.0),
        _n(P("noic"), "Phenotype", "Mock phenotype without ic"),
    ]
    for i in range(1, 17):
        nodes.append(_n(D(i), "Disease", f"Mock disease {i}"))

    e = _Edges()
    # pathways: genes 1 and 2 share both pathways, gene 3 has its own, gene 4 none
    for gene, pw in (("mock:gene:1", "mock:pathway:1"), ("mock:gene:1", "mock:pathway:2"), ("mock:gene:2", "mock:pathway:1"), ("mock:gene:2", "mock:pathway:2"), ("mock:gene:3", "mock:pathway:3")):
        e.add(gene, pw, "in_pathway")
    # phenotype ontology (child -> parent)
    for child, parent in (("mid", "root"), ("leaf1", "mid"), ("leaf2", "mid"), ("low1", "root"), ("low2", "root"), ("low3", "root"), ("high", "root"), ("noic", "root")):
        e.add(P(child), P(parent), "is_a")
    # a disease is_a edge must never count as a phenotype ancestor
    e.add(D(13), D(14), "is_a")

    # mechanisms (origin on the edge; disease 6 has none, disease 4 is somatic by edge and id)
    e.add(D(1), "mech:MOCKGENE1:LoF", "has_mechanism", origin="germline")
    e.add(D(2), "mech:MOCKGENE2:LoF", "has_mechanism", origin="germline")
    e.add(D(3), "mech:MOCKGENE1:GoF", "has_mechanism", origin="germline")
    e.add(D(4), "mech:MOCKGENE3:GoF:somatic", "has_mechanism", origin="somatic")
    e.add(D(5), "mech:MOCKGENE3:GoF", "has_mechanism", origin="germline")
    e.add(D(6), "mech:MOCKGENE2:LoF", "has_mechanism")

    # disease -> gene
    assoc = {1: "mock:gene:1", 2: "mock:gene:2", 3: "mock:gene:1", 4: "mock:gene:3", 5: "mock:gene:3", 6: "mock:gene:2"}
    for d, g in assoc.items():
        e.add(D(d), g, "associated_with", origin="germline" if d != 4 else "somatic")
    for d in (7, 8, 9, 10, 11, 12, 13, 14):
        e.add(D(d), "mock:gene:4", "associated_with")
    e.add(D(16), "mock:gene:1", "associated_with", labels=["below_score_threshold"])

    # disease -> phenotype
    phen = {
        1: ["leaf1", "low1"],
        2: ["leaf1", "low1"],
        3: ["leaf2", "low2"],
        4: ["high"],
        5: ["high"],
        6: ["leaf1"],
        7: ["high", "low1"],
        8: ["high", "low2"],
        9: ["low1", "low2", "low3", "leaf1"],
        10: ["low1", "low2", "low3", "leaf2"],
        11: ["leaf1"],
        12: ["leaf2"],
        13: ["noic"],
        16: ["high"],
    }
    for d, names in phen.items():
        for name in names:
            e.add(D(d), P(name), "has_phenotype")

    return {
        "meta": {
            "built_at": f"{TODAY}T00:00:00+00:00",
            "sources": [{"name": "mock", "version": "", "retrieved_at": TODAY, "license": "mock"}],
            "coverage": {},
        },
        "nodes": nodes,
        "edges": e.rows,
        "claims": [],
        "clusters": [],
    }


@pytest.fixture(scope="module")
def graph() -> Graph:
    return Graph.model_validate_json(json.dumps(graph_dict()), strict=True)


@pytest.fixture(scope="module")
def features(graph: Graph) -> sim.Features:
    return sim.build_features(graph, CFG)


@pytest.fixture(scope="module")
def drafts(graph: Graph) -> list[dict[str, Any]]:
    return sim.compute_similarity(graph, BALANCED, CFG, today=TODAY)


def pair(drafts: list[dict[str, Any]], a: int, b: int) -> dict[str, Any] | None:
    key = tuple(sorted((D(a), D(b))))
    for d in drafts:
        if (d["source"], d["target"]) == key:
            return d
    return None


# --------------------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------------------


def test_disease_mechanisms_decodes_direction_and_origin(graph: Graph) -> None:
    mechs = sim.disease_mechanisms(graph)
    assert set(mechs) == {D(i) for i in range(1, 7)}
    assert mechs[D(1)] == [sim.MechanismRef("mech:MOCKGENE1:LoF", "MOCKGENE1", "LoF", "germline")]
    assert mechs[D(4)][0].origin == "somatic" and mechs[D(4)][0].direction == "GoF"
    assert mechs[D(6)][0].origin is None, "no attributes.origin and no :somatic suffix -> unknown"


def test_phenotype_closure_follows_is_a_between_phenotypes_only(graph: Graph) -> None:
    closed = sim.disease_phenotype_closure(graph)
    assert closed[D(11)] == {P("leaf1"): 5.0, P("mid"): 1.0, P("root"): 0.0}
    assert closed[D(13)] == {P("noic"): 0.0, P("root"): 0.0}, "missing ic -> ic_missing_weight (0)"
    assert D(13) in closed and D(14) not in closed, "the Disease is_a edge is not a phenotype ancestor"
    flat = sim.disease_phenotype_closure(graph, closure="none")
    assert flat[D(11)] == {P("leaf1"): 5.0}
    weighted = sim.disease_phenotype_closure(graph, ic_missing_weight=0.25)
    assert weighted[D(13)][P("noic")] == 0.25
    with pytest.raises(ValueError):
        sim.disease_phenotype_closure(graph, closure="descendants")


def test_pathways_of_skips_excluded_associations(graph: Graph) -> None:
    with_all = sim.pathways_of(graph)
    assert with_all[D(1)] == {"mock:pathway:1", "mock:pathway:2"}
    assert with_all[D(4)] == {"mock:pathway:3"}
    assert D(7) not in with_all, "gene without in_pathway edges reaches no pathway"
    assert with_all[D(16)] == {"mock:pathway:1", "mock:pathway:2"}
    filtered = sim.pathways_of(graph, exclude_labels=["below_score_threshold"])
    assert D(16) not in filtered and filtered[D(1)] == with_all[D(1)]


def test_candidates_need_a_mechanism_or_an_ic_phenotype(graph: Graph) -> None:
    cands = sim.candidate_diseases(graph, CFG)
    assert cands == sorted(cands)
    assert D(13) not in cands, "only a phenotype without ic"
    assert D(14) not in cands, "no phenotype, no mechanism"
    assert D(15) not in cands, "isolated"
    assert set(cands) == {D(i) for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16)}
    everything = sim.candidate_diseases(graph, {**CFG, "candidates": {"require_mechanism_or_phenotype": False}})
    assert len(everything) == 16


def test_candidate_cap_keeps_mechanism_diseases_then_degree(graph: Graph) -> None:
    capped = sim.candidate_diseases(graph, {**CFG, "candidates": {"require_mechanism_or_phenotype": True, "max_diseases": 3}})
    assert capped == [D(1), D(2), D(3)], "mechanism diseases first, highest degree, ties by id"
    seven = sim.candidate_diseases(graph, {**CFG, "candidates": {"require_mechanism_or_phenotype": True, "max_diseases": 7}})
    assert len(seven) == 7 and {D(i) for i in range(1, 7)} <= set(seven)
    extra = (set(seven) - {D(i) for i in range(1, 7)}).pop()
    assert extra in (D(9), D(10)), "the highest-degree non-mechanism disease fills the last slot"


# --------------------------------------------------------------------------------------
# pair scores
# --------------------------------------------------------------------------------------


def test_identical_mechanism_and_phenotypes_score_high(features: sim.Features, drafts) -> None:
    basis = sim.score_pair(features, D(1), D(2), BALANCED, CFG)
    assert basis["mechanism_components"] == {"same_direction": 1.0, "same_origin": 1.0, "shared_pathway": 1.0}
    assert basis["mechanism_score"] == approx(1.0)
    assert basis["phenotype_overlap"] == approx(1.0)
    assert basis["conflict_penalty"] == 0.0
    assert basis["score"] == approx(0.9)
    draft = pair(drafts, 1, 2)
    assert draft is not None and draft["confidence"] == approx(0.45)
    assert draft == max(drafts, key=lambda d: d["confidence_basis"]["score"])


def test_opposite_direction_on_the_same_gene_is_penalised_to_a_negative_score(features, drafts) -> None:
    basis = sim.score_pair(features, D(1), D(3), BALANCED, CFG)
    assert basis["conflict_components"]["opposite_direction_same_gene"] == 1.0
    assert basis["conflict_components"]["opposite_direction_same_pathway"] == 1.0
    assert basis["conflict_components"]["germline_vs_somatic"] == 0.0
    assert basis["opposite_direction_genes"] == ["MOCKGENE1"]
    assert basis["conflict_penalty"] == approx(1.5)
    assert basis["score"] < 0
    assert pair(drafts, 1, 3) is None


def test_opposite_direction_on_different_genes_sharing_a_pathway_pays_only_the_pathway_penalty(features, drafts) -> None:
    basis = sim.score_pair(features, D(2), D(3), BALANCED, CFG)
    assert basis["conflict_components"] == {
        "opposite_direction_same_gene": 0.0,
        "opposite_direction_same_pathway": 1.0,
        "germline_vs_somatic": 0.0,
    }
    assert basis["conflict_penalty"] == approx(0.5)
    assert basis["score"] < CFG["similarity"]["min_score"]
    assert pair(drafts, 2, 3) is None


def test_germline_vs_somatic_is_penalised(features, drafts, graph: Graph) -> None:
    basis = sim.score_pair(features, D(4), D(5), BALANCED, CFG)
    assert basis["mechanism_components"] == {"same_direction": 1.0, "same_origin": 0.0, "shared_pathway": 1.0}
    assert basis["conflict_components"]["germline_vs_somatic"] == 1.0
    assert basis["conflict_penalty"] == approx(1.0)
    assert basis["score"] == approx(0.5 * (2 / 3) + 0.4 * 1.0 - 0.6 * 1.0)
    assert pair(drafts, 4, 5) is None, "falls below min_score once penalised"

    off = {**CFG, "conflict": {k: 0.0 for k in CFG["conflict"]}}
    unpenalised = sim.score_pair(features, D(4), D(5), BALANCED, off)
    assert unpenalised["conflict_penalty"] == 0.0
    assert unpenalised["score"] == approx(0.5 * (2 / 3) + 0.4)
    assert unpenalised["conflict_weights"] == {k: 0.0 for k in CFG["conflict"]}
    assert pair(sim.compute_similarity(graph, BALANCED, off, today=TODAY), 4, 5) is not None


def test_repulsions_are_the_negative_scores_and_vanish_without_penalties(graph: Graph, features) -> None:
    """compute_repulsions lists exactly the candidate pairs with score < 0 (weight = -score), sorted,
    source < target; with every conflict weight at 0 nothing scores below 0."""
    rows = sim.compute_repulsions(graph, BALANCED, CFG, features=features)
    assert rows, "the fixture has penalised pairs"
    pairs = {(r["source"], r["target"]) for r in rows}
    assert (D(1), D(3)) in pairs, "opposite direction on the same gene repels"
    assert (D(1), D(2)) not in pairs, "an agreeing pair never repels"
    # germline against somatic with identical phenotypes: penalised below min_score but still net
    # positive (0.5 * 2/3 + 0.4 - 0.6 > 0), so the pair loses its edge without repelling
    assert (D(4), D(5)) not in pairs
    for r in rows:
        assert r["source"] < r["target"]
        assert r["score"] < 0 and r["weight"] == approx(-r["score"])
        assert r["conflict_penalty"] > 0 and r["weighting"] == "balanced"
        assert r["score"] == approx(sim.score_pair(features, r["source"], r["target"], BALANCED, CFG)["score"])
    assert rows == sorted(rows, key=lambda r: (r["source"], r["target"]))
    # the complete set: every candidate pair below zero, none above
    negatives = {
        (a, b)
        for i, a in enumerate(features.candidates)
        for b in features.candidates[i + 1 :]
        if sim.score_pair(features, a, b, BALANCED, CFG)["score"] < 0
    }
    assert pairs == negatives
    # no repulsion row is also an edge
    edge_pairs = {(d["source"], d["target"]) for d in sim.compute_similarity(graph, BALANCED, CFG, today=TODAY, features=features)}
    assert not (pairs & edge_pairs)
    off = {**CFG, "conflict": {k: 0.0 for k in CFG["conflict"]}}
    assert sim.compute_repulsions(graph, BALANCED, off, features=features) == []
    # the same without a precomputed Features object
    assert sim.compute_repulsions(graph, BALANCED, CFG) == rows


def test_unknown_origin_scores_half(features) -> None:
    basis = sim.score_pair(features, D(1), D(6), BALANCED, CFG)
    assert basis["mechanism_components"]["same_origin"] == 0.5
    assert basis["conflict_components"]["germline_vs_somatic"] == 0.0, "unknown is not a conflict"
    assert basis["b"]["origins"] == [] and basis["a"]["origins"] == ["germline"]
    custom = {**CFG, "similarity": {**CFG["similarity"], "unknown_origin_score": 0.25}}
    assert sim.score_pair(features, D(1), D(6), BALANCED, custom)["mechanism_components"]["same_origin"] == 0.25


def test_ic_weighting_one_rare_phenotype_outweighs_several_common_ones(features) -> None:
    rare = sim.score_pair(features, D(7), D(8), BALANCED, CFG)
    common = sim.score_pair(features, D(9), D(10), BALANCED, CFG)
    assert rare["n_shared_phenotypes"] == 2 and common["n_shared_phenotypes"] == 5
    assert rare["phenotype_overlap"] == approx(8.0 / 9.0)
    assert common["phenotype_overlap"] == approx(2.5 / 12.5)
    assert rare["phenotype_overlap"] > common["phenotype_overlap"]
    assert rare["top_shared_phenotypes"][0] == {"id": P("high"), "label": "Mock phenotype high", "ic": 8.0}


def test_ancestors_count_through_is_a(graph: Graph, features) -> None:
    closed = sim.score_pair(features, D(11), D(12), BALANCED, CFG)
    assert closed["phenotype_overlap"] == approx(1.0 / 11.0)
    assert [p["id"] for p in closed["top_shared_phenotypes"]] == [P("mid"), P("root")]
    flat_cfg = {**CFG, "similarity": {**CFG["similarity"], "phenotype_closure": "none"}}
    flat = sim.score_pair(sim.build_features(graph, flat_cfg), D(11), D(12), BALANCED, flat_cfg)
    assert flat["phenotype_overlap"] == 0.0 and flat["n_shared_phenotypes"] == 0


def test_weighting_changes_the_score_and_is_recorded(features) -> None:
    heavy = CFG["weightings"][1]
    a = sim.score_pair(features, D(7), D(8), BALANCED, CFG)
    b = sim.score_pair(features, D(7), D(8), heavy, CFG)
    assert a["weighting"] == {"name": "balanced", "w_m": 0.5, "w_p": 0.4, "w_c": 0.6}
    assert b["weighting"]["name"] == "phenotype_heavy"
    assert b["score"] == approx(0.35 * b["mechanism_score"] + 0.55 * b["phenotype_overlap"])
    assert b["score"] > a["score"], "a phenotype-driven pair gains under the phenotype-heavy weighting"
    with pytest.raises(ValueError):
        sim.score_pair(features, D(7), D(8), {"name": "", "w_m": 1, "w_p": 1, "w_c": 1}, CFG)


def test_mechanism_components_config_selects_the_mean(features) -> None:
    only_direction = {**CFG, "similarity": {**CFG["similarity"], "mechanism_components": ["same_direction"]}}
    basis = sim.score_pair(features, D(4), D(5), BALANCED, only_direction)
    assert basis["mechanism_score"] == 1.0 and basis["mechanism_components_used"] == ["same_direction"]


# --------------------------------------------------------------------------------------
# filtering, determinism, contract
# --------------------------------------------------------------------------------------


def test_min_score_and_top_k_filtering(graph: Graph) -> None:
    n_cands = len(sim.candidate_diseases(graph, CFG))
    all_pairs = n_cands * (n_cands - 1) // 2
    unfiltered = {**CFG, "similarity": {**CFG["similarity"], "min_score": -10.0, "top_k_per_disease": 0}}
    everything = sim.compute_similarity(graph, BALANCED, unfiltered, today=TODAY)
    assert len(everything) == all_pairs, "no min_score, no top-k: every candidate pair"

    strict = {**CFG, "similarity": {**CFG["similarity"], "min_score": 0.85}}
    assert [(d["source"], d["target"]) for d in sim.compute_similarity(graph, BALANCED, strict, today=TODAY)] == [(D(1), D(2))]

    k = 2
    topk = {**CFG, "similarity": {**CFG["similarity"], "min_score": -10.0, "top_k_per_disease": k}}
    kept = sim.compute_similarity(graph, BALANCED, topk, today=TODAY)
    assert 0 < len(kept) < all_pairs
    best: dict[str, list[tuple[float, str]]] = {}
    for d in everything:
        s = d["confidence_basis"]["score"]
        best.setdefault(d["source"], []).append((-s, d["target"]))
        best.setdefault(d["target"], []).append((-s, d["source"]))
    top = {node: {other for _s, other in sorted(lst)[:k]} for node, lst in best.items()}
    for d in kept:
        assert d["target"] in top[d["source"]] or d["source"] in top[d["target"]], "survivor must be in an endpoint's top-k"
    for d in everything:
        in_top = d["target"] in top[d["source"]] or d["source"] in top[d["target"]]
        assert in_top == (pair(kept, int(d["source"].rsplit(":", 1)[1]), int(d["target"].rsplit(":", 1)[1])) is not None)


def test_drafts_are_sorted_and_source_precedes_target(drafts) -> None:
    keys = [(d["source"], d["target"]) for d in drafts]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    assert all(s < t for s, t in keys)


def test_determinism_under_reordered_input(graph: Graph, drafts) -> None:
    again = sim.compute_similarity(graph, BALANCED, CFG, today=TODAY)
    assert json.dumps(again, sort_keys=True) == json.dumps(drafts, sort_keys=True)
    raw = graph_dict()
    raw["nodes"].reverse()
    raw["edges"].reverse()
    reordered = Graph.model_validate(raw)
    assert json.dumps(sim.compute_similarity(reordered, BALANCED, CFG, today=TODAY), sort_keys=True) == json.dumps(drafts, sort_keys=True)


def test_every_draft_validates_as_an_inferred_edge(graph: Graph, drafts) -> None:
    assert drafts, "the fixture must produce at least one edge"
    required = {
        "weighting",
        "score",
        "mechanism_score",
        "mechanism_components",
        "phenotype_overlap",
        "conflict_penalty",
        "conflict_components",
        "conflict_weights",
        "n_shared_phenotypes",
        "top_shared_phenotypes",
    }
    edges = []
    for i, d in enumerate(drafts, start=1):
        assert "id" not in d
        edge = Edge.model_validate({**d, "id": f"e_{900000 + i:06d}"})
        edges.append(edge)
        assert edge.relation == "similar_to" and edge.evidence_class == "inferred" and edge.status == "supported"
        assert 0.0 <= edge.confidence <= 0.5
        assert edge.confidence == approx(0.5 * min(max(d["confidence_basis"]["score"], 0.0), 1.0))
        assert required <= set(edge.confidence_basis)
        assert set(edge.confidence_basis["mechanism_components"]) == {"same_direction", "same_origin", "shared_pathway"}
        assert set(edge.confidence_basis["conflict_components"]) == set(CFG["conflict"])
        assert edge.confidence_basis["weighting"] == BALANCED
        assert len(edge.confidence_basis["top_shared_phenotypes"]) <= 5
        assert edge.sources[0].kind == "db" and edge.sources[0].id == "similarity:balanced" and edge.sources[0].date == TODAY
        assert edge.attributes["labels"] == ["similarity"]
        assert edge.supporting == [] and edge.contradicting == []
    merged = graph.model_dump()
    merged["edges"] += [e.model_dump() for e in edges]
    Graph.model_validate(merged)  # referential integrity holds with the drafts appended
    Graph.model_validate_json(json.dumps(merged), strict=True)


def test_today_is_required() -> None:
    with pytest.raises(ValueError):
        sim.compute_similarity(Graph.model_validate(graph_dict()), BALANCED, CFG, today="")


# --------------------------------------------------------------------------------------
# the seed file
# --------------------------------------------------------------------------------------


def test_logic_yaml_matches_the_shared_design(graph: Graph) -> None:
    seeds = load_seeds("logic")
    names = [w["name"] for w in seeds["weightings"]]
    assert names[0] == "balanced" and set(names) == {"balanced", "phenotype_heavy", "mechanism_heavy"}
    for w in seeds["weightings"]:
        assert set(w) == {"name", "w_m", "w_p", "w_c"}
    s = sim.similarity_cfg(seeds)
    assert s["min_score"] == 0.2 and s["top_k_per_disease"] == 10 and s["phenotype_closure"] == "ancestors"
    assert s["mechanism_components"] == ["same_direction", "same_origin", "shared_pathway"]
    # an unknown origin is no evidence of a shared one; the pathway-only baseline (w_m / 3) stays below min_score
    assert s["unknown_origin_score"] == 0.0
    assert s["min_score"] > seeds["weightings"][0]["w_m"] / 3
    assert seeds["clustering"]["repulsion_weight"] == 1.0 and seeds["clustering"]["n_iterations"] == -1
    assert sim.conflict_cfg(seeds) == {"opposite_direction_same_gene": 1.0, "opposite_direction_same_pathway": 0.5, "germline_vs_somatic": 1.0}
    assert sim.candidates_cfg(seeds) == {"require_mechanism_or_phenotype": True, "max_diseases": 400}
    assert seeds["clustering"]["method"] == "leiden" and seeds["clustering"]["seed"] == 7
    assert seeds["rules"]["direction_mismatch"]["loss_directions"] == ["LoF", "DN"]
    assert seeds["rules"]["direction_match_candidate"]["gain_directions"] == ["GoF"]
    assert seeds["rules"]["existing_treatment_relations"] == ["treats", "indicated_for", "contraindicated_for"]
    # the yaml names every similarity key the module reads, with the module's default values
    assert set(seeds["similarity"]) == set(sim.DEFAULT_SIMILARITY) and s == sim.DEFAULT_SIMILARITY
    # the file drives compute_similarity directly (the test CFG keeps the pre-tuning 0.15 / 0.5
    # values so the fixture pairs are exercised; align those two keys for the comparison)
    out = sim.compute_similarity(graph, seeds["weightings"][0], seeds, today=TODAY)
    aligned = {**CFG, "similarity": {**CFG["similarity"], "min_score": s["min_score"], "unknown_origin_score": s["unknown_origin_score"]}}
    assert json.dumps(out, sort_keys=True) == json.dumps(sim.compute_similarity(graph, BALANCED, aligned, today=TODAY), sort_keys=True)
    assert out, "the yaml still yields edges on the fixture"


def test_section_access_accepts_a_bare_section() -> None:
    assert sim.similarity_cfg(CFG["similarity"])["min_score"] == 0.15
    assert sim.similarity_cfg(None) == sim.DEFAULT_SIMILARITY
    assert sim.conflict_cfg({"conflict": {"germline_vs_somatic": 0.0}})["germline_vs_somatic"] == 0.0
    assert sim.conflict_cfg({"conflict": {"germline_vs_somatic": 0.0}})["opposite_direction_same_gene"] == 1.0
    # a null in the yaml keeps the default; 0 is the documented way to disable the top-k cap
    assert sim.similarity_cfg({"similarity": {"top_k_per_disease": None}})["top_k_per_disease"] == 10
    assert sim.similarity_cfg({"similarity": {"top_k_per_disease": 0}})["top_k_per_disease"] == 0
    # the unknown-origin default lives in one place (DEFAULT_SIMILARITY, mirrored by the yaml)
    assert sim.similarity_cfg({})["unknown_origin_score"] == 0.0
    assert sim.similarity_cfg({"similarity": {"unknown_origin_score": None}})["unknown_origin_score"] == 0.0


def test_unknown_origin_default_is_zero_in_the_score(features) -> None:
    """score_pair reads unknown_origin_score only from the merged config: without the test CFG's
    0.5 the pair with one unknown origin gets the documented default, 0."""
    basis = sim.score_pair(features, D(1), D(6), BALANCED, {**CFG, "similarity": {k: v for k, v in CFG["similarity"].items() if k != "unknown_origin_score"}})
    assert basis["mechanism_components"]["same_origin"] == 0.0
