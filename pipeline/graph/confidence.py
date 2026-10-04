"""Confidence rubric for observed edges and literature claims (README section 6).

Every scoring function returns ``(confidence, basis)``: the number in [0, 1] and a dict holding
the RAW inputs it was computed from, which the build writes to ``Edge.confidence_basis`` (hard
rule 4: a confidence without its basis is not allowed). Nothing here reads the store or the
network; the build passes the store edge's ``props`` dict in.

Rubric (the numbers are documented, general rules; none depends on a specific gene or disease)
---------------------------------------------------------------------------------------------
* :func:`disease_gene`: base = (``evidence_score`` - min) / (max - min) clipped to [0, 1];
  +0.1 when ``sources.direct`` lists two or more databases; +0.1 when ``number_of_pmids`` >= 5;
  capped at 1.0. ``min``/``max`` are the observed range of the score, supplied by the caller.
* :func:`disease_phenotype`: HPO ``evidence_type`` PCS 0.9, TAS 0.75, IEA 0.4, anything else or
  missing 0.5; the best code wins when several are listed. The basis keeps the annotation's raw
  ``aspect`` (P / I / C / M / H), frequency, onset and references.
* :func:`ontology_is_a`: 0.95 (ontology parent links are curated facts).
* :func:`go_annotation`: best GO evidence code wins: experimental 0.9, author statement 0.6,
  computational 0.5, IEA 0.3, ND 0.1, unknown code or none 0.3.
* :func:`reactome_pathway`: 0.8 (curated pathway membership without a per-edge code).
* :func:`drug_edge`: 0.7 base for backbone drug-target and drug-indication edges.
* :func:`apply_relation_conflict`: when the backbone marks ``relation_conflict`` the confidence
  is multiplied by 0.6 and the raw ``relation_assertions`` are kept in the basis; the caller adds
  ``"conflicting_assertions"`` to ``attributes.labels``.
* :func:`claim_confidence`: trial 1.0, cohort 0.8, case_series 0.6, case_report 0.4,
  in_vitro 0.3, review 0.5 (unknown study type 0.3).
* :func:`aggregate_claim_confidence` (the single definition of the shared design D formula, used
  by :func:`edge_confidence_from_claims`, :mod:`pipeline.graph.evidence` and the build):
  ``min(1, max supporting weight + 0.05 * (n_supporting - 1)) * (1 - 0.3 * min(1, n_contradicting / n_supporting))``;
  with no supporting claim the edge scores 0 (nothing in the literature supports it).
* :func:`edge_confidence_from_claims`: the same over Claim models or dicts (weights from
  :func:`claim_confidence`). An OBSERVED edge that claims attach to keeps
  ``max(observed confidence, claim confidence)``, except that a database fact contradicted by every
  claim is multiplied by 0.7.
* :func:`cap_inferred`: inferred edges never exceed 0.5 (``pipeline.models.Edge`` enforces it).
* :func:`status_from_claims`: the status/label conventions of the README section 6 table (shared
  design D, the single definition for every claim-bearing edge): ``contested`` iff both polarities;
  ``small_n`` decided only by patient-level claims that report an ``n``; ``promising`` with >= 1
  supporting patient-level claim; ``preprint`` when a claim's paper is a ``doi:`` node (the
  contract's spelling for a preprint, README section 4).

Loader layers (ClinVar, ClinicalTrials.gov, NIH RePORTER, patient-group seeds); the raw inputs
are copied from the store edge's ``props`` into the basis:

* :func:`variant_of`: 0.95 (ClinVar asserts which gene a variant lies in; an identity fact).
* :func:`clinvar_condition`: Variant -> Disease from ClinVar's ``review_status``, matched on
  lower-cased substrings: practice guideline 1.0, reviewed by expert panel 0.9, criteria provided
  with multiple submitters and no conflicts 0.8, criteria provided by a single submitter 0.6,
  criteria provided with conflicting classifications 0.4, no assertion criteria / no classification /
  empty 0.3.
* :func:`study_edge`: 0.8 for a ClinicalTrials.gov registry record linking a study to the
  condition it studies or the drug it evaluates (``studies`` / ``evaluates``).
* :func:`text_mention`: 0.3, ``inferred``: a study found only through a gene-symbol query term.
* :func:`funded_project`: 0.4, ``inferred``: a RePORTER grant whose title/abstract names a gene or
  disease (a text match, not an assertion).
* :func:`community_edge`: 0.8 for a verified patient-group / registry seed row naming a disease
  (``supports_community`` / ``covers``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "CLAIM_LABELS",
    "CLAIM_SUPPORT_BONUS",
    "CLINVAR_REVIEW_STATUS",
    "CLINVAR_REVIEW_UNKNOWN",
    "COMMUNITY_EDGE",
    "CONTRADICTION_PENALTY",
    "DRUG_EDGE",
    "FUNDED_PROJECT",
    "GO_CLASSES",
    "GO_UNKNOWN",
    "INFERRED_CAP",
    "INFERRED_LABEL",
    "ONTOLOGY_IS_A",
    "PATIENT_LEVEL_STUDY_TYPES",
    "PHENOTYPE_DEFAULT",
    "PHENOTYPE_EVIDENCE",
    "PREPRINT_PAPER_PREFIX",
    "REACTOME_PATHWAY",
    "RELATION_CONFLICT_FACTOR",
    "RELATION_CONFLICT_LABEL",
    "SMALL_N",
    "STUDY_EDGE",
    "STUDY_TYPE_CONFIDENCE",
    "STUDY_TYPE_UNKNOWN",
    "TEXT_MENTION",
    "VARIANT_OF",
    "aggregate_claim_confidence",
    "apply_relation_conflict",
    "cap_inferred",
    "claim_confidence",
    "clinvar_condition",
    "community_edge",
    "disease_gene",
    "disease_phenotype",
    "drug_edge",
    "edge_confidence_from_claims",
    "funded_project",
    "go_annotation",
    "has_relation_conflict",
    "ontology_is_a",
    "reactome_pathway",
    "sources_direct",
    "status_from_claims",
    "study_edge",
    "text_mention",
    "variant_of",
]

INFERRED_CAP = 0.5
INFERRED_LABEL = "inferred"
RELATION_CONFLICT_FACTOR = 0.6
RELATION_CONFLICT_LABEL = "conflicting_assertions"

# Loader layers (see the module docstring).
VARIANT_OF = 0.95
STUDY_EDGE = 0.8
TEXT_MENTION = 0.3
FUNDED_PROJECT = 0.4
COMMUNITY_EDGE = 0.8
# ClinVar review_status -> confidence; the FIRST entry whose substring occurs in the lower-cased
# status wins, so the order matters ("criteria provided, conflicting" before "criteria provided").
CLINVAR_REVIEW_STATUS: tuple[tuple[str, float], ...] = (
    ("practice guideline", 1.0),
    ("reviewed by expert panel", 0.9),
    ("multiple submitters, no conflicts", 0.8),
    ("conflicting", 0.4),
    ("single submitter", 0.6),
    ("no assertion criteria", 0.3),
    ("no classification", 0.3),
)
CLINVAR_REVIEW_UNKNOWN = 0.3

# Claim aggregation (edge_confidence_from_claims).
CLAIM_SUPPORT_BONUS = 0.05  # per additional supporting claim
CONTRADICTION_PENALTY = 0.3  # times min(1, n_contradicting / n_supporting)
CONTRADICTED_FACT_FACTOR = 0.7  # an observed fact every claim contradicts
# The display labels status_from_claims owns (other labels on an edge are left alone).
CLAIM_LABELS = frozenset({"promising", "contested", "small_n", "preprint"})
# A Paper node id with this prefix is a preprint (README section 4: ``sources[].kind: doi`` for
# preprints, ``Claim.pmid == ""``); the paper id, not the pmid field, decides the label.
PREPRINT_PAPER_PREFIX = "doi:"

PHENOTYPE_EVIDENCE: dict[str, float] = {"PCS": 0.9, "TAS": 0.75, "IEA": 0.4}
PHENOTYPE_DEFAULT = 0.5
ONTOLOGY_IS_A = 0.95
REACTOME_PATHWAY = 0.8
DRUG_EDGE = 0.7

# (class name, codes, confidence); the best class among an edge's codes wins.
GO_CLASSES: tuple[tuple[str, frozenset[str], float], ...] = (
    ("experimental", frozenset({"EXP", "IDA", "IPI", "IMP", "IGI", "IEP", "HTP", "HDA", "HMP", "HGI", "HEP"}), 0.9),
    ("author_statement", frozenset({"TAS", "NAS"}), 0.6),
    ("computational", frozenset({"ISS", "ISO", "ISA", "ISM", "IGC", "IBA", "IBD", "IKR", "IRD", "RCA"}), 0.5),
    ("electronic", frozenset({"IEA"}), 0.3),
    ("no_data", frozenset({"ND"}), 0.1),
)
GO_UNKNOWN: tuple[str, float] = ("unknown", 0.3)

STUDY_TYPE_CONFIDENCE: dict[str, float] = {
    "trial": 1.0,
    "cohort": 0.8,
    "case_series": 0.6,
    "case_report": 0.4,
    "in_vitro": 0.3,
    "review": 0.5,
}
STUDY_TYPE_UNKNOWN = 0.3
# Study types that describe patients; in_vitro and review carry no n and never count toward small-n.
PATIENT_LEVEL_STUDY_TYPES = frozenset({"trial", "cohort", "case_series", "case_report"})
SMALL_N = 10


# --------------------------------------------------------------------------------------
# helpers over raw props
# --------------------------------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, Mapping)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sources_direct(props: Mapping[str, Any]) -> list[str]:
    """``props.sources.direct`` as a list of strings (``sources`` may also be a plain list)."""
    sources = props.get("sources")
    if isinstance(sources, Mapping):
        return [str(s) for s in _as_list(sources.get("direct"))]
    return [str(s) for s in _as_list(sources)]


def has_relation_conflict(props: Mapping[str, Any]) -> bool:
    return bool(props.get("relation_conflict"))


# --------------------------------------------------------------------------------------
# observed edges
# --------------------------------------------------------------------------------------


def disease_gene(props: Mapping[str, Any], score_min: float, score_max: float) -> tuple[float, dict[str, Any]]:
    """OptimusKG disease-gene edge from ``evidence_score`` (normalised to the observed range),
    with bonuses for several direct sources and for ``number_of_pmids`` >= 5."""
    score = _as_float(props.get("evidence_score"))
    span = float(score_max) - float(score_min)
    if score is None:
        base = 0.0
    elif span > 0:
        base = min(1.0, max(0.0, (score - float(score_min)) / span))
    else:
        base = 1.0 if score >= float(score_max) else 0.0
    direct = sources_direct(props)
    pmids = _as_int(props.get("number_of_pmids"))
    bonuses: list[str] = []
    conf = base
    if len(direct) >= 2:
        conf += 0.1
        bonuses.append("sources.direct>=2")
    if pmids is not None and pmids >= 5:
        conf += 0.1
        bonuses.append("number_of_pmids>=5")
    conf = min(1.0, conf)
    basis = {
        "rule": "disease_gene",
        "evidence_score": score,
        "score_min": float(score_min),
        "score_max": float(score_max),
        "base": base,
        "sources_direct": direct,
        "number_of_pmids": pmids,
        "bonuses": bonuses,
    }
    return conf, basis


def disease_phenotype(props: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    """HPO annotation: PCS 0.9, TAS 0.75, IEA 0.4, else 0.5 (best listed code wins)."""
    codes = [str(c).upper() for c in _as_list(props.get("evidence_type"))]
    scored = [(PHENOTYPE_EVIDENCE.get(c, PHENOTYPE_DEFAULT), c) for c in codes]
    if scored:
        conf, best = max(scored)
    else:
        conf, best = PHENOTYPE_DEFAULT, None
    basis = {
        "rule": "disease_phenotype",
        "evidence_type": codes,
        "best_code": best,
        "aspect": _as_list(props.get("aspect")),  # P (phenotypic abnormality) / I / C / M / H, kept raw
        "frequency": props.get("frequency"),
        "onset": props.get("onset"),
        "references": _as_list(props.get("references")),
    }
    return conf, basis


def ontology_is_a() -> tuple[float, dict[str, Any]]:
    """Ontology parent link (Phenotype or Disease ``is_a``)."""
    return ONTOLOGY_IS_A, {"rule": "ontology_is_a"}


def go_annotation(codes: Iterable[str] | str | None) -> tuple[float, dict[str, Any]]:
    """GO annotation by evidence code class; the best code among ``codes`` wins."""
    listed = [str(c).upper() for c in _as_list(codes)]
    best_conf, best_class, best_code = -1.0, GO_UNKNOWN[0], None
    for code in listed:
        cls, conf = GO_UNKNOWN
        for name, members, value in GO_CLASSES:
            if code in members:
                cls, conf = name, value
                break
        if conf > best_conf:
            best_conf, best_class, best_code = conf, cls, code
    if best_code is None:
        best_conf = GO_UNKNOWN[1]
    basis = {"rule": "go_annotation", "codes": listed, "best_code": best_code, "class": best_class}
    return best_conf, basis


def reactome_pathway() -> tuple[float, dict[str, Any]]:
    """Reactome pathway membership (curated, no per-edge evidence code)."""
    return REACTOME_PATHWAY, {"rule": "reactome_pathway"}


def drug_edge(props: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    """Backbone drug-target or drug-indication edge: 0.7 base, raw action kept."""
    basis = {
        "rule": "drug_edge",
        "backbone_relation": props.get("backbone_relation"),
        "mechanisms_of_action": _as_list(props.get("mechanisms_of_action")),
        "sources_direct": sources_direct(props),
        "source_ids": _as_list(props.get("source_ids")),
        "reference_ids": _as_list(props.get("reference_ids")),
        "highest_clinical_trial_phase": props.get("highest_clinical_trial_phase"),
    }
    return DRUG_EDGE, basis


def variant_of(props: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    """ClinVar Variant -> Gene: a database identity fact (0.95); the raw match kept."""
    basis = {"rule": "variant_of", "gene_symbol": props.get("gene_symbol"), "geneid": props.get("geneid"), "record_id": props.get("record_id"), "match": props.get("match")}
    return VARIANT_OF, basis


def clinvar_condition(props: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    """ClinVar Variant -> Disease (``has_condition``) from the record's ``review_status``
    (:data:`CLINVAR_REVIEW_STATUS`, lower-cased substring match; unknown or empty 0.3)."""
    status = str(props.get("review_status") or "")
    low = status.lower()
    conf, matched = CLINVAR_REVIEW_UNKNOWN, None
    for needle, value in CLINVAR_REVIEW_STATUS:
        if needle in low:
            conf, matched = value, needle
            break
    basis = {
        "rule": "clinvar_condition",
        "review_status": status,
        "matched": matched,
        "classification": props.get("classification"),
        "classification_block": props.get("classification_block"),
        "last_evaluated": props.get("last_evaluated"),
        "n_scv": props.get("n_scv"),
        "n_rcv": props.get("n_rcv"),
        "match": props.get("match"),
        "trait_name": props.get("trait_name"),
    }
    return conf, basis


def study_edge(props: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    """ClinicalTrials.gov registry record -> condition / intervention link (0.8)."""
    basis = {
        "rule": "study_edge",
        "record_id": props.get("record_id"),
        "match": props.get("match"),
        "matched_texts": _as_list(props.get("matched_texts")),
        "overall_status": props.get("overall_status"),
        "phases": _as_list(props.get("phases")),
        "study_type": props.get("study_type"),
        "enrollment": props.get("enrollment"),
        "has_results": props.get("has_results"),
        "intervention_type": props.get("intervention_type"),
    }
    return STUDY_EDGE, basis


def text_mention(props: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    """A study found only through a gene-symbol query term (inferred, 0.3)."""
    basis = {"rule": "text_mention", "record_id": props.get("record_id"), "match": props.get("match"), "evidence_class": "inferred"}
    return cap_inferred(TEXT_MENTION), basis


def funded_project(props: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    """RePORTER grant text naming a gene or disease (inferred, 0.4); raw grant facts kept."""
    basis = {
        "rule": "funded_project",
        "method": props.get("method"),
        "matched_text": _as_list(props.get("matched_text")),
        "n_projects": props.get("n_projects"),
        "core_project_nums": _as_list(props.get("core_project_nums")),
        "fiscal_years": _as_list(props.get("fiscal_years")),
        "agency": _as_list(props.get("agency")),
        "award_amount_total": props.get("award_amount_total"),
        "evidence_class": "inferred",
    }
    return cap_inferred(FUNDED_PROJECT), basis


def community_edge(matched_term: str, url: str, retrieved_at: str) -> tuple[float, dict[str, Any]]:
    """A verified patient-group / registry seed row naming a disease (0.8)."""
    return COMMUNITY_EDGE, {"rule": "community_edge", "matched_term": matched_term, "url": url, "retrieved_at": retrieved_at}


def apply_relation_conflict(
    conf: float, basis: dict[str, Any], props: Mapping[str, Any]
) -> tuple[float, dict[str, Any]]:
    """Downgrade by 0.6 when the backbone flags ``relation_conflict``; the raw assertions go
    into the basis. The caller adds :data:`RELATION_CONFLICT_LABEL` to ``attributes.labels``."""
    if not has_relation_conflict(props):
        return conf, basis
    out = dict(basis)
    out["relation_conflict"] = _as_list(props.get("relation_assertions"))
    out["relation_conflict_factor"] = RELATION_CONFLICT_FACTOR
    return conf * RELATION_CONFLICT_FACTOR, out


# --------------------------------------------------------------------------------------
# claims and inferred edges
# --------------------------------------------------------------------------------------


def claim_confidence(study_type: str | None, n_patients: int | None) -> tuple[float, dict[str, Any]]:
    """Per-claim confidence by study type (README section 6: trial > cohort > case series >
    case report > in vitro). ``n_patients`` is kept for the small-n label."""
    key = str(study_type) if study_type is not None else ""
    conf = STUDY_TYPE_CONFIDENCE.get(key, STUDY_TYPE_UNKNOWN)
    return conf, {"rule": "claim_confidence", "study_type": key or None, "n_patients": n_patients}


def cap_inferred(conf: float) -> float:
    """Inferred edges (rules, similarity, curated seeds) are capped at 0.5."""
    return min(float(conf), INFERRED_CAP)


def _claim_field(claim: Any, name: str) -> Any:
    if isinstance(claim, Mapping):
        return claim.get(name)
    return getattr(claim, name, None)


def _claim_weight(claim: Any) -> dict[str, Any]:
    """One claim's weight with its raw inputs (``id``, ``polarity``, ``study_type``, ``n_patients``)."""
    study_type = _claim_field(claim, "study_type")
    study_type = getattr(study_type, "value", study_type)
    conf, basis = claim_confidence(study_type, _as_int(_claim_field(claim, "n_patients")))
    polarity = _claim_field(claim, "polarity")
    return {"id": _claim_field(claim, "id"), "polarity": getattr(polarity, "value", polarity), "confidence": conf, **basis}


