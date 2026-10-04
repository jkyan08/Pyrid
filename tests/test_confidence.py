"""pipeline/graph/confidence.py: the README section 6 rubric at its boundaries. Pure functions,
synthetic inputs, no store and no network."""

from __future__ import annotations

import pytest

from pipeline.graph import confidence as c


def _approx(value: float) -> float:
    return pytest.approx(value, abs=1e-9)


# --------------------------------------------------------------------------------------
# disease_gene
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0.1, 0.0), (0.9, 1.0), (0.5, 0.5), (0.0, 0.0), (0.95, 1.0)],
    ids=["at-min", "at-max", "mid", "below-min-clipped", "above-max-clipped"],
)
def test_disease_gene_normalises_to_the_observed_range(score: float, expected: float) -> None:
    conf, basis = c.disease_gene({"evidence_score": score}, 0.1, 0.9)
    assert conf == _approx(expected)
    assert basis["evidence_score"] == score and basis["score_min"] == 0.1 and basis["score_max"] == 0.9
    assert basis["base"] == _approx(expected) and basis["bonuses"] == []


def test_disease_gene_bonuses_and_cap() -> None:
    props = {"evidence_score": 0.5, "sources": {"direct": ["MOCK_A", "MOCK_B"]}, "number_of_pmids": 5}
    conf, basis = c.disease_gene(props, 0.0, 1.0)
    assert conf == _approx(0.7)
    assert basis["bonuses"] == ["sources.direct>=2", "number_of_pmids>=5"]
    assert basis["sources_direct"] == ["MOCK_A", "MOCK_B"] and basis["number_of_pmids"] == 5

    # one direct source, 4 pmids: no bonus
    conf, basis = c.disease_gene({"evidence_score": 0.5, "sources": {"direct": ["MOCK_A"]}, "number_of_pmids": 4}, 0.0, 1.0)
    assert conf == _approx(0.5) and basis["bonuses"] == []

    # at the max with both bonuses: capped at 1.0
    conf, _ = c.disease_gene({"evidence_score": 1.0, "sources": {"direct": ["A", "B"]}, "number_of_pmids": 50}, 0.0, 1.0)
    assert conf == 1.0

    # pmids None never counts
    conf, basis = c.disease_gene({"evidence_score": 0.5, "number_of_pmids": None}, 0.0, 1.0)
    assert conf == _approx(0.5) and basis["number_of_pmids"] is None


def test_disease_gene_missing_score_and_degenerate_range() -> None:
    conf, basis = c.disease_gene({}, 0.1, 0.9)
    assert conf == 0.0 and basis["evidence_score"] is None
    assert c.disease_gene({"evidence_score": 0.4}, 0.4, 0.4)[0] == 1.0
    assert c.disease_gene({"evidence_score": 0.3}, 0.4, 0.4)[0] == 0.0
    # sources given as a plain list still count as direct sources
    assert c.disease_gene({"evidence_score": 0.0, "sources": ["A", "B"]}, 0.0, 1.0)[0] == _approx(0.1)


# --------------------------------------------------------------------------------------
# disease_phenotype, ontology, GO, Reactome, drugs
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("evidence_type", "expected", "best"),
    [
        (["PCS"], 0.9, "PCS"),
        ("TAS", 0.75, "TAS"),
        (["IEA"], 0.4, "IEA"),
        (None, 0.5, None),
        (["MOCKCODE"], 0.5, "MOCKCODE"),
        (["IEA", "PCS"], 0.9, "PCS"),
        (["iea", "tas"], 0.75, "TAS"),
    ],
)
def test_disease_phenotype_by_evidence_type(evidence_type, expected: float, best) -> None:
    props = {"evidence_type": evidence_type, "frequency": "mock 3/4", "onset": ["mock onset"], "references": ["mock:ref"]}
    conf, basis = c.disease_phenotype(props)
    assert conf == _approx(expected)
    assert basis["best_code"] == best
    assert basis["frequency"] == "mock 3/4" and basis["onset"] == ["mock onset"] and basis["references"] == ["mock:ref"]


def test_ontology_and_reactome_constants() -> None:
    assert c.ontology_is_a() == (0.95, {"rule": "ontology_is_a"})
    assert c.reactome_pathway() == (0.8, {"rule": "reactome_pathway"})


