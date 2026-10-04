"""pipeline/graph/cluster.py on a synthetic six-disease graph with an injected similarity function.

Everything is a placeholder (``mock:`` ids, "Mock " labels, ``MOCKGENE<n>`` symbols); no external
identifier appears here. The fake ``sim_fn`` returns hand-made ``similar_to`` drafts, so these
tests do not depend on ``pipeline/graph/similarity.py``; the real similarity module is only
touched through the lazy-import test, which substitutes a stub module.

Graph shape: diseases 1-3 share a mechanism direction, a gene and a pathway (community A),
diseases 4-5 share another (community B), disease 6 is isolated (somatic mechanism, no similarity
edge). Phenotypes carry ``props.ic`` (one deliberately without it).
"""

from __future__ import annotations

import copy
import random
import sys
import types
from typing import Any

import pytest

from pipeline.graph import cluster as cl
from pipeline.models import Cluster, Graph, NodeType

TODAY = "2026-10-03"
_SOURCE = {"kind": "db", "id": "mock:source:1", "date": TODAY}

WEIGHTINGS = [
    {"name": "balanced", "w_m": 0.5, "w_p": 0.4, "w_c": 0.6},
    {"name": "phenotype_heavy", "w_m": 0.35, "w_p": 0.55, "w_c": 0.6},
    {"name": "mechanism_heavy", "w_m": 0.6, "w_p": 0.25, "w_c": 0.5},
]
CLUSTERING = {
    "method": "leiden",
    "objective": "modularity",
    "resolution": 1.0,
    "seed": 7,
    "robust_min_jaccard": 0.8,
    "singleton_clusters": True,
}
LOGIC_CFG = {"weightings": WEIGHTINGS, "clustering": CLUSTERING, "similarity": {"min_score": 0.15}}

D = {n: f"mock:disease:{n}" for n in range(1, 7)}
A = [D[1], D[2], D[3]]
B = [D[4], D[5]]
ISOLATED = D[6]


# --------------------------------------------------------------------------------------
# synthetic graph
# --------------------------------------------------------------------------------------


def _n(node_id: str, node_type: str, label: str, **props: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "label": label, "synonyms": [], "props": props}


def _e(edge_id: str, source: str, target: str, relation: str, **attributes: Any) -> dict[str, Any]:
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "relation": relation,
        "evidence_class": "observed",
        "confidence": 0.9,
        "confidence_basis": {"mock": True},
        "status": "supported",
        "sources": [dict(_SOURCE)],
        "supporting": [],
        "contradicting": [],
        "attributes": attributes,
    }


