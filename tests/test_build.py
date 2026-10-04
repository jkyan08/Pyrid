"""pipeline/graph/build.py and pipeline/export.py over a synthetic store in ``tmp_path``.

Placeholders only (``mock:`` ids, "Mock " labels, ``MOCKGENE<n>`` symbols); nothing here is a
resolved record. Fully offline: the export is run with ``--skip-download``.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from pipeline import export
from pipeline.graph import build as b
from pipeline.graph.confidence import INFERRED_LABEL, RELATION_CONFLICT_LABEL
from pipeline.models import ClaimsBundle, EdgeStatus, EvidenceClass, Graph, NodeType
from pipeline.sources.clinvar import scrub_doses
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


def edge(source: str, target: str, relation: str, source_db: str = "mock", **props: Any) -> dict[str, Any]:
    return {"source": source, "target": target, "relation": relation, "source_db": source_db, "props": props, "retrieved_at": TS}


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


def maybe_edge(graph: Graph, source: str, target: str, relation: str):
    hits = [e for e in graph.edges if e.source == source and e.target == target and e.relation == relation]
    return hits[0] if hits else None


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
    assert dg.sources[0].model_dump() == {"kind": "db", "id": "mock MOCKDIS_1->MOCKENSG1", "date": TS[:10]}, "the record behind the edge, dated by the retrieval day"

    weak = edge_between(graph, "mock:disease:1", "mock:gene:2", "associated_with")
    assert weak.attributes == {"labels": [b.BELOW_THRESHOLD_LABEL]}, "a weak seed-gene edge is exported, labelled, never pruned"
    assert weak.confidence == pytest.approx(0.05)
    assert weak.confidence_basis["below_threshold"] == {"disease_gene_min_score": 0.3, "keep_disgenet_direct": True}

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
    assert hm.sources[0].model_dump() == {"kind": "db", "id": "MOCKDIS_1", "date": TS[:10]}
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
    assert r["thresholds"]["max_diseases_per_seed_gene"] == 40 and r["thresholds"]["max_genes_per_disease"] == 10, "export defaults documented in cluster.yaml"
    assert r["edges_below_threshold"] == 1 and r["edges_with_relation_conflict"] == 1
    assert r["mechanisms"]["by_rule"] == {"R1.gof": 1}
    assert r["seed_resolution"]["genes"] == {"MOCKGENE1": ["mock:gene:1"], "MOCKGENE2": ["mock:gene:2"], "MOCKGENE9": []}
    assert r["seed_resolution"]["disease_terms"] == {"Mock immune dysregulation 1": ["mock:disease:1"], "Mock nothing": []}
    assert r["seed_resolution"]["drug_terms"] == {"mock-inib": ["mock:drug:1"], "mock-missing": []}
    assert r["seed_resolution"]["unresolved"] == {"genes": ["MOCKGENE9"], "disease_terms": ["Mock nothing"], "drug_terms": ["mock-missing"]}
    assert r["claims"] == {"in_bundle": 0, "in_graph": 0, "attached_to_edges": 0, "unattached": [], "variant_unresolved": [], "dropped": []}
    assert r["evidence"] == {"how": "no claims bundle"}
    assert r["patient_groups"] == {"status": "no patient_groups seeds"}
    assert r["prune"]["nodes_dropped"] == {} and r["prune"]["edges_dropped_by_relation"] == {}, "the whole mock is anchored by its seed genes"
    assert r["prune"]["diseases"] == {"direct_via_seed_genes": 1, "capped_away_per_seed_gene": 0, "term_matched": 1, "anchored_by_evidence": 0, "claim_referenced": 0, "kept": 2}
    assert r["sizes"]["total_bytes"] > 0 and set(r["sizes"]["nodes_by_type"]) == {"Disease", "Drug", "Gene", "Mechanism", "Pathway", "Phenotype"}

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


def test_merge_edge_unions_two_assertions_of_one_triple() -> None:
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    first = {"source": "mock:a", "target": "mock:b", "relation": "r", "source_db": "x", "evidence_class": "inferred", "confidence": 0.4, "confidence_basis": {"rule": "one"}, "status": "inferred_unsupported", "sources": [{"kind": "db", "id": "mock:1", "date": TS}], "supporting": [], "contradicting": [], "attributes": {"labels": [INFERRED_LABEL], "reason": "mock"}}
    second = {"source": "mock:a", "target": "mock:b", "relation": "r", "source_db": "y", "evidence_class": "observed", "confidence": 0.9, "confidence_basis": {"rule": "two"}, "status": "supported", "sources": [{"kind": "db", "id": "mock:2", "date": TS}], "supporting": ["c_000001"], "contradicting": [], "attributes": {"labels": ["other"], "origin": "germline"}}
    b._merge_edge(edges, first)
    merged = b._merge_edge(edges, second)
    assert len(edges) == 1 and merged is edges[("mock:a", "mock:b", "r")]
    assert merged["evidence_class"] == "observed" and merged["confidence"] == 0.9 and merged["status"] == "supported"
    assert [s["id"] for s in merged["sources"]] == ["mock:1", "mock:2"]
    assert merged["confidence_basis"]["rule"] == "one" and merged["confidence_basis"]["also_asserted_by"][0]["basis"] == {"rule": "two"}
    assert merged["attributes"] == {"labels": ["other"], "origin": "germline"}, "the inferred badge and the rule reason go when a database asserts the edge"
    assert merged["supporting"] == ["c_000001"]


# --------------------------------------------------------------------------------------
# loader layers: Variant, Study, Investigator edges and the inferred evidence class
# --------------------------------------------------------------------------------------


def populate_layers(store: Store) -> None:
    store.upsert_nodes(
        [
            node("mock:variant:1", "Variant", "Mock variant one", ["M1X"], keep_in_graph=True, has_somatic_block=False, protein_change="M1X", gene_symbols=["MOCKGENE1"], review_status="criteria provided, multiple submitters, no conflicts", classification="Pathogenic", title="Mock variant one", rcv=["mock"] * 30, traits=[{"trait_name": "x"}] * 5),
            node("mock:variant:2", "Variant", "Mock variant two", keep_in_graph=False, has_somatic_block=False, protein_change="M2X", gene_symbols=["MOCKGENE1"]),
            node("mock:variant:3", "Variant", "Mock variant three", keep_in_graph=False, has_somatic_block=True, protein_change="M3X", gene_symbols=["MOCKGENE1"]),
            node("mock:study:1", "Study", "Mock study one", record_id="NCT00000000", source_url="https://mock.invalid/study/1", overall_status="RECRUITING", phases=["PHASE2"], study_type="INTERVENTIONAL", enrollment=12, conditions=["Mock immune dysregulation 1"], interventions=[{"name": "Mock-inib", "type": "DRUG"}], eligibility={"criteria": "x" * 2000, "truncated": False}, sponsor="Mock sponsor", data_timestamp="2026-10-03", keywords=["k"] * 50, references=[{"pmid": "1"}] * 20),
            node("mock:person:1", "Investigator", "Mock Person", name={"full": "Mock Person"}, match_confidence="low", n_projects=1, role_label="PI on 1 NIH project", projects=[{"core_project_num": "MOCK1", "title": "Mock grant", "fiscal_years": [2026], "award_amount_total": 1, "agency_ic_admin": {"abbreviation": "MOCK", "name": "Mock institute"}, "detail_url": "https://mock.invalid/grant/1", "pmids": ["1"] * 80, "appl_ids": [1]}], source_url="https://mock.invalid/grant/1", pmids=["1"] * 80),
            node("mock:person:2", "Investigator", "Mock Nobody", name={"full": "Mock Nobody"}, match_confidence="low", n_projects=0, projects=[]),
        ]
    )
    store.upsert_edges(
        [
            edge("mock:variant:1", "mock:gene:1", "variant_of", "MockVar", evidence_class="observed", gene_symbol="MOCKGENE1", record_id="1", match={"method": "props.symbol", "matched_text": "MOCKGENE1"}),
            edge("mock:variant:2", "mock:gene:1", "variant_of", "MockVar", evidence_class="observed", gene_symbol="MOCKGENE1"),
            edge("mock:variant:3", "mock:gene:1", "variant_of", "MockVar", evidence_class="observed", gene_symbol="MOCKGENE1"),
            edge("mock:variant:1", "mock:disease:1", "has_condition", "MockVar", evidence_class="observed", review_status="criteria provided, multiple submitters, no conflicts", classification="Pathogenic", trait_name="Mock trait", record_id="1", n_scv=10, match={"method": "xref:MOCK", "matched_text": "MOCK:1"}),
            edge("mock:variant:3", "mock:disease:1", "has_condition", "MockVar", evidence_class="observed", review_status="no assertion criteria provided"),
            edge("mock:study:1", "mock:disease:1", "studies", "MockTrials", evidence_class="observed", record_id="NCT00000000", match={"method": "condition", "matched_text": "Mock immune dysregulation 1"}, overall_status="RECRUITING", enrollment=12),
            edge("mock:study:1", "mock:drug:1", "evaluates", "MockTrials", evidence_class="observed", record_id="NCT00000000", match={"method": "intervention_name", "matched_text": "Mock-inib"}),
            edge("mock:study:1", "mock:gene:1", "mentions", "MockTrials", evidence_class="inferred", record_id="NCT00000000", match={"method": "query_term", "matched_text": "MOCKGENE1"}),
            edge("mock:person:1", "mock:gene:1", "has_funded_project", "MockGrants", evidence_class="inferred", method="gene_symbol_in_title_or_abstract", matched_text=["MOCKGENE1"], n_projects=1, fiscal_years=[2026], agency=["MOCK"], award_amount_total=1, detail_urls=["https://mock.invalid/grant/1"]),
        ]
    )
    store.add_coverage("mock:variant:2", "mock trait link", "traits", "2026-10-03", 0)


def test_loader_layer_edges_are_scored_and_inferred_edges_capped(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate(s)
        populate_layers(s)
        result = b.build(s, SEEDS, now=NOW)
    graph = result.graph
    idx = graph.node_index()

    vo = edge_between(graph, "mock:variant:1", "mock:gene:1", "variant_of")
    assert vo.confidence == 0.95 and vo.confidence_basis["rule"] == "variant_of" and vo.confidence_basis["match"]["matched_text"] == "MOCKGENE1"
    hc = edge_between(graph, "mock:variant:1", "mock:disease:1", "has_condition")
    assert hc.confidence == 0.8 and hc.confidence_basis["matched"] == "multiple submitters, no conflicts" and hc.confidence_basis["classification"] == "Pathogenic"
    assert hc.evidence_class is EvidenceClass.observed and hc.status is EdgeStatus.supported
    assert edge_between(graph, "mock:variant:3", "mock:disease:1", "has_condition").confidence == 0.3
    st = edge_between(graph, "mock:study:1", "mock:disease:1", "studies")
    assert st.confidence == 0.8 and st.confidence_basis["rule"] == "study_edge" and st.confidence_basis["enrollment"] == 12
    assert {(s.kind.value, s.id) for s in st.sources} >= {("nct", "NCT00000000")}
    assert edge_between(graph, "mock:study:1", "mock:drug:1", "evaluates").confidence == 0.8
    mention = edge_between(graph, "mock:study:1", "mock:gene:1", "mentions")
    assert mention.evidence_class is EvidenceClass.inferred and mention.confidence == 0.3 and mention.status is EdgeStatus.supported
    assert mention.attributes["labels"] == [INFERRED_LABEL], "the UI renders the evidence_class badge"
    grant = edge_between(graph, "mock:person:1", "mock:gene:1", "has_funded_project")
    assert grant.evidence_class is EvidenceClass.inferred and grant.confidence == 0.4 and grant.confidence_basis["matched_text"] == ["MOCKGENE1"]
    assert ("url", "https://mock.invalid/grant/1") in {(s.kind.value, s.id) for s in grant.sources}
    # contact routes: a RePORTER PI's project page, on the node (both key names) and on the grant edge; never an address
    pi = idx["mock:person:1"]
    assert pi.props["contact_source"] == "https://mock.invalid/grant/1" and pi.props["contact"] == "https://mock.invalid/grant/1" and pi.props["contact_source_kind"] == "reporter_project_page"
    assert grant.attributes["contact_source"] == "https://mock.invalid/grant/1"
    assert result.report["contact_routes"]["by_kind"] == {"reporter_project_page": 1} and result.report["contact_routes"]["edges_with_contact_source"] == 1

    # Variants: keep_in_graph or somatic block stay, the plain record goes (and so does its edge)
    assert "mock:variant:1" in idx and "mock:variant:3" in idx and "mock:variant:2" not in idx
    assert maybe_edge(graph, "mock:variant:2", "mock:gene:1", "variant_of") is None
    assert result.report["prune"]["nodes_dropped"]["Variant"]["n"] == 1
    # Investigators: only with a surviving edge
    assert "mock:person:1" in idx and "mock:person:2" not in idx
    assert result.report["prune"]["nodes_dropped"]["Investigator"]["n"] == 1
    # Studies: all
    assert "mock:study:1" in idx
    # coverage rows for the pruned variant leave meta.coverage; the drop is counted and summarised
    assert "mock:variant:2" not in graph.meta.coverage and result.report["coverage_rows_dropped_for_pruned_entities"] == 1
    prune_rows = {(s.query.split(":")[0], s.hits) for s in graph.meta.coverage["source:build"].searched}
    assert ("Variant nodes not exported", 1) in prune_rows and ("Investigator nodes not exported", 1) in prune_rows


def test_props_are_slimmed_capped_and_scrubbed(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate(s)
        populate_layers(s)
        gene = node(
            "mock:gene:1", "Gene", "MOCKGENE1", [f"MOCKSYN{i}" for i in range(20)],
            symbol="MOCKGENE1", name="mock gene one", biotype="protein_coding", backbone_id="MOCKENSG1",
            xrefs=[{"id": "1", "source": "HGNC"}, {"id": "IPR1", "source": "InterPro"}, {"id": "P1", "source": "UniProt"}],
            function_descriptions=["x" * 5000],
        )
        gene["props"]["synonyms"] = ["bulk"] * 100  # the backbone's own bulky name list
        s.upsert_nodes(
            [
                gene,
                node("mock:disease:1", "Disease", "Mock immune dysregulation 1", ["MOCKGENE1 gain of function"], backbone_id="MOCKDIS_1", description="Mock autosomal dominant disorder. Contact mock.person@mock.invalid or +1 301-496-1234; given 5 mg twice daily. " + "y" * 600, xrefs=["MOCK:1"], therapeutic_areas=["mock"], sources={"direct": ["bulk"]}),
                node("mock:drug:1", "Drug", "Mock-inib", ["mockinib", "MOCKINIB 5 MG"], backbone_id="MOCKCHEMBL1", maximum_clinical_trial_phase=3.0, type="Small molecule", description="d" * 400, inchi="bulk", canonical_smiles="bulk"),
            ]
        )
        result = b.build(s, SEEDS, now=NOW)
    graph = result.graph
    idx = graph.node_index()
    gene = idx["mock:gene:1"]
    assert set(gene.props) == {"symbol", "name", "biotype", "xrefs", "backbone_id"}
    assert gene.props["xrefs"] == [{"id": "1", "source": "HGNC"}, {"id": "P1", "source": "UniProt"}]
    assert len(gene.synonyms) == b.MAX_SYNONYMS == 12
    disease = idx["mock:disease:1"]
    assert set(disease.props) == {"name", "description", "xrefs", "backbone_id", "therapeutic_areas"} - {"name"} | ({"name"} if "name" in disease.props else set())
    assert len(disease.props["description"]) <= 500 and disease.props["description"].endswith("…")
    drug = idx["mock:drug:1"]
    assert drug.props["max_phase"] == 3.0 and drug.props["drug_type"] == "Small molecule" and "inchi" not in drug.props
    assert len(drug.props["description"]) <= 300
    assert drug.synonyms == ["mockinib", "MOCKINIB [dose removed]"], "an RxNorm-style dose in a drug synonym is scrubbed (hard rule 7)"
    variant = idx["mock:variant:1"]
    assert set(variant.props) <= set(b.EXPORT_PROPS_KEEP["Variant"]) and "rcv" not in variant.props and "traits" not in variant.props
    assert variant.props["gene_symbol"] == "MOCKGENE1"
    study = idx["mock:study:1"]
    assert "keywords" not in study.props and "references" not in study.props
    assert len(study.props["eligibility"]["criteria"]) <= 1500 and study.props["eligibility"]["truncated"] is True
    person = idx["mock:person:1"]
    assert person.props["projects"] == [{"core_project_num": "MOCK1", "title": "Mock grant", "fiscal_years": [2026], "award_amount_total": 1, "detail_url": "https://mock.invalid/grant/1", "agency": "MOCK", "pmids": ["1"] * 50}]
    assert len(person.props["pmids"]) == 50
    text = graph.model_dump_json()
    assert "mock.person@mock.invalid" not in text and "301-496-1234" not in text and "5 mg" not in text
    assert "[email removed]" in text and "[phone removed]" in text and "[dose removed]" in text
    # the report counts the placeholders that reached the export (one e-mail, one phone, the description's dose run and the drug synonym)
    assert result.report["scrub_placeholders"] == {"emails_removed": 1, "phones_removed": 1, "doses_removed": 2}


def test_dois_survive_scrubbing_on_export(store: Store) -> None:
    """Hard rule 1: a DOI suffix such as ``j.0105-2896.2005.00220.x`` looks like a phone number to
    the contact pass; the identifier must leave the export unchanged."""
    dois = ["10.1111/j.0105-2896.2005.00220.x", "10.1111/j.1749-6632.2011.06387.x", "10.7534/j.issn.1009-2137.2018.01.050", "10.13201/j.issn.1001-1781.2017.11.019"]
    for doi in dois:
        assert b.scrub_text(doi) == doi
        assert b.scrub_text(f"See doi:{doi}. Tel: 0049 30 1234567.") == f"See doi:{doi}. {b.PHONE_PLACEHOLDER}."
    paper = {"id": "pmid:0", "type": "Paper", "label": "Mock paper", "synonyms": [], "props": {"title": "Mock", "doi": dois[0], "abstract": f"Published as {dois[0]} (doi:{dois[1]}) by one@mock.invalid.", "authors": []}}
    slim = b.slim_node(paper)
    assert slim["props"]["doi"] == dois[0]
    assert slim["props"]["abstract"] == f"Published as {dois[0]} (doi:{dois[1]}) by {b.EMAIL_PLACEHOLDER}."
    assert b.PHONE_PLACEHOLDER not in json.dumps(slim)


def test_scrub_text_rules() -> None:
    assert b.scrub_text("write to a.b+c@mock.invalid or call +44 20 7946 0958 or (301) 496-1234") == f"write to {b.EMAIL_PLACEHOLDER} or call {b.PHONE_PLACEHOLDER} or {b.PHONE_PLACEHOLDER}"
    assert b.scrub_text("Tel: 0049 30 1234567.") == f"{b.PHONE_PLACEHOLDER}."
    assert b.scrub_text("between 2016-2020, ZIP 20892-1674, c.1915-11G>A, IL2RG") == "between 2016-2020, ZIP 20892-1674, c.1915-11G>A, IL2RG", "ranges, ZIP+4 and HGVS are not phone numbers or doses"
    assert b.scrub_text("ruxolitinib 10 mg/kg/day for 6 weeks") == "ruxolitinib [dose removed] for 6 weeks"
    assert b.scrub_text("ORCID 0000-0002-1825-009X, phone 0000 0002 1825 0097") == f"ORCID 0000-0002-1825-009X, {b.PHONE_PLACEHOLDER}", "an ORCID is an identifier, not a phone number; 'phone <digits>' goes as one unit"
    assert b.scrub_text("0000-0001-5018-0466") == "0000-0001-5018-0466"
    assert b.scrub_text(None) is None and b.scrub_text("") == ""
    assert b.scrub_tree({"a": ["x@y.org", 3, {"b": "BID"}]}) == {"a": [b.EMAIL_PLACEHOLDER, 3, {"b": "[dose removed]"}]}


# --------------------------------------------------------------------------------------
# pruning
# --------------------------------------------------------------------------------------

OT = {"direct": ["MOCK_OT"], "indirect": []}
CURATED = {"direct": ["MOCK_OT", "DISGENET"], "indirect": []}


def populate_wide(store: Store) -> None:
    """A wider backbone slice: the export must keep only what the inclusion rule and caps allow."""
    store.upsert_nodes(
        [
            node("mock:gene:1", "Gene", "MOCKGENE1", symbol="MOCKGENE1", backbone_id="G1"),
            node("mock:gene:x", "Gene", "MOCKX", symbol="MOCKX", backbone_id="GX"),
            node("mock:gene:y", "Gene", "MOCKY", symbol="MOCKY", backbone_id="GY"),
            node("mock:gene:z", "Gene", "MOCKZ", symbol="MOCKZ", backbone_id="GZ"),
            node("mock:disease:a", "Disease", "Mock disease A", backbone_id="DA"),
            node("mock:disease:b", "Disease", "Mock disease B", backbone_id="DB"),
            node("mock:disease:c", "Disease", "Mock disease C", backbone_id="DC"),
            node("mock:disease:d", "Disease", "Mock disease D", backbone_id="DD"),
            node("mock:disease:e", "Disease", "Mock disease E", backbone_id="DE"),
            node("mock:disease:p", "Disease", "Mock parent P", backbone_id="DP"),
            node("mock:disease:q", "Disease", "Mock grandparent Q", backbone_id="DQ"),
            node("mock:phenotype:a", "Phenotype", "Mock phenotype A", backbone_id="PA"),
            node("mock:phenotype:b", "Phenotype", "Mock phenotype B", backbone_id="PB"),
            node("mock:phenotype:anc", "Phenotype", "Mock phenotype ancestor", backbone_id="PANC"),
            node("mock:pathway:1", "Pathway", "Mock reactome pathway", ontology="Reactome", backbone_id="R1"),
            node("mock:pathway:go", "Pathway", "Mock GO process", ontology="GO", backbone_id="GO1"),
            node("mock:drug:a", "Drug", "Mock-inib-A", backbone_id="DRA"),
            node("mock:drug:b", "Drug", "Mock-umab-B", backbone_id="DRB"),
            node("mock:drug:c", "Drug", "Mockazole", backbone_id="DRC"),
        ]
    )
    store.upsert_edges(
        [
            edge("mock:disease:a", "mock:gene:1", "associated_with", evidence_score=0.8, sources=CURATED),  # curated + high: included
            edge("mock:disease:b", "mock:gene:1", "associated_with", evidence_score=0.5, sources=OT),  # above the floor, not curated, below high: excluded
            edge("mock:disease:c", "mock:gene:1", "associated_with", evidence_score=0.1, sources=CURATED),  # curated but below the floor: excluded
            edge("mock:disease:d", "mock:gene:1", "associated_with", evidence_score=0.1, sources=OT),  # term-matched disease: seed-gene edge kept, labelled
            edge("mock:disease:e", "mock:gene:1", "associated_with", evidence_score=0.9, sources=OT),  # high: included
            edge("mock:disease:a", "mock:gene:x", "associated_with", evidence_score=0.7, sources=OT),  # other gene, high
            edge("mock:disease:a", "mock:gene:y", "associated_with", evidence_score=0.5, sources=OT),  # other gene, fails the rule
            edge("mock:disease:a", "mock:gene:z", "associated_with", evidence_score=0.65, sources=OT),  # other gene, high (second by score)
            edge("mock:disease:b", "mock:gene:x", "associated_with", evidence_score=0.9, sources=OT),  # belongs to an excluded disease
            edge("mock:disease:a", "mock:disease:p", "is_a", backbone_relation="PARENT"),
            edge("mock:disease:p", "mock:disease:q", "is_a", backbone_relation="PARENT"),
            edge("mock:disease:a", "mock:phenotype:a", "has_phenotype", evidence_type=["PCS"]),
            edge("mock:disease:b", "mock:phenotype:b", "has_phenotype", evidence_type=["PCS"]),
            edge("mock:phenotype:a", "mock:phenotype:anc", "is_a", backbone_relation="PARENT"),
            edge("mock:phenotype:b", "mock:phenotype:anc", "is_a", backbone_relation="PARENT"),
            edge("mock:gene:1", "mock:pathway:1", "in_pathway"),
            edge("mock:gene:x", "mock:pathway:1", "in_pathway"),
            edge("mock:gene:1", "mock:pathway:go", "in_pathway", evidence=["IDA"]),
            edge("mock:gene:x", "mock:pathway:go", "in_pathway", evidence=["IDA"]),  # GO process of a non-seed gene: dropped
            edge("mock:drug:a", "mock:gene:1", "inhibits", backbone_relation="INHIBITOR"),
            edge("mock:drug:a", "mock:disease:a", "indicated_for", backbone_relation="INDICATION"),
            edge("mock:drug:b", "mock:disease:a", "indicated_for", backbone_relation="INDICATION"),  # drug without a seed-gene edge: dropped
            edge("mock:drug:c", "mock:disease:b", "indicated_for", backbone_relation="INDICATION"),  # seed-term drug, excluded disease
        ]
    )
    store.add_coverage("mock:disease:b", "mock", "phenotypes", "2026-10-03", 1)
    store.upsert_source("mock backbone", retrieved_at=TS, license="mock", notes={"evidence_score_min": 0.0, "evidence_score_max": 1.0})


WIDE_SEEDS: dict[str, Any] = {
    "genes": ["MOCKGENE1"],
    "disease_terms": ["Mock disease D"],
    "drug_terms": ["Mockazole"],
    "thresholds": {"disease_gene_min_score": 0.3, "keep_disgenet_direct": True, "include_parent_levels": 1, "max_genes_per_disease": 1},
}


def test_prune_applies_inclusion_rule_caps_and_anchors(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate_wide(s)
        result = b.build(s, WIDE_SEEDS, now=NOW)
    graph = result.graph
    ids = set(graph.node_index())
    diseases = {i for i in ids if i.startswith("mock:disease:")}
    assert diseases == {"mock:disease:a", "mock:disease:d", "mock:disease:e", "mock:disease:p"}, "A and E pass the rule, D is a seed term, P is A's parent; B, C and the grandparent go"
    genes = {i for i in ids if i.startswith("mock:gene:")}
    assert genes == {"mock:gene:1", "mock:gene:x"}, "the seed gene and the single strongest other gene (max_genes_per_disease=1); Y fails the rule, Z is capped"
    assert {i for i in ids if i.startswith("mock:drug:")} == {"mock:drug:a", "mock:drug:c"}, "a seed-gene inhibitor and a seed drug term; B has neither"
    assert {i for i in ids if i.startswith("mock:phenotype:")} == {"mock:phenotype:a", "mock:phenotype:anc"}, "annotated to an exported disease plus ancestors"
    assert {i for i in ids if i.startswith("mock:pathway:")} == {"mock:pathway:1", "mock:pathway:go"}
    assert maybe_edge(graph, "mock:gene:x", "mock:pathway:go", "in_pathway") is None, "GO processes only for seed genes"
    assert maybe_edge(graph, "mock:gene:x", "mock:pathway:1", "in_pathway") is not None
    weak = edge_between(graph, "mock:disease:d", "mock:gene:1", "associated_with")
    assert weak.attributes["labels"] == [b.BELOW_THRESHOLD_LABEL], "a weak seed-gene edge to a term-matched disease stays visible"
    assert maybe_edge(graph, "mock:disease:a", "mock:gene:y", "associated_with") is None and maybe_edge(graph, "mock:disease:a", "mock:gene:z", "associated_with") is None
    assert maybe_edge(graph, "mock:drug:c", "mock:disease:b", "indicated_for") is None, "drug-disease edges only for exported diseases"

    cov = graph.meta.coverage
    below = [s for s in cov["mock:disease:a"].searched if s.source == b.PRUNE_SOURCE]
    assert len(below) == 1 and below[0].hits == 1 and "below the disease-gene inclusion rule" in below[0].query and "DISGENET" in below[0].query
    assert "mock:disease:b" not in cov, "coverage rows of pruned entities leave meta.coverage"
    summary = {s.query.split(":")[0]: s.hits for s in cov["source:build"].searched}
    assert summary == {"Disease nodes not exported": 3, "Drug nodes not exported": 1, "Gene nodes not exported": 2, "Phenotype nodes not exported": 1}
    r = result.report["prune"]
    assert r["diseases"] == {"direct_via_seed_genes": 2, "capped_away_per_seed_gene": 0, "term_matched": 1, "anchored_by_evidence": 0, "claim_referenced": 0, "kept": 4}
    assert r["genes"]["capped_away_per_disease"] == 1 and r["associated_with_below_inclusion_rule"] == 1, "only A->Y fails the rule as an other-gene edge; B->1 and C->1 are seed-gene edges that leave with their diseases"
    assert r["go_in_pathway_edges_dropped_non_seed_gene"] == 1
    assert r["nodes_dropped"]["Disease"]["n"] == 3 and "inclusion rule" in r["nodes_dropped"]["Disease"]["reason"]
    assert r["edges_dropped_by_relation"]["associated_with"] == 5  # B->1, C->1 and B->X go with B / C; A->Y (rule); A->Z (cap)
    assert result.report["coverage_rows_dropped_for_pruned_entities"] >= 1


def test_prune_caps_diseases_per_seed_gene_and_keeps_evidence_anchors(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate_wide(s)
        # an observed evidence edge (a ClinVar-style layer) reaches disease B, which the rule excludes
        s.upsert_nodes([node("mock:variant:1", "Variant", "Mock variant one", keep_in_graph=True, gene_symbols=["MOCKGENE1"])])
        s.upsert_edges([edge("mock:variant:1", "mock:disease:b", "has_condition", "MockVar", evidence_class="observed", review_status="reviewed by expert panel")])
        seeds = {**WIDE_SEEDS, "thresholds": {**WIDE_SEEDS["thresholds"], "max_diseases_per_seed_gene": 1}}
        result = b.build(s, seeds, now=NOW)
    ids = set(result.graph.node_index())
    assert "mock:disease:e" in ids and "mock:disease:a" not in ids, "with a cap of one per seed gene only the top-scored disease (E, 0.9) enters via the seed gene"
    assert "mock:disease:b" in ids, "an observed evidence edge anchors its disease even when the backbone rule excludes it"
    assert "mock:disease:d" in ids, "seed terms are anchors"
    assert edge_between(result.graph, "mock:variant:1", "mock:disease:b", "has_condition").confidence == 0.9
    r = result.report["prune"]
    assert r["diseases"]["capped_away_per_seed_gene"] == 1 and r["diseases"]["anchored_by_evidence"] == 1


def test_prune_keeps_nodes_a_claim_resolved_to(tmp_path: Path) -> None:
    """Design item G: a Drug (or Gene) named by a claim stays exported even when the claim's other
    side is unresolved, so the claim in claims[] never points at a node that is not in nodes[]."""
    bundle = ClaimsBundle.model_validate(
        {
            "extracted_at": TS,
            "model": "mock-model",
            "papers": [{"id": "mock:paper:1", "type": "Paper", "label": "Mock paper", "synonyms": [], "props": {"abstract": "Mock-umab-B helped one mock patient with a mock condition. A mock compound treats mock disease B.", "year": 2026}}],
            "claims": [
                {"id": "c_000001", "pmid": "", "paper": "mock:paper:1", "quote": "Mock-umab-B helped one mock patient with a mock condition.", "polarity": "supports", "subject": "mock:drug:b", "predicate": "treats", "object": "text:a mock condition", "variant": "", "study_type": "case_report", "n_patients": 1},
                {"id": "c_000002", "pmid": "", "paper": "mock:paper:1", "quote": "A mock compound treats mock disease B.", "polarity": "supports", "subject": "text:a mock compound", "predicate": "treats", "object": "mock:disease:b", "variant": "", "study_type": "review", "n_patients": None},
            ],
        }
    )
    with Store(tmp_path / "evidence.sqlite") as s:
        populate_wide(s)
        without = b.build(s, WIDE_SEEDS, now=NOW)
        result = b.build(s, WIDE_SEEDS, bundle, now=NOW)
    idx = result.graph.node_index()
    assert "mock:drug:b" not in without.graph.node_index() and "mock:disease:b" not in without.graph.node_index(), "pruned without a claim: an indication-only drug, a disease failing the inclusion rule"
    assert "mock:drug:b" in idx and "mock:disease:b" in idx, "the claims' resolved endpoints anchor the drug and the disease"
    prune = result.report["prune"]
    assert prune["drugs"]["claim_referenced"] == 1 and prune["diseases"]["claim_referenced"] == 1 and prune["genes"]["claim_referenced"] == 0
    assert [(c.subject, c.object) for c in result.graph.claims] == [("mock:drug:b", "text:a mock condition"), ("text:a mock compound", "mock:disease:b")]
    assert result.report["claims"]["unattached"] == ["c_000001", "c_000002"]
    for entity in ("mock:drug:b", "mock:disease:b"):
        assert any(r.source == b.CLAIMS_COVERAGE_SOURCE and r.hits == 0 for r in result.graph.meta.coverage[entity].searched)
    assert b.CLAIMS_COVERAGE_SOURCE == "unresolved literature mention"


def test_min_papers_per_investigator_threshold(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(sys.modules, "pipeline.graph.evidence", _fake_evidence_module(calls))
    seeds = {**SEEDS, "thresholds": {**SEEDS["thresholds"], "min_papers_per_investigator": 2}}
    result = b.build(store, seeds, _evidence_bundle(), now=NOW)
    assert "person:mock-author" not in result.graph.node_index(), "a one-paper author is below the threshold"
    assert maybe_edge(result.graph, "person:mock-author", "mock:paper:1", "author_of") is None
    assert result.report["prune"]["investigators"] == {"before": 1, "below_min_papers": 1, "kept": 0}
    assert result.report["prune"]["nodes_dropped"]["Investigator"] == {"n": 1, "reason": "no surviving edge, or fewer than min_papers_per_investigator (2) papers and no ORCID"}
    default = b.build(store, SEEDS, _evidence_bundle(), now=NOW)
    assert "person:mock-author" in default.graph.node_index() and default.report["prune"]["investigators"] == {"before": 1, "below_min_papers": 0, "kept": 1}

    # an ORCID exempts a one-paper author from the threshold (unless the exemption is switched off)
    module = sys.modules["pipeline.graph.evidence"]
    original = module.integrate_claims

    def with_orcid(nodes: Any, edges: Any, bundle: Any, *, today: str, scrub: Any, **kwargs: Any) -> Any:
        out = original(nodes, edges, bundle, today=today, scrub=scrub, **kwargs)
        out.investigator_nodes["person:mock-author"]["props"]["orcid"] = "0000-0002-1825-009X"
        return out

    monkeypatch.setattr(module, "integrate_claims", with_orcid)
    kept = b.build(store, seeds, _evidence_bundle(), now=NOW)
    assert kept.graph.node_index()["person:mock-author"].props["orcid"] == "0000-0002-1825-009X"
    strict = b.build(store, {**seeds, "thresholds": {**seeds["thresholds"], "keep_orcid_investigators": False}}, _evidence_bundle(), now=NOW)
    assert "person:mock-author" not in strict.graph.node_index()
    # with the exemption off the cap is handed to the evidence module too, so its report counts what is exported
    assert [c["investigator_min_papers"] for c in calls] == [1, 1, 1, 2]


def test_orcid_investigators_get_their_public_orcid_page_as_contact_route(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    module = _fake_evidence_module(calls)
    plain = module.integrate_claims

    def with_orcid(nodes: Any, edges: Any, bundle: Any, *, today: str, scrub: Any, **kwargs: Any) -> Any:
        out = plain(nodes, edges, bundle, today=today, scrub=scrub, **kwargs)
        # the same person under an orcid: id (PubMed supplied an ORCID): rename the node and its edges
        node_ = out.investigator_nodes.pop("person:mock-author")
        node_["id"] = "orcid:0000-0002-1825-009X"
        node_["props"]["orcid"] = "0000-0002-1825-009X"
        out.investigator_nodes[node_["id"]] = node_
        out.new_edges = {(node_["id"] if s == "person:mock-author" else s, t, r): {**e, "source": node_["id"] if s == "person:mock-author" else s} for (s, t, r), e in out.new_edges.items()}
        return out

    monkeypatch.setattr(module, "integrate_claims", with_orcid)
    monkeypatch.setitem(sys.modules, "pipeline.graph.evidence", module)
    result = b.build(store, SEEDS, _evidence_bundle(), now=NOW)
    author = result.graph.node_index()["orcid:0000-0002-1825-009X"]
    assert author.props["contact_source"] == "https://orcid.org/0000-0002-1825-009X" and author.props["contact"] == author.props["contact_source"]
    assert author.props["contact_source_kind"] == "orcid_public_page"
    assert edge_between(result.graph, author.id, "mock:disease:1", "publishes_on").attributes["contact_source"] == "https://orcid.org/0000-0002-1825-009X"
    assert "contact_source" not in edge_between(result.graph, author.id, "mock:paper:1", "author_of").attributes, "only publishes_on / has_funded_project carry the route"
    assert result.report["contact_routes"]["by_kind"] == {"orcid_public_page": 1} and result.report["contact_routes"]["edges_with_contact_source"] == 1
    assert "@" not in json.dumps(author.props), "scrubbed, and the ORCID survives the phone scrubber"


def test_inferred_text_matches_never_anchor_nodes(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate_wide(s)
        s.upsert_nodes([node("mock:person:1", "Investigator", "Mock Person", name={"full": "Mock Person"}, match_confidence="low", projects=[])])
        s.upsert_edges([edge("mock:person:1", "mock:disease:b", "has_funded_project", "MockGrants", evidence_class="inferred", method="disease_name_in_title_or_abstract", matched_text=["Mock disease B"], n_projects=1)])
        result = b.build(s, WIDE_SEEDS, now=NOW)
    ids = set(result.graph.node_index())
    assert "mock:disease:b" not in ids and "mock:person:1" not in ids, "an inferred grant text match cannot pull a disease in, and the investigator then has no edge"
    assert result.report["prune"]["nodes_dropped"]["Investigator"]["n"] == 1


# --------------------------------------------------------------------------------------
# patient groups and registries
# --------------------------------------------------------------------------------------

PATIENT_GROUPS: dict[str, Any] = {
    "meta": {"curated_by": "mock", "date": "2026-10-03"},
    "groups": [
        {
            "name": "Mock Immune Foundation",
            "url": "https://mock.invalid/foundation",
            "kind": "patient_group",
            "diseases": ["Mock immune dysregulation 1", "Mock parent disorder 2 (umbrella)", "Mock parent disorder 2 / Mock unknown thing"],
            "scope": "umbrella",
            "verified": {"retrieved_at": "2026-10-02", "method": "webfetch", "note": "n" * 600},
            "source_of_claim": "mock seed row",
        },
        {"name": "Mock Registry", "url": "https://mock.invalid/registry", "kind": "registry", "diseases": ["Mock immune dysregulation 1"], "scope": "dedicated", "verified": {"retrieved_at": "2026-10-03", "method": "webfetch", "note": "ok"}},
        {"name": "Mock Lonely Group", "url": "https://mock.invalid/lonely", "kind": "foundation", "diseases": ["Mock no such disease"], "scope": "dedicated", "verified": {"retrieved_at": "2026-10-03", "method": "webfetch"}},
    ],
    "searches": [
        {"directory": "Mock directory", "url": "https://mock.invalid/search?q=x", "query": "mock dedicated group", "date": "2026-10-03", "hits": 0, "outcome": "nothing"},
        {"directory": "Mock blocked directory", "url": "https://mock.invalid/blocked", "query": "mock dedicated group", "date": "2026-10-03", "hits": None, "outcome": "blocked"},
        {"directory": "Mock directory", "url": "https://mock.invalid/search?q=y", "query": "other group", "date": "2026-10-03", "hits": 2, "outcome": "two leads"},
    ],
}


def test_patient_groups_become_nodes_edges_and_coverage(store: Store) -> None:
    result = b.build(store, {**SEEDS, "patient_groups": PATIENT_GROUPS}, now=NOW)
    graph = result.graph
    idx = graph.node_index()
    foundation = idx["org:mock-immune-foundation"]
    assert foundation.type is NodeType.PatientGroup and foundation.label == "Mock Immune Foundation"
    assert foundation.props["kind"] == "patient_group" and foundation.props["scope"] == "umbrella" and foundation.props["verified_retrieved_at"] == "2026-10-02"
    assert foundation.props["method"] == "webfetch" and len(foundation.props["note"]) <= 400 and foundation.props["source_of_claim"] == "mock seed row"
    assert idx["org:mock-registry"].type is NodeType.Registry
    assert idx["org:mock-lonely-group"].type is NodeType.PatientGroup, "a verified row with no resolved disease is still exported (its gap is in coverage)"

    e = edge_between(graph, "org:mock-immune-foundation", "mock:disease:1", "supports_community")
    assert e.evidence_class is EvidenceClass.observed and e.confidence == 0.8 and e.status is EdgeStatus.supported
    assert e.confidence_basis["matched_term"] == "Mock immune dysregulation 1" and e.confidence_basis["url"] == "https://mock.invalid/foundation" and e.confidence_basis["retrieved_at"] == "2026-10-02"
    assert [s.model_dump() for s in e.sources] == [{"kind": "url", "id": "https://mock.invalid/foundation", "date": "2026-10-02"}]
    umbrella = edge_between(graph, "org:mock-immune-foundation", "mock:disease:2", "supports_community")
    assert umbrella.confidence_basis["matched_term"] == "Mock parent disorder 2" and umbrella.confidence_basis["seed_terms"] == ["Mock parent disorder 2 (umbrella)", "Mock parent disorder 2"], "two seed terms naming one disease give one edge"
    assert edge_between(graph, "org:mock-registry", "mock:disease:1", "covers").evidence_class is EvidenceClass.observed

    cov = graph.meta.coverage
    terms = {(s.query, s.hits) for s in cov["org:mock-immune-foundation"].searched if s.source == b.PATIENT_GROUP_TERM_SOURCE}
    assert terms == {("Mock immune dysregulation 1", 1), ("Mock parent disorder 2 (umbrella)", 1), ("Mock parent disorder 2", 1), ("Mock unknown thing", 0)}
    assert [(s.query, s.hits) for s in cov["org:mock-lonely-group"].searched] == [("Mock no such disease", 0)]
    searches = cov["seed:patient_group_search:Mock directory"].searched
    assert [(s.source, s.query, s.hits) for s in searches] == [("Mock directory", "mock dedicated group (https://mock.invalid/search?q=x)", 0), ("Mock directory", "other group (https://mock.invalid/search?q=y)", 2)]
    blocked = cov["seed:patient_group_search:Mock blocked directory"].searched[0]
    assert blocked.hits == 0 and blocked.query.endswith("[not executable]")
    assert graph.meta.sources[-1].name == "patient group seeds: pipeline/seeds/patient_groups.yaml" and graph.meta.sources[-1].retrieved_at == "2026-10-03"
    assert result.report["patient_groups"] == {
        "groups": 3,
        "by_type": {"PatientGroup": 2, "Registry": 1},
        "edges": 3,
        "edges_by_resolution": {"direct": 3, "mechanism": 0, "via_mechanism": 0, "via_ancestor": 0},
        "terms_resolved": 4,
        "terms_resolved_via_mechanism": 0,
        "terms_unresolved": 2,
        "searches_logged": 3,
        "zero_hit_searches": 2,
        "descendant_depth": 3,
        "max_descendants_per_term": 200,
        "descendant_cap_hits": [],
        "directory_search_gaps": {
            "rule": "zero-hit directory search attached to every disease a matching seed term reached (query/outcome contains the normalised term)",
            "rows_attached": 0,
            "diseases": 0,
            "by_search": {},
        },
    }, "mock:disease:2 is disease 1's PARENT, so the umbrella rule adds nothing here and no resolution rows appear; no search names a seed term"
    assert not any("[resolution:" in s.query for s in cov["org:mock-immune-foundation"].searched)
    # asset attributes (Act tab): a direct seed match is reusable now, nothing differs, nothing to review
    assert {k: e.attributes[k] for k in ("verdict", "differs", "needs_review")} == {"verdict": "reusable_now", "differs": [], "needs_review": []}
    assert edge_between(graph, "org:mock-registry", "mock:disease:1", "covers").attributes["verdict"] == "reusable_now"


def test_zero_hit_directory_searches_naming_a_seed_term_reach_its_diseases(store: Store) -> None:
    pg = {
        **PATIENT_GROUPS,
        "searches": [
            # names the foundation's umbrella term (plural, different case) in the QUERY: attached to every disease the term reached
            {"directory": "Mock directory A", "url": "https://mock.invalid/a", "query": "MOCK PARENT DISORDER 2s patient organisation", "date": "2026-10-03", "hits": 0, "outcome": "nothing"},
            # names the dedicated term only in the OUTCOME text, and was not executable (null hits -> 0)
            {"directory": "Mock directory B", "url": "https://mock.invalid/b", "query": "MOCKGENE1 group", "date": "2026-10-03", "hits": None, "outcome": "Blocked; no Mock immune dysregulation 1 group could be read."},
            # a zero-hit search naming no seed term: stays a seed:* row only
            {"directory": "Mock directory C", "url": "https://mock.invalid/c", "query": "some other thing", "date": "2026-10-03", "hits": 0, "outcome": "nothing"},
            # a search WITH hits naming a seed term: not a gap, not attached
            {"directory": "Mock directory D", "url": "https://mock.invalid/d", "query": "Mock immune dysregulation 1 group", "date": "2026-10-03", "hits": 2, "outcome": "two leads"},
            # names the unresolved term of the lonely group: no disease to attach to
            {"directory": "Mock directory E", "url": "https://mock.invalid/e", "query": "Mock no such disease group", "date": "2026-10-03", "hits": 0, "outcome": "nothing"},
        ],
    }
    result = b.build(store, {**SEEDS, "patient_groups": pg}, now=NOW)
    cov = result.graph.meta.coverage
    d1 = [(s.source, s.query, s.hits) for s in cov["mock:disease:1"].searched if s.source.startswith("Mock directory")]
    d2 = [(s.source, s.query, s.hits) for s in cov["mock:disease:2"].searched if s.source.startswith("Mock directory")]
    assert d2 == [("Mock directory A", "MOCK PARENT DISORDER 2s patient organisation (https://mock.invalid/a)", 0)], "the umbrella term's disease gets the search that names it (plural tolerated)"
    assert d1 == [("Mock directory B", "MOCKGENE1 group (https://mock.invalid/b) [not executable]", 0)], "matched through the outcome text; the executed search with hits and the unrelated search are not gaps here"
    for directory in ("A", "B", "C", "D", "E"):
        assert f"seed:patient_group_search:Mock directory {directory}" in cov, "the seed:* rows stay"
    report = result.report["patient_groups"]["directory_search_gaps"]
    assert report["rows_attached"] == 2 and report["diseases"] == 2
    assert report["by_search"] == {
        "seed:patient_group_search:Mock directory A": {"query": "MOCK PARENT DISORDER 2s patient organisation", "matched_terms": ["Mock parent disorder 2", "Mock parent disorder 2 (umbrella)"], "diseases": 1},
        "seed:patient_group_search:Mock directory B": {"query": "MOCKGENE1 group", "matched_terms": ["Mock immune dysregulation 1"], "diseases": 1},
    }
    assert b._term_pattern("myeloproliferative neoplasms (umbrella for the three above)").search("myeloproliferative neoplasm patient organization")
    assert b._term_pattern("STAT1 gain of function").search("stat1 gain of function patient organization") and not b._term_pattern("STAT1 gain of function").search("STAT1")
    assert b._term_pattern("") is None


def test_build_patient_groups_without_seeds_is_a_no_op() -> None:
    out = b.build_patient_groups(None, {}, today="2026-10-03")
    assert out.nodes == {} and out.edges == [] and out.coverage == [] and out.source is None
    knobs_only = b.build_patient_groups({"descendant_depth": 1}, {}, today="2026-10-03")
    assert knobs_only.report == {"status": "no patient_groups seeds"} and knobs_only.source is None, "the cluster.yaml knobs alone are not a seed file"


# --- resolution rules: mechanism label (R-A) and umbrella descendants (R-B) ------------------


def test_normalize_mechanism_term_and_options() -> None:
    assert b.normalize_mechanism_term("MOCKGENE1 GoF") == "mockgene1 gain of function"
    assert b.normalize_mechanism_term("MOCKGENE1-GoF (umbrella)") == "mockgene1 gain of function"
    assert b.normalize_mechanism_term("  mockgene2_LoF ") == "mockgene2 loss of function"
    assert b.normalize_mechanism_term("MOCKGENE3 dn") == "mockgene3 dominant negative"
    assert b.normalize_mechanism_term("MOCKGENE1 somatic gain of function") == "mockgene1 somatic gain of function"
    assert b.normalize_mechanism_term(None) == ""
    assert b.patient_group_options({}) == {"descendant_depth": 3, "max_descendants_per_term": 200}
    assert b.patient_group_options({"patient_groups": {"descendant_depth": 1, "max_descendants_per_term": None, "groups": []}}) == {"descendant_depth": 1, "max_descendants_per_term": 200}
    assert b.patient_group_options(None) == b.DEFAULT_PATIENT_GROUP_OPTIONS


def _group(name: str, terms: list[str], kind: str = "patient_group") -> dict[str, Any]:
    return {"name": name, "url": f"https://mock.invalid/{b.slugify(name)}", "kind": kind, "diseases": terms, "scope": "umbrella", "verified": {"retrieved_at": "2026-10-02", "method": "webfetch", "note": "ok"}}


def test_term_matching_a_mechanism_label_links_the_mechanism_and_its_diseases(store: Store) -> None:
    # "MOCKGENE1 GoF" matches no Disease label / synonym (disease 1's synonym is spelled out) but
    # equals the Mechanism label "MOCKGENE1 gain of function" once both are normalised.
    pg = {"meta": {"date": "2026-10-03"}, "groups": [_group("Mock Mechanism Alliance", ["MOCKGENE1 GoF", "Mock nothing at all"]), _group("Mock Mechanism Registry", ["MOCKGENE1-gof"], kind="registry")], "searches": []}
    result = b.build(store, {**SEEDS, "patient_groups": pg}, now=NOW)
    graph = result.graph
    assert Graph.model_validate(graph.model_dump(mode="json"))
    idx = graph.node_index()
    assert idx["mech:MOCKGENE1:GoF"].label == "MOCKGENE1 gain of function"

    mech_edge = edge_between(graph, "org:mock-mechanism-alliance", "mech:MOCKGENE1:GoF", "supports_community")
    assert mech_edge.evidence_class is EvidenceClass.observed and mech_edge.confidence == 0.8 and mech_edge.status is EdgeStatus.supported
    assert mech_edge.confidence_basis["matched_term"] == "MOCKGENE1 GoF" and mech_edge.confidence_basis["matched_label"] == "MOCKGENE1 gain of function"
    assert mech_edge.confidence_basis["resolution"] == "mechanism label" and mech_edge.confidence_basis["url"] == "https://mock.invalid/mock-mechanism-alliance" and mech_edge.confidence_basis["retrieved_at"] == "2026-10-02"
    assert [s.model_dump() for s in mech_edge.sources] == [{"kind": "url", "id": "https://mock.invalid/mock-mechanism-alliance", "date": "2026-10-02"}]

    via = edge_between(graph, "org:mock-mechanism-alliance", "mock:disease:1", "supports_community")
    has_mech = edge_between(graph, "mock:disease:1", "mech:MOCKGENE1:GoF", "has_mechanism")
    assert via.evidence_class is EvidenceClass.inferred and via.confidence == 0.5 and via.status is EdgeStatus.supported, "a rule-derived route is inferred (capped at 0.5)"
    assert via.attributes["labels"] == ["via_mechanism", INFERRED_LABEL], "the route label plus the badge label every inferred edge carries"
    # Act tab attributes by route: the mechanism match itself is a direct match; the disease reached through it needs an eligibility check
    assert {k: mech_edge.attributes[k] for k in ("verdict", "differs", "needs_review")} == {"verdict": "reusable_now", "differs": [], "needs_review": []}
    assert {k: via.attributes[k] for k in ("verdict", "differs", "needs_review")} == {"verdict": "adaptable_needs_review", "differs": ["organisation names the mechanism, not this disease"], "needs_review": ["eligibility"]}
    assert via.confidence_basis["rule_evidence_class_note"] == b.COMMUNITY_RULE_EVIDENCE_CLASS_NOTE
    assert via.confidence_basis["via_mechanism"] == "mech:MOCKGENE1:GoF" and via.confidence_basis["disease_mechanism_edge_confidence"] == has_mech.confidence
    assert via.confidence_basis["resolution"] == "mechanism label" and via.confidence_basis["matched_term"] == "MOCKGENE1 GoF" and via.sources == mech_edge.sources
    assert maybe_edge(graph, "org:mock-mechanism-alliance", "mock:disease:2", "supports_community") is None, "disease 2 is the parent, not a descendant"
    # a Registry gets the same route with relation `covers`
    registry_mech = edge_between(graph, "org:mock-mechanism-registry", "mech:MOCKGENE1:GoF", "covers")
    assert registry_mech.confidence == 0.8 and registry_mech.evidence_class is EvidenceClass.observed, "the page names the mechanism: observed"
    registry_via = edge_between(graph, "org:mock-mechanism-registry", "mock:disease:1", "covers")
    assert registry_via.attributes["labels"] == ["via_mechanism", INFERRED_LABEL] and registry_via.evidence_class is EvidenceClass.inferred and registry_via.confidence == 0.5

    rows = [(s.query, s.hits) for s in graph.meta.coverage["org:mock-mechanism-alliance"].searched]
    assert rows == [("MOCKGENE1 GoF", 1), ("MOCKGENE1 GoF [resolution: mechanism label]", 1), ("Mock nothing at all", 0)], "the term counts the diseases it reached; the unresolved term stays a zero-hit row"
    report = result.report["patient_groups"]
    assert report["edges_by_resolution"] == {"direct": 0, "mechanism": 2, "via_mechanism": 2, "via_ancestor": 0} and report["edges"] == 4
    assert report["terms_resolved"] == 0 and report["terms_resolved_via_mechanism"] == 2 and report["terms_unresolved"] == 1


def populate_tree(store: Store) -> None:
    """An is_a tree of mock diseases: U > {C1, C2}; C1 > G1 > GG > G4; X unrelated."""
    labels = {"u": "Mock umbrella U", "c1": "Mock child C1", "c2": "Mock child C2", "g1": "Mock grandchild G1", "gg": "Mock great grandchild GG", "g4": "Mock level four G4", "x": "Mock unrelated X"}
    store.upsert_nodes([node(f"mock:disease:{k}", "Disease", label, backbone_id=f"D{k.upper()}") for k, label in labels.items()])
    store.upsert_edges([edge(f"mock:disease:{child}", f"mock:disease:{parent}", "is_a", backbone_relation="PARENT") for child, parent in (("c1", "u"), ("c2", "u"), ("g1", "c1"), ("gg", "g1"), ("g4", "gg"))])
    store.upsert_source("mock backbone", retrieved_at=TS, license="mock", version="v0", url="", notes={"evidence_score_min": 0.0, "evidence_score_max": 1.0})


TREE_SEEDS: dict[str, Any] = {
    "genes": [],
    "disease_terms": ["Mock umbrella U", "Mock child C1", "Mock child C2", "Mock grandchild G1", "Mock great grandchild GG", "Mock level four G4", "Mock unrelated X"],
    "drug_terms": [],
    "thresholds": {"include_parent_levels": 1},
}
TREE_GROUPS: dict[str, Any] = {"meta": {"date": "2026-10-03"}, "groups": [_group("Mock Umbrella Alliance", ["Mock umbrella U", "Mock child C2", "Mock no such disease"])], "searches": []}


def _tree_build(tmp_path: Path, **options: Any) -> b.BuildResult:
    with Store(tmp_path / "evidence.sqlite") as s:
        populate_tree(s)
        return b.build(s, {**TREE_SEEDS, "patient_groups": {**TREE_GROUPS, **options}}, now=NOW)


def test_umbrella_descendants_get_edges_with_depth_cap_and_direct_dedupe(tmp_path: Path) -> None:
    result = _tree_build(tmp_path)
    graph = result.graph
    assert Graph.model_validate(graph.model_dump(mode="json"))
    org = "org:mock-umbrella-alliance"
    assert edge_between(graph, org, "mock:disease:u", "supports_community").confidence == 0.8
    direct_c2 = edge_between(graph, org, "mock:disease:c2", "supports_community")
    assert direct_c2.confidence == 0.8 and "labels" not in direct_c2.attributes, "a descendant the organisation names directly keeps its direct edge only"
    expected = {"mock:disease:c1": (0.45, 1), "mock:disease:g1": (0.4, 2), "mock:disease:gg": (0.35, 3)}
    for disease_id, (conf, depth) in expected.items():
        e = edge_between(graph, org, disease_id, "supports_community")
        assert e.evidence_class is EvidenceClass.inferred and e.status is EdgeStatus.supported and e.confidence == conf, f"{disease_id}: rule-derived, 0.45 - 0.05 x (depth - 1), never above 0.5"
        assert e.attributes["labels"] == ["via_ancestor", INFERRED_LABEL] and e.attributes["scope"] == "umbrella"
        assert e.confidence_basis["rule_evidence_class_note"] == b.COMMUNITY_RULE_EVIDENCE_CLASS_NOTE
        assert e.confidence_basis["via_ancestor"] == "mock:disease:u" and e.confidence_basis["depth"] == depth and e.confidence_basis["matched_term"] == "Mock umbrella U"
        assert e.confidence_basis["resolution"] == "ancestor" and e.confidence_basis["seed_terms"] == ["Mock umbrella U"]
        assert [s.model_dump() for s in e.sources] == [{"kind": "url", "id": "https://mock.invalid/mock-umbrella-alliance", "date": "2026-10-02"}]
        assert e.attributes["verdict"] == "adaptable_needs_review" and e.attributes["needs_review"] == ["eligibility", "biological"]
        assert e.attributes["differs"] == [f"organisation serves the umbrella disease Mock umbrella U, {depth} level(s) above"]
    assert {k: direct_c2.attributes[k] for k in ("verdict", "differs", "needs_review")} == {"verdict": "reusable_now", "differs": [], "needs_review": []}
    assert b.asset_attributes("via_ancestor", ancestor_label="Mock umbrella U", depth=2)["differs"] == ["organisation serves the umbrella disease Mock umbrella U, 2 level(s) above"]
    assert maybe_edge(graph, org, "mock:disease:g4", "supports_community") is None, "depth 4 is beyond descendant_depth 3"
    assert maybe_edge(graph, org, "mock:disease:x", "supports_community") is None
    assert sum(1 for e in graph.edges if e.source == org) == 5

    rows = [(s.query, s.hits) for s in graph.meta.coverage[org].searched]
    assert rows == [("Mock umbrella U", 4), ("Mock child C2", 1), ("Mock no such disease", 0), ("Mock umbrella U [resolution: ancestor]", 3)]
    report = result.report["patient_groups"]
    assert report["edges_by_resolution"] == {"direct": 2, "mechanism": 0, "via_mechanism": 0, "via_ancestor": 3} and report["edges"] == 5
    assert report["terms_resolved"] == 2 and report["terms_unresolved"] == 1 and report["descendant_cap_hits"] == []
    assert result.report["counts"]["edges_by_label"]["via_ancestor"] == 3


def test_descendant_depth_and_cap_knobs(tmp_path: Path) -> None:
    org = "org:mock-umbrella-alliance"
    shallow = _tree_build(tmp_path / "depth1", descendant_depth=1).graph
    assert {e.target for e in shallow.edges if e.source == org} == {"mock:disease:u", "mock:disease:c2", "mock:disease:c1"}
    off = _tree_build(tmp_path / "depth0", descendant_depth=0)
    assert {e.target for e in off.graph.edges if e.source == org} == {"mock:disease:u", "mock:disease:c2"} and off.report["patient_groups"]["edges_by_resolution"]["via_ancestor"] == 0
    assert [(s.query, s.hits) for s in off.graph.meta.coverage[org].searched] == [("Mock umbrella U", 1), ("Mock child C2", 1), ("Mock no such disease", 0)], "no route row when the rule added nothing"
    capped = _tree_build(tmp_path / "cap1", max_descendants_per_term=1)
    assert {e.target for e in capped.graph.edges if e.source == org} == {"mock:disease:u", "mock:disease:c2", "mock:disease:c1"}, "the nearest descendant is kept first"
    assert capped.report["patient_groups"]["descendant_cap_hits"] == [{"org": org, "matched_term": "Mock umbrella U", "candidates": 3, "kept": 1}]
    assert capped.report["patient_groups"]["max_descendants_per_term"] == 1
    assert [(s.query, s.hits) for s in capped.graph.meta.coverage[org].searched][-1] == ("Mock umbrella U [resolution: ancestor]", 1)


# --------------------------------------------------------------------------------------
# claims bundle (legacy triple match while pipeline.graph.evidence is absent)
# --------------------------------------------------------------------------------------


def _bundle() -> ClaimsBundle:
    abstract = "Mock sentence one supports the mock association. Mock sentence two is unresolved."
    return ClaimsBundle.model_validate(
        {
            "extracted_at": "2026-10-03T11:00:00+00:00",
            "model": "mock-model",
            "papers": [{"id": "mock:paper:1", "type": "Paper", "label": "Mock paper", "synonyms": [], "props": {"abstract": abstract, "year": 2026, "title": "Mock paper", "authors": [{"name": "Mock Author", "affiliation": "Mock University, mock.author@mock.invalid"}], "pub_types": ["Journal Article"]}}],
            "claims": [
                {"id": "c_000001", "pmid": "", "paper": "mock:paper:1", "quote": "Mock sentence one supports the mock association.", "polarity": "supports", "subject": "mock:disease:1", "predicate": "associated_with", "object": "mock:gene:1", "variant": "", "study_type": "case_report", "n_patients": 1},
                {"id": "c_000002", "pmid": "", "paper": "mock:paper:1", "quote": "Mock sentence two is unresolved.", "polarity": "supports", "subject": "text:some mention", "predicate": "treats", "object": "mock:disease:1", "variant": "", "study_type": "review", "n_patients": None},
                {"id": "c_000003", "pmid": "", "paper": "mock:paper:1", "quote": "Mock sentence two is unresolved.", "polarity": "supports", "subject": "mock:disease:1", "predicate": "has_phenotype", "object": "mock:phenotype:1", "variant": "mock:variant:404", "study_type": "review", "n_patients": None},
            ],
            "coverage": {"mock:disease:1": {"searched": [{"source": "mock pubmed", "query": "mock query", "date": "2026-10-03", "hits": 0}]}},
        }
    )


@pytest.fixture
def no_evidence_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the legacy path even when the parallel lane's pipeline.graph.evidence exists."""
    monkeypatch.setattr(b, "_load_integrate_claims", lambda: None)


