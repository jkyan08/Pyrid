"""pipeline/graph/logic.py (the orchestrator) on a synthetic graph and on the inline mock graph.

Everything is a placeholder (``mock:`` ids, "Mock " labels, ``MOCKGENE<n>`` symbols); no external
identifier appears here. The synthetic graph has three mechanism groups so similarity, the signed
Leiden layer and both rules all fire:

* diseases 1-3: gain-direction mechanism on MOCKGENE1 (disease 3 with unknown origin), pathway one,
  shared phenotypes -> one cluster; the drug inhibits MOCKGENE1 -> three direction-match candidates
* diseases 4-5: loss-direction mechanism on MOCKGENE2 (germline), pathways one and two -> one
  cluster; the drug inhibits MOCKGENE2 -> direction mismatch on disease 4, disease 5 is suppressed
  by an existing ``indicated_for`` edge
* disease 6: somatic gain-direction mechanism on MOCKGENE3 -> repelled by both groups, a singleton

Ground-truth evaluation is checked on ``tests.conftest.inline_mock_graph_dict`` (built to satisfy
tests 1-5 and 7) before and after the logic runs on it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pipeline.config import load_seeds
from pipeline.graph import logic
from pipeline.models import EdgeStatus, EvidenceClass, Graph, NodeType
from tests.conftest import inline_mock_graph_dict

TODAY = "2026-10-03"
BUILT_AT = "2026-09-30T12:00:00+00:00"  # deliberately not TODAY: the default date comes from here
_SOURCE = {"kind": "db", "id": "mock:source:1", "date": TODAY}

D = {n: f"mock:disease:{n}" for n in range(1, 7)}
DRUG = "mock:drug:1"


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
        _n("mech:MOCKGENE1:GoF", "Mechanism", "MOCKGENE1 gain of function", gene="mock:gene:1", direction="GoF"),
        _n("mech:MOCKGENE2:LoF", "Mechanism", "MOCKGENE2 loss of function", gene="mock:gene:2", direction="LoF"),
        _n("mech:MOCKGENE3:GoF:somatic", "Mechanism", "MOCKGENE3 somatic gain of function", gene="mock:gene:3", direction="GoF", origin="somatic"),
        *[_n(D[i], "Disease", f"Mock disease {i}") for i in range(1, 7)],
        _n("mock:phenotype:1", "Phenotype", "Mock phenotype A", ic=5.0),
        _n("mock:phenotype:2", "Phenotype", "Mock phenotype B", ic=3.0),
        _n("mock:phenotype:3", "Phenotype", "Mock phenotype C", ic=4.0),
        _n("mock:phenotype:4", "Phenotype", "Mock phenotype D"),  # no ic on purpose
        _n("mock:phenotype:5", "Phenotype", "Mock phenotype E", ic=2.0),
        _n("mock:pathway:1", "Pathway", "Mock pathway one"),
        _n("mock:pathway:2", "Pathway", "Mock pathway two"),
        _n(DRUG, "Drug", "Mock inhibitor"),
    ]
    edges = [
        _e("e_000001", D[1], "mech:MOCKGENE1:GoF", "has_mechanism", origin="germline"),
        _e("e_000002", D[2], "mech:MOCKGENE1:GoF", "has_mechanism", origin="germline"),
        _e("e_000003", D[3], "mech:MOCKGENE1:GoF", "has_mechanism"),  # origin unknown
        _e("e_000004", D[4], "mech:MOCKGENE2:LoF", "has_mechanism", origin="germline"),
        _e("e_000005", D[5], "mech:MOCKGENE2:LoF", "has_mechanism", origin="germline"),
        _e("e_000006", D[6], "mech:MOCKGENE3:GoF:somatic", "has_mechanism", origin="somatic"),
        _e("e_000007", D[1], "mock:gene:1", "associated_with"),
        _e("e_000008", D[2], "mock:gene:1", "associated_with"),
        _e("e_000009", D[3], "mock:gene:1", "associated_with"),
        _e("e_000010", D[4], "mock:gene:2", "associated_with"),
        _e("e_000011", D[5], "mock:gene:2", "associated_with"),
        _e("e_000012", D[6], "mock:gene:3", "associated_with"),
        _e("e_000013", "mock:gene:1", "mock:pathway:1", "in_pathway"),
        _e("e_000014", "mock:gene:2", "mock:pathway:1", "in_pathway"),
        _e("e_000015", "mock:gene:2", "mock:pathway:2", "in_pathway"),
        _e("e_000016", "mock:gene:3", "mock:pathway:2", "in_pathway"),
        _e("e_000017", D[1], "mock:phenotype:1", "has_phenotype"),
        _e("e_000018", D[2], "mock:phenotype:1", "has_phenotype"),
        _e("e_000019", D[3], "mock:phenotype:1", "has_phenotype"),
        _e("e_000020", D[1], "mock:phenotype:2", "has_phenotype"),
        _e("e_000021", D[2], "mock:phenotype:2", "has_phenotype"),
        _e("e_000022", D[3], "mock:phenotype:5", "has_phenotype"),
        _e("e_000023", D[4], "mock:phenotype:3", "has_phenotype"),
        _e("e_000024", D[5], "mock:phenotype:3", "has_phenotype"),
        _e("e_000025", D[6], "mock:phenotype:4", "has_phenotype"),
        _e("e_000026", DRUG, "mock:gene:1", "inhibits"),
        _e("e_000027", DRUG, "mock:gene:2", "inhibits"),
        _e("e_000028", DRUG, D[5], "indicated_for"),  # the graph already speaks about this pair
    ]
    return {
        "meta": {
            "built_at": BUILT_AT,
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


@pytest.fixture(scope="module")
def result(graph: Graph) -> logic.LogicResult:
    return logic.run_graph_logic(graph, today=TODAY)


@pytest.fixture(scope="module")
def inline_mock() -> Graph:
    return Graph.model_validate_json(json.dumps(inline_mock_graph_dict()), strict=True)


def new_edges(before: Graph, after: Graph):
    old = {e.id for e in before.edges}
    return [e for e in after.edges if e.id not in old]


# --------------------------------------------------------------------------------------
# apply_graph_logic / run_graph_logic
# --------------------------------------------------------------------------------------


def test_apply_graph_logic_returns_a_validated_graph_and_leaves_the_input_alone(graph: Graph):
    before = graph.model_dump_json()
    out = logic.apply_graph_logic(graph, today=TODAY)
    assert isinstance(out, Graph)
    assert graph.model_dump_json() == before, "the input graph is not modified"
    # the strict file-level contract (what Graph.load applies) accepts the result
    Graph.model_validate_json(out.model_dump_json(), strict=True)
    assert len(out.edges) > len(graph.edges) and out.clusters


def test_every_emitted_edge_is_inferred_capped_and_sourced(graph: Graph, result: logic.LogicResult):
    added = new_edges(graph, result.graph)
    assert added
    for e in added:
        assert e.evidence_class is EvidenceClass.inferred
        assert e.confidence <= 0.5
        assert e.confidence_basis, "the full score breakdown travels with the edge"
        assert e.status in (EdgeStatus.supported, EdgeStatus.inferred_unsupported)
        assert e.sources and e.sources[0].kind == "db" and e.sources[0].date == TODAY
        assert e.sources[0].id.startswith(logic.LOGIC_SOURCE_PREFIXES)
        assert e.attributes.get("labels"), "attributes.labels carries the display set"
        assert not e.supporting and not e.contradicting
    similar = [e for e in added if e.relation == "similar_to"]
    assert similar and all(e.source < e.target for e in similar)
    assert all(e.sources[0].id == "similarity:balanced" for e in similar)
    assert {e.relation for e in added} == {"similar_to", "treats"}


def test_edge_ids_continue_the_graph_sequence(graph: Graph, result: logic.LogicResult):
    assert logic.next_edge_number(graph.edges) == 29
    added = new_edges(graph, result.graph)
    ids = [e.id for e in added]
    assert ids[0] == "e_000029"
    assert ids == [f"e_{n:06d}" for n in range(29, 29 + len(ids))], "contiguous, in draft order"
    assert ids == sorted(ids)
    assert result.report["edge_ids"] == {"continued_from": "e_000028", "first": "e_000029", "last": ids[-1], "n_new": len(ids)}
    assert len({e.id for e in result.graph.edges}) == len(result.graph.edges)
    # drafts are ordered by (relation, source, target): similar_to before treats
    relations = [e.relation for e in added]
    assert relations == sorted(relations)


def test_similarity_edges_and_clusters_follow_the_mechanism_groups(result: logic.LogicResult):
    similar = {(e.source, e.target) for e in result.graph.edges if e.relation == "similar_to"}
    assert (D[1], D[2]) in similar and (D[1], D[3]) in similar and (D[4], D[5]) in similar
    assert not any(D[6] in pair for pair in similar), "the somatic disease attracts nobody"
    assert not any({D[1], D[4]} == set(pair) for pair in similar), "opposite directions on a shared pathway"
    groups = sorted(sorted(c.members) for c in result.graph.clusters)
    assert groups == [[D[1], D[2], D[3]], [D[4], D[5]], [D[6]]]
    assert [c.id for c in result.graph.clusters] == ["cl_001", "cl_002", "cl_003"]
    assert all(set(c.members) <= set(D.values()) for c in result.graph.clusters), "members are Disease ids only"
    # the penalised pairs repel: all of them end up across clusters
    assert result.repulsions and all(r["score"] < 0 for r in result.repulsions)
    repelled = {(r["source"], r["target"]) for r in result.repulsions}
    assert (D[4], D[6]) in repelled and (D[1], D[6]) in repelled and (D[1], D[4]) in repelled
    assert all(c.why["internal_repulsions"] == 0 for c in result.graph.clusters)
    assert result.graph.clusters[0].why["method"]["signed"] is True
    assert result.report["clusters"]["internal_repulsions"] == 0 and result.report["clusters"]["signed"] is True


def test_rule_edges_flag_the_loss_direction_and_skip_known_pairs(result: logic.LogicResult):
    treats = [e for e in result.graph.edges if e.relation == "treats"]
    by_target = {e.target: e for e in treats}
    assert set(by_target) == {D[1], D[2], D[3], D[4]}, "disease 5 already has an indicated_for edge"
    mismatch = by_target[D[4]]
    assert mismatch.status is EdgeStatus.inferred_unsupported
    assert mismatch.attributes["reason"] == "direction mismatch"
    assert mismatch.sources[0].id == "rules:direction_mismatch"
    for d in (D[1], D[2], D[3]):
        assert by_target[d].status is EdgeStatus.supported
        assert "candidate" in by_target[d].attributes["labels"] and "hypothesis" in by_target[d].attributes["labels"]
    assert result.report["rules"]["summary"]["by_rule"] == {"direction_match_candidate": 3, "direction_mismatch": 1}
    assert result.report["counts"]["rule_edges"] == 4 and result.report["counts"]["similar_to_edges"] == len(result.similarity_edges)
    # every outcome-affecting parameter the run used came from logic.yaml (names only)
    effective = result.report["config"]
    assert effective["rules"]["direction_match_candidate"]["gain_directions"] == ["GoF"]
    assert effective["rules"]["existing_treatment_relations"] == ["treats", "indicated_for", "contraindicated_for"]
    assert effective["clustering"]["n_iterations"] == -1
    assert result.report["rules"]["report"]["prior_rule_edges_ignored"] == 0


def test_clusters_are_replaced_not_appended(graph: Graph):
    data = graph_dict()
    data["clusters"] = [{"id": "cl_999", "members": [D[6], "mech:MOCKGENE3:GoF:somatic"], "robust": True, "why": {"stale": True}}]
    stale = Graph.model_validate(data)
    out = logic.apply_graph_logic(stale, today=TODAY)
    assert "cl_999" not in {c.id for c in out.clusters}
    assert all("stale" not in c.why for c in out.clusters)
    members = [m for c in out.clusters for m in c.members]
    assert sorted(members) == sorted(D.values()) and len(members) == len(set(members)), "a partition over the Disease nodes"


def test_penalties_disabled_zeroes_every_conflict_weight(graph: Graph, result: logic.LogicResult):
    off = logic.run_graph_logic(graph, today=TODAY, penalties_enabled=False)
    assert off.report["penalties_enabled"] is False and result.report["penalties_enabled"] is True
    zero = {"opposite_direction_same_gene": 0.0, "opposite_direction_same_pathway": 0.5 * 0, "germline_vs_somatic": 0.0}
    assert off.report["config"]["conflict"] == zero
    assert result.report["config"]["conflict"] == {"opposite_direction_same_gene": 1.0, "opposite_direction_same_pathway": 0.5, "germline_vs_somatic": 1.0}
    for e in off.graph.edges:
        if e.relation == "similar_to":
            assert e.confidence_basis["conflict_weights"] == zero
            assert e.confidence_basis["conflict_penalty"] == 0.0
    assert off.repulsions == [] and off.report["similarity"]["repulsions"]["n_pairs"] == 0
    assert off.graph.clusters[0].why["method"]["signed"] is False
    assert result.report["similarity"]["repulsions"]["n_pairs"] == len(result.repulsions) > 0
    # nothing else about the configuration changes
    on_cfg = {k: v for k, v in result.report["config"].items() if k != "conflict"}
    off_cfg = {k: v for k, v in off.report["config"].items() if k != "conflict"}
    assert on_cfg == off_cfg


def test_today_defaults_to_the_built_at_date(graph: Graph):
    out = logic.run_graph_logic(graph)
    assert out.report["today"] == "2026-09-30"
    assert all(e.sources[0].date == "2026-09-30" for e in new_edges(graph, out.graph))
    assert logic.build_date(graph) == "2026-09-30" and logic.build_date(graph, TODAY) == TODAY
    with pytest.raises(ValueError):
        logic.build_date(graph, "  ")


def test_run_is_deterministic_and_idempotent(graph: Graph, result: logic.LogicResult):
    again = logic.run_graph_logic(graph, today=TODAY)
    assert again.graph.model_dump_json() == result.graph.model_dump_json()
    assert again.clusters == result.clusters
    # a second pass over the output strips the earlier logic edges and re-adds the same ones
    twice = logic.run_graph_logic(result.graph, today=TODAY)
    assert twice.report["edges_stripped_from_input"] == result.report["edge_ids"]["n_new"]
    assert twice.graph.model_dump_json() == result.graph.model_dump_json()


def test_strip_logic_edges_recognises_module_sources(result: logic.LogicResult, graph: Graph):
    kept, removed = logic.strip_logic_edges(result.graph)
    assert removed == result.report["edge_ids"]["n_new"]
    assert [e.id for e in kept] == [e.id for e in graph.edges]
    assert logic.strip_logic_edges(graph) == (list(graph.edges), 0)


# --------------------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------------------


def test_load_logic_config_sources():
    from_yaml = logic.load_logic_config(None)
    assert from_yaml == load_seeds("logic")
    assert from_yaml["weightings"][0]["name"] == "balanced"
    # the cluster seeds (what export.py passes) carry none of the logic keys -> the yaml is used
    assert logic.load_logic_config({"genes": ["MOCKGENE1"], "thresholds": {"x": 1}}) == from_yaml
    # a logic mapping is used as given (deep-copied)
    explicit = {"weightings": [{"name": "only", "w_m": 1.0, "w_p": 0.0, "w_c": 0.0}], "similarity": {"min_score": 0.3}}
    cfg = logic.load_logic_config(explicit)
    assert cfg["weightings"] == explicit["weightings"] and cfg["similarity"]["min_score"] == 0.3
    cfg["weightings"][0]["name"] = "changed"
    assert explicit["weightings"][0]["name"] == "only"
    # nested under "logic"
    assert logic.load_logic_config({"logic": explicit})["weightings"][0]["name"] == "only"
    with pytest.raises(ValueError):
        logic.load_logic_config({"weightings": []})
    with pytest.raises(ValueError):
        logic.load_logic_config({"weightings": "balanced"})


def test_load_logic_config_rejects_duplicate_or_unnamed_weightings():
    """Names key the per-weighting similarity cache and the edges' source ids: a duplicate would
    silently reuse the primary weighting's edges, so `robust` would compare a partition with itself."""
    base = {"w_m": 0.5, "w_p": 0.4, "w_c": 0.6}
    with pytest.raises(ValueError, match="unique"):
        logic.load_logic_config({"weightings": [{"name": "same", **base}, {"name": "same", **base}]})
    with pytest.raises(ValueError, match="name"):
        logic.load_logic_config({"weightings": [{"name": "ok", **base}, dict(base)]})
    with pytest.raises(ValueError, match="name"):
        logic.load_logic_config({"weightings": [{"name": "  ", **base}]})
    assert [w["name"] for w in logic.load_logic_config({"weightings": [{"name": "a", **base}, {"name": "b", **base}]})["weightings"]] == ["a", "b"]