def graph_dict() -> dict[str, Any]:
    nodes = [
        _n("mock:gene:1", "Gene", "MOCKGENE1", symbol="MOCKGENE1"),
        _n("mock:gene:2", "Gene", "MOCKGENE2", symbol="MOCKGENE2"),
        _n("mock:gene:3", "Gene", "MOCKGENE3", symbol="MOCKGENE3"),
        _n("mech:MOCKGENE1:GoF", "Mechanism", "MOCKGENE1 gain of function", direction="GoF"),
        _n("mech:MOCKGENE2:LoF", "Mechanism", "MOCKGENE2 loss of function", direction="LoF"),
        _n("mech:MOCKGENE3:GoF:somatic", "Mechanism", "MOCKGENE3 somatic gain of function", direction="GoF", origin="somatic"),
        _n(D[1], "Disease", "Mock disease one"),
        _n(D[2], "Disease", "Mock disease two"),
        _n(D[3], "Disease", "Mock disease three"),
        _n(D[4], "Disease", "Mock disease four"),
        _n(D[5], "Disease", "Mock disease five"),
        _n(D[6], "Disease", "Mock disease six"),
        _n("mock:phenotype:1", "Phenotype", "Mock phenotype A", ic=5.0),
        _n("mock:phenotype:2", "Phenotype", "Mock phenotype B", ic=3.0),
        _n("mock:phenotype:3", "Phenotype", "Mock phenotype C", ic=4.0),
        _n("mock:phenotype:4", "Phenotype", "Mock phenotype D"),  # no ic on purpose
        _n("mock:phenotype:5", "Phenotype", "Mock phenotype E", ic=2.0),
        _n("mock:pathway:1", "Pathway", "Mock pathway one"),
        _n("mock:pathway:2", "Pathway", "Mock pathway two"),
    ]
    edges = [
        # Disease -> Mechanism (origin on the edge; the somatic one also carries the :somatic suffix)
        _e("e_000001", D[1], "mech:MOCKGENE1:GoF", "has_mechanism", origin="germline"),
        _e("e_000002", D[2], "mech:MOCKGENE1:GoF", "has_mechanism", origin="germline"),
        _e("e_000003", D[3], "mech:MOCKGENE1:GoF", "has_mechanism"),  # origin unknown
        _e("e_000004", D[4], "mech:MOCKGENE2:LoF", "has_mechanism", origin="germline"),
        _e("e_000005", D[5], "mech:MOCKGENE2:LoF", "has_mechanism", origin="germline"),
        _e("e_000006", D[6], "mech:MOCKGENE3:GoF:somatic", "has_mechanism", origin="somatic"),
        # Disease -> Gene
        _e("e_000007", D[1], "mock:gene:1", "associated_with"),
        _e("e_000008", D[2], "mock:gene:1", "associated_with"),
        _e("e_000009", D[3], "mock:gene:1", "associated_with"),
        _e("e_000010", D[4], "mock:gene:2", "associated_with"),
        _e("e_000011", D[5], "mock:gene:2", "associated_with"),
        _e("e_000012", D[6], "mock:gene:3", "associated_with"),
        # Gene -> Pathway
        _e("e_000013", "mock:gene:1", "mock:pathway:1", "in_pathway"),
        _e("e_000014", "mock:gene:2", "mock:pathway:1", "in_pathway"),
        _e("e_000015", "mock:gene:2", "mock:pathway:2", "in_pathway"),
        _e("e_000016", "mock:gene:3", "mock:pathway:2", "in_pathway"),
        # Disease -> Phenotype
        _e("e_000017", D[1], "mock:phenotype:1", "has_phenotype"),
        _e("e_000018", D[2], "mock:phenotype:1", "has_phenotype"),
        _e("e_000019", D[3], "mock:phenotype:1", "has_phenotype"),
        _e("e_000020", D[1], "mock:phenotype:2", "has_phenotype"),
        _e("e_000021", D[2], "mock:phenotype:2", "has_phenotype"),
        _e("e_000022", D[3], "mock:phenotype:5", "has_phenotype"),
        _e("e_000023", D[4], "mock:phenotype:3", "has_phenotype"),
        _e("e_000024", D[5], "mock:phenotype:3", "has_phenotype"),
        _e("e_000025", D[6], "mock:phenotype:4", "has_phenotype"),
    ]
    return {
        "meta": {
            "built_at": f"{TODAY}T00:00:00+00:00",
            "sources": [{"name": "mock", "version": "", "retrieved_at": TODAY, "license": "mock"}],
            "coverage": {},
        },
        "nodes": nodes,
        "edges": edges,
        "claims": [],
        "clusters": [],
    }


@pytest.fixture(scope="module")
def graph() -> Graph:
    return Graph.model_validate(graph_dict())


# --------------------------------------------------------------------------------------
# fake similarity functions (the shape pipeline.graph.similarity.compute_similarity emits)
# --------------------------------------------------------------------------------------


def sim_draft(a: str, b: str, score: float, weighting: str = "balanced") -> dict[str, Any]:
    source, target = sorted((a, b))
    return {
        "source": source,
        "target": target,
        "relation": "similar_to",
        "evidence_class": "inferred",
        "confidence": 0.5 * max(0.0, min(score, 1.0)),
        "confidence_basis": {"weighting": weighting, "score": score},
        "status": "supported",
        "sources": [{"kind": "db", "id": "similarity:" + weighting, "date": TODAY}],
        "supporting": [],
        "contradicting": [],
        "attributes": {"labels": ["similarity"]},
    }


