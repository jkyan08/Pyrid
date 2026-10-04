"""pipeline/graph/evidence.py over synthetic drafts and a synthetic claims bundle.

Everything is a placeholder (``mock:`` ids, "Mock " labels, ``MOCKGENE<n>`` symbols, a ``CHEMBL0``
/ ``doi:10.0000/...`` / ``pmid:0`` id SHAPE that no record carries); fully offline. The abstracts
are built from the mock quotes so every claim passes the quote check.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import pytest

from pipeline.graph import evidence as ev
from pipeline.graph.build import finalize_edges
from pipeline.models import ClaimsBundle, Graph

TODAY = "2026-10-03"
TS = "2026-10-03T12:00:00+00:00"
DRUG = "CHEMBL:CHEMBL0"  # the backbone spelling of a bare "CHEMBL0" id (shape, not a record)
PMID_PAPER = "pmid:0"  # no PMID 0 exists; exercises the pmid source kind
DOI_PAPER = "doi:10.0000/mock-preprint"  # 10.0000 is not a DOI registrant; exercises the doi kind
MOCK_PAPER = "mock:paper:1"
QUIET_PAPER = "mock:paper:4"  # only unresolved claims: its author must not be exported
SEED_RECORD = {"kind": "db", "id": "mock seed file MOCKGENE1:GoF", "date": "2026-10-01"}  # the R4 seed row as a source

Q = {
    "treat_ok": "Mock-inib treated one mock patient.",
    "treat_no": "Mock-inib failed in four mock patients.",
    "treat_vitro": "Mock-inib normalised mock signalling in sixteen cell lines.",
    "treat_cohort": "Mock-second-inib helped a mock cohort of twenty-five.",
    "treat_case": "Mock-second-inib helped two more mock patients.",
    "mech_variant": "Mock variant one drives the MOCKGENE1 mock gain of function route.",
    "mech_new": "Mock disease two reflects MOCKGENE2 loss of function in a mock review.",
    "mech_curated": "A mock cohort of twelve linked disease two to the MOCKGENE1 route.",
    "pheno": "Five mock patients showed mock phenotype one.",
    "pheno_text": "Mock mechanism patients showed some mock symptom.",
    "causes_gene": "MOCKGENE1 mutations cause mock disease one.",
    "causes_mech": "The mock gain of function route causes mock disease one.",
    "assoc_variant": "Mock variant two was found in mock disease one.",
    "causes_new": "MOCKGENE2 mutations cause mock disease two.",
    "modulates": "Mock-inib modulates MOCKGENE1 in vitro.",
    "modulates_text": "Mock-inib modulates a mock kinase.",
    "text_both": "Something mock treats something else mock.",
    "contra_only": "Mock-second-inib did not help mock disease one.",
    "mismatch": "MOCKGENE1 shows mock phenotype one.",
    "dose": "Mock-inib given 5 mg twice daily improved things.",
    "bad_variant": "A mock unknown variant was described.",
    "quiet": "A mock mention treats another mock mention.",
    # mutation-killing cases: every endpoint-type branch of _plan is exercised by a resolving claim
    "mech_variant2": "Mock variant two also drives the MOCKGENE1 route.",
    "pheno_mech": "Mock gain of function patients showed mock phenotype one.",
    "modulates_mech": "Mock-inib dampens the MOCKGENE1 mock gain of function route.",
    "modulates_pathway": "Mock-inib modulates the mock pathway.",
    "assoc_variant_new": "Mock variant one was also seen in mock disease two.",
}


def _abstract(*keys: str) -> str:
    return " ".join(Q[k] for k in keys)


def _paper(pid: str, label: str, abstract: str, authors: list[Any], year: int = 2020) -> dict[str, Any]:
    props: dict[str, Any] = {"title": label, "abstract": abstract, "year": year, "journal": "Mock Journal", "authors": authors, "pub_types": ["Mock Article"], "bulky_field": "x" * 50}
    return {"id": pid, "type": "Paper", "label": label, "synonyms": [], "props": props}


def _claim(n: int, paper: str, quote_key: str, subject: str, predicate: str, obj: str, *, polarity: str = "supports", study_type: str = "case_report", n_patients: int | None = None, variant: str = "") -> dict[str, Any]:
    return {
        "id": f"c_{n:06d}",
        "pmid": paper[len("pmid:"):] if paper.startswith("pmid:") else "",
        "paper": paper,
        "quote": Q[quote_key],
        "polarity": polarity,
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "variant": variant,
        "study_type": study_type,
        "n_patients": n_patients,
    }


AUTHOR_ONE_P0 = {"name": "Mock Author One", "affiliation": "Mock Institute, Mock City. Electronic address: one@mock.example.", "orcid": "mock-orcid-1"}
AUTHOR_ONE_P1 = {"name": "Mock Author One", "affiliation": "Mock Hospital, Mock Town; tel: +1 555 010 0100", "orcid": "https://orcid.org/mock-orcid-1"}
AUTHOR_TWO = {"name": "Mock Author Two", "affiliation": "Mock Lab (two@mock.example)"}
AUTHOR_THREE = "Mock Author Three"
AUTHOR_FOUR = {"name": "Mock Author Four"}


def bundle() -> ClaimsBundle:
    papers = [
        _paper(PMID_PAPER, "Mock pmid paper", _abstract("treat_ok", "mech_variant", "pheno", "causes_gene", "causes_mech", "modulates", "dose", "bad_variant", "modulates_mech", "modulates_pathway"), [AUTHOR_ONE_P0, AUTHOR_TWO], year=2019),
        _paper(MOCK_PAPER, "Mock paper one", _abstract("treat_no", "mech_new", "mech_curated", "assoc_variant", "causes_new", "modulates_text", "text_both", "contra_only", "mismatch", "mech_variant2", "pheno_mech", "assoc_variant_new"), [AUTHOR_ONE_P1, AUTHOR_TWO]),
        _paper(DOI_PAPER, "Mock preprint", _abstract("treat_vitro", "treat_cohort", "treat_case", "pheno_text"), [AUTHOR_THREE], year=2021),
        _paper(QUIET_PAPER, "Mock quiet paper", _abstract("quiet"), [AUTHOR_FOUR]),
    ]
    claims = [
        # Edge A: Drug -> disease 1 treats; bare CHEMBL subject; contested + small_n + promising + preprint
        _claim(1, PMID_PAPER, "treat_ok", "CHEMBL0", "treats", "mock:disease:1", study_type="case_report", n_patients=1),
        _claim(2, MOCK_PAPER, "treat_no", "CHEMBL0", "treats", "mock:disease:1", polarity="contradicts", study_type="case_series", n_patients=4),
        _claim(3, DOI_PAPER, "treat_vitro", "CHEMBL0", "treats", "mock:disease:1", study_type="in_vitro", n_patients=16),
        # Edge B: drug 2 -> disease 2 treats; a cohort of 25 blocks small_n
        _claim(4, DOI_PAPER, "treat_cohort", "mock:drug:2", "treats", "mock:disease:2", study_type="cohort", n_patients=25),
        _claim(5, DOI_PAPER, "treat_case", "mock:drug:2", "treats", "mock:disease:2", study_type="case_report", n_patients=2),
        # has_mechanism: upgrade of the observed edge + variant-specific Variant -> Mechanism route
        _claim(6, PMID_PAPER, "mech_variant", "mock:disease:1", "has_mechanism", "mech:MOCKGENE1:GoF", n_patients=1, variant="mock:variant:1"),
        # has_mechanism: mech: endpoint whose node does not exist yet
        _claim(7, MOCK_PAPER, "mech_new", "mock:disease:2", "has_mechanism", "mech:MOCKGENE2:LoF", study_type="review"),
        # has_mechanism: upgrades the inferred (curated) edge to extracted
        _claim(8, MOCK_PAPER, "mech_curated", "mock:disease:2", "has_mechanism", "mech:MOCKGENE1:GoF", study_type="cohort", n_patients=12),
        # has_phenotype: attaches to the observed edge / unresolved text object
        _claim(9, PMID_PAPER, "pheno", "mock:disease:1", "has_phenotype", "mock:phenotype:1", study_type="case_series", n_patients=5),
        _claim(10, DOI_PAPER, "pheno_text", "mech:MOCKGENE1:GoF", "has_phenotype", "text:some mock symptom"),
        # causes / associated_with: either orientation onto existing edges, else create
        _claim(11, PMID_PAPER, "causes_gene", "mock:gene:1", "causes", "mock:disease:1", study_type="review"),
        _claim(12, PMID_PAPER, "causes_mech", "mech:MOCKGENE1:GoF", "causes", "mock:disease:1", study_type="review"),
        _claim(13, MOCK_PAPER, "assoc_variant", "mock:variant:2", "associated_with", "mock:disease:1", n_patients=1),
        _claim(14, MOCK_PAPER, "causes_new", "mock:gene:2", "causes", "mock:disease:2", study_type="review"),
        # modulates: both resolve / object unresolved
        _claim(15, PMID_PAPER, "modulates", "CHEMBL0", "modulates", "mock:gene:1", study_type="in_vitro"),
        _claim(16, MOCK_PAPER, "modulates_text", "CHEMBL0", "modulates", "text:a mock kinase", study_type="in_vitro"),
        # both sides text
        _claim(17, MOCK_PAPER, "text_both", "text:something mock", "treats", "text:something else mock", study_type="review"),
        # contradicting-only against a pair with no edge
        _claim(18, MOCK_PAPER, "contra_only", "mock:drug:2", "treats", "mock:disease:1", polarity="contradicts", n_patients=1),
        # type mismatch: Gene -> Phenotype has_phenotype
        _claim(19, MOCK_PAPER, "mismatch", "mock:gene:1", "has_phenotype", "mock:phenotype:1", study_type="review"),
        # a dosed quote: abstract and quote are scrubbed together
        _claim(20, PMID_PAPER, "dose", "CHEMBL0", "treats", "mock:disease:1", study_type="case_report", n_patients=1),
        # variant that is not a Variant node: cleared, recorded
        _claim(21, PMID_PAPER, "bad_variant", "mock:disease:1", "has_phenotype", "mock:phenotype:1", study_type="review", variant="text:unknown mock variant"),
        # the quiet paper: unresolved on both sides
        _claim(22, QUIET_PAPER, "quiet", "text:a mock mention", "treats", "text:another mock mention", study_type="review"),
        # has_mechanism with a VARIANT subject (no existing edge): created, variant_specific
        _claim(23, MOCK_PAPER, "mech_variant2", "mock:variant:2", "has_mechanism", "mech:MOCKGENE1:GoF", n_patients=1, variant="mock:variant:2"),
        # has_phenotype with a MECHANISM subject (no existing edge): created extracted
        _claim(24, MOCK_PAPER, "pheno_mech", "mech:MOCKGENE1:GoF", "has_phenotype", "mock:phenotype:1", study_type="case_series", n_patients=3),
        # modulates with a Mechanism / Pathway object
        _claim(25, PMID_PAPER, "modulates_mech", "CHEMBL0", "modulates", "mech:MOCKGENE1:GoF", study_type="in_vitro"),
        _claim(26, PMID_PAPER, "modulates_pathway", "CHEMBL0", "modulates", "mock:pathway:1", study_type="in_vitro"),
        # {Variant, Disease} with no existing has_condition edge: created as Variant -> Disease has_condition
        _claim(27, MOCK_PAPER, "assoc_variant_new", "mock:variant:1", "associated_with", "mock:disease:2", n_patients=1),
    ]
    return ClaimsBundle.model_validate({"extracted_at": TS, "model": "mock-model", "papers": papers, "claims": claims})


def _node(node_id: str, node_type: str, label: str, synonyms: list[str] = (), **props: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "label": label, "synonyms": list(synonyms), "props": props}


def _edge(source: str, target: str, relation: str, *, evidence_class: str = "observed", confidence: float = 0.7, labels: list[str] | None = None, **attributes: Any) -> dict[str, Any]:
    attrs = dict(attributes)
    if labels is not None:
        attrs["labels"] = labels
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "source_db": "mock",
        "evidence_class": evidence_class,
        "confidence": confidence,
        "confidence_basis": {"rule": "mock", "raw": 1},
        "status": "supported",
        "sources": [{"kind": "db", "id": f"mock {relation} {source}->{target}", "date": TS}],
        "supporting": [],
        "contradicting": [],
        "attributes": attrs,
    }


def drafts() -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]]]:
    nodes = {
        n["id"]: n
        for n in [
            _node("mock:gene:1", "Gene", "MOCKGENE1", symbol="MOCKGENE1"),
            _node("mock:gene:2", "Gene", "MOCKGENE2", symbol="MOCKGENE2"),
            _node("mock:disease:1", "Disease", "Mock disease one"),
            _node("mock:disease:2", "Disease", "Mock disease two"),
            _node("mock:phenotype:1", "Phenotype", "Mock phenotype one"),
            _node("mock:pathway:1", "Pathway", "Mock pathway"),
            _node(DRUG, "Drug", "Mock-inib"),
            _node("mock:drug:2", "Drug", "Mock-second-inib"),
            _node("mock:variant:1", "Variant", "Mock variant one", gene="mock:gene:1"),
            _node("mock:variant:2", "Variant", "Mock variant two", gene="mock:gene:1"),
            _node("mech:MOCKGENE1:GoF", "Mechanism", "MOCKGENE1 gain of function", gene_symbol="MOCKGENE1", direction="GoF"),
            _node("person:mock-reporter-pi-mock-org-1", "Investigator", "Mock Reporter PI", match_confidence="low"),
        ]
    }
    edges = {
        (e["source"], e["target"], e["relation"]): e
        for e in [
            _edge("mock:disease:1", "mock:gene:1", "associated_with", confidence=0.7, origin="germline"),
            _edge("mock:disease:1", "mock:phenotype:1", "has_phenotype", confidence=0.9),
            _edge("mock:disease:1", "mech:MOCKGENE1:GoF", "has_mechanism", confidence=0.6, origin="germline"),
            _edge("mock:disease:2", "mech:MOCKGENE1:GoF", "has_mechanism", evidence_class="inferred", confidence=0.4, labels=["curated_unverified"], reason="curated seed from cluster doc (mock)", origin="germline"),
            _edge("mock:variant:2", "mock:disease:1", "has_condition", confidence=0.8),
        ]
    }
    # the curated edge as pipeline.graph.mechanisms R4 writes it: the seed row is the asserting source
    curated = edges[("mock:disease:2", "mech:MOCKGENE1:GoF", "has_mechanism")]
    curated["confidence_basis"] = {"rule": "R4.curated", "seed_source": "mock seed file", "seed_row": {"gene": "MOCKGENE1", "direction": "GoF", "origin": "germline"}}
    curated["sources"] = [dict(SEED_RECORD), {"kind": "db", "id": "MOCKDIS_2", "date": TS}]
    return nodes, edges


def merged_graph(nodes: dict[str, dict[str, Any]], edges: dict[tuple[str, str, str], dict[str, Any]], res: ev.EvidenceResult) -> Graph:
    """What build.py will do with the result: merge the additions and validate strictly."""
    all_nodes = {**nodes, **res.mechanism_nodes, **res.paper_nodes, **res.investigator_nodes}
    all_edges = {**edges, **res.new_edges}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity, source, query, date, hits in res.coverage_rows:
        grouped[entity].append({"source": source, "query": query, "date": date, "hits": hits})
    graph = Graph.model_validate(
        {
            "meta": {"built_at": TS, "sources": [{"name": "mock", "version": "", "retrieved_at": TS, "license": "mock"}], "coverage": {k: {"searched": v} for k, v in grouped.items()}},
            "nodes": [all_nodes[k] for k in sorted(all_nodes, key=lambda k: (all_nodes[k]["type"], k))],
            "edges": finalize_edges(all_edges.values()),
            "claims": res.claims,
            "clusters": [],
        }
    )
    return Graph.model_validate_json(graph.model_dump_json(), strict=True)


@pytest.fixture
def integrated() -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]], ev.EvidenceResult]:
    nodes, edges = drafts()
    res = ev.integrate_claims(nodes, edges, bundle(), today=TODAY)
    return nodes, edges, res


def by_id(res: ev.EvidenceResult) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in res.claims}


# --------------------------------------------------------------------------------------
# validity of the whole result
# --------------------------------------------------------------------------------------


def test_result_merges_into_a_valid_graph_and_round_trips(integrated) -> None:
    nodes, edges, res = integrated
    graph = merged_graph(nodes, edges, res)
    assert graph.claims and graph.edges
    attached = {cid for e in graph.edges for cid in e.supporting + e.contradicting}
    unattached = {u["id"] for u in res.unattached}
    assert attached | unattached == {c["id"] for c in res.claims}, "every claim is attached or listed as unattached"
    assert not (attached & unattached)
    dumped = graph.model_dump_json()
    for forbidden in ("@", "5 mg", "twice daily", "555 010"):
        assert forbidden not in dumped, forbidden


def test_report_counts_are_consistent(integrated) -> None:
    _, _, res = integrated
    r = res.report
    assert r["claims"]["in_bundle"] == 27 and r["claims"]["in_graph"] == 27
    assert r["claims"]["attached"] + r["claims"]["unattached"] == 27
    assert r["claims"]["dropped_quote_mismatch_after_scrub"] == []
    assert r["coverage_rows"] == len(res.coverage_rows)
    assert r["edges"]["created_by_relation"] == {"associated_with": 1, "has_condition": 1, "has_mechanism": 3, "has_phenotype": 1, "modulates": 3, "treats": 2}
    assert r["edges"]["upgraded_by_relation"] == {"associated_with": 1, "has_condition": 1, "has_mechanism": 2, "has_phenotype": 1}
    assert r["edges"]["upgrade_outcomes"] == {"inferred->extracted": 1, "observed": 4}
    assert r["endpoints"]["resolved_by_prefix"]["CHEMBL (bare)"] == 8 and r["endpoints"]["unresolved_by_prefix"]["text"] == 6
    assert r["endpoints"]["mapping_gaps"] == {"has_phenotype: Gene -> Phenotype": 1}
    assert r["mechanism_nodes_created"] == ["mech:MOCKGENE2:LoF"] and r["mechanism_nodes_created_without_edges"] == []
    assert r["variant_mechanism_routes"] == {"mock:variant:1": ["mech:MOCKGENE1:GoF"], "mock:variant:2": ["mech:MOCKGENE1:GoF"]}
    # replacements made in exported text: an affiliation is scrubbed once for the Paper node and once for the Investigator
    assert r["scrubbed_emails"] == 6 and r["scrubbed"]["phones"] == 2 and r["scrubbed"]["dose_fields_changed"] >= 2
    assert r["papers"] == {"in_bundle": 4, "exported": 4, "preprints": 1}


# --------------------------------------------------------------------------------------
# endpoint resolution and coverage
# --------------------------------------------------------------------------------------


def test_bare_chembl_subject_is_normalised_to_the_backbone_spelling(integrated) -> None:
    _, _, res = integrated
    assert by_id(res)["c_000001"]["subject"] == DRUG
    assert (DRUG, "mock:disease:1", "treats") in res.new_edges
    assert ev._prefix_of("CHEMBL0") == "CHEMBL (bare)" and ev._prefix_of("MONDO_0") == "MONDO (underscore)" and ev._prefix_of("text:x") == "text"


def test_unresolved_endpoints_become_coverage_rows(integrated) -> None:
    _, _, res = integrated
    rows = {(e, q, h) for e, s, q, d, h in res.coverage_rows if s == ev.COVERAGE_SOURCE}
    assert all(d == TODAY for _, _, _, d, _ in res.coverage_rows)
    assert ("mech:MOCKGENE1:GoF", "text:some mock symptom", 0) in rows, "entity = the resolved side"
    assert (DRUG, "text:a mock kinase", 0) in rows
    assert (ev.COVERAGE_UNRESOLVED_ENTITY, "text:something mock", 0) in rows and (ev.COVERAGE_UNRESOLVED_ENTITY, "text:something else mock", 0) in rows
    assert ("mock:disease:1", "text:unknown mock variant [claim variant is not a Variant node]", 0) in rows
    assert any(e == "mock:gene:1" and "no edge mapping" in q for e, q, _ in rows), "a mapping gap is a coverage row too"
    reasons = {u["id"]: u["reason"] for u in res.unattached}
    assert "unresolved (text)" in reasons["c_000010"] and "unresolved (text)" in reasons["c_000017"]
    assert reasons["c_000019"].startswith("no edge mapping for has_phenotype between Gene and Phenotype")
    assert reasons["c_000018"].startswith("only contradicting claims")
    assert (("mock:drug:2", "mock:disease:1", "treats") not in res.new_edges), "no edge is created from contradicting claims alone"


def test_claim_variant_that_is_not_a_variant_node_is_cleared(integrated) -> None:
    _, _, res = integrated
    assert by_id(res)["c_000021"]["variant"] == "" and res.report["claims"]["variant_cleared"] == ["c_000021"]
    assert by_id(res)["c_000006"]["variant"] == "mock:variant:1"


# --------------------------------------------------------------------------------------
# predicate mapping: create vs upgrade
# --------------------------------------------------------------------------------------


def test_treats_edge_is_contested_small_n_promising_and_preprint(integrated) -> None:
    _, _, res = integrated
    e = res.new_edges[(DRUG, "mock:disease:1", "treats")]
    assert e["evidence_class"] == "extracted" and e["source_db"] == "claims"
    assert e["supporting"] == ["c_000001", "c_000003", "c_000020"] and e["contradicting"] == ["c_000002"]
    assert e["status"] == "contested"
    assert e["attributes"]["labels"] == ["promising", "contested", "small_n", "preprint"], "in_vitro n=16 never blocks small_n"
    assert e["attributes"]["preprint"] is True
    # weights: case_report 0.4, in_vitro 0.3, case_report 0.4 -> base 0.4 + 2 * 0.05 = 0.5; 1 contradicting / 3 supporting
    assert e["confidence"] == pytest.approx(0.5 * (1 - 0.3 * (1 / 3)))
    basis = e["confidence_basis"]["claims"]
    assert basis["n_supporting"] == 3 and basis["n_contradicting"] == 1 and basis["literature_confidence"] == pytest.approx(0.45)
    assert e["confidence_basis"]["claim_status"]["patient_level_n"] == [1, 1, 4], "the in_vitro n never counts"
    assert {p["id"]: p["confidence"] for p in basis["per_claim"]} == {"c_000001": 0.4, "c_000003": 0.3, "c_000020": 0.4, "c_000002": 0.6}
    kinds = [(s["kind"], s["id"], s["date"]) for s in e["sources"]]
    assert kinds == [("pmid", "0", "2019"), ("doi", "10.0000/mock-preprint", "2021"), ("db", MOCK_PAPER, "2020")], "one dated source per distinct paper"


def test_cohort_of_twenty_five_blocks_small_n(integrated) -> None:
    _, _, res = integrated
    e = res.new_edges[("mock:drug:2", "mock:disease:2", "treats")]
    assert e["status"] == "supported" and e["attributes"]["labels"] == ["promising", "preprint"]
    assert e["confidence"] == pytest.approx(0.85), "cohort 0.8 + one extra supporting claim"


def test_has_mechanism_upgrades_observed_edge_and_adds_variant_route(integrated) -> None:
    nodes, edges, res = integrated
    e = edges[("mock:disease:1", "mech:MOCKGENE1:GoF", "has_mechanism")]
    assert e["evidence_class"] == "observed", "a database edge enriched by papers stays observed"
    assert e["supporting"] == ["c_000006", "c_000012"] and e["status"] == "small_n"
    assert e["confidence"] == pytest.approx(0.6), "max(observed 0.6, claims 0.45)"
    assert e["attributes"]["labels"] == ["promising", "small_n"] and e["attributes"]["origin"] == "germline"
    assert e["confidence_basis"]["rule"] == "mock" and "claims" in e["confidence_basis"]
    route = res.new_edges[("mock:variant:1", "mech:MOCKGENE1:GoF", "has_mechanism")]
    assert route["supporting"] == ["c_000006"] and route["evidence_class"] == "extracted"
    assert "variant_specific" in route["attributes"]["labels"] and route["status"] == "small_n"


def test_mech_endpoint_creates_the_mechanism_node(integrated) -> None:
    nodes, _, res = integrated
    node = res.mechanism_nodes["mech:MOCKGENE2:LoF"]
    assert nodes["mech:MOCKGENE2:LoF"] is node, "also inserted into the drafts so edges can point at it"
    assert node["type"] == "Mechanism" and node["label"] == "MOCKGENE2 loss of function"
    assert node["props"] == {"gene_symbol": "MOCKGENE2", "direction": "LoF", "source": "claims", "gene": "mock:gene:2"}
    e = res.new_edges[("mock:disease:2", "mech:MOCKGENE2:LoF", "has_mechanism")]
    assert e["supporting"] == ["c_000007"] and e["status"] == "supported" and e["attributes"]["labels"] == ["in_vitro_only"], "a review is not patient-level: no 'promising', and the review-only rule labels the edge"
    assert e["confidence"] == pytest.approx(0.5)
    assert e["confidence_basis"]["in_vitro_only"] == {"study_types": ["review"], "cap": 0.5, "uncapped_confidence": pytest.approx(0.5)}


def test_inferred_edge_with_support_becomes_extracted(integrated) -> None:
    _, edges, res = integrated
    e = edges[("mock:disease:2", "mech:MOCKGENE1:GoF", "has_mechanism")]
    assert e["evidence_class"] == "extracted" and e["confidence"] == pytest.approx(0.8) and e["status"] == "supported"
    assert e["attributes"]["labels"] == ["promising"], "curated_unverified is dropped once a claim supports the edge"
    assert e["confidence_basis"]["upgraded_from"] == "inferred" and e["confidence_basis"]["prior"]["confidence"] == 0.4
    # hard rule 10: what only the seed row asserted does not survive the upgrade as if it were sourced
    assert "reason" not in e["attributes"] and "origin" not in e["attributes"], "seed-only reason and germline origin are gone"
    assert SEED_RECORD not in e["sources"] and e["sources"][0] == {"kind": "db", "id": "MOCKDIS_2", "date": TS}, "sources list retrieved records only"
    assert [s["kind"] for s in e["sources"]] == ["db", "db"] and e["sources"][1]["id"] == MOCK_PAPER
    assert e["confidence_basis"]["prior"]["seed_record"] == SEED_RECORD and e["confidence_basis"]["prior"]["rule"] == "R4.curated"


def test_has_phenotype_attaches_to_the_observed_edge(integrated) -> None:
    _, edges, _ = integrated
    e = edges[("mock:disease:1", "mock:phenotype:1", "has_phenotype")]
    assert e["supporting"] == ["c_000009", "c_000021"] and e["evidence_class"] == "observed" and e["confidence"] == 0.9
    assert e["status"] == "small_n" and e["attributes"]["labels"] == ["promising", "small_n"]


def test_causes_and_associated_with_attach_in_either_orientation_or_create(integrated) -> None:
    _, edges, res = integrated
    assert edges[("mock:disease:1", "mock:gene:1", "associated_with")]["supporting"] == ["c_000011"]
    assert edges[("mock:variant:2", "mock:disease:1", "has_condition")]["supporting"] == ["c_000013"]
    created = res.new_edges[("mock:disease:2", "mock:gene:2", "associated_with")]
    assert created["supporting"] == ["c_000014"] and created["evidence_class"] == "extracted"


def test_modulates_only_when_both_resolve(integrated) -> None:
    _, _, res = integrated
    e = res.new_edges[(DRUG, "mock:gene:1", "modulates")]
    assert e["supporting"] == ["c_000015"] and e["confidence"] == pytest.approx(0.3)
    assert not any(k[2] == "modulates" and k[1].startswith("text:") for k in res.new_edges)
    # a Mechanism or a Pathway object is a valid modulates target too
    assert res.new_edges[(DRUG, "mech:MOCKGENE1:GoF", "modulates")]["supporting"] == ["c_000025"]
    assert res.new_edges[(DRUG, "mock:pathway:1", "modulates")]["supporting"] == ["c_000026"]


def test_variant_subject_has_mechanism_creates_a_variant_specific_edge(integrated) -> None:
    _, _, res = integrated
    e = res.new_edges[("mock:variant:2", "mech:MOCKGENE1:GoF", "has_mechanism")]
    assert e["supporting"] == ["c_000023"] and e["evidence_class"] == "extracted" and e["status"] == "small_n"
    assert e["attributes"]["labels"] == ["promising", "small_n", "variant_specific"]
    assert ("mech:MOCKGENE1:GoF", "mock:variant:2", "has_mechanism") not in res.new_edges


def test_mechanism_subject_has_phenotype_creates_an_extracted_edge(integrated) -> None:
    _, edges, res = integrated
    e = res.new_edges[("mech:MOCKGENE1:GoF", "mock:phenotype:1", "has_phenotype")]
    assert e["supporting"] == ["c_000024"] and e["evidence_class"] == "extracted" and e["status"] == "small_n"
    assert edges[("mock:disease:1", "mock:phenotype:1", "has_phenotype")]["supporting"] == ["c_000009", "c_000021"], "the disease edge is untouched"


def test_variant_disease_pair_without_an_edge_creates_has_condition(integrated) -> None:
    _, _, res = integrated
    e = res.new_edges[("mock:variant:1", "mock:disease:2", "has_condition")]
    assert e["supporting"] == ["c_000027"] and e["evidence_class"] == "extracted" and e["source_db"] == "claims"
    assert ("mock:disease:2", "mock:variant:1", "has_condition") not in res.new_edges, "the ClinVar orientation (Variant -> Disease) is the one created"


# --------------------------------------------------------------------------------------
# scrubbing
# --------------------------------------------------------------------------------------


def test_dosed_quote_and_abstract_are_scrubbed_together(integrated) -> None:
    _, _, res = integrated
    c = by_id(res)["c_000020"]
    assert c["quote"] == "Mock-inib given [dose removed] improved things."
    abstract = res.paper_nodes[PMID_PAPER]["props"]["abstract"]
    assert "5 mg" not in abstract and c["quote"] in abstract


def test_scrub_contacts_removes_emails_and_phones_but_keeps_postal_codes() -> None:
    text, n_mail, n_phone = ev.scrub_contacts("Mock Dept, Bethesda, Maryland 20892-1674, USA. Electronic address: who@mock.example. Tel: +1 301 555 0100; London SE1 9RT")
    assert n_mail == 1 and n_phone == 1
    assert "@" not in text and "301 555" not in text and "20892-1674" in text and "SE1 9RT" in text
    assert text.count(ev.EMAIL_PLACEHOLDER) == 1 and text.count(ev.PHONE_PLACEHOLDER) == 1
    plain, a, b = ev.scrub_contacts("INSERM 1163, Paris; Imagine Institute")
    assert (a, b) == (0, 0) and plain.startswith("INSERM 1163")


def test_quote_that_no_longer_matches_after_scrubbing_is_dropped_and_counted() -> None:
    nodes, edges = drafts()
    b = bundle()
    # a scrubber that rewrites the abstract but not this quote breaks the match: the claim is dropped, never exported unverified
    res = ev.integrate_claims(nodes, edges, b, today=TODAY, scrub=lambda s: s.replace("Mock-inib treated", "Mock-inib [dose removed] treated") if "one mock patient" in s and len(s) > 60 else s)
    assert res.report["claims"]["dropped_quote_mismatch_after_scrub"] == ["c_000001"]
    assert "c_000001" not in by_id(res)
    merged_graph(nodes, edges, res)


# --------------------------------------------------------------------------------------
# papers and investigators
# --------------------------------------------------------------------------------------


def test_paper_nodes_keep_the_contract_props_and_scrub_authors(integrated) -> None:
    _, _, res = integrated
    p = res.paper_nodes[PMID_PAPER]
    assert set(p["props"]) == {"title", "abstract", "year", "journal", "pmid", "doi", "authors", "pub_types", "preprint"} - {"doi"}
    assert p["props"]["pmid"] == "0" and p["props"]["preprint"] is False and "bulky_field" not in p["props"]
    assert p["props"]["authors"][0] == {"name": "Mock Author One", "affiliation": "Mock Institute, Mock City. [email removed].", "orcid": "mock-orcid-1"}
    pre = res.paper_nodes[DOI_PAPER]
    assert pre["props"]["preprint"] is True and pre["props"]["doi"] == "10.0000/mock-preprint" and pre["props"]["authors"] == ["Mock Author Three"]


def test_investigators_merge_across_papers_by_orcid_or_name_slug(integrated) -> None:
    _, _, res = integrated
    inv = res.investigator_nodes
    assert set(inv) == {"orcid:mock-orcid-1", "person:mock-author-two", "person:mock-author-three"}, "the quiet paper's author has no publishes_on edge"
    one = inv["orcid:mock-orcid-1"]
    assert one["type"] == "Investigator" and one["label"] == "Mock Author One"
    assert one["props"]["match_confidence"] == "high" and one["props"]["orcid"] == "mock-orcid-1" and one["props"]["n_papers"] == 2
    assert one["props"]["papers"] == [PMID_PAPER, MOCK_PAPER]
    assert one["props"]["affiliations"] == ["Mock Institute, Mock City. [email removed].", "Mock Hospital, Mock Town; [phone removed]"]
    assert one["props"]["role_label"] == "author of 2 papers on Mock disease one, MOCKGENE1 gain of function"
    two = inv["person:mock-author-two"]
    assert two["props"]["match_confidence"] == "low" and two["props"]["n_papers"] == 2 and two["props"]["affiliations"] == ["Mock Lab ([email removed])"]
    three = inv["person:mock-author-three"]
    assert three["props"]["role_label"].startswith("author of 1 paper on ") and three["props"]["affiliations"] == []
    assert "leader" not in json.dumps(inv).lower()
    # topics: One/Two (papers 0 + 1) -> disease 1, disease 2, both mechanisms; Three (preprint) -> disease 1, disease 2, mechanism 1
    assert one["props"]["papers_by_match"] == {"orcid": [PMID_PAPER, MOCK_PAPER], "name": []} and two["props"]["papers_by_match"] == {"orcid": [], "name": [PMID_PAPER, MOCK_PAPER]}
    assert res.report["investigators"] == {"total": 4, "exported": 3, "with_orcid": 1, "merged_by_name": 0, "ambiguous_names": {}, "min_papers": 1, "below_min_papers": 0, "merged_into_existing_nodes": 0, "skipped_id_collision": 0, "skipped_nameless": 0, "author_of_edges": 5, "publishes_on_edges": 11}


def test_name_only_entries_fold_into_the_single_orcid_record() -> None:
    """PubMed supplies an ORCID on some of a person's papers only: the name-only record folds into
    the ORCID record (one node, every paper, the name-matched ones flagged), never two nodes."""
    nodes, edges = drafts()
    b = bundle()
    b.papers[1].props["authors"][1] = {**AUTHOR_TWO, "orcid": "https://orcid.org/mock-orcid-2"}  # Author Two carries an ORCID on the mock paper only
    res = ev.integrate_claims(nodes, edges, b, today=TODAY)
    inv = res.investigator_nodes
    assert "person:mock-author-two" not in inv and set(inv) == {"orcid:mock-orcid-1", "orcid:mock-orcid-2", "person:mock-author-three"}
    two = inv["orcid:mock-orcid-2"]
    assert two["label"] == "Mock Author Two" and two["props"]["orcid"] == "mock-orcid-2" and two["props"]["n_papers"] == 2
    assert two["props"]["papers"] == [PMID_PAPER, MOCK_PAPER], "bundle order"
    assert two["props"]["papers_by_match"] == {"orcid": [MOCK_PAPER], "name": [PMID_PAPER]} and two["props"]["match_confidence"] == "mixed"
    assert two["props"]["affiliations"] == ["Mock Lab ([email removed])"]
    assert res.new_edges[("orcid:mock-orcid-2", PMID_PAPER, "author_of")]["confidence_basis"]["matched_by"] == "name"
    assert res.new_edges[("orcid:mock-orcid-2", MOCK_PAPER, "author_of")]["confidence_basis"]["matched_by"] == "orcid"
    assert not any(k[0] == "person:mock-author-two" for k in res.new_edges)
    assert res.new_edges[("orcid:mock-orcid-2", "mock:disease:1", "publishes_on")]["confidence"] == pytest.approx(0.5), "both papers count once"
    r = res.report["investigators"]
    assert r["merged_by_name"] == 1 and r["ambiguous_names"] == {} and r["total"] == 4 and r["with_orcid"] == 2
    merged_graph(nodes, edges, res)


def test_a_name_seen_with_two_orcids_is_ambiguous_and_stays_split() -> None:
    nodes, edges = drafts()
    b = bundle()
    b.papers[0].props["authors"][1] = {**AUTHOR_TWO, "orcid": "mock-orcid-2"}
    b.papers[1].props["authors"][1] = {**AUTHOR_TWO, "orcid": "mock-orcid-3"}
    b.papers[2].props["authors"].append(dict(AUTHOR_TWO))  # a name-only entry: which of the two people is it?
    res = ev.integrate_claims(nodes, edges, b, today=TODAY)
    inv = res.investigator_nodes
    assert {"orcid:mock-orcid-2", "orcid:mock-orcid-3", "person:mock-author-two"} <= set(inv)
    assert inv["person:mock-author-two"]["props"]["papers"] == [DOI_PAPER] and inv["person:mock-author-two"]["props"]["match_confidence"] == "low"
    assert inv["orcid:mock-orcid-2"]["props"]["match_confidence"] == "high" and inv["orcid:mock-orcid-2"]["props"]["n_papers"] == 1
    r = res.report["investigators"]
    assert r["merged_by_name"] == 0 and r["ambiguous_names"] == {"mock-author-two": ["orcid:mock-orcid-2", "orcid:mock-orcid-3"]}
    merged_graph(nodes, edges, res)


def test_investigator_min_papers_is_a_general_cap() -> None:
    nodes, edges = drafts()
    res = ev.integrate_claims(nodes, edges, bundle(), today=TODAY, investigator_min_papers=2)
    assert set(res.investigator_nodes) == {"orcid:mock-orcid-1", "person:mock-author-two"}
    assert res.report["investigators"]["below_min_papers"] == 2 and res.report["investigators"]["min_papers"] == 2
    assert not any(k[0] == "person:mock-author-three" for k in res.new_edges)
    merged_graph(nodes, edges, res)


def test_author_of_and_publishes_on_edges(integrated) -> None:
    _, _, res = integrated
    author_of = res.new_edges[("orcid:mock-orcid-1", PMID_PAPER, "author_of")]
    assert author_of["evidence_class"] == "observed" and author_of["confidence"] == 0.9 and author_of["confidence_basis"]["pubmed_record"] == PMID_PAPER
    assert author_of["sources"] == [{"kind": "pmid", "id": "0", "date": "2019"}]
    pub = res.new_edges[("orcid:mock-orcid-1", "mock:disease:1", "publishes_on")]
    assert pub["confidence"] == pytest.approx(0.5), "0.3 + 0.1 * 2 papers"
    assert pub["confidence_basis"]["n_papers"] == 2 and pub["confidence_basis"]["paper_ids"] == [MOCK_PAPER, PMID_PAPER]
    assert set(pub["confidence_basis"]["via_claims"]) >= {"c_000001", "c_000002", "c_000006"}
    assert [s["kind"] for s in pub["sources"]] == ["db", "pmid"]
    three = res.new_edges[("person:mock-author-three", "mock:disease:2", "publishes_on")]
    assert three["confidence"] == pytest.approx(0.4)
    assert ("person:mock-author-three", "mech:MOCKGENE1:GoF", "publishes_on") in res.new_edges, "a mech: endpoint counts as a topic"


def test_claim_status_small_n_ignores_unknown_n_and_non_patient_studies() -> None:
    def c(study_type: str, n: int | None, paper: str = MOCK_PAPER) -> dict[str, Any]:
        return {"study_type": study_type, "n_patients": n, "paper": paper}

    # a cohort with no reported n neither blocks nor grants small_n; in_vitro n=16 never counts
    status, labels, basis = ev.claim_status([c("case_report", 1), c("cohort", None), c("in_vitro", 16)], [c("case_series", 2)])
    assert status == "contested" and labels == ["promising", "contested", "small_n"] and basis["patient_level_n"] == [1, 2] and basis["patient_level_without_n"] == 1
    status, labels, _ = ev.claim_status([c("cohort", 25), c("case_report", 1)], [])
    assert status == "supported" and labels == ["promising"]
    status, labels, _ = ev.claim_status([c("cohort", None)], [])
    assert status == "supported" and labels == ["promising"], "no known n: no small_n"
    status, labels, _ = ev.claim_status([c("review", None, DOI_PAPER)], [])
    assert status == "supported" and labels == ["preprint"]
    status, labels, _ = ev.claim_status([], [c("case_report", 1)])
    assert status == "small_n" and labels == ["small_n"], "contradicting-only on an existing edge is still small-n evidence"


def test_slugify_and_aggregate_confidence() -> None:
    assert ev.slugify("Édith  Mock-Author ") == "edith-mock-author" and ev.slugify("") == ""
    conf, basis = ev.aggregate_confidence([0.4, 0.8, 0.6], 3)
    assert conf == pytest.approx(0.9 * 0.7) and basis["base"] == pytest.approx(0.9) and basis["contradiction_ratio"] == 1.0
    conf, basis = ev.aggregate_confidence([], 2)
    assert conf == 0.0 and basis["max_supporting_weight"] is None
    conf, _ = ev.aggregate_confidence([1.0, 1.0, 1.0], 0)
    assert conf == 1.0


# --------------------------------------------------------------------------------------
# general rules added 2026-10-04: contradicting claims on mechanism edges, protein-change
# mentions, in-vitro-only mechanism edges
# --------------------------------------------------------------------------------------


def _mini_bundle(quotes: dict[str, str], claims: list[dict[str, Any]]) -> ClaimsBundle:
    abstract = " ".join(quotes.values())
    paper = {"id": MOCK_PAPER, "type": "Paper", "label": "Mock paper one", "synonyms": [], "props": {"title": "Mock paper one", "abstract": abstract, "year": 2020, "authors": [AUTHOR_FOUR]}}
    return ClaimsBundle.model_validate({"extracted_at": TS, "model": "mock-model", "papers": [paper], "claims": claims})


def _mini_claim(n: int, quote: str, subject: str, predicate: str, obj: str, *, polarity: str = "supports", study_type: str = "case_report", n_patients: int | None = None, variant: str = "") -> dict[str, Any]:
    return {"id": f"c_{n:06d}", "pmid": "", "paper": MOCK_PAPER, "quote": quote, "polarity": polarity, "subject": subject, "predicate": predicate, "object": obj, "variant": variant, "study_type": study_type, "n_patients": n_patients}


def test_contradicting_claims_reach_a_mechanism_edge_only_through_has_mechanism() -> None:
    nodes, edges = drafts()
    q = {
        "con_causes": "Mock drug pharmacodynamics looked different in patient cells.",
        "con_mech": "Mock disease one does not show the MOCKGENE1 route in this series.",
        "sup_causes": "The mock gain of function route causes mock disease one.",
    }
    bundle_ = _mini_bundle(
        q,
        [
            # a contradicting claim phrased as `causes` between the Mechanism and the Disease: NOT on the mechanism edge
            _mini_claim(1, q["con_causes"], "mech:MOCKGENE1:GoF", "causes", "mock:disease:1", polarity="contradicts", study_type="cohort", n_patients=14),
            # a contradicting has_mechanism claim whose object IS the Mechanism node: attaches
            _mini_claim(2, q["con_mech"], "mock:disease:1", "has_mechanism", "mech:MOCKGENE1:GoF", polarity="contradicts", study_type="case_series", n_patients=3),
            # a supporting `causes` claim still attaches in either orientation
            _mini_claim(3, q["sup_causes"], "mech:MOCKGENE1:GoF", "causes", "mock:disease:1", study_type="review"),
        ],
    )
    res = ev.integrate_claims(nodes, edges, bundle_, today=TODAY)
    e = edges[("mock:disease:1", "mech:MOCKGENE1:GoF", "has_mechanism")]
    assert e["supporting"] == ["c_000003"] and e["contradicting"] == ["c_000002"] and e["status"] == "contested"
    reasons = {u["id"]: u["reason"] for u in res.unattached}
    assert reasons == {"c_000001": ev.CONTRADICTING_MECHANISM_PROBLEM.format(predicate="causes")}
    assert res.report["claims"]["contradicting_not_on_mechanism_edge"] == 1 and res.report["endpoints"]["mapping_gaps"] == {}, "not a mapping gap: the pair maps, the polarity does not"
    assert any(e_ == "mech:MOCKGENE1:GoF" and "contradicting causes claim" in q_ for e_, s_, q_, _, _ in res.coverage_rows if s_ == ev.COVERAGE_SOURCE), "the failed link is a coverage row"
    assert ev.COVERAGE_SOURCE == "unresolved literature mention"
    assert next(c for c in res.claims if c["id"] == "c_000001")["polarity"] == "contradicts", "the claim stays in claims[]"
    merged_graph(nodes, edges, res)


def _mention_drafts() -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]]]:
    nodes, edges = drafts()
    variants = [
        _node("mock:variant:a", "Variant", "Mock variant A (p.Arg274Trp)", ["p.Arg274Trp", "MOCKGENE1 R274W"], protein_change="R274W", hgvs_p="p.Arg274Trp", gene_symbol="MOCKGENE1", gene_symbols=["MOCKGENE1"]),
        _node("mock:variant:b", "Variant", "Mock variant B (p.Arg274Trp)", ["p.Arg274Trp"], protein_change="R274W", gene_symbol="MOCKGENE2"),  # same token, other gene
        _node("mock:variant:c", "Variant", "Mock variant C", ["T419R"], protein_change="T419R", gene_symbols=["MOCKGENE1"]),
        _node("mock:variant:d", "Variant", "Mock variant D", [], protein_change="P1104A", gene_symbol="MOCKGENE1"),
        _node("mock:variant:e", "Variant", "Mock variant E", [], protein_change="P1104A", gene_symbol="MOCKGENE1"),  # ambiguous within the gene
        _node("mock:variant:f", "Variant", "Mock variant F", ["V617F"], protein_change="V617F", gene_symbol="MOCKGENE2"),  # unique across all variants
    ]
    for v in variants:
        nodes[v["id"]] = v
    return nodes, edges


def test_protein_change_mentions_resolve_to_exactly_one_variant_of_the_named_gene() -> None:
    nodes, edges = _mention_drafts()
    q = {
        "s_named": "The MOCKGENE1 R274W change drives the route.",
        "v_mech": "Mock disease one carries the route in one patient.",
        "v_disease": "Mock-inib helped the mock patient.",
        "v_nogene": "Mock-inib changed the mock pathway.",
        "v_wrong": "Mock-inib treated another mock patient.",
        "v_unique": "Mock-inib touched the pathway again.",
        "ambig": "Homozygosity was seen in two mock patients.",
        "phrase": "Mock-inib modulates mutated cell lines.",
        "two": "Two changes showed the mock phenotype.",
        "causes": "The other change was found in mock disease one.",
    }
    bundle_ = _mini_bundle(
        q,
        [
            # subject and variant both name MOCKGENE1 R274W: the gene in the mention picks variant A over B
            _mini_claim(1, q["s_named"], "text:MOCKGENE1 R274W", "has_mechanism", "mech:MOCKGENE1:GoF", variant="text:R274W", n_patients=1),
            # bare token; the gene comes from the Mechanism endpoint (and the Disease's mechanism edge)
            _mini_claim(2, q["v_mech"], "mock:disease:1", "has_mechanism", "mech:MOCKGENE1:GoF", variant="text:R274W", n_patients=1),
            # bare token; the gene comes from the Disease endpoint's mechanism edge only
            _mini_claim(3, q["v_disease"], "CHEMBL0", "treats", "mock:disease:1", variant="text:R274W", n_patients=1),
            # bare token, no gene anywhere: R274W exists in two genes -> ambiguous, stays unresolved
            _mini_claim(4, q["v_nogene"], "CHEMBL0", "modulates", "mock:pathway:1", variant="text:R274W", study_type="in_vitro"),
            # the named gene (via the disease) has no such variant: unresolved, never the other gene's variant
            _mini_claim(5, q["v_wrong"], "CHEMBL0", "treats", "mock:disease:1", variant="text:V617F", n_patients=1),
            # no gene anywhere and the token is unique across all Variant nodes: resolves
            _mini_claim(6, q["v_unique"], "CHEMBL0", "modulates", "mock:pathway:1", variant="text:V617F", study_type="in_vitro"),
            # two variants of the named gene carry the token: ambiguous
            _mini_claim(7, q["ambig"], "text:MOCKGENE1 P1104A homozygosity", "causes", "mock:disease:1", variant="text:P1104A", n_patients=2),
            # a phrase that merely contains a variant is not the variant
            _mini_claim(8, q["phrase"], "CHEMBL0", "modulates", "text:MOCKGENE1 T419R-mutated cell lines", study_type="in_vitro"),
            # two tokens naming two variants: ambiguous
            _mini_claim(9, q["two"], "text:MOCKGENE1 T419R and R274W", "has_phenotype", "mock:phenotype:1", n_patients=2),
            # a resolved Variant subject takes the normal {Variant, Disease} route (has_condition)
            _mini_claim(10, q["causes"], "text:MOCKGENE1 T419R", "causes", "mock:disease:1", n_patients=1),
        ],
    )
    res = ev.integrate_claims(nodes, edges, bundle_, today=TODAY)
    claims = by_id(res)
    assert claims["c_000001"]["subject"] == "mock:variant:a" and claims["c_000001"]["variant"] == "mock:variant:a"
    assert claims["c_000002"]["variant"] == "mock:variant:a" and claims["c_000003"]["variant"] == "mock:variant:a"
    assert claims["c_000004"]["variant"] == "" and claims["c_000005"]["variant"] == "" and claims["c_000007"]["variant"] == ""
    assert claims["c_000006"]["variant"] == "mock:variant:f"
    assert claims["c_000007"]["subject"] == "text:MOCKGENE1 P1104A homozygosity" and claims["c_000008"]["object"] == "text:MOCKGENE1 T419R-mutated cell lines"
    assert claims["c_000009"]["subject"] == "text:MOCKGENE1 T419R and R274W" and claims["c_000010"]["subject"] == "mock:variant:c"
    # the variant-specific route ran for the resolved mentions
    route = res.new_edges[("mock:variant:a", "mech:MOCKGENE1:GoF", "has_mechanism")]
    assert route["supporting"] == ["c_000001", "c_000002"] and "variant_specific" in route["attributes"]["labels"]
    notes = route["confidence_basis"]["resolved_mentions"]
    assert {(n["claim"], n["field"], n["resolved_from"], n["resolved_to"]) for n in notes} == {
        ("c_000001", "subject", "text:MOCKGENE1 R274W", "mock:variant:a"),
        ("c_000001", "variant", "text:R274W", "mock:variant:a"),
        ("c_000002", "variant", "text:R274W", "mock:variant:a"),
    }
    assert all(n["method"] == ev.VARIANT_MENTION_METHOD for n in notes)
    condition = res.new_edges[("mock:variant:c", "mock:disease:1", "has_condition")]
    assert condition["supporting"] == ["c_000010"] and condition["confidence_basis"]["resolved_mentions"][0]["resolved_to"] == "mock:variant:c"
    # unresolved mentions keep today's coverage rows and reasons
    rows = {(e, q_) for e, s, q_, _, _ in res.coverage_rows if s == ev.COVERAGE_SOURCE}
    assert (DRUG, "text:R274W [claim variant is not a Variant node]") in rows and (DRUG, "text:V617F [claim variant is not a Variant node]") in rows, "keyed by the resolved subject, as today"
    assert ("mock:disease:1", "text:MOCKGENE1 P1104A homozygosity") in rows and (DRUG, "text:MOCKGENE1 T419R-mutated cell lines") in rows
    assert sorted(res.report["claims"]["variant_cleared"]) == ["c_000004", "c_000005", "c_000007"]
    report = res.report["claims"]["variant_mentions"]
    assert report["method"] == ev.VARIANT_MENTION_METHOD
    assert report["outcomes"] == {"ambiguous": 3, "mention_names_more_than_the_variant": 2, "no_matching_variant": 1, "resolved": 6}, (
        "ambiguous: R274W with no gene, P1104A twice (subject and variant); more than the variant: the -mutated phrase and the two-token 'and' mention; V617F against the wrong gene"
    )
    assert [(r["claim"], r["field"], r["resolved_to"]) for r in report["resolved"]] == [
        ("c_000001", "subject", "mock:variant:a"),
        ("c_000001", "variant", "mock:variant:a"),
        ("c_000002", "variant", "mock:variant:a"),
        ("c_000003", "variant", "mock:variant:a"),
        ("c_000006", "variant", "mock:variant:f"),
        ("c_000010", "subject", "mock:variant:c"),
    ]
    assert res.report["endpoints"]["resolution_how"][ev.VARIANT_MENTION_HOW] == 6, "every field resolved this way (2 subjects + 4 variant fields) moves from the 'text' tally"
    assert res.report["endpoints"]["resolved_by_prefix"]["text"] == 2, "the subject / object tallies count the two resolved subjects"
    merged_graph(nodes, edges, res)


def test_protein_change_token_shape() -> None:
    hits = ev.PROTEIN_CHANGE_TOKEN.findall("STAT1 R274W, K119*, p.Arg274Trp and p.Lys119Ter; not CD14+, JAK2V617F, L106VfsTer42, c.820C>T, HP:0001548, IL17A or Stat1-/-")
    assert hits == ["R274W", "K119*", "p.Arg274Trp", "p.Lys119Ter"]


def test_in_vitro_only_mechanism_edges_are_labelled_and_capped() -> None:
    nodes, edges = drafts()
    q = {
        "vitro": "MOCKGENE2 loss of function in mock cells.",
        "review": "A mock review summarises MOCKGENE2 loss of function.",
        "vitro2": "MOCKGENE2 loss of function in other mock cells.",
        "patient": "One mock patient carried the MOCKGENE2 route.",
        "obs_review": "A mock review repeats the MOCKGENE1 route.",
    }
    bundle_ = _mini_bundle(
        q,
        [
            # a new disease -> mechanism edge from two non-patient claims: capped at 0.5 and labelled
            _mini_claim(1, q["vitro"], "mock:disease:1", "has_mechanism", "mech:MOCKGENE2:LoF", study_type="in_vitro"),
            _mini_claim(2, q["review"], "mock:disease:1", "has_mechanism", "mech:MOCKGENE2:LoF", study_type="review"),
            # a second new edge with a patient-level claim among the support: untouched
            _mini_claim(3, q["vitro2"], "mock:disease:2", "has_mechanism", "mech:MOCKGENE2:LoF", study_type="in_vitro"),
            _mini_claim(4, q["patient"], "mock:disease:2", "has_mechanism", "mech:MOCKGENE2:LoF", study_type="case_report", n_patients=1),
            # a review on the OBSERVED R1 edge: the database still supports it, no cap
            _mini_claim(5, q["obs_review"], "mock:disease:1", "has_mechanism", "mech:MOCKGENE1:GoF", study_type="review"),
        ],
    )
    res = ev.integrate_claims(nodes, edges, bundle_, today=TODAY)
    capped = res.new_edges[("mock:disease:1", "mech:MOCKGENE2:LoF", "has_mechanism")]
    assert capped["evidence_class"] == "extracted" and capped["confidence"] == pytest.approx(0.5), "review 0.5 + 0.05 for the second claim = 0.55, capped"
    assert capped["attributes"]["labels"] == ["in_vitro_only"]
    assert capped["confidence_basis"]["in_vitro_only"] == {"study_types": ["in_vitro", "review"], "cap": 0.5, "uncapped_confidence": pytest.approx(0.55)}
    assert capped["confidence_basis"]["claims"]["literature_confidence"] == pytest.approx(0.55), "the aggregate itself is unchanged"
    mixed = res.new_edges[("mock:disease:2", "mech:MOCKGENE2:LoF", "has_mechanism")]
    assert "in_vitro_only" not in mixed["attributes"]["labels"] and "in_vitro_only" not in mixed["confidence_basis"] and mixed["confidence"] == pytest.approx(0.45)
    observed = edges[("mock:disease:1", "mech:MOCKGENE1:GoF", "has_mechanism")]
    assert observed["evidence_class"] == "observed" and observed["confidence"] == 0.6 and "in_vitro_only" not in observed["attributes"].get("labels", [])
    merged_graph(nodes, edges, res)