def test_disable_penalties_only_touches_the_conflict_section():
    cfg = load_seeds("logic")
    off = logic.disable_penalties(cfg)
    assert all(v == 0.0 for v in off["conflict"].values()) and set(off["conflict"]) == set(cfg["conflict"])
    assert {k: v for k, v in off.items() if k != "conflict"} == {k: v for k, v in cfg.items() if k != "conflict"}
    assert cfg["conflict"]["germline_vs_somatic"] == 1.0, "the input is not modified"


def test_explicit_config_drives_the_run(graph: Graph):
    cfg = load_seeds("logic")
    cfg["similarity"]["min_score"] = 10.0  # nothing can reach it
    out = logic.run_graph_logic(graph, cfg, today=TODAY)
    assert out.similarity_edges == [] and out.report["counts"]["similar_to_edges"] == 0
    assert len(out.graph.clusters) == 6, "no edges: every disease is a singleton cluster"
    assert out.report["config"]["similarity"]["min_score"] == 10.0


# --------------------------------------------------------------------------------------
# evaluate_ground_truth / ablation
# --------------------------------------------------------------------------------------


def test_evaluate_ground_truth_returns_the_five_tests_with_booleans(result: logic.LogicResult):
    ev = logic.evaluate_ground_truth(result.graph)
    assert set(ev) == {"1", "2", "3", "5", "7"} == set(logic.GROUND_TRUTH_TEST_IDS)
    for tid, entry in ev.items():
        assert isinstance(entry["pass"], bool)
        assert isinstance(entry["detail"], str) and entry["detail"]
        assert entry["test"].startswith(f"test_{tid}_")
    # the synthetic graph carries none of the real cluster's mechanisms, so every test fails with a reason
    assert not any(entry["pass"] for entry in ev.values())
    assert all(entry["detail"].startswith(("FAIL:", "ERROR:")) for entry in ev.values())