AGREEING_EDGES = [(D[1], D[2], 0.9), (D[1], D[3], 0.8), (D[2], D[3], 0.85), (D[4], D[5], 0.8)]


def sim_agree(graph: Graph, weighting: dict[str, Any], cfg: Any, *, today: str) -> list[dict[str, Any]]:
    """Every weighting sees the same two communities."""
    assert today == TODAY
    return [sim_draft(a, b, s, weighting.get("name", "unnamed")) for a, b, s in AGREEING_EDGES]


def sim_disagree(graph: Graph, weighting: dict[str, Any], cfg: Any, *, today: str) -> list[dict[str, Any]]:
    """``phenotype_heavy`` moves disease three from community A to community B."""
    if weighting.get("name") != "phenotype_heavy":
        return sim_agree(graph, weighting, cfg, today=today)
    moved = [(D[1], D[2], 0.9), (D[3], D[4], 0.9), (D[3], D[5], 0.9), (D[4], D[5], 0.8)]
    return [sim_draft(a, b, s, weighting["name"]) for a, b, s in moved]


def members_of(clusters: list[dict[str, Any]]) -> list[list[str]]:
    return [c["members"] for c in clusters]


# --------------------------------------------------------------------------------------
# leiden_partition
# --------------------------------------------------------------------------------------


def test_leiden_partition_finds_two_communities_and_a_singleton():
    part = cl.leiden_partition(list(D.values()), sim_agree(None, WEIGHTINGS[0], None, today=TODAY), CLUSTERING)
    assert set(part) == set(D.values())
    assert len({part[d] for d in A}) == 1
    assert len({part[d] for d in B}) == 1
    assert part[D[1]] != part[D[4]] != part[ISOLATED] != part[D[1]]
    # labels follow (size desc, smallest member id): A -> 0, B -> 1, isolated -> 2
    assert part[D[1]] == 0 and part[D[4]] == 1 and part[ISOLATED] == 2


def test_leiden_partition_singletons_off_drops_edgeless_diseases():
    cfg = {**CLUSTERING, "singleton_clusters": False}
    part = cl.leiden_partition(list(D.values()), sim_agree(None, WEIGHTINGS[0], None, today=TODAY), cfg)
    assert ISOLATED not in part
    assert set(part) == set(A) | set(B)


def test_leiden_partition_ignores_foreign_endpoints_self_loops_zero_weights_and_other_relations():
    drafts = sim_agree(None, WEIGHTINGS[0], None, today=TODAY)
    drafts += [
        sim_draft(D[1], "mock:disease:99", 0.9),  # endpoint outside the disease set
        {**sim_draft(D[4], D[5], 0.9), "source": D[4], "target": D[4]},  # self-loop
        sim_draft(D[1], D[4], 0.0),  # zero weight: must not join A and B
        sim_draft(D[2], D[5], -0.5),  # negative score clipped to 0
        {**sim_draft(D[3], D[4], 0.9), "relation": "treats"},  # not a similarity edge
    ]
    part = cl.leiden_partition(list(D.values()), drafts, CLUSTERING)
    assert "mock:disease:99" not in part
    assert len({part[d] for d in A}) == 1 and len({part[d] for d in B}) == 1
    assert part[D[1]] != part[D[4]]


def test_leiden_partition_empty_inputs():
    assert cl.leiden_partition([], [], CLUSTERING) == {}
    assert cl.leiden_partition([D[1], D[2]], [], CLUSTERING) == {D[1]: 0, D[2]: 1}
    assert cl.leiden_partition([D[1], D[2]], [], {**CLUSTERING, "singleton_clusters": False}) == {}