def test_claims_attach_to_matching_edges(store: Store, tmp_path: Path, no_evidence_module: None) -> None:
    result = b.build(store, SEEDS, _bundle(), now=NOW)
    graph = result.graph
    assert Graph.load(graph.dump(tmp_path / "graph.json")).model_dump() == graph.model_dump()
    paper = graph.node_index()["mock:paper:1"]
    assert paper.type is NodeType.Paper
    assert set(paper.props) == {"abstract", "year", "title", "authors"} and paper.props["authors"] == ["Mock Author"], "Paper props are slimmed; authors become plain names (the contract test wants strings)"
    assert [c.id for c in graph.claims] == ["c_000001", "c_000002", "c_000003"]
    assert graph.claim_index()["c_000003"].variant == "", "an unresolvable variant pointer is blanked, the evidence kept"

    dg = edge_between(graph, "mock:disease:1", "mock:gene:1", "associated_with")
    assert dg.evidence_class is EvidenceClass.observed, "a backbone edge enriched by papers stays observed"
    assert dg.status is EdgeStatus.small_n and dg.supporting == ["c_000001"] and dg.contradicting == []
    assert dg.attributes["labels"] == ["promising", "small_n"] and "preprint" not in dg.attributes and dg.attributes["origin"] == "germline", "a mock paper is not a doi: preprint"
    assert dg.confidence == 1.0, "max(observed 1.0, case report 0.4)"
    assert dg.confidence_basis["claims"]["n_supporting"] == 1 and dg.confidence_basis["claims"]["literature_confidence"] == 0.4
    assert dg.confidence_basis["claim_status"]["patient_level_n"] == [1]
    assert {s.kind.value for s in dg.sources} == {"db"} and any(s.id == "mock:paper:1" and s.date == "2026" for s in dg.sources)
    hp = edge_between(graph, "mock:disease:1", "mock:phenotype:1", "has_phenotype")
    assert hp.supporting == ["c_000003"] and hp.status is EdgeStatus.supported and "labels" not in hp.attributes, "a review carries no patients: no small_n, no promising; no doi: paper, no preprint"

    assert result.report["claims"] == {"in_bundle": 3, "in_graph": 3, "attached_to_edges": 2, "unattached": ["c_000002"], "variant_unresolved": [{"id": "c_000003", "variant": "mock:variant:404"}], "dropped": []}
    assert result.report["evidence"]["how"].startswith("legacy triple match")
    assert [s.name for s in graph.meta.sources] == ["mock backbone", "claims bundle (data/build/claims.json)"]
    assert graph.meta.sources[1].version == "mock-model" and graph.meta.sources[1].retrieved_at == "2026-10-03T11:00:00+00:00"
    assert any(s.source == "mock pubmed" for s in graph.meta.coverage["mock:disease:1"].searched)
    assert "mock.author@mock.invalid" not in graph.model_dump_json()