def test_evaluate_ground_truth_passes_on_the_inline_mock_before_and_after_logic(inline_mock: Graph):
    before = logic.evaluate_ground_truth(inline_mock)
    assert all(entry["pass"] for entry in before.values()), before
    assert all(entry["detail"].startswith("PASS:") for entry in before.values())
    after = logic.apply_graph_logic(inline_mock, today=TODAY)
    Graph.model_validate_json(after.model_dump_json(), strict=True)
    evaluation = logic.evaluate_ground_truth(after)
    assert all(entry["pass"] for entry in evaluation.values()), evaluation
    # the mock's hand-written clusters were replaced by a disease-only partition
    assert all(all(m.startswith("mock:disease:") for m in c.members) for c in after.clusters)
    assert {m for c in after.clusters for m in c.members} == {n.id for n in after.nodes if n.type == NodeType.Disease}


def test_evaluate_ground_truth_accepts_a_subset_and_unknown_ids(result: logic.LogicResult):
    ev = logic.evaluate_ground_truth(result.graph, ("2", "42"))
    assert set(ev) == {"2", "42"}
    assert ev["42"]["pass"] is False and ev["42"]["test"] is None


def test_ablation_runs_both_ways_and_summarises(graph: Graph):
    out = logic.ablation(graph, today=TODAY)
    assert out["tests"] == ["1", "2", "3"]
    for tid in ("1", "2", "3"):
        row = out["summary"][tid]
        assert set(row) == {"penalties_on", "penalties_off", "requires_penalties"}
        assert all(isinstance(v, bool) for v in row.values())
        assert row["requires_penalties"] == (row["penalties_on"] and not row["penalties_off"])
        assert set(out["penalties_on"]["evaluation"][tid]) == {"pass", "detail", "test"}
    assert all(v == 0.0 for v in out["penalties_off"]["conflict_weights"].values())
    assert out["penalties_on"]["conflict_weights"]["germline_vs_somatic"] == 1.0
    assert isinstance(out["all_pass_with_penalties"], bool) and isinstance(out["all_pass_without_penalties"], bool)
    assert out["penalties_on"]["clusters"]["clusters"] == 3