@pytest.mark.parametrize(
    ("codes", "expected", "cls"),
    [
        (["EXP"], 0.9, "experimental"),
        (["IDA"], 0.9, "experimental"),
        (["HEP"], 0.9, "experimental"),
        (["TAS"], 0.6, "author_statement"),
        (["NAS"], 0.6, "author_statement"),
        (["ISS"], 0.5, "computational"),
        (["IBA"], 0.5, "computational"),
        (["RCA"], 0.5, "computational"),
        (["IEA"], 0.3, "electronic"),
        (["ND"], 0.1, "no_data"),
        (["MOCKX"], 0.3, "unknown"),
        ([], 0.3, "unknown"),
        (None, 0.3, "unknown"),
        (["IEA", "ISS", "IMP"], 0.9, "experimental"),
        ("tas", 0.6, "author_statement"),
    ],
)
def test_go_annotation_best_code_wins(codes, expected: float, cls: str) -> None:
    conf, basis = c.go_annotation(codes)
    assert conf == _approx(expected)
    assert basis["class"] == cls and basis["rule"] == "go_annotation"


def test_drug_edge_keeps_raw_action() -> None:
    props = {"backbone_relation": "INHIBITOR", "mechanisms_of_action": ["Mock kinase inhibitor"], "sources": {"direct": ["MOCK_DB"]}}
    conf, basis = c.drug_edge(props)
    assert conf == 0.7
    assert basis["backbone_relation"] == "INHIBITOR" and basis["mechanisms_of_action"] == ["Mock kinase inhibitor"]
    assert basis["sources_direct"] == ["MOCK_DB"]


# --------------------------------------------------------------------------------------
# relation conflict, claims, inferred cap, status
# --------------------------------------------------------------------------------------


def test_apply_relation_conflict_downgrades_and_keeps_assertions() -> None:
    basis = {"rule": "drug_edge"}
    assert c.apply_relation_conflict(0.7, basis, {"relation_conflict": False}) == (0.7, basis)
    assert not c.has_relation_conflict({"relation_conflict": False}) and not c.has_relation_conflict({})
    assertions = [{"source": "MOCK_A", "relation": "INHIBITOR"}, {"source": "MOCK_B", "relation": "AGONIST"}]
    conf, out = c.apply_relation_conflict(0.7, basis, {"relation_conflict": True, "relation_assertions": assertions})
    assert conf == _approx(0.42)
    assert out["relation_conflict"] == assertions and out["relation_conflict_factor"] == 0.6
    assert "relation_conflict" not in basis, "the input basis is not mutated"
    assert c.has_relation_conflict({"relation_conflict": True})


@pytest.mark.parametrize(
    ("study_type", "expected"),
    [("trial", 1.0), ("cohort", 0.8), ("case_series", 0.6), ("case_report", 0.4), ("in_vitro", 0.3), ("review", 0.5), ("mock_type", 0.3), (None, 0.3)],
)
def test_claim_confidence(study_type, expected: float) -> None:
    conf, basis = c.claim_confidence(study_type, 7)
    assert conf == _approx(expected)
    assert basis["n_patients"] == 7 and basis["study_type"] == study_type


def test_in_vitro_only_rule() -> None:
    vitro = {"id": "c_1", "polarity": "supports", "study_type": "in_vitro", "n_patients": None}
    review = {"id": "c_2", "polarity": "supports", "study_type": "review", "n_patients": None}
    case = {"id": "c_3", "polarity": "supports", "study_type": "case_report", "n_patients": 1}
    assert c.in_vitro_only([vitro, review]) and not c.in_vitro_only([vitro, case]) and not c.in_vitro_only([])
    edge = {"relation": "has_mechanism", "evidence_class": "extracted", "confidence": 0.75, "confidence_basis": {"rule": "x"}, "attributes": {"labels": ["promising"]}}
    assert c.apply_in_vitro_only(edge, [vitro, review]) is True
    assert edge["confidence"] == 0.5 and edge["attributes"]["labels"] == ["promising", c.IN_VITRO_ONLY_LABEL]
    assert edge["confidence_basis"]["in_vitro_only"] == {"study_types": ["in_vitro", "review"], "cap": c.IN_VITRO_ONLY_CAP, "uncapped_confidence": 0.75}
    # recomputed from scratch: a patient-level claim removes the label and the basis entry (the cap is not undone here; callers recompute the confidence first)
    assert c.apply_in_vitro_only(edge, [vitro, case]) is False
    assert edge["attributes"]["labels"] == ["promising"] and "in_vitro_only" not in edge["confidence_basis"]
    for untouched in ({"relation": "treats", "evidence_class": "extracted"}, {"relation": "has_mechanism", "evidence_class": "observed"}, {"relation": "has_mechanism", "evidence_class": "inferred"}):
        e = {**untouched, "confidence": 0.9, "confidence_basis": {}, "attributes": {}}
        assert c.apply_in_vitro_only(e, [vitro]) is False and e["confidence"] == 0.9 and e["attributes"] == {}