def test_leiden_partition_is_deterministic_on_a_planted_partition():
    """Thirty diseases in three planted communities with noisy edges, fixed RNG: identical runs."""
    rng = random.Random(0)
    ids = [f"mock:disease:{n}" for n in range(1, 31)]
    planted = {d: i % 3 for i, d in enumerate(ids)}
    drafts = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            same = planted[a] == planted[b]
            if rng.random() < (0.8 if same else 0.05):
                drafts.append(sim_draft(a, b, rng.uniform(0.6, 1.0) if same else rng.uniform(0.15, 0.3)))
    first = cl.leiden_partition(ids, drafts, CLUSTERING)
    second = cl.leiden_partition(ids, copy.deepcopy(drafts), CLUSTERING)
    assert first == second
    assert len(set(first.values())) == 3
    for a in ids:
        for b in ids:
            assert (first[a] == first[b]) == (planted[a] == planted[b])


def test_repulsions_split_what_the_positive_edges_alone_would_merge():
    """Four diseases, every pair but (1, 4) attracting strongly: modularity alone keeps them in one
    community; a repulsion between 1 and 4 (the signed layer) separates disease 4."""
    ids = [D[1], D[2], D[3], D[4]]
    pos = [sim_draft(a, b, 1.0) for a, b in [(D[1], D[2]), (D[3], D[4]), (D[2], D[3]), (D[1], D[3]), (D[2], D[4])]]
    neg = [{"source": D[1], "target": D[4], "weight": 3.0}]
    assert len(set(cl.leiden_partition(ids, pos, CLUSTERING).values())) == 1
    signed = cl.leiden_partition(ids, pos, CLUSTERING, neg)
    assert signed[D[1]] != signed[D[4]]
    assert signed[D[1]] == signed[D[2]] == signed[D[3]]
    # repulsion_weight 0 switches the layer off; a repulsion row with no weight uses -score
    assert cl.leiden_partition(ids, pos, {**CLUSTERING, "repulsion_weight": 0.0}, neg) == cl.leiden_partition(ids, pos, CLUSTERING)
    assert cl.leiden_partition(ids, pos, CLUSTERING, [{"source": D[1], "target": D[4], "score": -3.0}]) == signed
    # deterministic, and rows outside the vertex set / self-loops / non-positive weights are ignored
    assert cl.leiden_partition(ids, pos, CLUSTERING, neg) == signed
    noise = [{"source": D[1], "target": "mock:disease:99", "weight": 5.0}, {"source": D[2], "target": D[2], "weight": 5.0}, {"source": D[2], "target": D[3], "weight": 0.0}]
    assert cl.leiden_partition(ids, pos, CLUSTERING, noise) == cl.leiden_partition(ids, pos, CLUSTERING)
    # a repulsion alone does not make a disease "clustered" when singletons are off
    only_neg = cl.leiden_partition(ids + [D[5]], pos, {**CLUSTERING, "singleton_clusters": False}, [{"source": D[5], "target": D[1], "weight": 1.0}])
    assert D[5] not in only_neg


def test_repulsion_weight_and_helpers():
    assert cl.repulsion_weight({"weight": 0.4}) == 0.4
    assert cl.repulsion_weight({"score": -0.25}) == 0.25
    assert cl.repulsion_weight({"score": 0.3}) == 0.0
    assert cl.clustering_config({})["repulsion_weight"] == cl.DEFAULT_CLUSTERING["repulsion_weight"] == 1.0
    assert cl.clustering_config({"repulsion_weight": "0.5"})["repulsion_weight"] == 0.5
    with pytest.raises(ValueError):
        cl.clustering_config({"repulsion_weight": -1})


