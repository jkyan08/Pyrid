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
* :func:`cap_inferred`: inferred edges never exceed 0.5 (``pipeline.models.Edge`` enforces it).
* :func:`status_from_claims`: the status/label conventions of the README section 6 table.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "DRUG_EDGE",
    "GO_CLASSES",
    "GO_UNKNOWN",
    "INFERRED_CAP",
    "ONTOLOGY_IS_A",
    "PATIENT_LEVEL_STUDY_TYPES",
    "PHENOTYPE_DEFAULT",
    "PHENOTYPE_EVIDENCE",
    "REACTOME_PATHWAY",
    "RELATION_CONFLICT_FACTOR",
    "RELATION_CONFLICT_LABEL",
    "SMALL_N",
    "STUDY_TYPE_CONFIDENCE",
    "STUDY_TYPE_UNKNOWN",
    "apply_relation_conflict",
    "cap_inferred",
    "claim_confidence",
    "disease_gene",
    "disease_phenotype",
    "drug_edge",
    "go_annotation",
    "has_relation_conflict",
    "ontology_is_a",
    "reactome_pathway",
    "sources_direct",
    "status_from_claims",
]

INFERRED_CAP = 0.5
RELATION_CONFLICT_FACTOR = 0.6
RELATION_CONFLICT_LABEL = "conflicting_assertions"

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


def status_from_claims(supporting: Iterable[Any], contradicting: Iterable[Any]) -> tuple[str, list[str], dict[str, Any]]:
    """``(status, labels, basis)`` for an edge backed by claims (Claim models or dicts).

    Both polarities present => ``contested`` (dominant). Every patient-level study behind the
    edge has fewer than :data:`SMALL_N` patients (and there is at least one) => ``small_n``;
    contested dominates small_n, which is then carried in the labels only. Otherwise
    ``supported``. Labels: ``contested``, ``small_n``, ``promising`` (>= 1 supporting
    patient-level claim), ``preprint`` (>= 1 claim without a PMID).
    """
    sup = list(supporting)
    con = list(contradicting)
    everything = sup + con
    patient_level = [c for c in everything if str(_claim_field(c, "study_type")) in PATIENT_LEVEL_STUDY_TYPES]
    ns = [_as_int(_claim_field(c, "n_patients")) for c in patient_level]
    small_n = bool(patient_level) and all(n is not None and n < SMALL_N for n in ns)
    contested = bool(sup) and bool(con)
    labels: list[str] = []
    if any(str(_claim_field(c, "study_type")) in PATIENT_LEVEL_STUDY_TYPES for c in sup):
        labels.append("promising")
    if contested:
        labels.append("contested")
    if small_n:
        labels.append("small_n")
    if any(not _claim_field(c, "pmid") for c in everything):
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
        "small_n_threshold": SMALL_N,
    }
    return status, labels, basis
