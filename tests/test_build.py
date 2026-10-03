"""pipeline/graph/build.py and pipeline/export.py over a synthetic store in ``tmp_path``.

Placeholders only (``mock:`` ids, "Mock " labels, ``MOCKGENE<n>`` symbols); nothing here is a
resolved record. Fully offline: the export is run with ``--skip-download``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pipeline import export
from pipeline.graph import build as b
from pipeline.graph.confidence import RELATION_CONFLICT_LABEL
from pipeline.models import ClaimsBundle, EdgeStatus, EvidenceClass, Graph, NodeType
from pipeline.store import Store

TS = "2026-10-03T12:00:00+00:00"
NOW = "2026-10-03T13:00:00+00:00"
SEEDS: dict[str, Any] = {
    "genes": ["MOCKGENE1", "MOCKGENE2", "MOCKGENE9"],
    "disease_terms": ["Mock immune dysregulation 1", "Mock nothing"],
    "drug_terms": ["mock-inib", "mock-missing"],
    "thresholds": {"disease_gene_min_score": 0.3, "keep_disgenet_direct": True, "max_pathway_members_to_expand": 0, "include_parent_levels": 1},
}


def node(node_id: str, node_type: str, label: str, synonyms: list[str] = (), **props: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "label": label, "synonyms": list(synonyms), "props": props, "source": "mock", "retrieved_at": TS}


def edge(source: str, target: str, relation: str, **props: Any) -> dict[str, Any]:
    return {"source": source, "target": target, "relation": relation, "source_db": "mock", "props": props, "retrieved_at": TS}


def populate(store: Store, *, dangling: bool = False) -> None:
    store.upsert_nodes(
        [
            node("mock:gene:1", "Gene", "MOCKGENE1", symbol="MOCKGENE1", name="mock gene one", backbone_id="MOCKENSG1"),
            node("mock:gene:2", "Gene", "MOCKGENE2", symbol="MOCKGENE2", name="mock gene two", backbone_id="MOCKENSG2"),
            node("mock:disease:1", "Disease", "Mock immune dysregulation 1", ["MOCKGENE1 gain of function"], backbone_id="MOCKDIS_1", description="Mock autosomal dominant disorder."),
            node("mock:disease:2", "Disease", "Mock parent disorder 2", backbone_id="MOCKDIS_2"),
            node("mock:phenotype:1", "Phenotype", "Mock phenotype one", backbone_id="MOCKHP_1"),
            node("mock:phenotype:2", "Phenotype", "Mock phenotype two", backbone_id="MOCKHP_2"),
            node("mock:pathway:1", "Pathway", "Mock reactome pathway", ontology="Reactome", backbone_id="MOCKREACT:1"),
            node("mock:pathway:2", "Pathway", "Mock GO process", ontology="GO", backbone_id="MOCKGO_2"),
            node("mock:drug:1", "Drug", "Mock-inib", ["mockinib"], backbone_id="MOCKCHEMBL1"),
        ]
    )
    store.upsert_edges(
        [
            edge("mock:disease:1", "mock:gene:1", "associated_with", evidence_score=0.8, sources={"direct": ["MOCK_OT", "DISGENET"], "indirect": []}, number_of_pmids=6, backbone_relation="ASSOCIATED_WITH"),
            edge("mock:disease:1", "mock:gene:2", "associated_with", evidence_score=0.05, sources={"direct": ["MOCK_OT"], "indirect": []}, backbone_relation="ASSOCIATED_WITH"),
            edge("mock:disease:1", "mock:phenotype:1", "has_phenotype", evidence_type=["PCS"], frequency="3/4", references=["mock:ref:1"], backbone_relation="PHENOTYPE_PRESENT"),
            edge("mock:disease:1", "mock:phenotype:2", "has_phenotype", evidence_type=["IEA"], backbone_relation="PHENOTYPE_PRESENT"),
            edge("mock:phenotype:1", "mock:phenotype:2", "is_a", backbone_relation="PARENT"),
            edge("mock:disease:1", "mock:disease:2", "is_a", backbone_relation="PARENT"),
            edge("mock:gene:1", "mock:pathway:1", "in_pathway", backbone_relation="INTERACTS_WITH"),
            edge("mock:gene:1", "mock:pathway:2", "in_pathway", evidence=["IEA", "IDA"], backbone_relation="INTERACTS_WITH"),
            edge("mock:drug:1", "mock:gene:1", "inhibits", backbone_relation="INHIBITOR", relation_conflict=True, relation_assertions=[{"source": "MOCK_A", "relation": "INHIBITOR"}, {"source": "MOCK_B", "relation": "AGONIST"}]),
            edge("mock:drug:1", "mock:disease:1", "indicated_for", backbone_relation="INDICATION", highest_clinical_trial_phase=2.0),
        ]
    )
    if dangling:
        store.upsert_edges([edge("mock:disease:1", "mock:gene:404", "associated_with", evidence_score=0.9)])
    store.add_coverage("seed:gene:MOCKGENE9", "mock", "symbol MOCKGENE9", "2026-10-03", 0)
    store.add_coverage("mock:disease:1", "mock", "phenotypes", "2026-10-03", 2)
    store.add_coverage("mock:disease:1", "mock", "drugs", "2026-10-02", 1)
    store.upsert_source("mock backbone", retrieved_at=TS, license="mock", version="v0", url="", notes={"evidence_score_min": 0.0, "evidence_score_max": 1.0})
    store.set_hpo_ic({"mock:phenotype:1": {"ic": 2.5, "n_diseases": 3, "n_total": 36}})


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate(s)
        yield s


def edge_between(graph: Graph, source: str, target: str, relation: str):
    hits = [e for e in graph.edges if e.source == source and e.target == target and e.relation == relation]
    assert len(hits) == 1, f"expected one {relation} edge {source} -> {target}, got {[e.id for e in hits]}"
    return hits[0]


# --------------------------------------------------------------------------------------
# build_graph
# --------------------------------------------------------------------------------------


def test_build_graph_round_trips_through_the_strict_file_contract(store: Store, tmp_path: Path) -> None:
    graph = b.build_graph(store, SEEDS, now=NOW)
    assert isinstance(graph, Graph)
    path = graph.dump(tmp_path / "graph.json")
    loaded = Graph.load(path)
    assert loaded.model_dump() == graph.model_dump()
    assert graph.meta.built_at == NOW and graph.clusters == [] and graph.claims == []
    assert [s.name for s in graph.meta.sources] == ["mock backbone"]
    assert graph.meta.sources[0].version == "v0" and graph.meta.sources[0].license == "mock"


def test_edge_ids_are_deterministic_and_sorted(store: Store) -> None:
    g1 = b.build_graph(store, SEEDS, now=NOW)
    g2 = b.build_graph(store, SEEDS, now=NOW)
    assert g1.model_dump() == g2.model_dump()
    ids = [e.id for e in g1.edges]
    assert ids == [f"e_{i:06d}" for i in range(1, len(ids) + 1)]
    keys = [(e.relation, e.source, e.target) for e in g1.edges]
    assert keys == sorted(keys)
    node_keys = [(n.type.value, n.id) for n in g1.nodes]
    assert node_keys == sorted(node_keys)


def test_nodes_carry_props_ic_and_ontology(store: Store) -> None:
    graph = b.build_graph(store, SEEDS, now=NOW)
    idx = graph.node_index()
    assert idx["mock:phenotype:1"].props["ic"] == 2.5
    assert idx["mock:phenotype:1"].props["ic_n_diseases"] == 3 and idx["mock:phenotype:1"].props["ic_n_total"] == 36
    assert "ic" not in idx["mock:phenotype:2"].props
    assert idx["mock:pathway:1"].props["ontology"] == "Reactome" and idx["mock:pathway:2"].props["ontology"] == "GO"
    assert idx["mock:disease:1"].props["backbone_id"] == "MOCKDIS_1"
    mech_node = idx["mech:MOCKGENE1:GoF"]
    assert mech_node.type is NodeType.Mechanism and mech_node.props["origin"] == "germline" and mech_node.props["gene"] == "mock:gene:1"


def test_edge_confidence_labels_and_origin(store: Store) -> None:
    graph = b.build_graph(store, SEEDS, now=NOW)

    dg = edge_between(graph, "mock:disease:1", "mock:gene:1", "associated_with")
    assert dg.evidence_class is EvidenceClass.observed and dg.status is EdgeStatus.supported
    assert dg.confidence == 1.0, "0.8 normalised over [0, 1] + two bonuses, capped"
    assert dg.confidence_basis["rule"] == "disease_gene" and dg.confidence_basis["bonuses"] == ["sources.direct>=2", "number_of_pmids>=5"]
    assert dg.confidence_basis["source_db"] == "mock" and dg.confidence_basis["backbone_relation"] == "ASSOCIATED_WITH"
    assert dg.attributes == {"origin": "germline"}, "origin propagated from the mechanism rules"
    assert dg.sources[0].model_dump() == {"kind": "db", "id": "mock associated_with MOCKDIS_1->MOCKENSG1", "date": TS}

    weak = edge_between(graph, "mock:disease:1", "mock:gene:2", "associated_with")
    assert weak.attributes == {"labels": [b.BELOW_THRESHOLD_LABEL]}
    assert weak.confidence == pytest.approx(0.05)

    hp = edge_between(graph, "mock:disease:1", "mock:phenotype:1", "has_phenotype")
    assert hp.confidence == 0.9 and hp.confidence_basis["frequency"] == "3/4"
    assert [s.kind.value for s in hp.sources] == ["db"], "a non-PMID reference adds no source"
    assert edge_between(graph, "mock:disease:1", "mock:phenotype:2", "has_phenotype").confidence == 0.4

    assert edge_between(graph, "mock:phenotype:1", "mock:phenotype:2", "is_a").confidence == 0.95
    assert edge_between(graph, "mock:disease:1", "mock:disease:2", "is_a").confidence == 0.95
    assert edge_between(graph, "mock:gene:1", "mock:pathway:1", "in_pathway").confidence == 0.8
    go = edge_between(graph, "mock:gene:1", "mock:pathway:2", "in_pathway")
    assert go.confidence == 0.9 and go.confidence_basis["best_code"] == "IDA" and go.confidence_basis["ontology"] == "GO"

    inhibits = edge_between(graph, "mock:drug:1", "mock:gene:1", "inhibits")
    assert inhibits.confidence == pytest.approx(0.42), "0.7 drug base downgraded by 0.6 for the relation conflict"
    assert inhibits.attributes["labels"] == [RELATION_CONFLICT_LABEL]
    assert inhibits.confidence_basis["relation_conflict"] == [{"source": "MOCK_A", "relation": "INHIBITOR"}, {"source": "MOCK_B", "relation": "AGONIST"}]
    assert inhibits.confidence_basis["backbone_relation"] == "INHIBITOR"
    assert edge_between(graph, "mock:drug:1", "mock:disease:1", "indicated_for").confidence == 0.7

    hm = edge_between(graph, "mock:disease:1", "mech:MOCKGENE1:GoF", "has_mechanism")
    assert hm.confidence == 0.6 and hm.attributes == {"origin": "germline"}
    assert hm.sources[0].model_dump() == {"kind": "db", "id": "MOCKDIS_1", "date": TS}
    assert all(e.confidence_basis for e in graph.edges) and all(e.sources for e in graph.edges)


def test_meta_coverage_groups_rows_by_entity(store: Store) -> None:
    graph = b.build_graph(store, SEEDS, now=NOW)
    cov = graph.meta.coverage
    assert cov["seed:gene:MOCKGENE9"].searched[0].model_dump() == {"source": "mock", "query": "symbol MOCKGENE9", "date": "2026-10-03", "hits": 0}
    d1 = [(s.source, s.query, s.hits) for s in cov["mock:disease:1"].searched]
    assert ("mock", "phenotypes", 2) in d1 and ("mock", "drugs", 1) in d1
    # the mechanism rules record the parent term that has no associated gene
    rows = cov["mock:disease:2"].searched
    assert len(rows) == 1 and rows[0].source == "mechanism rules" and rows[0].hits == 0 and rows[0].date == "2026-10-03"


def test_pmid_reference_regex_only_accepts_pmid_shape() -> None:
    synthetic = "PMID:" + "0" * 8  # not an identifier of any record
    assert b._PMID_REF.match(synthetic).group(1) == "0" * 8
    assert b._PMID_REF.match("mock:ref:1") is None and b._PMID_REF.match("PMID:abc") is None


def test_dangling_edges_are_dropped_and_reported(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate(s, dangling=True)
        result = b.build(s, SEEDS, now=NOW)
    assert result.report["edges_dropped_dangling"] == [{"source": "mock:disease:1", "target": "mock:gene:404", "relation": "associated_with", "source_db": "mock"}]
    assert all(e.target != "mock:gene:404" for e in result.graph.edges)


def test_build_report_and_summarize(store: Store) -> None:
    result = b.build(store, SEEDS, now=NOW)
    r = result.report
    assert r["score_range"] == {"min": 0.0, "max": 1.0, "how": "sources.notes of 'mock backbone'"}
    assert r["thresholds"]["disease_gene_min_score"] == 0.3
    assert r["edges_below_threshold"] == 1 and r["edges_with_relation_conflict"] == 1
    assert r["mechanisms"]["by_rule"] == {"R1.gof": 1}
    assert r["seed_resolution"]["genes"] == {"MOCKGENE1": ["mock:gene:1"], "MOCKGENE2": ["mock:gene:2"], "MOCKGENE9": []}
    assert r["seed_resolution"]["disease_terms"] == {"Mock immune dysregulation 1": ["mock:disease:1"], "Mock nothing": []}
    assert r["seed_resolution"]["drug_terms"] == {"mock-inib": ["mock:drug:1"], "mock-missing": []}
    assert r["seed_resolution"]["unresolved"] == {"genes": ["MOCKGENE9"], "disease_terms": ["Mock nothing"], "drug_terms": ["mock-missing"]}
    assert r["claims"] == {"in_bundle": 0, "in_graph": 0, "attached_to_edges": 0, "unattached": [], "dropped": []}

    s = b.summarize(result.graph)
    assert s["nodes"] == len(result.graph.nodes) and s["edges"] == len(result.graph.edges) == 11
    assert s["nodes_by_type"] == {"Disease": 2, "Drug": 1, "Gene": 2, "Mechanism": 1, "Pathway": 2, "Phenotype": 2}
    assert s["edges_by_relation"] == {"associated_with": 2, "has_mechanism": 1, "has_phenotype": 2, "in_pathway": 2, "indicated_for": 1, "inhibits": 1, "is_a": 2}
    assert s["edges_by_evidence_class"] == {"observed": 11} and s["edges_by_status"] == {"supported": 11}
    assert s["edges_by_label"] == {RELATION_CONFLICT_LABEL: 1, b.BELOW_THRESHOLD_LABEL: 1}
    assert s["edges_with_origin"] == {"germline": 2}
    # phenotype 1 carries ic 2.5, phenotype 2 none; the one phenotype is_a edge cannot be compared
    assert s["phenotype_ic"] == {"nodes": 2, "nodes_without_ic": 1, "is_a_edges": 1, "is_a_edges_with_ic_on_both_ends": 0, "is_a_edges_child_ic_below_parent": 0}
    assert s["coverage"]["entities"] == 3 and s["coverage"]["zero_hit_searches"] == 2
    assert s["sources"] == ["mock backbone"]


def test_summarize_counts_phenotype_ic_drift(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate(s)
        # child (phenotype 1) less specific than its parent (phenotype 2): release drift between the IC and the hierarchy
        s.set_hpo_ic({"mock:phenotype:1": {"ic": 1.0, "n_diseases": 10, "n_total": 36}, "mock:phenotype:2": {"ic": 2.0, "n_diseases": 5, "n_total": 36}})
        summary = b.summarize(b.build_graph(s, SEEDS, now=NOW))
    assert summary["phenotype_ic"] == {"nodes": 2, "nodes_without_ic": 0, "is_a_edges": 1, "is_a_edges_with_ic_on_both_ends": 1, "is_a_edges_child_ic_below_parent": 1}


def test_score_range_falls_back_to_the_store(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate(s)
        s.upsert_source("mock backbone", retrieved_at=TS, license="mock")  # notes without a range
        assert b.score_range(s) == (0.05, 0.8, "observed in store")
    with Store(tmp_path / "empty.sqlite") as s:
        assert b.score_range(s) == (0.0, 1.0, "default (no scored disease-gene edge)")


def test_finalize_edges_orders_and_strips_source_db() -> None:
    rows = [
        {"relation": "b", "source": "x", "target": "y", "source_db": "m", "confidence": 0.123456},
        {"relation": "a", "source": "x", "target": "y", "source_db": "n", "confidence": 0.5},
        {"relation": "a", "source": "x", "target": "y", "source_db": "m", "confidence": 0.5},
    ]
    out = b.finalize_edges(rows)
    assert [e["id"] for e in out] == ["e_000001", "e_000002", "e_000003"]
    assert [(e["relation"], e.get("source_db")) for e in out] == [("a", None), ("a", None), ("b", None)]
    assert out[2]["confidence"] == 0.1235


# --------------------------------------------------------------------------------------
# claims bundle
# --------------------------------------------------------------------------------------


def _bundle() -> ClaimsBundle:
    abstract = "Mock sentence one supports the mock association. Mock sentence two is unresolved."
    return ClaimsBundle.model_validate(
        {
            "extracted_at": "2026-10-03T11:00:00+00:00",
            "model": "mock-model",
            "papers": [{"id": "mock:paper:1", "type": "Paper", "label": "Mock paper", "synonyms": [], "props": {"abstract": abstract, "year": 2026}}],
            "claims": [
                {"id": "c_000001", "pmid": "", "paper": "mock:paper:1", "quote": "Mock sentence one supports the mock association.", "polarity": "supports", "subject": "mock:disease:1", "predicate": "associated_with", "object": "mock:gene:1", "variant": "", "study_type": "case_report", "n_patients": 1},
                {"id": "c_000002", "pmid": "", "paper": "mock:paper:1", "quote": "Mock sentence two is unresolved.", "polarity": "supports", "subject": "text:some mention", "predicate": "treats", "object": "mock:disease:1", "variant": "", "study_type": "review", "n_patients": None},
                {"id": "c_000003", "pmid": "", "paper": "mock:paper:1", "quote": "Mock sentence two is unresolved.", "polarity": "supports", "subject": "mock:disease:1", "predicate": "has_phenotype", "object": "mock:phenotype:1", "variant": "mock:variant:404", "study_type": "review", "n_patients": None},
            ],
            "coverage": {"mock:disease:1": {"searched": [{"source": "mock pubmed", "query": "mock query", "date": "2026-10-03", "hits": 0}]}},
        }
    )


def test_claims_attach_to_matching_edges(store: Store, tmp_path: Path) -> None:
    result = b.build(store, SEEDS, _bundle(), now=NOW)
    graph = result.graph
    assert Graph.load(graph.dump(tmp_path / "graph.json")).model_dump() == graph.model_dump()
    assert graph.node_index()["mock:paper:1"].type is NodeType.Paper
    assert [c.id for c in graph.claims] == ["c_000001", "c_000002"], "the claim with an unknown variant is dropped"

    dg = edge_between(graph, "mock:disease:1", "mock:gene:1", "associated_with")
    assert dg.evidence_class is EvidenceClass.observed, "a backbone edge enriched by papers stays observed"
    assert dg.status is EdgeStatus.small_n and dg.supporting == ["c_000001"] and dg.contradicting == []
    assert dg.attributes["labels"] == ["promising", "small_n", "preprint"] and dg.attributes["preprint"] is True and dg.attributes["origin"] == "germline"
    assert dg.confidence_basis["claims"]["n_supporting"] == 1
    assert {s.kind.value for s in dg.sources} == {"db"} and any(s.id == "mock:paper:1" and s.date == "2026" for s in dg.sources)

    assert result.report["claims"] == {"in_bundle": 3, "in_graph": 2, "attached_to_edges": 1, "unattached": ["c_000002"], "dropped": [{"id": "c_000003", "reason": "variant 'mock:variant:404' is not a Variant node"}]}
    assert [s.name for s in graph.meta.sources] == ["mock backbone", "claims bundle (data/build/claims.json)"]
    assert graph.meta.sources[1].version == "mock-model" and graph.meta.sources[1].retrieved_at == "2026-10-03T11:00:00+00:00"
    assert any(s.source == "mock pubmed" for s in graph.meta.coverage["mock:disease:1"].searched)


def test_curated_seed_adds_a_source_record(store: Store) -> None:
    seeds = {**SEEDS, "mechanisms": {"source": "mock seed file", "transcribed_at": "2026-10-01", "mechanisms": [{"gene": "MOCKGENE2", "direction": "LoF", "origin": "germline", "disease_terms": ["Mock immune dysregulation 1"], "cluster_doc_confidence": "Background", "note": "mock"}]}}
    graph = b.build_graph(store, seeds, now=NOW)
    e = edge_between(graph, "mock:disease:1", "mech:MOCKGENE2:LoF", "has_mechanism")
    assert e.evidence_class is EvidenceClass.inferred and e.confidence == 0.4 and e.attributes["labels"] == ["curated_unverified"]
    assert e.attributes["origin"] == "germline", "the curated origin stays on the inferred, labelled edge"
    assert [s.model_dump() for s in e.sources] == [{"kind": "db", "id": "mock seed file MOCKGENE2:LoF", "date": "2026-10-01"}, {"kind": "db", "id": "MOCKDIS_1", "date": TS}]
    assert [s.name for s in graph.meta.sources] == ["mock backbone", "curated mechanism seeds: mock seed file"]
    assert graph.meta.sources[1].retrieved_at == "2026-10-01"
    weak = edge_between(graph, "mock:disease:1", "mock:gene:2", "associated_with")
    assert weak.attributes == {"labels": [b.BELOW_THRESHOLD_LABEL]}, "hard rule 10: a Background origin never surfaces on the observed edge"
    assert "origin" not in weak.confidence_basis


def test_upstream_sources_become_meta_source_records(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate(s)
        s.upsert_source(
            "mock backbone",
            retrieved_at=TS,
            license="mock",
            version="v0",
            notes={"evidence_score_min": 0.0, "evidence_score_max": 1.0, "upstream_sources": {"MOCK_UP_B": {"version": "", "license": "UNVERIFIED (mock)", "direct": 1, "indirect": 0}, "MOCK_UP_A": {"version": "7", "license": "mock CC", "direct": 2, "indirect": 1}}},
        )
        graph = b.build_graph(s, SEEDS, now=NOW)
    records = {src.name: src for src in graph.meta.sources}
    assert list(records) == ["mock backbone", "mock backbone upstream: MOCK_UP_A", "mock backbone upstream: MOCK_UP_B"]
    assert records["mock backbone upstream: MOCK_UP_A"].version == "7" and records["mock backbone upstream: MOCK_UP_A"].license == "mock CC"
    assert records["mock backbone upstream: MOCK_UP_B"].license == "UNVERIFIED (mock)" and records["mock backbone upstream: MOCK_UP_B"].retrieved_at == TS


def test_hpo_aspect_relations_are_scored_like_phenotypes(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate(s)
        s.upsert_edges([edge("mock:disease:1", "mock:phenotype:2", "has_inheritance", evidence_type=["TAS"], aspect=["I"], backbone_relation="PHENOTYPE_PRESENT")])
        graph = b.build_graph(s, SEEDS, now=NOW)
    assert "has_inheritance" in b.HPO_ANNOTATION_RELATIONS
    e = edge_between(graph, "mock:disease:1", "mock:phenotype:2", "has_inheritance")
    assert e.confidence == 0.75 and e.confidence_basis["rule"] == "disease_phenotype" and e.confidence_basis["aspect"] == ["I"]
    assert edge_between(graph, "mock:disease:1", "mock:phenotype:1", "has_phenotype").confidence_basis["aspect"] == []


# --------------------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------------------


def test_export_main_writes_graph_and_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store_path = tmp_path / "store" / "evidence.sqlite"
    with Store(store_path) as s:
        populate(s)
    out = tmp_path / "out" / "graph.json"
    report = tmp_path / "out" / "build_report.json"
    rc = export.main(["--skip-download", "--store", str(store_path), "--out", str(out), "--report", str(report)])
    assert rc == 0
    graph = Graph.load(out)
    assert graph.nodes and graph.edges
    data = json.loads(report.read_text(encoding="utf-8"))
    for key in ("built_at", "counts", "thresholds", "seed_resolution", "mechanisms", "timings_seconds", "source_versions", "store_counts", "loaders", "score_range"):
        assert key in data, key
    assert data["loaders"] == {name: {"status": "skipped (--skip-download)"} for name, _ in export.LOADERS}
    assert data["counts"]["edges"] == len(graph.edges) and data["store_counts"]["edges"] == 10
    # the real seed files hold names only; against this mock store they resolve to nothing, visibly
    assert data["seed_resolution"]["unresolved"]["genes"] == sorted(data["seed_resolution"]["genes"])
    printed = capsys.readouterr().out
    assert f"wrote {out}" in printed and "mechanisms:" in printed


def test_export_main_fails_on_invalid_claims_or_empty_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store_path = tmp_path / "evidence.sqlite"
    with Store(store_path) as s:
        populate(s)
    bad = tmp_path / "claims.json"
    bad.write_text(json.dumps({"extracted_at": "2026-10-03T11:00:00+00:00", "papers": [], "claims": [{"id": "c_000001"}]}), encoding="utf-8")
    rc = export.main(["--skip-download", "--store", str(store_path), "--claims", str(bad), "--out", str(tmp_path / "g.json"), "--report", str(tmp_path / "r.json")])
    assert rc == export.EXIT_CLAIMS_INVALID and "claims bundle invalid" in capsys.readouterr().err

    empty = tmp_path / "empty.sqlite"
    with Store(empty):
        pass
    rc = export.main(["--skip-download", "--store", str(empty), "--out", str(tmp_path / "g2.json"), "--report", str(tmp_path / "r2.json")])
    assert rc == export.EXIT_GRAPH_INVALID and "failed validation" in capsys.readouterr().err
    assert not (tmp_path / "g2.json").exists()