def test_cap_inferred() -> None:
    assert c.cap_inferred(0.9) == 0.5
    assert c.cap_inferred(0.5) == 0.5
    assert c.cap_inferred(0.3) == _approx(0.3)
    assert c.INFERRED_CAP == 0.5


def _claim(polarity: str, study_type: str, n: int | None, pmid: str = "mock") -> dict:
    return {"polarity": polarity, "study_type": study_type, "n_patients": n, "pmid": pmid}


def test_status_from_claims_conventions() -> None:
    # both polarities, every patient-level study < 10: contested dominates, small_n in labels
    status, labels, basis = c.status_from_claims([_claim("supports", "case_report", 1)], [_claim("contradicts", "case_series", 2)])
    assert status == "contested" and labels == ["promising", "contested", "small_n"]
    assert basis["n_supporting"] == 1 and basis["n_contradicting"] == 1 and basis["patient_level_n"] == [1, 2]

    # supporting only, small: small_n status
    status, labels, _ = c.status_from_claims([_claim("supports", "case_series", 9)], [])
    assert status == "small_n" and labels == ["promising", "small_n"]

    # a 10-patient study is not small-n
    status, labels, _ = c.status_from_claims([_claim("supports", "cohort", 10)], [])
    assert status == "supported" and labels == ["promising"]

    # in_vitro and review carry no patients and never count toward n; no patient-level study => no small_n
    status, labels, _ = c.status_from_claims([_claim("supports", "in_vitro", None), _claim("supports", "review", None)], [])
    assert status == "supported" and labels == []

    # a patient-level study with unknown n neither grants nor blocks small_n (design D): alone it
    # gives no small_n (no known n), next to small studies it is ignored
    status, labels, basis = c.status_from_claims([_claim("supports", "case_report", None)], [])
    assert status == "supported" and labels == ["promising"] and basis["patient_level_without_n"] == 1
    status, labels, basis = c.status_from_claims([_claim("supports", "case_report", 1), _claim("supports", "cohort", None)], [_claim("contradicts", "case_series", 2)])
    assert status == "contested" and labels == ["promising", "contested", "small_n"] and basis["patient_level_n"] == [1, 2]

    # only a claim whose paper is a doi: node (the contract's preprint spelling) marks the edge
    # preprint; a pmid-less claim from any other paper (a mock) does not (shared design D)
    _, labels, _ = c.status_from_claims([_claim("supports", "cohort", 40, pmid="")], [])
    assert labels == ["promising"]
    _, labels, _ = c.status_from_claims([{**_claim("supports", "cohort", 40, pmid=""), "paper": f"{c.PREPRINT_PAPER_PREFIX}10.0000/mock"}], [])
    assert labels == ["promising", "preprint"]

    # contradicting only: not contested, nothing promising; the one study behind the edge is small
    status, labels, _ = c.status_from_claims([], [_claim("contradicts", "case_report", 1)])
    assert status == "small_n" and labels == ["small_n"]
    status, labels, _ = c.status_from_claims([], [_claim("contradicts", "cohort", 30)])
    assert status == "supported" and labels == []


# --------------------------------------------------------------------------------------
# loader layers and claim aggregation (design item D)
# --------------------------------------------------------------------------------------


def test_variant_of_and_study_and_community_constants() -> None:
    conf, basis = c.variant_of({"gene_symbol": "MOCKGENE1", "record_id": "1", "match": {"method": "props.symbol", "matched_text": "MOCKGENE1"}})
    assert conf == 0.95 and basis["rule"] == "variant_of" and basis["gene_symbol"] == "MOCKGENE1"
    conf, basis = c.study_edge({"record_id": "NCT00000000", "phases": ["PHASE2"], "enrollment": 12, "match": {"method": "condition", "matched_text": "x"}})
    assert conf == 0.8 and basis["rule"] == "study_edge" and basis["phases"] == ["PHASE2"] and basis["enrollment"] == 12
    conf, basis = c.community_edge("Mock disease", "https://mock.invalid/", "2026-10-03")
    assert conf == 0.8 and basis == {"rule": "community_edge", "matched_term": "Mock disease", "url": "https://mock.invalid/", "retrieved_at": "2026-10-03"}