def test_abstract_and_quote_are_scrubbed_consistently(store: Store, no_evidence_module: None) -> None:
    abstract = "Mock patients received ruxolitinib 5 mg twice daily and improved. Mock second sentence."
    bundle = ClaimsBundle.model_validate(
        {
            "extracted_at": "2026-10-03T11:00:00+00:00",
            "papers": [{"id": "mock:paper:2", "type": "Paper", "label": "Mock paper", "synonyms": [], "props": {"abstract": abstract, "year": 2026}}],
            "claims": [{"id": "c_000001", "pmid": "", "paper": "mock:paper:2", "quote": "received ruxolitinib 5 mg twice daily and improved", "polarity": "supports", "subject": "mock:drug:1", "predicate": "indicated_for", "object": "mock:disease:1", "variant": "", "study_type": "case_series", "n_patients": 4}],
        }
    )
    graph = b.build_graph(store, SEEDS, bundle, now=NOW)
    paper = graph.node_index()["mock:paper:2"]
    assert "5 mg" not in paper.props["abstract"] and "[dose removed]" in paper.props["abstract"]
    claim = graph.claim_index()["c_000001"]
    assert claim.quote == "received ruxolitinib [dose removed] and improved" and claim.quote in paper.props["abstract"]
    e = edge_between(graph, "mock:drug:1", "mock:disease:1", "indicated_for")
    assert e.supporting == ["c_000001"] and e.status is EdgeStatus.small_n