def aggregate_claim_confidence(supporting_weights: Iterable[float], n_contradicting: int) -> tuple[float, dict[str, Any]]:
    """Shared design D, the one place the claim formula is spelled out:
    ``min(1, max supporting weight + 0.05 * (n_supporting - 1)) * (1 - 0.3 * min(1, n_contradicting / n_supporting))``.
    No supporting weight gives 0 (and the full penalty ratio when anything contradicts). The basis
    keeps the raw numbers: the best weight, both counts, the bonus and penalty constants, the
    pre-penalty ``base``, the ``contradiction_ratio`` and the applied ``contradiction_penalty``."""
    weights = [float(w) for w in supporting_weights]
    n_sup = len(weights)
    n_con = int(n_contradicting)
    best = max(weights) if weights else None
    if n_sup:
        base = min(1.0, float(best) + CLAIM_SUPPORT_BONUS * (n_sup - 1))
        ratio = min(1.0, n_con / n_sup)
    else:
        base = 0.0
        ratio = 1.0 if n_con else 0.0
    penalty = CONTRADICTION_PENALTY * ratio
    conf = base * (1.0 - penalty)
    basis = {
        "rule": "claims_aggregate",
        "max_supporting_weight": best,
        "n_supporting": n_sup,
        "n_contradicting": n_con,
        "support_bonus": CLAIM_SUPPORT_BONUS,
        "contradiction_penalty_max": CONTRADICTION_PENALTY,
        "base": base,
        "contradiction_ratio": ratio,
        "contradiction_penalty": penalty,
    }
    return conf, basis