@pytest.mark.parametrize(
    ("status", "expected", "matched"),
    [
        ("practice guideline", 1.0, "practice guideline"),
        ("reviewed by expert panel", 0.9, "reviewed by expert panel"),
        ("criteria provided, multiple submitters, no conflicts", 0.8, "multiple submitters, no conflicts"),
        ("Criteria Provided, Single Submitter", 0.6, "single submitter"),
        ("criteria provided, conflicting classifications", 0.4, "conflicting"),
        ("no assertion criteria provided", 0.3, "no assertion criteria"),
        ("no classification provided", 0.3, "no classification"),
        ("no classification for the single variant", 0.3, "no classification"),
        ("", 0.3, None),
        (None, 0.3, None),
        ("something new", 0.3, None),
    ],
)
def test_clinvar_condition_by_review_status(status, expected: float, matched) -> None:
    conf, basis = c.clinvar_condition({"review_status": status, "classification": "Pathogenic", "n_scv": 3})
    assert conf == _approx(expected)
    assert basis["rule"] == "clinvar_condition" and basis["matched"] == matched and basis["classification"] == "Pathogenic" and basis["n_scv"] == 3


def test_inferred_loader_layers_are_capped() -> None:
    conf, basis = c.text_mention({"record_id": "NCT00000000", "match": {"method": "query_term", "matched_text": "MOCKGENE1"}})
    assert conf == _approx(0.3) and basis["evidence_class"] == "inferred" and basis["rule"] == "text_mention"
    conf, basis = c.funded_project({"method": "gene_symbol_in_title_or_abstract", "matched_text": ["MOCKGENE1"], "n_projects": 2, "fiscal_years": [2025, 2026], "award_amount_total": 10})
    assert conf == _approx(0.4) and basis["matched_text"] == ["MOCKGENE1"] and basis["fiscal_years"] == [2025, 2026] and basis["award_amount_total"] == 10
    assert conf <= c.INFERRED_CAP


def test_edge_confidence_from_claims_formula() -> None:
    sup = [_claim("supports", "cohort", 25), _claim("supports", "case_report", 1), _claim("supports", "in_vitro", None)]
    conf, basis = c.edge_confidence_from_claims(sup, [])
    assert conf == _approx(min(1.0, 0.8 + 0.05 * 2)), "best supporting weight plus 0.05 per extra supporting claim"
    assert basis["n_supporting"] == 3 and basis["best_supporting"] == 0.8 and basis["contradiction_penalty"] == 0 and len(basis["claims"]) == 3
    assert basis["claims"][0]["study_type"] == "cohort" and basis["claims"][0]["n_patients"] == 25

    conf, basis = c.edge_confidence_from_claims([_claim("supports", "trial", 100)], [_claim("contradicts", "case_report", 1)])
    assert conf == _approx(1.0 * (1 - 0.3)) and basis["contradiction_penalty"] == _approx(0.3)
    conf, _ = c.edge_confidence_from_claims([_claim("supports", "cohort", 25)] * 2, [_claim("contradicts", "case_report", 1)])
    assert conf == _approx(0.85 * (1 - 0.15)), "penalty scales with the contradicting share"
    conf, _ = c.edge_confidence_from_claims([_claim("supports", "cohort", 25)], [_claim("contradicts", "case_report", 1)] * 5)
    assert conf == _approx(0.8 * 0.7), "the contradicting share is capped at 1"

    # no supporting claim: nothing in the literature supports the relation
    conf, basis = c.edge_confidence_from_claims([], [_claim("contradicts", "cohort", 30)])
    assert conf == 0.0 and basis["literature_confidence"] == 0.0
    # an observed edge keeps the better of the two, or is downgraded when only contradicted
    conf, basis = c.edge_confidence_from_claims([_claim("supports", "case_report", 1)], [], observed_confidence=0.9)
    assert conf == 0.9 and basis["observed_confidence"] == 0.9
    conf, _ = c.edge_confidence_from_claims([_claim("supports", "trial", 100)], [], observed_confidence=0.6)
    assert conf == 1.0
    conf, basis = c.edge_confidence_from_claims([], [_claim("contradicts", "cohort", 30)], observed_confidence=0.9)
    assert conf == _approx(0.63) and basis["contradicted_fact_factor"] == 0.7
    assert conf <= 1.0


def test_status_from_claims_treats_doi_papers_as_preprints() -> None:
    _, labels, _ = c.status_from_claims([{"polarity": "supports", "study_type": "cohort", "n_patients": 40, "pmid": "", "paper": "doi:10.1101/mock"}], [])
    assert labels == ["promising", "preprint"]
    assert c.CLAIM_LABELS == {"promising", "contested", "small_n", "preprint"}