def test_curated_seed_adds_a_source_record(store: Store) -> None:
    seeds = {**SEEDS, "mechanisms": {"source": "mock seed file", "transcribed_at": "2026-10-01", "mechanisms": [{"gene": "MOCKGENE2", "direction": "LoF", "origin": "germline", "disease_terms": ["Mock immune dysregulation 1"], "cluster_doc_confidence": "Background", "note": "mock"}]}}
    graph = b.build_graph(store, seeds, now=NOW)
    e = edge_between(graph, "mock:disease:1", "mech:MOCKGENE2:LoF", "has_mechanism")
    assert e.evidence_class is EvidenceClass.inferred and e.confidence == 0.4 and e.attributes["labels"] == ["curated_unverified"]
    assert e.attributes["origin"] == "germline", "the curated origin stays on the inferred, labelled edge"
    assert [s.model_dump() for s in e.sources] == [{"kind": "db", "id": "mock seed file MOCKGENE2:LoF", "date": "2026-10-01"}, {"kind": "db", "id": "MOCKDIS_1", "date": TS[:10]}]
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
    assert "aspect" not in edge_between(graph, "mock:disease:1", "mock:phenotype:1", "has_phenotype").confidence_basis, "an empty raw list is not exported (compact_edge)"


# --------------------------------------------------------------------------------------
# the evidence call site (design item A) with a fake pipeline.graph.evidence
# --------------------------------------------------------------------------------------