def test_build_clusters_uses_the_repulsion_fn_and_reports_it(graph):
    """With the fake agreeing edges plus a repulsion between the two communities' members, the
    partition is unchanged (they were apart already) but ``why`` records the signed layer."""
    calls: list[str] = []

    def repel(g: Graph, weighting: dict[str, Any], cfg: Any, *, today: str) -> list[dict[str, Any]]:
        assert today == TODAY
        calls.append(weighting["name"])
        return [{"source": D[1], "target": D[4], "weight": 0.9, "score": -0.9}]

    clusters = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY, repulsion_fn=repel)
    assert calls == ["balanced", "phenotype_heavy", "mechanism_heavy"]
    assert members_of(clusters) == [A, B, [ISOLATED]]
    a = clusters[0]
    assert a["why"]["method"]["signed"] is True and a["why"]["method"]["repulsion_weight"] == 1.0
    assert a["why"]["method"]["repulsions"] == 1
    assert all(c["why"]["internal_repulsions"] == 0 for c in clusters)
    assert cl.cluster_report(clusters)["signed"] is True and cl.cluster_report(clusters)["internal_repulsions"] == 0

    # a repulsion inside a community is counted, and the layer pulls the pair apart when strong
    def repel_inside(g: Graph, weighting: dict[str, Any], cfg: Any, *, today: str) -> list[dict[str, Any]]:
        return [{"source": D[1], "target": D[2], "weight": 5.0}]

    split = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY, repulsion_fn=repel_inside)
    assert all(not ({D[1], D[2]} <= set(c["members"])) for c in split), "a strong repulsion separates the pair"
    off = cl.build_clusters(graph, WEIGHTINGS, {**LOGIC_CFG, "clustering": {**CLUSTERING, "repulsion_weight": 0.0}}, sim_agree, today=TODAY, repulsion_fn=repel_inside)
    assert members_of(off) == [A, B, [ISOLATED]]
    assert off[0]["why"]["internal_repulsions"] == 1 and off[0]["why"]["method"]["signed"] is False

    # without a repulsion_fn nothing is signed
    plain = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY)
    assert plain[0]["why"]["method"]["signed"] is False and plain[0]["why"]["method"]["repulsions"] == 0


def test_weight_is_twice_the_confidence_clipped_at_zero():
    assert cl.edge_weight(sim_draft(D[1], D[2], 0.7)) == pytest.approx(0.7)
    assert cl.edge_weight(sim_draft(D[1], D[2], -0.3)) == 0.0
    assert cl.edge_weight({"confidence_basis": {"score": 0.4}}) == pytest.approx(0.4)
    assert cl.edge_weight({"confidence_basis": {"score": -1}}) == 0.0


# --------------------------------------------------------------------------------------
# build_clusters
# --------------------------------------------------------------------------------------


def test_two_clusters_and_a_singleton_in_size_order(graph):
    clusters = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY)
    assert [c["id"] for c in clusters] == ["cl_001", "cl_002", "cl_003"]
    assert members_of(clusters) == [A, B, [ISOLATED]]
    sizes = [len(c["members"]) for c in clusters]
    assert sizes == sorted(sizes, reverse=True)


def test_members_are_disease_ids_only_and_form_a_partition(graph):
    clusters = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY)
    index = graph.node_index()
    seen: list[str] = []
    for c in clusters:
        assert c["members"] == sorted(c["members"])
        for m in c["members"]:
            assert index[m].type == NodeType.Disease
        seen += c["members"]
    assert len(seen) == len(set(seen)), "a disease appears in two clusters"
    assert set(seen) == {n.id for n in graph.nodes if n.type == NodeType.Disease}
    # the whole graph still validates with the drafts as its clusters (partition check in Graph)
    data = graph_dict()
    data["clusters"] = clusters
    assert len(Graph.model_validate(data).clusters) == 3


def test_drafts_validate_as_cluster_models(graph):
    clusters = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY)
    for draft in clusters:
        model = Cluster.model_validate(draft)
        assert model.id == draft["id"] and model.members and isinstance(model.robust, bool)


def test_robust_when_every_weighting_agrees(graph):
    clusters = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY)
    assert [c["robust"] for c in clusters] == [True, True, True]
    for c in clusters:
        assert c["why"]["robust_jaccard_min"] == 1.0
        assert c["why"]["robust_by_weighting"] == {"phenotype_heavy": 1.0, "mechanism_heavy": 1.0}
        assert c["why"]["robust_threshold"] == 0.8