def edge_confidence_from_claims(
    supporting: Iterable[Any], contradicting: Iterable[Any], observed_confidence: float | None = None
) -> tuple[float, dict[str, Any]]:
    """``(confidence, basis)`` for an edge backed by claims (Claim models or dicts).

    :func:`aggregate_claim_confidence` over the per-claim weights of :func:`claim_confidence`.
    No supporting claim gives 0 from the literature. With ``observed_confidence`` (a database
    edge the claims attach to) the result is ``max(observed, literature)``, or
    ``observed * 0.7`` when every claim contradicts the database fact. The basis lists every
    claim's weight (raw study type and n) and the intermediate numbers.
    """
    sup = [_claim_weight(c) for c in supporting]
    con = [_claim_weight(c) for c in contradicting]
    n_sup, n_con = len(sup), len(con)
    literature, agg = aggregate_claim_confidence((w["confidence"] for w in sup), n_con)
    basis: dict[str, Any] = {
        "rule": "edge_confidence_from_claims",
        "claims": sup + con,
        "n_supporting": n_sup,
        "n_contradicting": n_con,
        "best_supporting": float(agg["max_supporting_weight"] or 0.0),
        "support_bonus": CLAIM_SUPPORT_BONUS,
        "base": agg["base"],
        "contradiction_ratio": agg["contradiction_ratio"],
        "contradiction_penalty": agg["contradiction_penalty"],
        "literature_confidence": literature,
    }
    if observed_confidence is None:
        return literature, basis
    observed = float(observed_confidence)
    basis["observed_confidence"] = observed
    if n_con and not n_sup:
        basis["contradicted_fact_factor"] = CONTRADICTED_FACT_FACTOR
        return observed * CONTRADICTED_FACT_FACTOR, basis
    return max(observed, literature), basis