def _fake_evidence_module(calls: list[dict[str, Any]]) -> types.ModuleType:
    module = types.ModuleType("pipeline.graph.evidence")

    def integrate_claims(nodes: dict[str, Any], edges: dict[tuple[str, str, str], Any], bundle: Any, *, today: str, scrub: Any, investigator_min_papers: int = 1) -> Any:
        calls.append({"n_nodes": len(nodes), "n_edges": len(edges), "today": today, "scrub": scrub, "investigator_min_papers": investigator_min_papers})
        # upgrade in place: attach the associated_with claim to the backbone edge (status left for the build to reconcile)
        dg = edges[("mock:disease:1", "mock:gene:1", "associated_with")]
        dg["supporting"].append("c_000001")
        dg["supporting"].append("c_000001")  # a duplicate the build must remove
        dg["contradicting"].append("c_000004")
        dg["evidence_class"] = "observed"
        papers = {p.id: {**p.model_dump(mode="json"), "props": {**p.props, "authors": [{"name": scrub(a["name"]), "affiliation": scrub(a.get("affiliation", ""))} for a in p.props.get("authors", [])]}} for p in bundle.papers}
        investigators = {"person:mock-author": {"id": "person:mock-author", "type": "Investigator", "label": "Mock Author", "synonyms": [], "props": {"name": "Mock Author", "affiliations": [scrub("Mock University, mock.author@mock.invalid")], "match_confidence": "low", "n_papers": 1, "papers": ["mock:paper:1"], "role_label": "author of 1 paper on Mock immune dysregulation 1"}}}
        new_edges = {
            ("mock:drug:1", "mock:disease:1", "treats"): {
                "source": "mock:drug:1", "target": "mock:disease:1", "relation": "treats", "evidence_class": "extracted", "confidence": 0.5,
                "confidence_basis": {"rule": "fake"}, "status": "supported", "sources": [{"kind": "db", "id": "mock:paper:1", "date": "2026"}],
                "supporting": ["c_000002"], "contradicting": [], "attributes": {},
            },
            ("person:mock-author", "mock:paper:1", "author_of"): {
                "source": "person:mock-author", "target": "mock:paper:1", "relation": "author_of", "evidence_class": "observed", "confidence": 0.9,
                "confidence_basis": {"pubmed_record": "mock:paper:1"}, "status": "supported", "sources": [{"kind": "db", "id": "mock:paper:1", "date": "2026"}],
                "supporting": [], "contradicting": [], "attributes": {},
            },
            ("person:mock-author", "mock:disease:1", "publishes_on"): {
                "source": "person:mock-author", "target": "mock:disease:1", "relation": "publishes_on", "evidence_class": "observed", "confidence": 0.4,
                "confidence_basis": {"n_papers": 1}, "status": "supported", "sources": [{"kind": "db", "id": "mock:paper:1", "date": "2026"}],
                "supporting": [], "contradicting": [], "attributes": {},
            },
        }
        claims = [c.model_dump(mode="json") for c in bundle.claims]
        return types.SimpleNamespace(
            claims=claims,
            paper_nodes=papers,
            investigator_nodes=investigators,
            new_edges=new_edges,
            coverage_rows=[("claims:unresolved", "unresolved literature mention", "text:some mention", today, 0), {"entity_id": "mock:disease:1", "source": "unresolved literature mention", "query": "mock", "date": today, "hits": 1}],
            report={"resolved": 5, "unresolved": 1},
        )

    module.integrate_claims = integrate_claims  # type: ignore[attr-defined]
    return module


