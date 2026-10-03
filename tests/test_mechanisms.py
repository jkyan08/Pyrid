"""pipeline/graph/mechanisms.py: rules R1-R4 over a synthetic store in ``tmp_path``.

Everything is a placeholder (``mock:`` ids, "Mock " labels, ``MOCKGENE<n>`` symbols); no external
identifier, PMID or real disease text appears here. The disease texts are written to trigger (or
not trigger) one general rule each.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pipeline.graph import mechanisms as mech
from pipeline.models import ClaimsBundle
from pipeline.store import Store

TS = "2026-10-03T12:00:00+00:00"
NOW = "2026-10-03T13:00:00+00:00"
THRESHOLDS = {"disease_gene_min_score": 0.3, "keep_disgenet_direct": True}


# --------------------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------------------


def gene(n: int, name: str | None = None, synonyms: list[str] | None = None) -> dict[str, Any]:
    symbol = f"MOCKGENE{n}"
    return {
        "id": f"mock:gene:{n}",
        "type": "Gene",
        "label": symbol,
        "synonyms": synonyms or [],
        "props": {"symbol": symbol, "name": name or f"mock gene {n} name", "backbone_id": f"MOCKENSG{n}"},
        "source": "mock",
        "retrieved_at": TS,
    }


def disease(n: int, label: str, synonyms: list[str] = (), description: str | None = None) -> dict[str, Any]:
    props: dict[str, Any] = {"backbone_id": f"MOCKDIS_{n}"}
    if description is not None:
        props["description"] = description
    return {"id": f"mock:disease:{n}", "type": "Disease", "label": label, "synonyms": list(synonyms), "props": props, "source": "mock", "retrieved_at": TS}


def assoc(d: int, g: int, score: float | None, direct: list[str] = ("MOCK_OT",)) -> dict[str, Any]:
    props: dict[str, Any] = {"sources": {"direct": list(direct), "indirect": []}, "backbone_relation": "ASSOCIATED_WITH"}
    if score is not None:
        props["evidence_score"] = score
    return {"source": f"mock:disease:{d}", "target": f"mock:gene:{g}", "relation": "associated_with", "source_db": "mock", "props": props, "retrieved_at": TS}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "evidence.sqlite") as s:
        s.upsert_nodes(
            [
                gene(1),
                gene(2),
                gene(3),
                gene(4),
                gene(5),
                gene(6, name="mock common gamma chain receptor"),
                # 1: GoF named in a synonym, two candidate genes, no origin text
                disease(1, "Mock immune dysregulation 1", ["MOCKGENE1 gain of function", "Mock IMD-1"]),
                # 2: "<GENE> deficiency" names the gene; description agrees (LoF) and gives the origin
                disease(
                    2,
                    "Mock severe combined immunodeficiency due to MOCKGENE3 deficiency",
                    description="Mock autosomal recessive loss-of-function disorder of the mock pathway.",
                ),
                # 3: deficiency wording, no gene named, exactly one associated gene above threshold
                disease(3, "Mock enzyme deficiency 3"),
                # 4: direction clear, two genes above threshold, none named: ambiguous
                disease(4, "Mock disorder with loss of function 4"),
                # 5: direction clear, the only associated gene is below threshold and not DisGeNET
                disease(5, "Mock hypomorph disorder 5"),
                # 6: somatic GoF from "acquired"/"clonal" in the description
                disease(6, "Mock myeloproliferative disorder 6", ["MOCKGENE5 gain of function"], description="An acquired clonal disorder of mock stem cells."),
                # 7: "immunodeficiency" alone must not trigger the deficiency rule
                disease(7, "Mock immunodeficiency 7", description="Mock X-linked disorder."),
                # 8: the phrase before "deficiency" names a gene through its full name
                disease(8, "Mock common gamma chain deficiency", ["Mock SCID-8"], description="Mock X-linked severe combined immunodeficiency."),
                # 9: conflicting directions in the name fields: nothing assigned
                disease(9, "Mock gain of function and loss of function disorder 9"),
                # 10: no associated gene at all (a parent term)
                disease(10, "Mock parent disorder 10"),
                {"id": "mock:variant:1", "type": "Variant", "label": "Mock MOCKGENE1 variant one", "synonyms": [], "props": {"gene": "mock:gene:1"}, "source": "mock", "retrieved_at": TS},
            ]
        )
        s.upsert_edges(
            [
                assoc(1, 1, 0.8),
                assoc(1, 2, 0.5),
                assoc(2, 3, 0.9),
                assoc(2, 1, 0.4),
                assoc(3, 4, 0.7),
                assoc(3, 2, 0.05),  # below threshold: not a candidate for the single-gene fallback
                assoc(4, 1, 0.8),
                assoc(4, 2, 0.8),
                assoc(5, 2, 0.1),
                assoc(6, 5, 0.85),
                assoc(7, 4, 0.6),
                assoc(8, 6, 0.9),
                assoc(8, 3, 0.4),
                assoc(9, 1, 0.9),
            ]
        )
        yield s


SEED_DATE = "2026-10-01"


def seeds(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"thresholds": dict(THRESHOLDS)}
    if rows is not None:
        out["mechanisms"] = {"source": "mock seed file", "transcribed_at": SEED_DATE, "mechanisms": rows}
    return out


def edge_for(result: mech.MechanismResult, disease_id: str, mech_id: str) -> dict[str, Any]:
    hits = [e for e in result.edges if e["source"] == disease_id and e["target"] == mech_id]
    assert len(hits) == 1, f"expected one edge {disease_id} -> {mech_id}, got {hits}"
    return hits[0]


def rows_for(result: mech.MechanismResult, entity_id: str) -> list[str]:
    return [r["query"] for r in result.coverage if r["entity_id"] == entity_id]


# --------------------------------------------------------------------------------------
# the rule table
# --------------------------------------------------------------------------------------


def test_rule_table_ids_and_guards() -> None:
    ids = [r.id for r in mech.RULES]
    assert ids == ["R1.gof", "R1.lof", "R1.lof.deficiency", "R1.dn", "R2.somatic", "R2.germline"]
    by_id = {r.id: r for r in mech.RULES}
    assert by_id["R1.gof"].pattern.search("a Gain-of-Function mutation")
    assert by_id["R1.gof"].pattern.search("mock GOF disease")
    assert by_id["R1.gof"].pattern.search("constitutively active mock kinase")
    assert not by_id["R1.gof"].pattern.search("an inactivating mutation"), "'inactivating mutation' is not an activating one"
    assert by_id["R1.lof"].pattern.search("mock LOF") and by_id["R1.lof"].pattern.search("hypomorphic allele")
    assert by_id["R1.lof.deficiency"].pattern.search("MOCKGENE3 deficiency") and by_id["R1.lof.deficiency"].pattern.search("mock deficient mice")
    assert not by_id["R1.lof.deficiency"].pattern.search("immunodeficiency 31C"), "'immunodeficiency' is a disease name, not a LoF statement"
    assert by_id["R1.dn"].pattern.search("dominant-negative effect")
    assert by_id["R2.somatic"].pattern.search("acquired") and by_id["R2.somatic"].pattern.search("a clonal disorder")
    assert not by_id["R2.somatic"].pattern.search("monoclonal antibody therapy")
    assert by_id["R2.germline"].pattern.search("autosomal recessive") and by_id["R2.germline"].pattern.search("X-linked")
    assert by_id["R2.germline"].pattern.search("congenital") and not by_id["R2.germline"].pattern.search("mock autosomal trait")


def test_association_threshold() -> None:
    assert mech.association_passes({"evidence_score": 0.3}, THRESHOLDS)
    assert not mech.association_passes({"evidence_score": 0.29}, THRESHOLDS)
    assert mech.association_passes({"evidence_score": 0.01, "sources": {"direct": ["DISGENET"]}}, THRESHOLDS)
    assert not mech.association_passes({"evidence_score": 0.01, "sources": {"direct": ["DISGENET"]}}, {**THRESHOLDS, "keep_disgenet_direct": False})
    assert mech.association_passes({}, THRESHOLDS), "an edge with no score comes from a source that asserts the pair directly"


# --------------------------------------------------------------------------------------
# R1 / R2
# --------------------------------------------------------------------------------------


def test_r1_gof_from_synonym_names_the_gene(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    e = edge_for(r, "mock:disease:1", "mech:MOCKGENE1:GoF")
    assert e["relation"] == "has_mechanism" and e["evidence_class"] == "observed" and e["status"] == "supported"
    assert e["confidence"] == 0.6, "one field group matched (synonyms only)"
    b = e["confidence_basis"]
    assert b["rule"] == "R1.gof" and b["field"] == ["synonyms"] and b["matched_text"] == ["gain of function"]
    assert b["gene_resolution"]["how"] == "named_in_label_or_synonyms" and b["gene_resolution"]["gene_symbol"] == "MOCKGENE1"
    assert b["origin_rule"] is None and "origin" not in e["attributes"]
    assert e["sources"] == [{"kind": "db", "id": "MOCKDIS_1", "date": TS}]
    assert e["source_db"] == mech.RULE_SOURCE
    # origin unknown is a visible gap
    assert any("origin unknown" in q for q in rows_for(r, "mock:disease:1"))
    node = next(n for n in r.nodes if n["id"] == "mech:MOCKGENE1:GoF")
    assert node["type"] == "Mechanism" and node["label"] == "MOCKGENE1 gain of function"
    assert node["props"] == {"gene_symbol": "MOCKGENE1", "direction": "GoF", "gene": "mock:gene:1"}


def test_r1_lof_deficiency_names_the_gene_and_description_raises_confidence(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    e = edge_for(r, "mock:disease:2", "mech:MOCKGENE3:LoF")
    b = e["confidence_basis"]
    assert e["confidence"] == 0.7, "direction matched in both the label and the description"
    assert b["rule"] == "R1.lof+R1.lof.deficiency" and sorted(b["field"]) == ["description", "label"]
    assert b["gene_resolution"] == {"how": "deficient_entity", "phrase": "MOCKGENE3", "gene_name": "MOCKGENE3", "field": "label", "gene_symbol": "MOCKGENE3", "evidence_score": 0.9}
    assert e["attributes"] == {"origin": "germline"}
    assert b["origin_rule"] == "R2.germline" and b["origin_matched_text"] == ["autosomal recessive"] and b["origin_field"] == ["description"]
    assert r.origins[("mock:disease:2", "mock:gene:3")] == "germline"
    assert ("mock:disease:2", "mock:gene:1") not in r.origins
    assert rows_for(r, "mock:disease:2") == []


def test_r1_lof_single_candidate_when_no_gene_is_named(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    e = edge_for(r, "mock:disease:3", "mech:MOCKGENE4:LoF")
    res = e["confidence_basis"]["gene_resolution"]
    assert res["how"] == "single_candidate_above_threshold" and res["gene_symbol"] == "MOCKGENE4" and res["evidence_score"] == 0.7


def test_ambiguous_gene_gives_no_edge_and_a_coverage_row(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    assert not [e for e in r.edges if e["source"] == "mock:disease:4"]
    rows = rows_for(r, "mock:disease:4")
    assert len(rows) == 1 and "ambiguous gene" in rows[0] and "MOCKGENE1" in rows[0] and "MOCKGENE2" in rows[0]
    row = next(c for c in r.coverage if c["entity_id"] == "mock:disease:4")
    assert row == {"entity_id": "mock:disease:4", "source": "mechanism rules", "query": rows[0], "date": "2026-10-03", "hits": 0}


def test_below_threshold_gene_is_not_a_fallback_candidate(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    assert not [e for e in r.edges if e["source"] == "mock:disease:5"]
    assert any("no associated gene passes the threshold" in q for q in rows_for(r, "mock:disease:5"))


def test_r2_somatic_from_description(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    e = edge_for(r, "mock:disease:6", "mech:MOCKGENE5:GoF:somatic")
    assert e["attributes"] == {"origin": "somatic"}
    b = e["confidence_basis"]
    assert b["origin_rule"] == "R2.somatic" and sorted(b["origin_matched_text"]) == ["acquired", "clonal"]
    node = next(n for n in r.nodes if n["id"] == "mech:MOCKGENE5:GoF:somatic")
    assert node["label"] == "MOCKGENE5 somatic gain of function" and node["props"]["origin"] == "somatic"
    assert r.origins[("mock:disease:6", "mock:gene:5")] == "somatic"


def test_immunodeficiency_alone_is_not_a_direction(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    assert not [e for e in r.edges if e["source"] == "mock:disease:7"]
    assert rows_for(r, "mock:disease:7") == ["mechanism: no direction rule matched for Mock immunodeficiency 7"]


def test_deficiency_phrase_matches_a_gene_name(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    e = edge_for(r, "mock:disease:8", "mech:MOCKGENE6:LoF")
    res = e["confidence_basis"]["gene_resolution"]
    assert res["how"] == "deficient_entity" and res["phrase"] == "common gamma chain", "at most three words before 'deficiency'"
    assert res["gene_name"] == "mock common gamma chain receptor"
    assert e["attributes"] == {"origin": "germline"}, "X-linked in the description"


def test_conflicting_directions_assign_nothing(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    assert not [e for e in r.edges if e["source"] == "mock:disease:9"]
    rows = rows_for(r, "mock:disease:9")
    assert len(rows) == 1 and "conflicting directions" in rows[0] and "GoF" in rows[0] and "LoF" in rows[0]


def test_disease_without_associated_gene_is_a_gap(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    assert rows_for(r, "mock:disease:10") == ["mechanism: no associated gene in the store for Mock parent disorder 10"]


def test_report_and_tuple_shape(store: Store) -> None:
    r = mech.derive(store, seeds(), now=NOW)
    assert r.report["diseases_considered"] == 10 and r.report["diseases_with_associated_genes"] == 9
    assert r.report["assignments"] == 5
    assert r.report["by_rule"] == {"R1.gof": 2, "R1.lof+R1.lof.deficiency": 1, "R1.lof.deficiency": 2}
    assert r.report["mechanism_nodes"] == 5 and r.report["mechanism_edges"] == 5 and r.report["curated_edges"] == 0
    nodes, edges, coverage, origins = mech.derive_mechanisms(store, seeds(), now=NOW)
    assert [n["id"] for n in nodes] == sorted(n["id"] for n in r.nodes)
    assert edges == r.edges and coverage == r.coverage and origins == r.origins
    assert all(set(e) >= {"source", "target", "relation", "source_db", "evidence_class", "confidence", "confidence_basis", "status", "sources", "supporting", "contradicting", "attributes"} for e in edges)
    assert all("id" not in e for e in edges)


# --------------------------------------------------------------------------------------
# R4: curated fallback
# --------------------------------------------------------------------------------------


def test_r4_curated_fallback_fires_only_where_r1_found_nothing(store: Store) -> None:
    rows = [
        # R1 already decided (disease 1, MOCKGENE1) with the SAME direction: nothing added, nothing recorded
        {"gene": "MOCKGENE1", "direction": "GoF", "origin": "germline", "disease_terms": ["Mock immune dysregulation 1"], "cluster_doc_confidence": "Retrieved", "note": "mock"},
        # R1 gave disease 2 / MOCKGENE3 LoF: a curated DN row for the same pair is NOT applied and the split is recorded
        {"gene": "MOCKGENE3", "direction": "DN", "origin": "germline", "disease_terms": ["Mock severe combined immunodeficiency due to MOCKGENE3 deficiency"], "cluster_doc_confidence": "Background", "note": "mock"},
        # disease 4 was ambiguous for R1: the curated row for MOCKGENE2 applies; the term carries its own provenance
        {
            "gene": "MOCKGENE2",
            "direction": "DN",
            "origin": "germline",
            "disease_terms": [{"term": "mock disorder with loss of function 4", "from": "mock backbone synonym seen in a mock spike"}],
            "cluster_doc_confidence": "Background",
            "note": "mock",
        },
        # matched by synonym; MOCKGENE5 is not associated with disease 1 in the store: skipped, recorded
        {"gene": "MOCKGENE5", "direction": "GoF", "origin": "somatic", "disease_terms": ["Mock IMD-1"], "cluster_doc_confidence": "Background", "note": "mock"},
        # a term that matches no disease node
        {"gene": "MOCKGENE4", "direction": "GoF", "origin": "germline", "disease_terms": ["Mock nonexistent disorder"], "cluster_doc_confidence": "Background", "note": "mock"},
        # a gene that is not in the store
        {"gene": "MOCKGENE99", "direction": "GoF", "origin": "germline", "disease_terms": ["Mock parent disorder 10"], "cluster_doc_confidence": "Background", "note": "mock"},
    ]
    r = mech.derive(store, seeds(rows), now=NOW)

    assert [e for e in r.edges if e["source"] == "mock:disease:1"] == [edge_for(r, "mock:disease:1", "mech:MOCKGENE1:GoF")]
    assert edge_for(r, "mock:disease:1", "mech:MOCKGENE1:GoF")["evidence_class"] == "observed", "R1 result untouched by the agreeing curated row"
    assert not [e for e in r.edges if e["source"] == "mock:disease:2" and e["target"] == "mech:MOCKGENE3:DN"]
    assert rows_for(r, "mock:disease:2") == ["mechanism: curated MOCKGENE3 DN not applied, R1.lof+R1.lof.deficiency gave LoF for Mock severe combined immunodeficiency due to MOCKGENE3 deficiency"]

    e = edge_for(r, "mock:disease:4", "mech:MOCKGENE2:DN")
    assert e["evidence_class"] == "inferred" and e["confidence"] == 0.4 and e["status"] == "supported"
    assert e["attributes"] == {"labels": ["curated_unverified"], "reason": "curated seed from cluster doc (Background)", "origin": "germline"}
    b = e["confidence_basis"]
    assert b["rule"] == "R4.curated" and b["seed_source"] == "mock seed file" and b["disease_term"] == "mock disorder with loss of function 4" and b["matched_field"] == "label"
    assert b["disease_term_from"] == "mock backbone synonym seen in a mock spike"
    assert b["seed_row"]["gene"] == "MOCKGENE2" and b["seed_row"]["cluster_doc_confidence"] == "Background"
    # the seed file is the asserting source (dated by its transcription), the disease record only supplied the name match
    assert e["sources"] == [{"kind": "db", "id": "mock seed file MOCKGENE2:DN", "date": SEED_DATE}, {"kind": "db", "id": "MOCKDIS_4", "date": TS}]
    # hard rule 10: a curated (Background) origin never propagates onto the observed disease-gene edge
    assert ("mock:disease:4", "mock:gene:2") not in r.origins
    node = next(n for n in r.nodes if n["id"] == "mech:MOCKGENE2:DN")
    assert node["label"] == "MOCKGENE2 dominant negative" and node["props"]["origin"] == "germline"

    assert any("store does not associate MOCKGENE5" in q for q in rows_for(r, "mock:disease:1"))
    assert rows_for(r, "seed:mechanism:MOCKGENE4:GoF") == ["mechanism: curated disease term 'Mock nonexistent disorder' matches no Disease node"]
    assert rows_for(r, "seed:mechanism:MOCKGENE99:GoF") == ["mechanism: curated gene MOCKGENE99 is not a Gene node in the store"]
    assert r.report["curated_edges"] == 1 and r.report["by_rule"]["R4.curated"] == 1
    assert r.report["seed_term_resolution"]["seed:mechanism:MOCKGENE2:DN"] == {"mock disorder with loss of function 4": ["mock:disease:4"]}
    # the curated row's origin does not leak into the propagation table, R1/R2 origins do
    assert r.origins == {("mock:disease:2", "mock:gene:3"): "germline", ("mock:disease:6", "mock:gene:5"): "somatic", ("mock:disease:8", "mock:gene:6"): "germline"}


def test_r4_without_transcribed_at_dates_the_seed_source_with_the_build_day(store: Store) -> None:
    row = {"gene": "MOCKGENE2", "direction": "DN", "origin": "germline", "disease_terms": ["mock disorder with loss of function 4"], "cluster_doc_confidence": "Background", "note": "mock"}
    r = mech.derive(store, {"thresholds": dict(THRESHOLDS), "mechanisms": [row]}, now=NOW)  # bare row list, no source / date
    e = edge_for(r, "mock:disease:4", "mech:MOCKGENE2:DN")
    assert e["sources"][0] == {"kind": "db", "id": "curated mechanism seeds MOCKGENE2:DN", "date": NOW[:10]}
    assert "disease_term_from" not in e["confidence_basis"]


def test_coverage_rows_are_dated_by_the_disease_text_not_the_build(tmp_path: Path) -> None:
    with Store(tmp_path / "evidence.sqlite") as s:
        s.upsert_nodes([gene(1), {**disease(1, "Mock disorder with loss of function 1"), "retrieved_at": "2026-09-15T08:00:00+00:00"}, gene(2)])
        s.upsert_edges([assoc(1, 1, 0.8), assoc(1, 2, 0.8)])
        r = mech.derive(s, seeds([{"gene": "MOCKGENE9", "direction": "GoF", "origin": "germline", "disease_terms": ["nothing"], "cluster_doc_confidence": "Background", "note": "mock"}]), now="2026-12-24T10:00:00+00:00")
    by_entity = {c["entity_id"]: c["date"] for c in r.coverage}
    assert by_entity["mock:disease:1"] == "2026-09-15", "disease-level gap rows carry the retrieval date of the text searched"
    assert by_entity["seed:mechanism:MOCKGENE9:GoF"] == "2026-12-24", "seed-key rows have no node and carry the build day"


def test_r4_without_seed_rows_is_skipped(store: Store) -> None:
    r = mech.derive(store, seeds(None), now=NOW)
    assert r.report["curated_edges"] == 0 and "R4.curated" not in r.report["by_rule"]
    assert not any("curated" in q for c in r.coverage for q in [c["query"]])


# --------------------------------------------------------------------------------------
# R3: literature claims
# --------------------------------------------------------------------------------------


def _bundle(claims: list[dict[str, Any]]) -> ClaimsBundle:
    abstract = "Mock sentence A about the mock mechanism. Mock sentence B about the mock variant. Mock sentence C against it."
    return ClaimsBundle.model_validate(
        {
            "extracted_at": "2026-10-03T11:00:00+00:00",
            "model": "mock-model",
            "papers": [{"id": "mock:paper:1", "type": "Paper", "label": "Mock paper", "synonyms": [], "props": {"abstract": abstract, "year": 2026}}],
            "claims": claims,
        }
    )


def _claim(cid: str, subject: str, obj: str, polarity: str, quote: str, study_type: str, n: int | None, variant: str = "") -> dict[str, Any]:
    return {"id": cid, "pmid": "", "paper": "mock:paper:1", "quote": quote, "polarity": polarity, "subject": subject, "predicate": "has_mechanism", "object": obj, "variant": variant, "study_type": study_type, "n_patients": n}


def test_r3_claims_add_and_upgrade_edges(store: Store) -> None:
    bundle = _bundle(
        [
            # a new disease -> mechanism edge from the literature (disease 7 had no R1 result)
            _claim("c_000001", "mock:disease:7", "mech:MOCKGENE4:LoF", "supports", "Mock sentence A about the mock mechanism.", "case_series", 3),
            # supporting + contradicting on the R1 edge of disease 1: contested
            _claim("c_000002", "mock:disease:1", "mech:MOCKGENE1:GoF", "supports", "Mock sentence A about the mock mechanism.", "cohort", 25),
            _claim("c_000003", "mock:disease:1", "mech:MOCKGENE1:GoF", "contradicts", "Mock sentence C against it.", "case_report", 1),
            # variant -> mechanism, variant-specific
            _claim("c_000004", "mock:variant:1", "mech:MOCKGENE1:GoF", "supports", "Mock sentence B about the mock variant.", "in_vitro", None, variant="mock:variant:1"),
            # object is not a mechanism id / subject is not a node: unlinked
            _claim("c_000005", "mock:disease:1", "text:some mechanism", "supports", "Mock sentence A about the mock mechanism.", "review", None),
            _claim("c_000006", "text:unknown disease", "mech:MOCKGENE1:GoF", "supports", "Mock sentence A about the mock mechanism.", "review", None),
            # only contradicted and no database assertion: no edge to hang it on
            _claim("c_000007", "mock:disease:3", "mech:MOCKGENE2:GoF", "contradicts", "Mock sentence C against it.", "case_report", 1),
        ]
    )
    r = mech.derive(store, seeds(), bundle, now=NOW)

    new = edge_for(r, "mock:disease:7", "mech:MOCKGENE4:LoF")
    assert new["evidence_class"] == "extracted" and new["source_db"] == mech.CLAIMS_SOURCE
    assert new["confidence"] == 0.6 and new["status"] == "small_n"
    assert new["supporting"] == ["c_000001"] and new["contradicting"] == []
    assert new["attributes"] == {"labels": ["promising", "small_n", "preprint"], "preprint": True}
    assert new["sources"] == [{"kind": "db", "id": "mock:paper:1", "date": "2026"}]
    assert new["confidence_basis"]["rule"] == "R3.claims" and new["confidence_basis"]["claims"][0]["study_type"] == "case_series"

    upgraded = edge_for(r, "mock:disease:1", "mech:MOCKGENE1:GoF")
    assert upgraded["evidence_class"] == "extracted" and upgraded["status"] == "contested"
    assert upgraded["confidence"] == 0.8, "max(R1 0.6, cohort 0.8)"
    assert upgraded["supporting"] == ["c_000002"] and upgraded["contradicting"] == ["c_000003"]
    assert upgraded["confidence_basis"]["db_text"]["rule"] == "R1.gof"
    assert upgraded["attributes"]["labels"] == ["promising", "contested", "preprint"] and upgraded["attributes"]["preprint"] is True
    assert upgraded["sources"][0]["kind"] == "db" and upgraded["sources"][0]["id"] == "MOCKDIS_1"

    variant = edge_for(r, "mock:variant:1", "mech:MOCKGENE1:GoF")
    assert variant["evidence_class"] == "extracted" and variant["confidence"] == 0.3 and variant["status"] == "supported"
    assert "variant_specific" in variant["attributes"]["labels"] and "origin" not in variant["attributes"]

    assert not [e for e in r.edges if e["source"] == "mock:disease:3" and e["target"] == "mech:MOCKGENE2:GoF"]
    assert r.report["claims_unlinked"] == ["c_000005", "c_000006", "c_000007"]
    assert r.report["claim_edges"] == 3 and r.report["by_rule"]["R3.claims"] == 3
    # disease 7 now has a mechanism node even though no text rule fired
    assert any(n["id"] == "mech:MOCKGENE4:LoF" for n in r.nodes)


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def test_resolve_terms_is_case_insensitive_and_prefers_labels(store: Store) -> None:
    out = mech.resolve_terms(store.iter_nodes("Disease"), ["MOCK IMD-1", "mock immune dysregulation 1", "nothing here"])
    assert out["MOCK IMD-1"] == [("mock:disease:1", "synonyms")]
    assert out["mock immune dysregulation 1"] == [("mock:disease:1", "label")]
    assert out["nothing here"] == []


def test_word_pattern_is_bounded_and_separator_flexible() -> None:
    pat = mech._word_pattern("Mock kinase 3")
    assert pat.search("mock kinase-3 deficiency") and pat.search("MOCK KINASE 3") and pat.search("a Mock-kinase 3 disorder")
    assert not pat.search("mock kinase 31") and not pat.search("mock kinase 3b")
    symbol = mech._word_pattern("MOCKGENE1")
    assert symbol.search("the MOCKGENE1 gene") and not symbol.search("MOCKGENE10") and not symbol.search("xMOCKGENE1")
    assert mech._word_pattern("T-B+ mock").search("a T B+ mock disorder")


def test_mechanism_label() -> None:
    assert mech.mechanism_label("MOCKGENE1", "GoF", False) == "MOCKGENE1 gain of function"
    assert mech.mechanism_label("MOCKGENE1", "LoF", False) == "MOCKGENE1 loss of function"
    assert mech.mechanism_label("MOCKGENE1", "DN", False) == "MOCKGENE1 dominant negative"
    assert mech.mechanism_label("MOCKGENE1", "GoF", True) == "MOCKGENE1 somatic gain of function"
