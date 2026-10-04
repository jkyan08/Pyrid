"""pipeline/graph/rules.py on a synthetic graph (``mock:`` ids, ``MOCKGENE<n>`` symbols, "Mock "
labels). Offline; no external identifier appears here.

The graph is built so every branch of both rules fires once:

==================  ===========================================================================
drug -> disease     what it shows
==================  ===========================================================================
drug 1 -> D1        pathway-level route: the drug inhibits the pathway, the LoF gene is a member
                    -> ``direction_mismatch`` via ``pathway``, confidence 0.1 x 0.5
drug 1 -> D2        pathway-level ``direction_match_candidate``, confidence 0.3 x 0.5
drug 1 -> D3        mixed: LoF on gene 1 and GoF on gene 2, both reached through the pathway ->
                    mismatch wins, the gain hit stays in ``confidence_basis.hits``
drug 2 -> D1        direct AND pathway hit on the same gene -> the direct hit is chosen (full
                    confidence), both hits in the basis
drug 2 -> D2        suppressed by an existing extracted ``treats`` edge (claim-backed)
drug 2 -> D4        suppressed by an existing observed ``treats`` edge
drug 3 -> D5        mechanism gene resolved case-insensitively through the Gene label (no
                    ``props.gene``, no ``props.symbol``)
drug 3 -> D6        DN is a loss direction (edge stored Mechanism -> Disease: either direction)
drug 3 -> D7        mechanism gene resolved through ``props.symbol`` (no ``props.gene``) -> candidate
drug 3 -> D8        suppressed by ``contraindicated_for``
D9                  an off-convention mechanism id and a mechanism whose gene has no Gene node ->
                    unresolved report rows, no edge
variant 1           a Variant -> Mechanism ``has_mechanism`` edge is not a disease and is skipped
==================  ===========================================================================
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pipeline.config import load_seeds
from pipeline.graph import rules
from pipeline.models import Edge, EdgeStatus, EvidenceClass, Graph

TODAY = "2026-10-03"
_SOURCE = {"kind": "db", "id": "mock:source:1", "date": TODAY}

D = {n: f"mock:disease:{n}" for n in range(1, 10)}
DRUG = {n: f"mock:drug:{n}" for n in range(1, 4)}
GENE = {n: f"mock:gene:{n}" for n in range(1, 6)}
PATHWAY = "mock:pathway:1"
QUOTE = "Mock sentence about the mock inhibitor."


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
        _n(GENE[1], "Gene", "MOCKGENE1", symbol="MOCKGENE1"),
        _n(GENE[2], "Gene", "MOCKGENE2", symbol="MOCKGENE2"),
        _n(GENE[3], "Gene", "Mockgene3"),  # no props.symbol: resolved through the label, case-insensitively
        _n(GENE[4], "Gene", "MOCKGENE4", symbol="MOCKGENE4"),
        _n(GENE[5], "Gene", "Mock gene five", symbol="MOCKGENE5"),  # label differs: resolved through props.symbol
        _n("mech:MOCKGENE1:LoF", "Mechanism", "MOCKGENE1 loss of function", gene=GENE[1], direction="LoF"),
        _n("mech:MOCKGENE1:GoF", "Mechanism", "MOCKGENE1 gain of function", gene=GENE[1], direction="GoF"),
        _n("mech:MOCKGENE2:GoF", "Mechanism", "MOCKGENE2 gain of function", gene=GENE[2], direction="GoF"),
        _n("mech:MOCKGENE3:LoF", "Mechanism", "MOCKGENE3 loss of function"),  # no props.gene
        _n("mech:MOCKGENE4:DN", "Mechanism", "MOCKGENE4 dominant negative", gene=GENE[4], direction="DN"),
        _n("mech:MOCKGENE5:GoF", "Mechanism", "MOCKGENE5 gain of function"),  # no props.gene
        _n("mech:MOCKGENE9:LoF", "Mechanism", "MOCKGENE9 loss of function"),  # no Gene node at all
        _n("mock:mechanism:odd", "Mechanism", "Mock mechanism with an off-convention id"),
        *[_n(D[i], "Disease", f"Mock disease {i}") for i in range(1, 10)],
        _n(PATHWAY, "Pathway", "Mock pathway one"),
        _n(DRUG[1], "Drug", "Mock pathway inhibitor"),
        _n(DRUG[2], "Drug", "Mock dual inhibitor"),
        _n(DRUG[3], "Drug", "Mock gene inhibitor"),
        _n("mock:variant:1", "Variant", "Mock variant one", gene=GENE[1]),
        _n("mock:paper:1", "Paper", "Mock paper", title="Mock paper", abstract=f"Mock abstract. {QUOTE}", year=2026),
    ]
    edges = [
        _e("e_000001", D[1], "mech:MOCKGENE1:LoF", "has_mechanism", origin="germline"),
        _e("e_000002", D[2], "mech:MOCKGENE2:GoF", "has_mechanism", origin="germline"),
        _e("e_000003", D[3], "mech:MOCKGENE1:LoF", "has_mechanism", origin="germline"),
        _e("e_000004", D[3], "mech:MOCKGENE2:GoF", "has_mechanism", origin="germline"),
        _e("e_000005", D[4], "mech:MOCKGENE1:GoF", "has_mechanism", origin="germline"),
        _e("e_000006", D[5], "mech:MOCKGENE3:LoF", "has_mechanism", origin="germline"),
        _e("e_000007", "mech:MOCKGENE4:DN", D[6], "has_mechanism", origin="germline"),  # stored the other way round
        _e("e_000008", D[7], "mech:MOCKGENE5:GoF", "has_mechanism", origin="germline"),
        _e("e_000009", D[8], "mech:MOCKGENE4:DN", "has_mechanism", origin="germline"),
        _e("e_000010", D[9], "mech:MOCKGENE9:LoF", "has_mechanism", origin="germline"),
        _e("e_000011", D[9], "mock:mechanism:odd", "has_mechanism", origin="germline"),
        _e("e_000012", "mock:variant:1", "mech:MOCKGENE1:GoF", "has_mechanism", origin="germline"),
        _e("e_000013", GENE[1], PATHWAY, "in_pathway"),
        _e("e_000014", GENE[2], PATHWAY, "in_pathway"),
        _e("e_000015", DRUG[1], PATHWAY, "inhibits"),
        _e("e_000016", DRUG[2], PATHWAY, "inhibits"),
        _e("e_000017", DRUG[2], GENE[1], "inhibits"),
        _e("e_000018", DRUG[3], GENE[3], "inhibits"),
        _e("e_000019", DRUG[3], GENE[4], "inhibits"),
        _e("e_000020", DRUG[3], GENE[5], "inhibits"),
        {
            **_e("e_000021", DRUG[2], D[2], "treats", labels=["promising", "small_n"]),
            "evidence_class": "extracted",
            "confidence": 0.4,
            "status": "small_n",
            "supporting": ["c_000001"],
        },
        _e("e_000022", DRUG[2], D[4], "treats"),
        _e("e_000023", DRUG[3], D[8], "contraindicated_for"),
    ]
    claims = [
        {
            "id": "c_000001",
            "pmid": "",
            "paper": "mock:paper:1",
            "quote": QUOTE,
            "polarity": "supports",
            "subject": DRUG[2],
            "predicate": "treats",
            "object": D[2],
            "variant": "",
            "study_type": "case_report",
            "n_patients": 1,
        }
    ]
    return {
        "meta": {
            "built_at": f"{TODAY}T00:00:00+00:00",
            "sources": [{"name": "mock", "version": "", "retrieved_at": TODAY, "license": "mock"}],
            "coverage": {},
        },
        "nodes": nodes,
        "edges": edges,
        "claims": claims,
        "clusters": [],
    }


@pytest.fixture(scope="module")
def graph() -> Graph:
    return Graph.model_validate_json(json.dumps(graph_dict()), strict=True)


@pytest.fixture(scope="module")
def result(graph: Graph) -> rules.RulesResult:
    return rules.run_rules(graph, None, today=TODAY)


def by_pair(drafts: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out = {(d["source"], d["target"]): d for d in drafts}
    assert len(out) == len(drafts), "one draft per (drug, disease)"
    return out


# --------------------------------------------------------------------------------------
# the two rules on the fixture
# --------------------------------------------------------------------------------------


def test_every_expected_pair_and_nothing_else(result: rules.RulesResult):
    pairs = by_pair(result.drafts)
    expected = {
        (DRUG[1], D[1]): ("direction_mismatch", "pathway", 0.05),
        (DRUG[1], D[2]): ("direction_match_candidate", "pathway", 0.15),
        (DRUG[1], D[3]): ("direction_mismatch", "pathway", 0.05),
        (DRUG[1], D[4]): ("direction_match_candidate", "pathway", 0.15),
        (DRUG[2], D[1]): ("direction_mismatch", "target", 0.1),
        (DRUG[2], D[3]): ("direction_mismatch", "target", 0.1),
        (DRUG[3], D[5]): ("direction_mismatch", "target", 0.1),
        (DRUG[3], D[6]): ("direction_mismatch", "target", 0.1),
        (DRUG[3], D[7]): ("direction_match_candidate", "target", 0.3),
    }
    assert set(pairs) == set(expected)
    for key, (rule, via, confidence) in expected.items():
        d = pairs[key]
        assert d["attributes"]["rule"] == rule and d["attributes"]["via"] == via, key
        assert d["confidence"] == pytest.approx(confidence), key
        assert d["confidence_basis"]["via"] == via and d["confidence_basis"]["confidence"] == pytest.approx(confidence)
    # sorted by (relation, source, target)
    assert [(d["source"], d["target"]) for d in result.drafts] == sorted(pairs)


def test_direction_mismatch_drafts_are_inferred_unsupported_with_the_reason(result: rules.RulesResult):
    for d in result.drafts:
        if d["attributes"]["rule"] != "direction_mismatch":
            continue
        assert d["relation"] == "treats" and d["evidence_class"] == "inferred"
        assert d["status"] == "inferred_unsupported"
        assert d["attributes"]["reason"] == "direction mismatch"
        assert d["attributes"]["labels"] == ["inferred_unsupported", "direction_mismatch"]
        assert d["sources"] == [{"kind": "db", "id": "rules:direction_mismatch", "date": TODAY}]
        assert d["supporting"] == [] and d["contradicting"] == []
        assert d["confidence_basis"]["mechanism_direction"] in ("LoF", "DN")
        assert d["confidence_basis"]["base_confidence"] == 0.1


def test_direction_match_candidates_are_labelled_hypotheses(result: rules.RulesResult):
    candidates = [d for d in result.drafts if d["attributes"]["rule"] == "direction_match_candidate"]
    assert len(candidates) == 3
    for d in candidates:
        assert d["status"] == "supported" and d["evidence_class"] == "inferred"
        assert d["attributes"]["reason"] == "direction match (hypothesis, no literature)"
        assert d["attributes"]["labels"] == ["candidate", "hypothesis", "direction_match"]
        assert d["sources"] == [{"kind": "db", "id": "rules:direction_match_candidate", "date": TODAY}]
        assert d["confidence_basis"]["mechanism_direction"] == "GoF"
        assert d["confidence_basis"]["base_confidence"] == 0.3


def test_pathway_level_route_halves_the_confidence_and_names_the_pathway(result: rules.RulesResult):
    d = by_pair(result.drafts)[(DRUG[1], D[1])]
    basis = d["confidence_basis"]
    assert basis["via"] == "pathway" and basis["confidence_factor"] == 0.5
    assert basis["pathway"] == PATHWAY and basis["pathway_label"] == "Mock pathway one"
    assert basis["inhibits_edge"] == "e_000015" and basis["has_mechanism_edge"] == "e_000001"
    assert basis["drug_target_gene"] == GENE[1] and basis["drug_target_symbol"] == "MOCKGENE1"
    assert basis["drug_action"] == "inhibits" and basis["mechanism"] == "mech:MOCKGENE1:LoF"
    assert basis["mechanism_somatic"] is False
    assert basis["loss_directions"] == ["LoF", "DN"] and basis["gain_directions"] == ["GoF"]


def test_direct_hit_beats_pathway_hit_and_both_stay_in_the_basis(result: rules.RulesResult):
    d = by_pair(result.drafts)[(DRUG[2], D[1])]
    basis = d["confidence_basis"]
    assert basis["via"] == "target" and basis["confidence_factor"] == 1.0 and d["confidence"] == 0.1
    assert basis["inhibits_edge"] == "e_000017" and basis["pathway"] is None and basis["pathway_label"] is None
    assert [(h["via"], h["classified_as"]) for h in basis["hits"]] == [("target", "loss"), ("pathway", "loss")]
    assert basis["hits"][1]["pathway"] == PATHWAY and basis["hits"][1]["inhibits_edge"] == "e_000016"
    assert basis["n_direction_mismatch_hits"] == 2 and basis["n_direction_match_hits"] == 0


def test_mixed_loss_and_gain_resolves_to_direction_mismatch(result: rules.RulesResult):
    pairs = by_pair(result.drafts)
    pathway_only = pairs[(DRUG[1], D[3])]
    assert pathway_only["status"] == "inferred_unsupported" and pathway_only["confidence"] == 0.05
    basis = pathway_only["confidence_basis"]
    assert basis["n_direction_mismatch_hits"] == 1 and basis["n_direction_match_hits"] == 1
    assert [(h["gene"], h["direction"], h["classified_as"]) for h in basis["hits"]] == [
        (GENE[1], "LoF", "loss"),
        (GENE[2], "GoF", "gain"),
    ]
    dual = pairs[(DRUG[2], D[3])]
    assert dual["status"] == "inferred_unsupported" and dual["confidence"] == 0.1 and dual["attributes"]["via"] == "target"
    assert dual["confidence_basis"]["n_direction_mismatch_hits"] == 2 and dual["confidence_basis"]["n_direction_match_hits"] == 1
    assert result.report["pairs_mixed_direction"] == 2


def test_existing_drug_disease_edges_suppress_the_pair(graph: Graph, result: rules.RulesResult):
    pairs = by_pair(result.drafts)
    assert (DRUG[2], D[2]) not in pairs, "an extracted treats edge already speaks about the pair"
    assert (DRUG[2], D[4]) not in pairs, "an observed treats edge already speaks about the pair"
    assert (DRUG[3], D[8]) not in pairs, "a contraindication already speaks about the pair"
    assert (DRUG[1], D[2]) in pairs and (DRUG[1], D[4]) in pairs, "another drug on the same disease is not suppressed"
    assert result.report["pairs_suppressed_by_relation"] == {"contraindicated_for": 1, "treats": 2}
    existing, ignored = rules.existing_treatment_pairs(graph, rules.DEFAULT_RULES_CFG["existing_treatment_relations"])
    assert existing == {(DRUG[2], D[2]): ["treats"], (DRUG[2], D[4]): ["treats"], (DRUG[3], D[8]): ["contraindicated_for"]}
    assert ignored == 0
    only_treats, _ = rules.existing_treatment_pairs(graph, ["treats"])
    assert set(only_treats) == {(DRUG[2], D[2]), (DRUG[2], D[4])}


def test_the_rules_own_earlier_edges_never_count_as_existing(graph: Graph, result: rules.RulesResult):
    """Re-running over a graph that already carries a rules pass yields the same drafts: the
    ``rules:`` sources are recognised and ignored (the standalone CLI on an exported graph)."""
    data = graph_dict()
    data["edges"] += [{**d, "id": f"e_{100 + i:06d}"} for i, d in enumerate(result.drafts)]
    again = rules.run_rules(Graph.model_validate(data), None, today=TODAY)
    assert again.drafts == result.drafts
    assert again.report["prior_rule_edges_ignored"] == len(result.drafts) == 9
    assert result.report["prior_rule_edges_ignored"] == 0
    assert again.report["pairs_suppressed_by_relation"] == result.report["pairs_suppressed_by_relation"]
    first_rule_edge = Edge.model_validate({**result.drafts[0], "id": "e_000999"})
    assert rules.is_rule_edge(first_rule_edge) and not any(rules.is_rule_edge(e) for e in graph.edges)


def test_report_counts(result: rules.RulesResult):
    r = result.report
    assert r["today"] == TODAY
    assert r["drugs_with_inhibits_edges"] == 3
    assert r["target_hits"] == {"target": 4, "pathway": 4}
    assert r["genes_with_mechanisms"] == 5
    assert r["mechanism_edges_considered"] == 9, "the Variant edge and the two unresolved edges are not counted"
    assert r["pairs_considered"] == 12 and r["pairs_unclassified"] == 0
    assert r["emitted"] == {"direction_mismatch": {"pathway": 2, "target": 4}, "direction_match_candidate": {"pathway": 2, "target": 1}}
    assert r["n_edges"] == 9 == len(result.drafts)
    assert [u["edge"] for u in r["mechanism_edges_unresolved"]] == ["e_000010", "e_000011"]
    assert r["mechanism_edges_unresolved"][0]["mechanism"] == "mech:MOCKGENE9:LoF" and "Gene node" in r["mechanism_edges_unresolved"][0]["reason"]
    assert r["mechanism_edges_unresolved"][1]["mechanism"] == "mock:mechanism:odd" and "mech:<GENE>" in r["mechanism_edges_unresolved"][1]["reason"]
    assert r["config"] == rules.rules_config(None)


# --------------------------------------------------------------------------------------
# switches
# --------------------------------------------------------------------------------------


def test_pathway_level_disabled_keeps_only_direct_targets(graph: Graph):
    cfg = {"rules": {"pathway_level": {"enabled": False}}}
    out = rules.run_rules(graph, cfg, today=TODAY)
    pairs = by_pair(out.drafts)
    assert set(pairs) == {(DRUG[2], D[1]), (DRUG[2], D[3]), (DRUG[3], D[5]), (DRUG[3], D[6]), (DRUG[3], D[7])}
    assert all(d["attributes"]["via"] == "target" for d in out.drafts)
    assert out.report["target_hits"] == {"target": 4, "pathway": 0}
    assert out.report["drugs_with_inhibits_edges"] == 2, "a drug that only inhibits a pathway reaches no gene"
    assert out.report["pairs_mixed_direction"] == 0, "the gain gene is only reachable through the pathway"
    assert pairs[(DRUG[2], D[3])]["confidence_basis"]["n_direction_match_hits"] == 0
    assert out.report["config"]["pathway_level"] == {"enabled": False, "confidence_factor": 0.5}


def test_candidate_rule_disabled_drops_only_the_hypotheses(graph: Graph, result: rules.RulesResult):
    cfg = {"rules": {"direction_match_candidate": {"enabled": False}}}
    out = rules.run_rules(graph, cfg, today=TODAY)
    assert all(d["attributes"]["rule"] == "direction_mismatch" for d in out.drafts)
    assert len(out.drafts) == 6
    assert out.drafts == [d for d in result.drafts if d["attributes"]["rule"] == "direction_mismatch"]
    assert out.report["pairs_unclassified"] == 3
    assert out.report["pairs_mixed_direction"] == 2, "mixed pairs still resolve to the mismatch rule"


def test_gain_directions_are_read_from_the_config(graph: Graph):
    cfg = {"rules": {"direction_match_candidate": {"gain_directions": []}}}
    out = rules.run_rules(graph, cfg, today=TODAY)
    assert len(out.drafts) == 6 and out.report["pairs_unclassified"] == 3
    assert out.report["pairs_mixed_direction"] == 0, "without a gain direction nothing is a gain hit"
    mixed = by_pair(out.drafts)[(DRUG[1], D[3])]["confidence_basis"]
    assert mixed["gain_directions"] == [] and mixed["n_direction_match_hits"] == 0
    assert [h["classified_as"] for h in mixed["hits"]] == ["loss", "other"]


def test_loss_directions_are_read_from_the_config(graph: Graph):
    cfg = {"rules": {"direction_mismatch": {"loss_directions": ["LoF"]}}}
    out = rules.run_rules(graph, cfg, today=TODAY)
    pairs = by_pair(out.drafts)
    assert (DRUG[3], D[6]) not in pairs, "DN is no longer a loss direction"
    assert out.report["pairs_unclassified"] == 1
    assert pairs[(DRUG[3], D[5])]["confidence_basis"]["loss_directions"] == ["LoF"]


def test_rules_config_merges_over_the_defaults():
    assert rules.rules_config(None) == rules.DEFAULT_RULES_CFG
    assert rules.rules_config({}) == rules.DEFAULT_RULES_CFG
    partial = rules.rules_config({"rules": {"direction_mismatch": {"confidence": 0.2}}})
    assert partial["direction_mismatch"] == {"confidence": 0.2, "loss_directions": ["LoF", "DN"]}
    assert partial["direction_match_candidate"] == {"confidence": 0.3, "enabled": True, "gain_directions": ["GoF"]}
    bare = rules.rules_config({"direction_match_candidate": {"enabled": False}})
    assert bare["direction_match_candidate"] == {"confidence": 0.3, "enabled": False, "gain_directions": ["GoF"]}
    assert rules.rules_config({"rules": {"existing_treatment_relations": ["treats"]}})["existing_treatment_relations"] == ["treats"]
    assert rules.rules_config({"rules": {"existing_treatment_relations": "treats"}})["existing_treatment_relations"] == ["treats", "indicated_for", "contraindicated_for"]
    # the defaults are copied, never shared
    cfg = rules.rules_config(None)
    cfg["direction_mismatch"]["loss_directions"].append("X")
    assert rules.DEFAULT_RULES_CFG["direction_mismatch"]["loss_directions"] == ["LoF", "DN"]


def test_logic_yaml_mirrors_the_defaults(graph: Graph, result: rules.RulesResult):
    seeds = load_seeds("logic")
    assert set(seeds["rules"]) == {"direction_mismatch", "direction_match_candidate", "pathway_level", "existing_treatment_relations"}
    assert rules.rules_config(seeds) == rules.DEFAULT_RULES_CFG, "every rules parameter lives in logic.yaml with the documented value"
    assert seeds["rules"]["direction_match_candidate"]["gain_directions"] == ["GoF"]
    assert seeds["rules"]["existing_treatment_relations"] == ["treats", "indicated_for", "contraindicated_for"]
    assert rules.run_rules(graph, seeds, today=TODAY).drafts == result.drafts


# --------------------------------------------------------------------------------------
# graph indexing helpers
# --------------------------------------------------------------------------------------


def test_mechanism_hits_resolve_the_gene_by_prop_symbol_or_label(graph: Graph):
    hits, unresolved = rules.mechanism_hits_by_gene(graph)
    assert set(hits) == set(GENE.values())
    assert [(h.disease_id, h.direction) for h in hits[GENE[1]]] == [(D[1], "LoF"), (D[3], "LoF"), (D[4], "GoF")], "sorted; the Variant edge is skipped"
    assert [h.disease_id for h in hits[GENE[3]]] == [D[5]], "props.gene absent, no symbol: the label matched case-insensitively"
    assert [h.disease_id for h in hits[GENE[4]]] == [D[6], D[8]], "the reversed edge is oriented"
    assert hits[GENE[4]][0].has_mechanism_edge == "e_000007" and hits[GENE[4]][0].direction == "DN"
    assert [h.disease_id for h in hits[GENE[5]]] == [D[7]], "props.gene absent: props.symbol matched exactly"
    assert all(h.somatic is False for hs in hits.values() for h in hs)
    assert [u["edge"] for u in unresolved] == ["e_000010", "e_000011"]


def test_ambiguous_symbol_leaves_the_mechanism_unresolved():
    data = graph_dict()
    data["nodes"].append(_n("mock:gene:6", "Gene", "Mock gene six", symbol="MOCKGENE5"))  # a second Gene with the same symbol
    g = Graph.model_validate(data)
    hits, unresolved = rules.mechanism_hits_by_gene(g)
    assert GENE[5] not in hits and "mock:gene:6" not in hits
    assert "e_000008" in {u["edge"] for u in unresolved}
    assert (DRUG[3], D[7]) not in by_pair(rules.run_rules(g, None, today=TODAY).drafts)


def test_mechanism_props_gene_wins_over_the_symbol():
    data = graph_dict()
    for n in data["nodes"]:
        if n["id"] == "mech:MOCKGENE5:GoF":
            n["props"]["gene"] = GENE[2]  # the prop names another Gene node: it is authoritative
    hits, _ = rules.mechanism_hits_by_gene(Graph.model_validate(data))
    assert GENE[5] not in hits and D[7] in {h.disease_id for h in hits[GENE[2]]}


def test_drug_targets_direct_then_pathway(graph: Graph):
    targets = rules.drug_targets(graph)
    assert list(targets) == [DRUG[1], DRUG[2], DRUG[3]]
    assert [(t.gene_id, t.via, t.pathway_id) for t in targets[DRUG[1]]] == [(GENE[1], "pathway", PATHWAY), (GENE[2], "pathway", PATHWAY)]
    assert [(t.gene_id, t.via) for t in targets[DRUG[2]]] == [(GENE[1], "target"), (GENE[1], "pathway"), (GENE[2], "pathway")]
    assert [(t.gene_id, t.via, t.inhibits_edge) for t in targets[DRUG[3]]] == [(GENE[3], "target", "e_000018"), (GENE[4], "target", "e_000019"), (GENE[5], "target", "e_000020")]
    direct = rules.drug_targets(graph, pathway_level=False)
    assert DRUG[1] not in direct and [t.via for t in direct[DRUG[2]]] == ["target"]


# --------------------------------------------------------------------------------------
# contract, determinism, reporting, CLI
# --------------------------------------------------------------------------------------


def test_every_draft_validates_as_an_inferred_edge_and_the_graph_still_validates(graph: Graph, result: rules.RulesResult):
    required = {
        "rule", "via", "drug_target_gene", "drug_target_symbol", "mechanism", "mechanism_direction", "mechanism_somatic",
        "drug_action", "pathway", "pathway_label", "inhibits_edge", "has_mechanism_edge", "base_confidence",
        "confidence_factor", "confidence", "loss_directions", "gain_directions", "n_direction_mismatch_hits",
        "n_direction_match_hits", "hits",
    }
    edges = []
    for i, d in enumerate(result.drafts, start=1):
        assert "id" not in d
        edge = Edge.model_validate({**d, "id": f"e_{900000 + i:06d}"})
        edges.append(edge)
        assert edge.evidence_class is EvidenceClass.inferred and edge.confidence <= 0.5
        assert edge.status in (EdgeStatus.supported, EdgeStatus.inferred_unsupported)
        assert required <= set(edge.confidence_basis)
        assert edge.confidence_basis["hits"], "every hit for the pair is kept"
        assert edge.sources[0].kind == "db" and edge.sources[0].date == TODAY
        assert set(edge.attributes) == {"reason", "labels", "rule", "via"}
    merged = graph.model_dump(mode="json")
    merged["edges"] += [e.model_dump(mode="json") for e in edges]
    Graph.model_validate_json(json.dumps(merged), strict=True)
    assert rules.apply_rules(graph, None, today=TODAY) == result.drafts


def test_deterministic_under_reordered_input(result: rules.RulesResult):
    raw = graph_dict()
    raw["nodes"].reverse()
    raw["edges"].reverse()
    again = rules.run_rules(Graph.model_validate(raw), None, today=TODAY)
    assert json.dumps(again.drafts, sort_keys=True) == json.dumps(result.drafts, sort_keys=True)
    assert again.report == result.report


def test_today_is_required(graph: Graph):
    with pytest.raises(ValueError):
        rules.run_rules(graph, None, today=" ")


def test_summarize_and_examples(graph: Graph, result: rules.RulesResult):
    assert rules.summarize(result.drafts) == {
        "n_edges": 9,
        "by_rule": {"direction_match_candidate": 3, "direction_mismatch": 6},
        "by_rule_via": {
            "direction_match_candidate/pathway": 2,
            "direction_match_candidate/target": 1,
            "direction_mismatch/pathway": 2,
            "direction_mismatch/target": 4,
        },
        "by_status": {"inferred_unsupported": 6, "supported": 3},
    }
    assert rules.summarize([]) == {"n_edges": 0, "by_rule": {}, "by_rule_via": {}, "by_status": {}}
    lines = rules.examples_by_label(graph, result.drafts, limit=20)
    assert len(lines) == 9 and all("direction_mismatch" in line for line in lines[:6]) and all("direction_match_candidate" in line for line in lines[6:])
    assert lines[0].startswith("Mock dual inhibitor -> Mock disease 1 [direction_mismatch via target conf 0.1] mech:MOCKGENE1:LoF")
    assert len(rules.examples_by_label(graph, result.drafts, limit=3)) == 3


def test_cli_dry_run_prints_the_report_and_writes_drafts(graph: Graph, result: rules.RulesResult, tmp_path, capsys):
    src = tmp_path / "graph.json"
    out = tmp_path / "out" / "rule_drafts.json"
    graph.dump(src)
    assert rules.main(["--in", str(src), "--out", str(out), "--today", TODAY, "--examples", "4"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["graph"] == str(src) and printed["drafts_written_to"] == str(out)
    assert printed["report"]["today"] == TODAY and printed["summary"]["n_edges"] == 9
    assert len(printed["examples"]) == 4
    assert json.loads(out.read_text()) == result.drafts
    # without --out nothing is written; without --today the UTC date is used
    assert rules.main(["--in", str(src)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert "drafts_written_to" not in printed and len(printed["report"]["today"]) == 10
    assert printed["summary"] == rules.summarize(result.drafts)