def test_mechanism_disease_clusters_rows(result: logic.LogicResult):
    rows = logic.mechanism_disease_clusters(result.graph)
    assert len(rows) == 6
    by_disease = {r["disease"]: r for r in rows}
    assert by_disease[D[6]]["mechanism"] == "mech:MOCKGENE3:GoF:somatic" and by_disease[D[6]]["origin"] == "somatic"
    assert by_disease[D[3]]["origin"] is None
    assert by_disease[D[1]]["cluster"] == by_disease[D[2]]["cluster"] == by_disease[D[3]]["cluster"]
    assert by_disease[D[1]]["cluster_size"] == 3 and by_disease[D[6]]["cluster_size"] == 1
    assert result.report["mechanism_disease_clusters"] == rows


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def test_cli_writes_the_graph_and_prints_the_evaluation(graph: Graph, tmp_path, capsys):
    src = tmp_path / "graph.json"
    out = tmp_path / "graph.logic.json"
    graph.dump(src)
    code = logic.main(["--in", str(src), "--out", str(out), "--ablation", "--today", TODAY])
    assert code == 0
    written = Graph.load(out)
    assert written.clusters and any(e.relation == "similar_to" for e in written.edges)
    printed = json.loads(capsys.readouterr().out)
    assert set(printed["ground_truth"]) == {"1", "2", "3", "5", "7"}
    assert printed["report"]["today"] == TODAY and printed["today"] == TODAY
    assert set(printed["ablation"]["summary"]) == {"1", "2", "3"}
    assert "report_written_to" not in printed, "no report file unless --out is under data/build or --report is given"
    assert not (tmp_path / logic.REPORT_FILENAME).exists()


def test_cli_report_option_writes_the_json(graph: Graph, tmp_path, capsys):
    src = tmp_path / "graph.json"
    out = tmp_path / "out" / "graph.logic.json"
    report = tmp_path / "reports" / "logic_report.json"
    graph.dump(src)
    assert logic.main(["--in", str(src), "--out", str(out), "--report", str(report)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["report_written_to"] == str(report)
    on_disk = json.loads(report.read_text())
    assert on_disk["ground_truth"] == printed["ground_truth"] and "ablation" not in on_disk
    assert on_disk["report"]["edge_ids"]["first"] == "e_000029"


def test_cli_no_penalties_writes_the_ablation_variant(graph: Graph, tmp_path, capsys):
    src = tmp_path / "graph.json"
    out = tmp_path / "graph.nopen.json"
    graph.dump(src)
    assert logic.main(["--in", str(src), "--out", str(out), "--no-penalties", "--today", TODAY]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["report"]["penalties_enabled"] is False
    assert all(v == 0.0 for v in printed["report"]["config"]["conflict"].values())
    Graph.load(out)