def status_from_claims(supporting: Iterable[Any], contradicting: Iterable[Any]) -> tuple[str, list[str], dict[str, Any]]:
    """``(status, labels, basis)`` for an edge backed by claims (Claim models or dicts).

    Both polarities present => ``contested`` (dominant). Every patient-level study behind the
    edge that REPORTS a patient count has fewer than :data:`SMALL_N` patients (and there is at
    least one such study) => ``small_n``; in-vitro and review claims carry no patients and never
    count, and a patient-level claim whose ``n`` is unknown neither blocks nor grants the label
    (shared design D). Contested dominates small_n, which is then carried in the labels only.
    Otherwise ``supported``. Labels: ``contested``, ``small_n``, ``promising`` (>= 1 supporting
    patient-level claim), ``preprint`` (>= 1 claim whose ``paper`` id starts with
    :data:`PREPRINT_PAPER_PREFIX`; a claim with an empty ``pmid`` from any other paper, such as a
    mock, is not a preprint).
    """
    sup = list(supporting)
    con = list(contradicting)
    everything = sup + con
    patient_level = [c for c in everything if str(_claim_field(c, "study_type")) in PATIENT_LEVEL_STUDY_TYPES]
    ns = [n for c in patient_level if (n := _as_int(_claim_field(c, "n_patients"))) is not None]
    small_n = bool(ns) and all(n < SMALL_N for n in ns)
    contested = bool(sup) and bool(con)
    labels: list[str] = []
    if any(str(_claim_field(c, "study_type")) in PATIENT_LEVEL_STUDY_TYPES for c in sup):
        labels.append("promising")
    if contested:
        labels.append("contested")
    if small_n:
        labels.append("small_n")
    if any(str(_claim_field(c, "paper") or "").startswith(PREPRINT_PAPER_PREFIX) for c in everything):
        labels.append("preprint")
    if contested:
        status = "contested"
    elif small_n:
        status = "small_n"
    else:
        status = "supported"
    basis = {
        "n_supporting": len(sup),
        "n_contradicting": len(con),
        "patient_level_n": ns,
        "patient_level_without_n": len(patient_level) - len(ns),
        "small_n_threshold": SMALL_N,
    }
    return status, labels, basis