def test_not_robust_when_one_weighting_moves_a_member(graph):
    clusters = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_disagree, today=TODAY)
    by_members = {tuple(c["members"]): c for c in clusters}
    assert set(by_members) == {tuple(A), tuple(B), (ISOLATED,)}  # the primary weighting still defines the clusters
    a, b, single = by_members[tuple(A)], by_members[tuple(B)], by_members[(ISOLATED,)]
    assert a["robust"] is False and b["robust"] is False and single["robust"] is True
    assert a["why"]["robust_by_weighting"]["phenotype_heavy"] == pytest.approx(2 / 3, abs=1e-3)
    assert a["why"]["robust_by_weighting"]["mechanism_heavy"] == 1.0
    assert a["why"]["robust_jaccard_min"] == pytest.approx(2 / 3, abs=1e-3)
    assert b["why"]["robust_by_weighting"]["phenotype_heavy"] == pytest.approx(2 / 3, abs=1e-3)
    assert single["why"]["robust_jaccard_min"] == 1.0


def test_robust_threshold_is_read_from_the_config(graph):
    cfg = {**LOGIC_CFG, "clustering": {**CLUSTERING, "robust_min_jaccard": 0.6}}
    clusters = cl.build_clusters(graph, WEIGHTINGS, cfg, sim_disagree, today=TODAY)
    assert all(c["robust"] for c in clusters)  # 2/3 >= 0.6


def test_single_weighting_is_vacuously_robust(graph):
    clusters = cl.build_clusters(graph, WEIGHTINGS[:1], LOGIC_CFG, sim_disagree, today=TODAY)
    assert all(c["robust"] for c in clusters)
    assert all(c["why"]["robust_by_weighting"] == {} and c["why"]["robust_jaccard_min"] == 1.0 for c in clusters)
    assert clusters[0]["why"]["weightings_used"] == ["balanced"]


def test_why_fields_come_from_the_graphs_own_edges(graph):
    clusters = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY)
    a, b, single = clusters

    assert a["why"]["size"] == 3 and b["why"]["size"] == 2 and single["why"]["size"] == 1
    assert a["why"]["weightings_used"] == ["balanced", "phenotype_heavy", "mechanism_heavy"]
    assert a["why"]["primary_weighting"] == "balanced"
    assert a["why"]["member_labels"] == ["Mock disease one", "Mock disease two", "Mock disease three"]
    assert a["why"]["internal_similarity_edges"] == 3 and b["why"]["internal_similarity_edges"] == 1

    # mechanism direction / origin from has_mechanism edges (origin: attributes.origin or :somatic)
    assert a["why"]["dominant_direction"] == "GoF" and a["why"]["direction_counts"] == {"GoF": 3}
    assert a["why"]["dominant_origin"] == "germline" and a["why"]["origin_counts"] == {"germline": 2}
    assert b["why"]["dominant_direction"] == "LoF" and b["why"]["dominant_origin"] == "germline"
    assert single["why"]["dominant_direction"] == "GoF" and single["why"]["dominant_origin"] == "somatic"

    # shared pathways through associated_with -> in_pathway (labels, most members first; a
    # singleton shares nothing, so its own pathways are not listed)
    assert a["why"]["shared_pathways"] == ["Mock pathway one"]
    assert b["why"]["shared_pathways"] == ["Mock pathway one", "Mock pathway two"]
    assert single["why"]["shared_pathways"] == []

    # top phenotypes from has_phenotype edges and props.ic: members first, then ic
    top = a["why"]["top_phenotypes"]
    assert [(p["id"], p["n_members"], p["ic"]) for p in top] == [
        ("mock:phenotype:1", 3, 5.0),
        ("mock:phenotype:2", 2, 3.0),
        ("mock:phenotype:5", 1, 2.0),
    ]
    assert top[0]["label"] == "Mock phenotype A"
    assert b["why"]["top_phenotypes"] == [{"id": "mock:phenotype:3", "label": "Mock phenotype C", "ic": 4.0, "n_members": 2}]
    assert single["why"]["top_phenotypes"] == [{"id": "mock:phenotype:4", "label": "Mock phenotype D", "ic": None, "n_members": 1}]

    method = a["why"]["method"]
    assert method["algorithm"] == "leiden" and method["objective"] == "modularity"
    assert method["seed"] == 7 and method["resolution"] == 1.0
    assert method["igraph"] and method["leidenalg"]