def _evidence_bundle() -> ClaimsBundle:
    abstract = "Mock sentence one supports the mock association. Mock sentence two is unresolved. Mock sentence three disagrees."
    return ClaimsBundle.model_validate(
        {
            "extracted_at": "2026-10-03T11:00:00+00:00",
            "model": "mock-model",
            "papers": [{"id": "mock:paper:1", "type": "Paper", "label": "Mock paper", "synonyms": [], "props": {"abstract": abstract, "year": 2026, "authors": [{"name": "Mock Author", "affiliation": "Mock University, mock.author@mock.invalid"}]}}],
            "claims": [
                {"id": "c_000001", "pmid": "", "paper": "mock:paper:1", "quote": "Mock sentence one supports the mock association.", "polarity": "supports", "subject": "mock:disease:1", "predicate": "causes", "object": "mock:gene:1", "variant": "", "study_type": "cohort", "n_patients": 25},
                {"id": "c_000002", "pmid": "", "paper": "mock:paper:1", "quote": "Mock sentence two is unresolved.", "polarity": "supports", "subject": "mock:drug:1", "predicate": "treats", "object": "mock:disease:1", "variant": "text:some variant", "study_type": "case_report", "n_patients": 1},
                {"id": "c_000004", "pmid": "", "paper": "mock:paper:1", "quote": "Mock sentence three disagrees.", "polarity": "contradicts", "subject": "mock:disease:1", "predicate": "causes", "object": "mock:gene:1", "variant": "", "study_type": "case_report", "n_patients": 2},
            ],
        }
    )