def test_top_phenotypes_and_shared_pathways_are_capped(graph):
    """A cluster whose members carry many phenotypes/pathways reports at most the documented caps."""
    data = graph_dict()
    for n in range(10, 30):
        data["nodes"].append(_n(f"mock:phenotype:{n}", "Phenotype", f"Mock phenotype {n}", ic=float(n)))
        data["edges"].append(_e(f"e_0001{n:02d}", D[1], f"mock:phenotype:{n}", "has_phenotype"))
        data["nodes"].append(_n(f"mock:pathway:{n}", "Pathway", f"Mock pathway {n}"))
        data["edges"].append(_e(f"e_0002{n:02d}", "mock:gene:1", f"mock:pathway:{n}", "in_pathway"))
    big = Graph.model_validate(data)
    clusters = cl.build_clusters(big, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY)
    a = clusters[0]
    assert len(a["why"]["top_phenotypes"]) == cl.MAX_TOP_PHENOTYPES
    assert a["why"]["top_phenotypes"][0]["id"] == "mock:phenotype:1"  # three members beat any ic
    assert a["why"]["top_phenotypes"][1]["id"] == "mock:phenotype:2"
    assert a["why"]["top_phenotypes"][2]["id"] == "mock:phenotype:29"  # then the highest ic among one-member phenotypes
    assert len(a["why"]["shared_pathways"]) == cl.MAX_SHARED_PATHWAYS


def test_shared_pathways_skip_the_associations_the_score_skips():
    """``why.shared_pathways`` explains the computed score: associations labelled with
    ``similarity.association_exclude_labels`` (sub-threshold backbone rows kept for display) add
    no pathways, exactly as ``similarity.pathways_of`` skips them."""
    data = graph_dict()
    for i, d in enumerate((D[1], D[2]), start=1):
        data["edges"].append(_e(f"e_0003{i:02d}", d, "mock:gene:3", "associated_with", labels=["below_score_threshold"]))
    g = Graph.model_validate(data)
    # LOGIC_CFG names no exclude labels: the similarity module's documented default applies
    default = cl.build_clusters(g, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY)
    assert default[0]["why"]["shared_pathways"] == ["Mock pathway one"]
    # an explicit empty list counts every association
    everything = {**LOGIC_CFG, "similarity": {**LOGIC_CFG["similarity"], "association_exclude_labels": []}}
    counted = cl.build_clusters(g, WEIGHTINGS, everything, sim_agree, today=TODAY)
    assert counted[0]["why"]["shared_pathways"] == ["Mock pathway one", "Mock pathway two"]
    # the label list is read from the config, not hard-coded
    custom = {**LOGIC_CFG, "similarity": {**LOGIC_CFG["similarity"], "association_exclude_labels": ["mock_other_label"]}}
    assert cl.build_clusters(g, WEIGHTINGS, custom, sim_agree, today=TODAY)[0]["why"]["shared_pathways"] == ["Mock pathway one", "Mock pathway two"]


def test_build_clusters_is_deterministic(graph):
    first = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_disagree, today=TODAY)
    second = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_disagree, today=TODAY)
    assert first == second


def test_disease_ids_override_restricts_the_partition(graph):
    clusters = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_agree, today=TODAY, disease_ids=A + B)
    assert members_of(clusters) == [A, B]


def test_singleton_clusters_off_leaves_edgeless_diseases_unclustered(graph):
    cfg = {**LOGIC_CFG, "clustering": {**CLUSTERING, "singleton_clusters": False}}
    clusters = cl.build_clusters(graph, WEIGHTINGS, cfg, sim_agree, today=TODAY)
    assert members_of(clusters) == [A, B]


def test_config_accepts_the_full_logic_dict_or_the_clustering_section():
    assert cl.clustering_config(LOGIC_CFG) == cl.clustering_config(CLUSTERING)
    assert cl.clustering_config(None) == cl.clustering_config({})
    assert cl.clustering_config({})["seed"] == cl.DEFAULT_CLUSTERING["seed"]
    with pytest.raises(ValueError):
        cl.clustering_config({"method": "louvain"})
    with pytest.raises(ValueError):
        cl.clustering_config({"objective": "surprise"})


def test_build_clusters_requires_a_weighting(graph):
    with pytest.raises(ValueError):
        cl.build_clusters(graph, [], LOGIC_CFG, sim_agree, today=TODAY)


def test_unnamed_weightings_get_positional_names(graph):
    weightings = [{"w_m": 0.5, "w_p": 0.4, "w_c": 0.6}, {"w_m": 0.3, "w_p": 0.6, "w_c": 0.6}]
    clusters = cl.build_clusters(graph, weightings, LOGIC_CFG, sim_agree, today=TODAY)
    assert clusters[0]["why"]["weightings_used"] == ["weighting_0", "weighting_1"]


def test_default_sim_fn_is_the_similarity_module_imported_lazily(graph, monkeypatch):
    calls: list[tuple[dict[str, Any], Any, str]] = []

    def compute_similarity(g: Graph, weighting: dict[str, Any], cfg: Any, *, today: str) -> list[dict[str, Any]]:
        calls.append((weighting, cfg, today))
        return sim_agree(g, weighting, cfg, today=today)

    stub = types.ModuleType("pipeline.graph.similarity")
    stub.compute_similarity = compute_similarity  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pipeline.graph.similarity", stub)

    clusters = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, today=TODAY)
    assert members_of(clusters) == [A, B, [ISOLATED]]
    assert [w["name"] for w, _, _ in calls] == ["balanced", "phenotype_heavy", "mechanism_heavy"]
    assert all(cfg is LOGIC_CFG and today == TODAY for _, cfg, today in calls)


def test_other_resolution_or_cpm_objective_still_partition(graph):
    """The alternative leidenalg partition types run and keep the obvious communities."""
    for section in ({**CLUSTERING, "resolution": 0.5}, {**CLUSTERING, "objective": "cpm", "resolution": 0.1}):
        part = cl.leiden_partition(list(D.values()), sim_agree(None, WEIGHTINGS[0], None, today=TODAY), section)
        assert len({part[d] for d in A}) == 1 and len({part[d] for d in B}) == 1 and part[D[1]] != part[D[4]]


def test_cluster_report_counts(graph):
    clusters = cl.build_clusters(graph, WEIGHTINGS, LOGIC_CFG, sim_disagree, today=TODAY)
    report = cl.cluster_report(clusters)
    assert report["clusters"] == 3 and report["robust"] == 1 and report["sensitive"] == 2
    assert report["singletons"] == 1 and report["multi_member"] == 2
    assert report["diseases_clustered"] == 6 and report["largest"] == 3 and report["sizes"] == [3, 2, 1]
    assert report["weightings_used"] == ["balanced", "phenotype_heavy", "mechanism_heavy"]
    assert cl.cluster_report([]) == {
        "clusters": 0,
        "robust": 0,
        "sensitive": 0,
        "singletons": 0,
        "multi_member": 0,
        "diseases_clustered": 0,
        "largest": 0,
        "sizes": [],
        "internal_repulsions": 0,
        "signed": False,
        "weightings_used": [],
    }
    assert report["internal_repulsions"] == 0 and report["signed"] is False