def test_build_merges_the_evidence_module_result(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(sys.modules, "pipeline.graph.evidence", _fake_evidence_module(calls))
    result = b.build(store, SEEDS, _evidence_bundle(), now=NOW)
    graph = result.graph
    assert len(calls) == 1 and calls[0]["today"] == "2026-10-03" and calls[0]["n_nodes"] >= 10
    assert calls[0]["scrub"] is scrub_doses, "the evidence module gets the DOSE scrubber and runs (and counts) its own e-mail / phone pass"
    assert calls[0]["investigator_min_papers"] == 1, "with the ORCID exemption on, prune_graph applies the paper cap"
    idx = graph.node_index()
    assert idx["mock:paper:1"].props["authors"] == ["Mock Author"]
    author = idx["person:mock-author"]
    assert author.type is NodeType.Investigator
    assert author.props["affiliations"] == ["Mock University, [email removed]"] and author.props["role_label"].startswith("author of 1 paper")
    assert edge_between(graph, "person:mock-author", "mock:paper:1", "author_of").confidence == 0.9
    assert edge_between(graph, "person:mock-author", "mock:disease:1", "publishes_on").confidence == 0.4

    treats = edge_between(graph, "mock:drug:1", "mock:disease:1", "treats")
    assert treats.evidence_class is EvidenceClass.extracted and treats.supporting == ["c_000002"] and treats.status is EdgeStatus.small_n
    assert treats.attributes["labels"] == ["promising", "small_n"] and "preprint" not in treats.attributes, "a mock paper is not a doi: preprint"
    dg = edge_between(graph, "mock:disease:1", "mock:gene:1", "associated_with")
    assert dg.supporting == ["c_000001"] and dg.contradicting == ["c_000004"], "duplicate claim ids are removed"
    assert dg.status is EdgeStatus.contested and dg.attributes["labels"] == ["promising", "contested"], "contested iff both sides; the n=25 cohort blocks small_n"
    assert dg.evidence_class is EvidenceClass.observed and dg.attributes["origin"] == "germline"
    assert graph.claim_index()["c_000002"].variant == ""
    assert result.report["evidence"] == {"how": "pipeline.graph.evidence.integrate_claims", "resolved": 5, "unresolved": 1}
    assert result.report["claims"]["variant_unresolved"] == [{"id": "c_000002", "variant": "text:some variant"}]
    assert result.report["claims"]["attached_to_edges"] == 3 and result.report["claims"]["unattached"] == []
    assert result.report["claim_edges_reconciled"]["claim_ids_removed_from_edges"] == 1
    cov = graph.meta.coverage
    assert cov["claims:unresolved"].searched[0].model_dump() == {"source": "unresolved literature mention", "query": "text:some mention", "date": "2026-10-03", "hits": 0}
    assert any(s.source == "unresolved literature mention" for s in cov["mock:disease:1"].searched)
    # investigator contact routes: the fake author has neither an orcid: id nor a source_url, so none is guessed
    assert "contact" not in author.props and "contact_source" not in author.props
    assert result.report["contact_routes"]["by_kind"] == {"none": 1} and result.report["contact_routes"]["edges_with_contact_source"] == 0
    assert "mock.author@mock.invalid" not in graph.model_dump_json()
    assert result.report["prune"]["diseases"]["anchored_by_evidence"] == 1


def test_extracted_edges_without_claims_are_removed_and_counted(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("pipeline.graph.evidence")

    def integrate_claims(nodes: Any, edges: Any, bundle: Any, *, today: str, scrub: Any, investigator_min_papers: int = 1) -> Any:
        edges[("mock:drug:1", "mock:disease:1", "treats")] = {
            "source": "mock:drug:1", "target": "mock:disease:1", "relation": "treats", "evidence_class": "extracted", "confidence": 0.5,
            "confidence_basis": {"rule": "fake"}, "status": "supported", "sources": [{"kind": "db", "id": "mock:paper:1", "date": "2026"}],
            "supporting": ["c_999999"], "contradicting": [], "attributes": {},
        }
        return types.SimpleNamespace(claims=[c.model_dump(mode="json") for c in bundle.claims], paper_nodes={p.id: p.model_dump(mode="json") for p in bundle.papers}, investigator_nodes={}, new_edges={}, coverage_rows=[], report={})

    module.integrate_claims = integrate_claims  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pipeline.graph.evidence", module)
    result = b.build(store, SEEDS, _evidence_bundle(), now=NOW)
    assert maybe_edge(result.graph, "mock:drug:1", "mock:disease:1", "treats") is None, "an extracted edge whose only claim id does not exist cannot be exported"
    assert result.report["claim_edges_reconciled"]["edges_dropped_without_claims"] == [{"source": "mock:drug:1", "target": "mock:disease:1", "relation": "treats", "reason": "extracted edge whose claims are not in claims[]"}]
    assert result.report["claims"]["unattached"] == ["c_000001", "c_000002", "c_000004"]


# --------------------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------------------


@pytest.fixture
def mock_cluster_seeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """The export reads pipeline/seeds/cluster.yaml, whose gene symbols never resolve against the
    mock store (and then nothing would anchor the export); serve the mock seeds for ``cluster``
    and the real files for everything else (mechanisms, patient_groups)."""
    real = export.config.load_seeds

    def fake(name: str) -> dict[str, Any]:
        return json.loads(json.dumps(SEEDS)) if name == "cluster" else real(name)

    monkeypatch.setattr(export.config, "load_seeds", fake)


def test_export_main_writes_graph_and_report(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, mock_cluster_seeds: None) -> None:
    monkeypatch.setattr(export, "LOGIC_MODULE", "pipeline.graph.mock_logic_module_that_does_not_exist")
    store_path = tmp_path / "store" / "evidence.sqlite"
    with Store(store_path) as s:
        populate(s)
    out = tmp_path / "out" / "graph.json"
    report = tmp_path / "out" / "build_report.json"
    rc = export.main(["--skip-download", "--no-claims", "--store", str(store_path), "--out", str(out), "--report", str(report)])
    assert rc == 0
    graph = Graph.load(out)
    assert graph.nodes and graph.edges
    data = json.loads(report.read_text(encoding="utf-8"))
    for key in ("built_at", "counts", "sizes", "thresholds", "seed_resolution", "mechanisms", "evidence", "prune", "patient_groups", "graph_logic", "gap_probe", "timings_seconds", "source_versions", "store_counts", "loaders", "score_range"):
        assert key in data, key
    assert data["loaders"] == {spec.module: {"status": "skipped (--skip-download)"} for spec in export.LOADERS}
    assert data["gap_probe"]["enabled"] is False, "the mock cluster seeds carry no gap_probe block (tests/test_gap_probe.py covers the enabled path)"
    assert [spec.name for spec in export.LOADERS] == ["optimuskg", "hpo", "clinvar", "ctgov", "reporter"]
    assert data["counts"]["edges"] == len(graph.edges) and data["store_counts"]["edges"] == 10
    assert data["sizes"]["graph_json_bytes"] == out.stat().st_size and data["claims_bundle"] == "disabled (--no-claims)"
    assert data["graph_logic"]["status"].startswith("absent")
    assert data["seed_resolution"]["unresolved"]["genes"] == ["MOCKGENE9"]
    # the real patient_groups.yaml rows become nodes whose terms resolve to nothing here (coverage, not edges)
    assert data["patient_groups"]["groups"] >= 1 and data["patient_groups"]["edges"] == 0
    assert any(k.startswith(b.PATIENT_GROUP_SEARCH_PREFIX) for k in graph.meta.coverage)
    printed = capsys.readouterr().out
    assert f"wrote {out}" in printed and "mechanisms:" in printed


def test_export_main_fails_on_invalid_claims_or_empty_store(tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_cluster_seeds: None) -> None:
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
    rc = export.main(["--skip-download", "--no-claims", "--store", str(empty), "--out", str(tmp_path / "g2.json"), "--report", str(tmp_path / "r2.json")])
    assert rc == export.EXIT_GRAPH_INVALID and "failed validation" in capsys.readouterr().err
    assert not (tmp_path / "g2.json").exists()


def test_export_applies_graph_logic_when_importable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_cluster_seeds: None) -> None:
    logic = types.ModuleType("pipeline.graph.mock_logic")
    seen: list[Any] = []

    def apply_graph_logic(graph: Graph, seeds: dict[str, Any]) -> Any:
        seen.append(seeds)
        data = graph.model_dump(mode="json")
        data["clusters"] = [{"id": "cl_001", "members": ["mock:disease:1", "mech:MOCKGENE1:GoF"], "robust": True, "why": {"mock": True}}]
        return types.SimpleNamespace(graph=Graph.model_validate(data), report={"clusters": 1})

    logic.apply_graph_logic = apply_graph_logic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pipeline.graph.mock_logic", logic)
    monkeypatch.setattr(export, "LOGIC_MODULE", "pipeline.graph.mock_logic")
    store_path = tmp_path / "evidence.sqlite"
    with Store(store_path) as s:
        populate(s)
    out, report = tmp_path / "graph.json", tmp_path / "report.json"
    rc = export.main(["--skip-download", "--no-claims", "--store", str(store_path), "--out", str(out), "--report", str(report)])
    assert rc == 0 and seen and "genes" in seen[0]
    graph = Graph.load(out)
    assert [c.id for c in graph.clusters] == ["cl_001"]
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["graph_logic"] == {"status": "applied", "report": {"clusters": 1}} and data["counts"]["clusters"] == 1


def test_run_loaders_order_seeds_and_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[Any, ...], bool]] = []

    def fake_module(name: str) -> types.ModuleType:
        m = types.ModuleType(name)

        def load(store: Any, *seed_args: Any, offline: bool = False) -> dict[str, Any]:
            calls.append((name, seed_args, offline))
            return {"source": name}

        m.load = load  # type: ignore[attr-defined]
        return m

    for spec in export.LOADERS:
        monkeypatch.setitem(sys.modules, spec.module, fake_module(spec.module))
    seeds = {"genes": ["MOCKGENE1"]}
    seed_files: list[str] = []

    def seed_loader(name: str) -> dict[str, Any]:
        seed_files.append(name)
        return {"seed_file": name}

    timings: dict[str, float] = {}
    with Store(tmp_path / "evidence.sqlite") as s:
        out = export.run_loaders(s, seeds, offline=True, timings=timings, seed_loader=seed_loader)
    assert [c[0] for c in calls] == [spec.module for spec in export.LOADERS]
    assert calls[0][1] == (seeds,) and calls[1][1] == () and calls[2][1] == ({"seed_file": "clinvar"}, seeds) and calls[3][1] == ({"seed_file": "ctgov"},) and calls[4][1] == ({"seed_file": "reporter"},)
    assert all(c[2] is True for c in calls) and seed_files == ["clinvar", "ctgov", "reporter"]
    assert all(out[spec.module] == {"status": "ok", "summary": {"source": spec.module}} for spec in export.LOADERS)
    assert set(timings) == {spec.module for spec in export.LOADERS}

    calls.clear()
    with Store(tmp_path / "evidence2.sqlite") as s:
        out = export.run_loaders(s, seeds, offline=False, timings={}, only={"ctgov"}, seed_loader=seed_loader)
    assert [c[0] for c in calls] == ["pipeline.sources.ctgov"]
    assert out["pipeline.sources.optimuskg"] == {"status": "skipped (--only)"} and out["pipeline.sources.ctgov"]["status"] == "ok"
    with pytest.raises(SystemExit, match="unknown loader"):
        export._parse_only("nope")
