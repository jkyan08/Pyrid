"""Assemble ``graph.json`` (:class:`pipeline.models.Graph`) from the evidence store.

:func:`build` reads ONLY the store tables (nodes, edges, coverage, sources, hpo_ic), the seeds and
an optional claims bundle; it never touches the network. :func:`build_graph` returns the graph,
:func:`build` also returns the report dict ``pipeline.export`` writes to ``build_report.json``.

Pipeline inside :func:`build`
-----------------------------
1. **Nodes**: every store node, ``props`` verbatim. Phenotype nodes gain ``props.ic`` (plus
   ``ic_n_diseases`` / ``ic_n_total``) from the ``hpo_ic`` table when present. Nodes are sorted by
   (type, id) on export.
2. **Mechanisms** (:mod:`pipeline.graph.mechanisms`): Mechanism nodes and Disease/Variant ->
   Mechanism edges from the general text rules, the curated fallback and the claims bundle (R3).
3. **Store edges**, scored with :mod:`pipeline.graph.confidence` by relation (``associated_with`` ->
   disease_gene over the observed ``evidence_score`` range, the HPO-aspect relations ->
   disease_phenotype, ``is_a`` -> ontology_is_a, ``in_pathway`` -> go_annotation / reactome_pathway,
   drug relations -> drug_edge, ``variant_of`` -> variant_of (0.95), ``has_condition`` -> ClinVar
   review status, ``studies`` / ``evaluates`` -> study_edge, ``mentions`` -> text_mention,
   ``has_funded_project`` -> funded_project, anything else -> :data:`UNSCORED_CONFIDENCE`),
   downgraded and labelled ``conflicting_assertions`` when the backbone flags a relation conflict.
   A store edge whose ``props.evidence_class`` is ``inferred`` (ClinicalTrials.gov ``mentions``,
   RePORTER ``has_funded_project``) is exported as ``inferred``: capped at 0.5, status
   ``supported``, ``attributes.labels`` carries ``"inferred"`` so the UI renders the badge.
   Edges are keyed by ``(source, target, relation)``; the same triple asserted twice is merged
   (sources appended, best confidence, the second basis kept under ``also_asserted_by``).
   ``associated_with`` edges carry ``attributes.origin`` when the mechanism rules read one from
   the disease's own text or from a claim (never from the curated seed file, hard rule 10) and
   ``attributes.labels ["below_score_threshold"]`` when the edge fails the seed threshold.
4. **Claims** (``data/build/claims.json``): when :mod:`pipeline.graph.evidence` is importable its
   ``integrate_claims(nodes, edges, bundle, today=..., scrub=...)`` resolves every claim endpoint,
   creates or upgrades edges, derives Paper and Investigator nodes and returns the additions, which
   are merged here. Without that module the legacy rule applies: a claim whose (subject,
   predicate, object) equals an edge's (source, relation, target) attaches to that edge. Either
   way every edge with claims then gets its status / labels from
   :func:`pipeline.graph.confidence.status_from_claims` (``_reconcile_claim_edges``; the claim
   arithmetic has one definition, in :mod:`pipeline.graph.confidence`), claim ids are
   deduplicated, an inferred edge with a supporting claim becomes extracted (and loses its
   ``curated_unverified`` label), and an extracted edge left without claims is removed and
   counted. A claim whose
   ``variant`` is not a Variant node (a ``text:`` mention) keeps its evidence with ``variant`` set
   to ``""``; the raw value is listed in the report (``claims.variant_unresolved``).
5. **Patient groups and registries** (``pipeline/seeds/patient_groups.yaml``, item H):
   ``org:<slug(name)>`` nodes (``PatientGroup`` for kind patient_group / foundation, ``Registry``
   for kind registry), ``supports_community`` / ``covers`` edges to every disease a seed term
   resolves to (case-insensitive exact label / synonym match after stripping a trailing
   parenthetical such as "(umbrella)" and splitting on " / "), coverage rows for every term (hits
   0 when unresolved) and for every directory search the seed file logged (``null`` hits -> 0 with
   the query suffixed " [not executable]"), so "no dedicated group found" is visible as zero-hit
   searches, never special-cased. A term that matches no Disease is then tried against the
   Mechanism nodes by normalised label (:func:`normalize_mechanism_term`: lower-case, hyphens and
   underscores to spaces, "gof" / "lof" / "dn" expanded, a trailing parenthetical dropped): a
   match gives an Org -> Mechanism edge (observed, :data:`COMMUNITY_MECHANISM_CONFIDENCE`, basis
   ``resolution: "mechanism label"``). Both rules are general: nothing names a gene, a disease or
   an organisation. The knobs live in ``pipeline/seeds/cluster.yaml`` under ``patient_groups:``
   (:data:`DEFAULT_PATIENT_GROUP_OPTIONS`).
5b. **Propagation** (:func:`propagate_patient_groups`, run AFTER the prune so that it links
   organisations to diseases already in the export and never pulls a disease in): for every Org ->
   Mechanism edge, an Org -> Disease edge to each exported Disease with a ``has_mechanism`` edge to
   that Mechanism (:data:`COMMUNITY_VIA_MECHANISM_CONFIDENCE`, basis ``via_mechanism`` +
   ``disease_mechanism_edge_confidence``, labels ``via_mechanism`` + ``inferred``); then, for every
   Org -> Disease edge (exact or via mechanism), edges to the disease's descendants through Disease
   ``is_a`` edges down to ``descendant_depth`` (:data:`COMMUNITY_VIA_ANCESTOR_CONFIDENCE` minus
   :data:`COMMUNITY_VIA_ANCESTOR_DEPTH_STEP` per extra level, basis ``via_ancestor`` + ``depth``,
   labels ``via_ancestor`` + ``inferred``), skipping diseases the organisation already links to and
   capped at ``max_descendants_per_term`` (the cap is reported when it bites). Both routes are
   produced by a rule, not read from the organisation's page, so their edges are ``evidence_class``
   :data:`COMMUNITY_RULE_EVIDENCE_CLASS` (README section 4/6: never above 0.5, status ``supported``,
   rendered with the badge) and the basis carries ``rule_evidence_class_note``
   (:data:`COMMUNITY_RULE_EVIDENCE_CLASS_NOTE`); only the exact Org -> Disease and Org -> Mechanism
   matches stay observed (the verified page names the term). Each seed term's coverage row
   then counts every disease it reached (direct + via mechanism + via ancestor) and a second row
   per route (``"<term> [resolution: mechanism label]"`` / ``"<term> [resolution: ancestor]"``)
   records how the asset was linked; a term that resolves to nothing stays a zero-hit row.
6. **Pruning** (:func:`prune_graph`, thresholds in ``pipeline/seeds/cluster.yaml``): the store
   keeps a wide slice so that Lane B's resolved endpoints stay resolvable; the export applies the
   stricter inclusion rule :func:`pipeline.sources.optimuskg.disease_gene_included` (a curated
   direct source AND ``evidence_score >= disease_gene_min_score``, OR ``evidence_score >=
   disease_gene_high_score``), caps the diseases a seed gene pulls in
   (``max_diseases_per_seed_gene``, by score) and the other genes per disease
   (``max_genes_per_disease``, by score), keeps drugs only when they act on a seed gene, match a
   seed drug term or are referenced by evidence, and keeps Variants that are ``keep_in_graph``,
   carry a somatic block or are referenced by a claim. Diseases named by a seed term or reached by
   an observed / extracted evidence edge (ClinVar, ClinicalTrials.gov, claims, patient groups) are
   anchors, and so is any backbone node (Disease, Gene, Drug, Phenotype, Pathway) a claim
   RESOLVED to, even when that claim attached to no edge: ``claims[]`` never names a node that is
   not in ``nodes[]``. Inferred text matches never pull a node in. Phenotype ancestors stay (IC-weighted
   overlap needs them), GO processes only for seed genes, Studies and Papers all, Investigators
   with at least one surviving edge. Edges below the inclusion rule are not exported and are
   counted per exported disease as coverage rows (source ``"build prune"``); every dropped node
   group is a ``source:build`` coverage row and a report entry. One documented exemption: a Gene
   whose ``props.gap_probe`` is true (the connection-less gene ``pipeline.export.run_gap_probe``
   chose for ground-truth test 6 by the rule in ``cluster.yaml``) is exported WITHOUT edges, so
   its coverage rows reach ``meta.coverage`` and the API answers ``no_supported_route`` with the
   searches shown (api/CONTRACT.md); no other edgeless node survives.
7. **Slimming and scrubbing**: ``props`` are reduced to :data:`EXPORT_PROPS_KEEP` per node type
   (long descriptions truncated, Gene xrefs filtered to HGNC / OMIM / UniProt, Paper authors as
   plain names), synonyms capped at :data:`MAX_SYNONYMS`, and every exported string (labels,
   synonyms, props, edge attributes and basis, claim quotes) passes :func:`scrub_text`: e-mail
   addresses and phone numbers are replaced by ``[email removed]`` / ``[phone removed]`` and dose
   amounts and schedules by ``pipeline.sources.clinvar.scrub_doses`` (hard rule 7); ORCIDs and
   DOIs are protected from every pass (hard rule 1), and the report's ``scrub_placeholders``
   counts the placeholders in the final JSON. Abstracts and
   quotes are scrubbed with the same function so the quote check (hard rule 5) still holds; a
   quote that no longer matches is dropped and counted (``claims.dropped``). Every edge then
   passes :func:`compact_edge` (``sources[].date`` as the retrieval date, no ``None`` / empty
   top-level basis entries, basis floats rounded to :data:`BASIS_FLOAT_DECIMALS` decimals): bytes,
   never evidence.
8. **Meta**: ``sources`` from the store's sources table, plus one record per upstream database a
   loader listed under ``notes.upstream_sources``, a record for the curated seed file when R4
   fired, one for the claims bundle and one for the patient-group seed file; ``coverage`` from the
   store, the mechanism rules, the evidence integration, the patient-group seeds and the prune
   step, restricted to exported node ids plus ``seed:*`` / ``source:*`` / ``claims:*`` / ``mech:*``
   keys and every ``claims.json endpoint`` row (a claim's failed link stays visible even when its
   resolved side was pruned); rows for other pruned entities stay in the store and are counted in
   the report.
9. Edge ids ``e_000001...`` are assigned after sorting by (relation, source, target), so two builds
   of one store give identical ids. ``clusters`` is empty here (``pipeline.graph.logic`` fills it).

The report records counts, the prune and evidence summaries and ``sizes`` (bytes of compact JSON
per node type and edge relation) so the 8 MB budget for ``web/public/graph.json`` is auditable.
Edges whose endpoints are not nodes cannot be exported; they are dropped and listed in the report
(``edges_dropped_dangling``), never silently.
"""

from __future__ import annotations

import importlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pipeline.graph import confidence as conf_rubric
from pipeline.graph import mechanisms
from pipeline.models import Graph, NodeType
from pipeline.sources import optimuskg
from pipeline.sources.clinvar import DOSE_PLACEHOLDER, scrub_doses
from pipeline.sources.optimuskg import ASPECT_RELATIONS
from pipeline.sources.reporter import slugify

__all__ = [
    "BACKBONE_SOURCE_DBS",
    "BASIS_FLOAT_DECIMALS",
    "BELOW_THRESHOLD_LABEL",
    "BuildResult",
    "COMMUNITY_MECHANISM_CONFIDENCE",
    "COMMUNITY_RULE_EVIDENCE_CLASS",
    "COMMUNITY_RULE_EVIDENCE_CLASS_NOTE",
    "COMMUNITY_VIA_ANCESTOR_CONFIDENCE",
    "COMMUNITY_VIA_ANCESTOR_DEPTH_STEP",
    "COMMUNITY_VIA_MECHANISM_CONFIDENCE",
    "DEFAULT_EXPORT_THRESHOLDS",
    "DEFAULT_PATIENT_GROUP_OPTIONS",
    "EMAIL_PLACEHOLDER",
    "EVIDENCE_MODULE",
    "EXPORT_PROPS_KEEP",
    "GAP_PROBE_PROP",
    "HPO_ANNOTATION_RELATIONS",
    "MAX_SYNONYMS",
    "PHONE_PLACEHOLDER",
    "PRUNE_SOURCE",
    "PatientGroupResult",
    "PruneResult",
    "RESOLUTION_ANCESTOR",
    "RESOLUTION_MECHANISM_LABEL",
    "UNSCORED_CONFIDENCE",
    "VARIANT_OF_CONFIDENCE",
    "VIA_ANCESTOR_LABEL",
    "VIA_MECHANISM_LABEL",
    "build",
    "build_graph",
    "build_patient_groups",
    "compact_edge",
    "finalize_edges",
    "normalize_mechanism_term",
    "patient_group_options",
    "propagate_patient_groups",
    "prune_graph",
    "score_range",
    "scrub_placeholders",
    "scrub_text",
    "scrub_tree",
    "seed_resolution",
    "sizes",
    "slim_node",
    "summarize",
]

BELOW_THRESHOLD_LABEL = "below_score_threshold"
# Database-asserted identity facts (ClinVar: variant X lies in gene Y) that carry no per-edge score.
VARIANT_OF_CONFIDENCE = conf_rubric.VARIANT_OF
# Relations the rubric does not cover: scored at the midpoint so the gap is visible in the basis.
UNSCORED_CONFIDENCE = 0.5
CONFIDENCE_DECIMALS = 4
# Floats inside confidence_basis are rounded to this many decimals on export (compact_edge).
BASIS_FLOAT_DECIMALS = 6
MAX_SYNONYMS = 12
EVIDENCE_MODULE = "pipeline.graph.evidence"
PRUNE_SOURCE = "build prune"
PRUNE_ENTITY = "source:build"
PATIENT_GROUP_SOURCE_DB = "patient_groups.yaml"
PATIENT_GROUP_TERM_SOURCE = "patient_groups.yaml disease term"
PATIENT_GROUP_SEARCH_PREFIX = "seed:patient_group_search:"
PATIENT_GROUP_SEED_LICENSE = "team-curated seed file (pipeline/seeds/patient_groups.yaml); every URL fetched on the recorded date"
NOTE_MAX_CHARS = 400
# How a patient-group seed term that matched no Disease label / synonym was resolved instead
# (confidence_basis["resolution"] and the coverage row "<term> [resolution: <value>]").
RESOLUTION_MECHANISM_LABEL = "mechanism label"
RESOLUTION_ANCESTOR = "ancestor"
VIA_MECHANISM_LABEL = "via_mechanism"
VIA_ANCESTOR_LABEL = "via_ancestor"
# Org -> Mechanism when the term equals a Mechanism label after normalisation: the organisation's
# own page names the mechanism, so the edge is observed like the exact Org -> Disease match
# (conf_rubric.COMMUNITY_EDGE).
COMMUNITY_MECHANISM_CONFIDENCE = 0.8
# The two propagation routes of step 5b (Org -> Disease for every disease that carries a named
# mechanism; Org -> Disease for the descendants of a disease the organisation names) are produced by
# a general rule, not read from the page, so their edges are evidence_class "inferred": README
# section 4/6 and pipeline.models.Edge cap an inferred edge at 0.5 (status "supported", rendered
# with the badge; conf_rubric.INFERRED_LABEL added to attributes.labels like every other inferred
# edge this module writes). via_mechanism 0.5; via_ancestor 0.45 at depth 1, minus 0.05 per further
# level (0.40 at depth 2, 0.35 at depth 3). The note goes into every such edge's confidence_basis.
COMMUNITY_RULE_EVIDENCE_CLASS = "inferred"
COMMUNITY_RULE_EVIDENCE_CLASS_NOTE = "rule-derived link: inferred (<= 0.5) per README section 4/6"
COMMUNITY_VIA_MECHANISM_CONFIDENCE = 0.5
COMMUNITY_VIA_ANCESTOR_CONFIDENCE = 0.45
COMMUNITY_VIA_ANCESTOR_DEPTH_STEP = 0.05
# pipeline/seeds/cluster.yaml ``patient_groups:`` (documented there); general knobs, no names.
DEFAULT_PATIENT_GROUP_OPTIONS: dict[str, Any] = {"descendant_depth": 3, "max_descendants_per_term": 200}
# Tokens expanded before a seed term is compared with a Mechanism label (normalize_mechanism_term).
MECHANISM_TOKEN_EXPANSIONS = {"gof": "gain of function", "lof": "loss of function", "dn": "dominant negative"}

REL_ASSOCIATED_WITH = mechanisms.REL_ASSOCIATED_WITH
REL_HAS_MECHANISM = mechanisms.REL_HAS_MECHANISM
REL_HAS_PHENOTYPE = "has_phenotype"
# HPO annotation edges by aspect (has_phenotype for "P", has_inheritance, has_clinical_course, ...):
# all scored with the disease_phenotype rubric, since every row carries an HPO evidence code.
HPO_ANNOTATION_RELATIONS = frozenset({REL_HAS_PHENOTYPE, *ASPECT_RELATIONS.values()})
REL_IS_A = "is_a"
REL_IN_PATHWAY = "in_pathway"
REL_VARIANT_OF = "variant_of"
REL_HAS_CONDITION = "has_condition"
REL_STUDIES = "studies"
REL_EVALUATES = "evaluates"
REL_MENTIONS = "mentions"
REL_HAS_FUNDED_PROJECT = "has_funded_project"
REL_SUPPORTS_COMMUNITY = "supports_community"
REL_COVERS = "covers"
DRUG_RELATIONS = frozenset({"inhibits", "targets", "indicated_for", "contraindicated_for"})
GO_ONTOLOGY = "GO"
# Loaders whose edges describe the backbone slice itself (their associated_with edges are the ones
# the inclusion rule judges).
BACKBONE_SOURCE_DBS = frozenset({optimuskg.SOURCE_DB, mechanisms.RULE_SOURCE, "hpo"})
# Relations of the backbone slice. An OBSERVED edge of any other relation (ClinVar has_condition,
# ClinicalTrials.gov studies / evaluates, patient-group supports_community / covers, author_of,
# publishes_on, ...), any EXTRACTED edge and any edge carrying claims is evidence that anchors its
# endpoints in the export; an INFERRED text match never does.
BACKBONE_RELATIONS = frozenset({REL_ASSOCIATED_WITH, REL_IS_A, REL_IN_PATHWAY, REL_HAS_MECHANISM, *HPO_ANNOTATION_RELATIONS, *DRUG_RELATIONS})
# Node types an evidence edge anchors in the export (see prune_graph).
ANCHORABLE_TYPES = frozenset({NodeType.Disease.value, NodeType.Gene.value, NodeType.Drug.value, NodeType.Phenotype.value, NodeType.Pathway.value})
EVIDENCE_CLASS_RANK = {"inferred": 0, "observed": 1, "extracted": 2}
# meta.coverage keeps rows for exported nodes, for these key prefixes (seed terms, build/source
# summaries, unresolved claim endpoints, mechanism ids: a Mechanism named by a claim may leave the
# export when nothing attaches to it, and its failed link must stay visible) and for every row of
# CLAIMS_COVERAGE_SOURCE (a claim in claims[] whose endpoint failed keeps its gap even when the
# resolved side was pruned; the API serves coverage-only ids as honest gaps, api/CONTRACT.md).
COVERAGE_KEEP_PREFIXES = ("seed:", "source:", "claims:", "mech:")
CLAIMS_COVERAGE_SOURCE = "claims.json endpoint"  # pipeline.graph.evidence.COVERAGE_SOURCE
# A Gene node carrying this prop (true) is the gap probe: exported without edges (prune_graph).
GAP_PROBE_PROP = optimuskg.GAP_PROBE_PROP

# Export-time pruning thresholds (pipeline/seeds/cluster.yaml documents them); general rules only.
DEFAULT_EXPORT_THRESHOLDS: dict[str, Any] = {
    "disease_gene_curated_sources": list(optimuskg.DEFAULT_CURATED_SOURCES),
    "disease_gene_high_score": optimuskg.DEFAULT_HIGH_SCORE,
    "max_diseases_per_seed_gene": 40,
    "max_genes_per_disease": 10,
    "include_parent_levels": 1,
    # Paper-derived Investigators (props.n_papers) need at least this many papers to be exported;
    # 1 = design item E (everyone with a publishes_on edge). RePORTER investigators are unaffected.
    "min_papers_per_investigator": 1,
    # ... unless they carry an ORCID (a reliably identified person, README section 5 "flagged low"
    # without one), which exempts them from the paper count.
    "keep_orcid_investigators": True,
}

# Props kept on export, per node type: ``None`` keeps the value as is, an int truncates a string
# to that many characters. Types absent from the table keep every prop (Mechanism, PatientGroup,
# Registry, Claim). Type-specific reshaping (Gene xrefs, Drug aliases, Study eligibility, Paper
# authors, Investigator projects) is in :func:`slim_node`.
EXPORT_PROPS_KEEP: dict[str, dict[str, int | None]] = {
    "Gene": {
        "symbol": None, "name": None, "biotype": None, "xrefs": None, "backbone_id": None,
        # the gap probe's marker, the rule that chose it and its (zero) backbone counts
        GAP_PROBE_PROP: None, f"{GAP_PROBE_PROP}_rule": None, f"{GAP_PROBE_PROP}_counts": None,
    },
    "Disease": {"name": None, "description": 500, "xrefs": None, "backbone_id": None, "ontology": None, "therapeutic_areas": None},
    "Phenotype": {"ic": None, "ic_n_diseases": None, "ic_n_total": None, "backbone_id": None, "description": 300},
    "Drug": {"name": None, "max_phase": None, "drug_type": None, "backbone_id": None, "description": 300, "is_approved": None},
    "Pathway": {"name": None, "ontology": None, "backbone_id": None, "species": None, "definition": 300},
    "Variant": {
        "protein_change": None, "cdna_change": None, "hgvs_p": None, "hgvs_c": None, "accession": None,
        "classification": None, "review_status": None, "last_evaluated": None, "molecular_consequence": None,
        "gene_symbols": None, "gene_symbol": None, "keep_reasons": None, "matched_variant_terms": None,
        "source_url": None, "has_somatic_block": None, "record_id": None,
    },
    "Study": {
        "overall_status": None, "phases": None, "study_type": None, "enrollment": None, "start_date": None,
        "primary_completion_date": None, "conditions": None, "interventions": None, "eligibility": None,
        "sponsor": None, "source_url": None, "data_timestamp": None, "record_id": None, "has_results": None,
        "found_by_queries": None, "reference_pmids": None,
    },
    "Paper": {"title": None, "abstract": None, "year": None, "journal": None, "pmid": None, "doi": None, "authors": None, "preprint": None},
    "Investigator": {
        "name": None, "affiliations": None, "orcid": None, "match_confidence": None, "n_papers": None, "papers": None,
        "papers_by_match": None, "role_label": None, "title": None, "organization": None, "profile_id": None, "record_id": None,
        "n_projects": None, "projects": None, "found_by_queries": None, "source_url": None, "pmids": None,
        "unlinked_projects": None,
    },
}
GENE_XREF_SOURCES = frozenset({"hgnc", "omim", "uniprot"})
DRUG_PROP_ALIASES = {"max_phase": "maximum_clinical_trial_phase", "drug_type": "type"}
ELIGIBILITY_CRITERIA_MAX = 1500
PROJECT_KEEP = ("core_project_num", "project_num", "title", "fiscal_years", "award_amount_total", "detail_url", "matched")
MAX_LIST_IDS = 50

_PMID_REF = re.compile(r"^PMID:(\d+)$", re.I)
_NCT_ID = re.compile(r"^NCT\d{8}$")
_ISO_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}")
_SCORE_RANGE_NOTE_KEYS = ("evidence_score_min", "evidence_score_max")
_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")

CURATED_SEED_LICENSE = "team-authored project document (docs/test-cluster-desc.md); not a licensed database"
CLAIMS_BUNDLE_LICENSE = "PubMed abstracts: public (NCBI / NLM terms), see README section 5"

# --------------------------------------------------------------------------------------
# scrubbing (hard rule 7 and no contact data in graph.json)
# --------------------------------------------------------------------------------------

EMAIL_PLACEHOLDER = "[email removed]"
PHONE_PLACEHOLDER = "[phone removed]"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# Three digit groups (optionally a country code and a parenthesised area code): "+1 301-496-1234",
# "(0049) 30 1234 5678"; a bare range such as "2016-2020" or a ZIP+4 is NOT a phone number.
_PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{3,4}(?:[\s.-]\d{2,4})?(?![A-Za-z0-9])"
    r"|(?i:\b(?:tel|telephone|phone|fax|mobile)\.?\s*:?\s*[+(]?\d[\d\s().-]{6,}\d)"
)
# Identifiers that look like phone numbers (or doses) but are not contact data: an ORCID (16 digits
# in four hyphenated groups, last character may be X) and a DOI (``10.<registrant>/<suffix>``, whose
# suffix often carries hyphenated digit groups such as ``j.0105-2896.2005.00220.x``). Protected
# before every scrubbing pass and restored verbatim afterwards (hard rule 1: no corrupted ids).
_PROTECTED_RE = re.compile(r"(?<![0-9A-Za-z])\d{4}-\d{4}-\d{4}-\d{3}[0-9X](?![0-9A-Za-z])|\b10\.\d{4,9}/\S+")
_SENTINEL = "\x00{}\x00"


def scrub_text(text: str) -> str:
    """E-mail addresses -> ``[email removed]``, phone numbers -> ``[phone removed]``, then dose
    amounts and schedules -> ``[dose removed]`` (:func:`pipeline.sources.clinvar.scrub_doses`).
    ORCIDs and DOIs are left alone. Non-strings pass through unchanged."""
    if not isinstance(text, str) or not text:
        return text
    protected: list[str] = []

    def keep(m: re.Match[str]) -> str:
        protected.append(m.group(0))
        return _SENTINEL.format(len(protected) - 1)

    out = _PROTECTED_RE.sub(keep, text)
    out = _EMAIL_RE.sub(EMAIL_PLACEHOLDER, out)
    out = _PHONE_RE.sub(PHONE_PLACEHOLDER, out)
    out = scrub_doses(out)
    for i, value in enumerate(protected):
        out = out.replace(_SENTINEL.format(i), value)
    return out


def scrub_tree(value: Any, scrub: Callable[[str], str] = scrub_text) -> Any:
    """Apply ``scrub`` to every string inside nested dicts / lists (keys are left alone)."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, Mapping):
        return {k: scrub_tree(v, scrub) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_tree(v, scrub) for v in value]
    return value


@dataclass
class BuildResult:
    graph: Graph
    report: dict[str, Any]


@dataclass
class PatientGroupResult:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    source: dict[str, str] | None = None
    report: dict[str, Any] = field(default_factory=dict)
    # (org id, seed term) -> disease ids the term reached, in total and per resolution route
    # (RESOLUTION_MECHANISM_LABEL / RESOLUTION_ANCESTOR); propagate_patient_groups extends them and
    # rewrites the coverage rows' hits from them.
    reached: dict[tuple[str, str], set[str]] = field(default_factory=lambda: defaultdict(set))
    reached_by: dict[tuple[str, str, str], set[str]] = field(default_factory=lambda: defaultdict(set))


@dataclass
class PruneResult:
    coverage: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _now_iso(now: str | None) -> str:
    return now or datetime.now(timezone.utc).isoformat(timespec="seconds")


def _round(conf: float) -> float:
    return round(float(conf), CONFIDENCE_DECIMALS)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _dedupe(items: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def score_range(store: Any) -> tuple[float, float, str]:
    """``(min, max, how)`` of ``evidence_score`` on disease-gene edges: the loader's record in a
    source's ``notes`` (``notes.evidence_score.{min,max}`` as the OptimusKG loader writes it, or
    flat ``evidence_score_min`` / ``evidence_score_max``; the range over the whole backbone file)
    when present, else the range observed in the store, else (0, 1)."""
    for row in store.sources_rows():
        notes = row.get("notes") or {}
        nested = notes.get("evidence_score")
        if isinstance(nested, Mapping):
            lo, hi = _as_float(nested.get("min")), _as_float(nested.get("max"))
        else:
            lo, hi = _as_float(notes.get(_SCORE_RANGE_NOTE_KEYS[0])), _as_float(notes.get(_SCORE_RANGE_NOTE_KEYS[1]))
        if lo is not None and hi is not None and hi >= lo:
            return lo, hi, f"sources.notes of {row['name']!r}"
    scores = [s for e in store.iter_edges(relation=REL_ASSOCIATED_WITH) if (s := _as_float((e.get("props") or {}).get("evidence_score"))) is not None]
    if scores:
        return min(scores), max(scores), "observed in store"
    return 0.0, 1.0, "default (no scored disease-gene edge)"


def _raw_id(node: Mapping[str, Any] | None, fallback: str) -> str:
    if node is None:
        return fallback
    backbone = (node.get("props") or {}).get("backbone_id")
    return str(backbone) if isinstance(backbone, str) and backbone.strip() else fallback


def _edge_sources(row: Mapping[str, Any], nodes: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    props = row.get("props") or {}
    date = str(row["retrieved_at"])
    src = _raw_id(nodes.get(row["source"]), row["source"])
    tgt = _raw_id(nodes.get(row["target"]), row["target"])
    # "<source_db> <raw source id>-><raw target id>": the database record behind the edge (the
    # relation is the edge's own field, so it is not repeated here; bytes matter at ~9,000 edges).
    out = [{"kind": "db", "id": f"{row['source_db']} {src}->{tgt}", "date": date}]
    refs = props.get("references")
    for ref in refs if isinstance(refs, list) else ([refs] if isinstance(refs, str) else []):
        m = _PMID_REF.match(str(ref).strip())
        if m:
            s = {"kind": "pmid", "id": m.group(1), "date": date}
            if s not in out:
                out.append(s)
    record_id = props.get("record_id")
    if isinstance(record_id, str) and _NCT_ID.match(record_id):
        out.append({"kind": "nct", "id": record_id, "date": date})
    urls = props.get("detail_urls")
    for url in (urls if isinstance(urls, list) else [])[:3]:
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            s = {"kind": "url", "id": url, "date": date}
            if s not in out:
                out.append(s)
    return out


def _score_edge(
    row: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
    srange: tuple[float, float],
    thresholds: Mapping[str, Any],
) -> tuple[float, dict[str, Any], list[str], str]:
    """``(confidence, basis, labels, evidence_class)`` for a store edge by relation."""
    relation = row["relation"]
    props = row.get("props") or {}
    labels: list[str] = []
    if relation == REL_ASSOCIATED_WITH:
        conf, basis = conf_rubric.disease_gene(props, srange[0], srange[1])
        if not mechanisms.association_passes(props, thresholds):
            labels.append(BELOW_THRESHOLD_LABEL)
            basis["below_threshold"] = {k: thresholds.get(k) for k in mechanisms.DEFAULT_THRESHOLDS}
    elif relation in HPO_ANNOTATION_RELATIONS:
        conf, basis = conf_rubric.disease_phenotype(props)
    elif relation == REL_IS_A:
        conf, basis = conf_rubric.ontology_is_a()
    elif relation == REL_IN_PATHWAY:
        target = nodes.get(row["target"]) or {}
        ontology = (target.get("props") or {}).get("ontology")
        if ontology == GO_ONTOLOGY or props.get("evidence") is not None:
            conf, basis = conf_rubric.go_annotation(props.get("evidence"))
        else:
            conf, basis = conf_rubric.reactome_pathway()
        basis["ontology"] = ontology
    elif relation == REL_VARIANT_OF:
        conf, basis = conf_rubric.variant_of(props)
    elif relation == REL_HAS_CONDITION:
        conf, basis = conf_rubric.clinvar_condition(props)
    elif relation in (REL_STUDIES, REL_EVALUATES):
        conf, basis = conf_rubric.study_edge(props)
    elif relation == REL_MENTIONS:
        conf, basis = conf_rubric.text_mention(props)
    elif relation == REL_HAS_FUNDED_PROJECT:
        conf, basis = conf_rubric.funded_project(props)
    elif relation in DRUG_RELATIONS or (nodes.get(row["source"]) or {}).get("type") == NodeType.Drug.value:
        conf, basis = conf_rubric.drug_edge(props)
    else:
        conf, basis = UNSCORED_CONFIDENCE, {"rule": "unscored_relation", "relation": relation, "source_db": row["source_db"]}
    basis.setdefault("backbone_relation", props.get("backbone_relation"))
    basis["source_db"] = row["source_db"]
    conf, basis = conf_rubric.apply_relation_conflict(conf, basis, props)
    if conf_rubric.has_relation_conflict(props):
        labels.append(conf_rubric.RELATION_CONFLICT_LABEL)
    evidence_class = "inferred" if str(props.get("evidence_class") or "").lower() == "inferred" else "observed"
    if evidence_class == "inferred":
        conf = conf_rubric.cap_inferred(conf)
        labels.append(conf_rubric.INFERRED_LABEL)
    return conf, basis, labels, evidence_class


def finalize_edges(edges: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Sort by (relation, source, target, source_db), assign ``e_000001...``, drop ``source_db``
    and round the confidence. Deterministic for the same input set."""
    ordered = sorted(edges, key=lambda e: (e["relation"], e["source"], e["target"], str(e.get("source_db", ""))))
    out: list[dict[str, Any]] = []
    for i, e in enumerate(ordered, start=1):
        d = {k: v for k, v in e.items() if k != "source_db"}
        d["id"] = f"e_{i:06d}"
        d["confidence"] = _round(d["confidence"])
        out.append(d)
    return out


def _coverage_row(row: Any) -> dict[str, Any]:
    """A coverage row given as a dict or an ``(entity_id, source, query, date, hits)`` tuple."""
    if isinstance(row, Mapping):
        return {"entity_id": str(row["entity_id"]), "source": str(row["source"]), "query": str(row["query"]), "date": str(row["date"]), "hits": int(row["hits"])}
    entity, source, query, date, hits = row
    return {"entity_id": str(entity), "source": str(source), "query": str(query), "date": str(date), "hits": int(hits or 0)}


def _group_coverage(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, str, str]] = set()
    for r in rows:
        key = (str(r["entity_id"]), str(r["source"]), str(r["query"]), str(r["date"]))
        if key in seen:
            continue
        seen.add(key)
        grouped[key[0]].append({"source": r["source"], "query": r["query"], "date": r["date"], "hits": int(r["hits"])})
    return {k: {"searched": grouped[k]} for k in sorted(grouped)}


def seed_resolution(store: Any, seeds: Mapping[str, Any]) -> dict[str, Any]:
    """How each seed NAME resolved against the store: genes by ``props.symbol``, disease and drug
    terms by case-insensitive label / synonym match (for the build report, hard rule 2)."""
    genes: dict[str, list[str]] = {}
    gene_nodes = list(store.iter_nodes(NodeType.Gene))
    for symbol in seeds.get("genes") or []:
        genes[str(symbol)] = sorted(n["id"] for n in gene_nodes if (n.get("props") or {}).get("symbol") == symbol or n.get("label") == symbol)
    diseases = {t: [d for d, _ in hits] for t, hits in mechanisms.resolve_terms(store.iter_nodes(NodeType.Disease), seeds.get("disease_terms") or []).items()}
    drugs = {t: [d for d, _ in hits] for t, hits in mechanisms.resolve_terms(store.iter_nodes(NodeType.Drug), seeds.get("drug_terms") or []).items()}
    return {
        "genes": genes,
        "disease_terms": diseases,
        "drug_terms": drugs,
        "unresolved": {
            "genes": sorted(k for k, v in genes.items() if not v),
            "disease_terms": sorted(k for k, v in diseases.items() if not v),
            "drug_terms": sorted(k for k, v in drugs.items() if not v),
        },
    }


# --------------------------------------------------------------------------------------
# edges keyed by (source, target, relation)
# --------------------------------------------------------------------------------------


def _merge_edge(edges: dict[tuple[str, str, str], dict[str, Any]], edge: dict[str, Any]) -> dict[str, Any]:
    """Add ``edge`` to the triple-keyed dict; an existing edge with the same (source, target,
    relation) absorbs it: sources appended, the stronger evidence class and confidence kept, the
    second basis recorded under ``also_asserted_by``, claim ids and labels unioned."""
    key = (edge["source"], edge["target"], edge["relation"])
    existing = edges.get(key)
    if existing is None:
        edge.setdefault("supporting", [])
        edge.setdefault("contradicting", [])
        edge.setdefault("attributes", {})
        edges[key] = edge
        return edge
    for s in edge.get("sources") or []:
        if s not in existing["sources"]:
            existing["sources"].append(s)
    existing["confidence_basis"].setdefault("also_asserted_by", []).append(
        {"source_db": edge.get("source_db"), "evidence_class": edge["evidence_class"], "confidence": edge["confidence"], "basis": edge["confidence_basis"]}
    )
    if EVIDENCE_CLASS_RANK[edge["evidence_class"]] > EVIDENCE_CLASS_RANK[existing["evidence_class"]]:
        existing["evidence_class"] = edge["evidence_class"]
    existing["confidence"] = max(float(existing["confidence"]), float(edge["confidence"]))
    if existing["evidence_class"] == "inferred":
        existing["confidence"] = conf_rubric.cap_inferred(existing["confidence"])
    existing["supporting"] = _dedupe([*existing.get("supporting", []), *edge.get("supporting", [])])
    existing["contradicting"] = _dedupe([*existing.get("contradicting", []), *edge.get("contradicting", [])])
    attrs = existing.setdefault("attributes", {})
    new_attrs = edge.get("attributes") or {}
    labels = _dedupe([*attrs.get("labels", []), *new_attrs.get("labels", [])])
    if existing["evidence_class"] != "inferred":
        labels = [l for l in labels if l != conf_rubric.INFERRED_LABEL]
    if labels:
        attrs["labels"] = labels
    for k, v in new_attrs.items():
        if k != "labels":
            attrs.setdefault(k, v)
    if existing["status"] == "inferred_unsupported" and edge["status"] != "inferred_unsupported":
        existing["status"] = edge["status"]
        attrs.pop("reason", None)
    return existing


# --------------------------------------------------------------------------------------
# claims
# --------------------------------------------------------------------------------------


def _load_integrate_claims() -> Callable[..., Any] | None:
    """``pipeline.graph.evidence.integrate_claims`` when that module exists, else ``None``."""
    try:
        module = importlib.import_module(EVIDENCE_MODULE)
    except ImportError:
        return None
    fn = getattr(module, "integrate_claims", None)
    return fn if callable(fn) else None


def _legacy_attach(
    nodes: dict[str, dict[str, Any]],
    edges: dict[tuple[str, str, str], dict[str, Any]],
    bundle: Any,
    stamp: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Without :mod:`pipeline.graph.evidence`: Paper nodes from the bundle, and a claim whose
    (subject, predicate, object) is an existing edge's (source, relation, target) attaches to it."""
    papers = {p.id: p for p in bundle.papers}
    for p in bundle.papers:
        nodes.setdefault(p.id, p.model_dump(mode="json"))
    by_triple: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for c in bundle.claims:
        by_triple[(c.subject, c.object, c.predicate)].append(c)  # the edge key order: (source, target, relation)
    attached = 0
    touched = 0
    for key, matching in by_triple.items():
        edge = edges.get(key)
        if edge is None:
            continue
        sup = [c for c in matching if c.polarity.value == "supports"]
        con = [c for c in matching if c.polarity.value == "contradicts"]
        edge["supporting"] = _dedupe([*edge["supporting"], *(c.id for c in sup)])
        edge["contradicting"] = _dedupe([*edge["contradicting"], *(c.id for c in con)])
        conf, cbasis = conf_rubric.edge_confidence_from_claims(sup, con, observed_confidence=edge["confidence"])
        edge["confidence"] = conf
        edge["confidence_basis"] = {**edge["confidence_basis"], "claims": cbasis}
        for s in mechanisms._claim_sources(matching, papers, bundle.extracted_at):
            if s not in edge["sources"]:
                edge["sources"].append(s)
        attached += len(matching)
        touched += 1
    claims = [c.model_dump(mode="json") for c in bundle.claims]
    report = {"how": f"legacy triple match ({EVIDENCE_MODULE} absent)", "claims_in_bundle": len(bundle.claims), "claims_matched_an_edge": attached, "edges_touched": touched}
    return claims, report


def _as_dict(obj: Any) -> dict[str, Any]:
    """A plain dict for a Node / Claim / Edge given as a dict or a pydantic model."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return dict(obj)


def _integrate_claims(
    nodes: dict[str, dict[str, Any]],
    edges: dict[tuple[str, str, str], dict[str, Any]],
    bundle: Any,
    stamp: str,
    *,
    investigator_min_papers: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """``(claims, evidence_report, coverage_rows)``: the evidence module when importable (design
    item A), else the legacy triple match. Additions it returns are merged into ``nodes`` / ``edges``.
    ``scrub`` is the shared DOSE scrubber (the evidence module runs its own e-mail / phone pass and
    counts the replacements for the report); every string is scrubbed again by :func:`scrub_text`
    on export. ``investigator_min_papers`` is the export cap on paper-derived Investigators, passed
    through so the evidence report counts what is actually exported."""
    integrate = _load_integrate_claims()
    if integrate is None:
        claims, report = _legacy_attach(nodes, edges, bundle, stamp)
        return claims, report, []
    result = integrate(nodes, edges, bundle, today=stamp[:10], scrub=scrub_doses, investigator_min_papers=max(1, int(investigator_min_papers)))
    for pid, paper in (getattr(result, "paper_nodes", None) or {}).items():
        paper = _as_dict(paper)
        existing = nodes.get(pid)
        if existing is None:
            nodes[pid] = paper
        else:
            existing["props"] = {**existing.get("props", {}), **(paper.get("props") or {})}
    for iid, inv in (getattr(result, "investigator_nodes", None) or {}).items():
        inv = _as_dict(inv)
        existing = nodes.get(iid)
        if existing is None:
            nodes[iid] = inv
        else:  # the exact same person id from RePORTER and the papers: one node, merged props
            existing["props"] = {**existing.get("props", {}), **(inv.get("props") or {})}
            existing["synonyms"] = _dedupe([*existing.get("synonyms", []), *(inv.get("synonyms") or [])])
    for new_edge in (getattr(result, "new_edges", None) or {}).values():
        e = _as_dict(new_edge)
        e.pop("id", None)
        e.setdefault("source_db", mechanisms.CLAIMS_SOURCE)
        _merge_edge(edges, e)
    claims = [_as_dict(c) for c in (getattr(result, "claims", None) or [])]
    coverage = [_coverage_row(r) for r in (getattr(result, "coverage_rows", None) or [])]
    report = {"how": f"{EVIDENCE_MODULE}.integrate_claims", **dict(getattr(result, "report", None) or {})}
    return claims, report, coverage


def _resolve_claim_variants(claims: list[dict[str, Any]], nodes: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    """A claim whose ``variant`` is not a Variant node keeps its evidence with ``variant = ""``;
    the raw value is returned for the report (the contract requires a Variant node id)."""
    unresolved: list[dict[str, str]] = []
    for c in claims:
        v = str(c.get("variant") or "")
        if v and (nodes.get(v) or {}).get("type") != NodeType.Variant.value:
            unresolved.append({"id": str(c["id"]), "variant": v})
            c["variant"] = ""
    return unresolved


def _reconcile_claim_edges(
    edges: dict[tuple[str, str, str], dict[str, Any]], claims_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Make every edge that carries claims satisfy the contract: claim ids deduplicated and
    restricted to existing claims of the right polarity, status / labels from
    :func:`pipeline.graph.confidence.status_from_claims` (contested iff both sides), an inferred
    edge that gained a SUPPORTING claim becomes ``extracted`` (one with only contradicting claims
    stays inferred and lists them), ``curated_unverified`` is dropped once a supporting claim backs
    an extracted edge, an extracted edge left without claims is removed."""
    removed_ids = 0
    dropped_edges: list[dict[str, str]] = []
    for key in list(edges):
        edge = edges[key]
        sup_raw, con_raw = list(edge.get("supporting") or []), list(edge.get("contradicting") or [])
        if not sup_raw and not con_raw:
            if edge["evidence_class"] == "extracted":
                dropped_edges.append({"source": key[0], "target": key[1], "relation": key[2], "reason": "extracted edge without claims"})
                del edges[key]
            continue
        sup = _dedupe(i for i in sup_raw if (claims_by_id.get(i) or {}).get("polarity") == "supports")
        con = _dedupe(i for i in con_raw if (claims_by_id.get(i) or {}).get("polarity") == "contradicts" and i not in sup)
        removed_ids += len(sup_raw) + len(con_raw) - len(sup) - len(con)
        edge["supporting"], edge["contradicting"] = sup, con
        if not sup and not con:
            if edge["evidence_class"] == "extracted":
                dropped_edges.append({"source": key[0], "target": key[1], "relation": key[2], "reason": "extracted edge whose claims are not in claims[]"})
                del edges[key]
            else:
                edge["status"] = "supported"
            continue
        attrs = edge.setdefault("attributes", {})
        status, labels, status_basis = conf_rubric.status_from_claims([claims_by_id[i] for i in sup], [claims_by_id[i] for i in con])
        if edge["evidence_class"] == "inferred":
            if sup:  # a supporting claim turns an inferred edge into an extracted one
                edge["evidence_class"] = "extracted"
                attrs["labels"] = [l for l in attrs.get("labels", []) if l != conf_rubric.INFERRED_LABEL]
            else:  # only contradicting claims: it stays inferred (contract: status supported), the claims listed
                status = "supported"
                labels = [l for l in labels if l != "small_n"]
        if sup and edge["evidence_class"] == "extracted":  # a supporting claim verifies a curated seed
            attrs["labels"] = [l for l in attrs.get("labels", []) if l != mechanisms.CURATED_LABEL]
        edge["status"] = status
        kept = [l for l in attrs.get("labels", []) if l not in conf_rubric.CLAIM_LABELS]
        merged = kept + [l for l in labels if l not in kept]
        if merged:
            attrs["labels"] = merged
        else:
            attrs.pop("labels", None)
        if "preprint" in labels:
            attrs["preprint"] = True
        edge["confidence_basis"]["claim_status"] = status_basis
    return {"claim_ids_removed_from_edges": removed_ids, "edges_dropped_without_claims": dropped_edges}


def _drop_claims(
    edges: dict[tuple[str, str, str], dict[str, Any]], claims: list[dict[str, Any]], drop: Mapping[str, str]
) -> list[dict[str, str]]:
    """Remove the claims in ``drop`` (id -> reason) from ``claims`` and from every edge."""
    if not drop:
        return []
    claims[:] = [c for c in claims if c["id"] not in drop]
    for edge in edges.values():
        edge["supporting"] = [i for i in edge.get("supporting") or [] if i not in drop]
        edge["contradicting"] = [i for i in edge.get("contradicting") or [] if i not in drop]
    return [{"id": cid, "reason": reason} for cid, reason in drop.items()]


def _normalize_ws(s: str) -> str:
    return " ".join(s.split())


# --------------------------------------------------------------------------------------
# patient groups and registries (pipeline/seeds/patient_groups.yaml)
# --------------------------------------------------------------------------------------


def _seed_terms(raw: Any) -> list[str]:
    """``"a / b (umbrella)"`` -> ``["a", "b (umbrella)"]`` (each part stripped)."""
    if not isinstance(raw, str):
        return []
    return [part.strip() for part in raw.split(" / ") if part.strip()]


def _term_candidates(term: str) -> list[str]:
    """The term as written, then without a trailing parenthetical ("(umbrella)", "(SCID)")."""
    stripped = _TRAILING_PAREN.sub("", term).strip()
    return _dedupe([term, stripped] if stripped and stripped != term else [term])


def normalize_mechanism_term(text: Any) -> str:
    """The form a seed term and a Mechanism label are compared in: a trailing parenthetical
    ("(umbrella)") dropped, lower-case, hyphens / underscores as spaces, whitespace collapsed and
    the tokens ``gof`` / ``lof`` / ``dn`` expanded (:data:`MECHANISM_TOKEN_EXPANSIONS`), so
    "MOCKGENE1 GoF" equals "MOCKGENE1 gain of function"."""
    t = _TRAILING_PAREN.sub("", str(text or "")).strip().lower()
    t = re.sub(r"[-_]+", " ", t)
    return " ".join(MECHANISM_TOKEN_EXPANSIONS.get(tok, tok) for tok in t.split())


def patient_group_options(seeds: Mapping[str, Any] | None) -> dict[str, Any]:
    """The ``patient_groups:`` knobs (``descendant_depth``, ``max_descendants_per_term``) from the
    seed dict over :data:`DEFAULT_PATIENT_GROUP_OPTIONS`; ``pipeline.export`` merges the
    cluster.yaml section into the patient-group seed dict, so both carry them."""
    raw = seeds.get("patient_groups") if isinstance(seeds, Mapping) and isinstance(seeds.get("patient_groups"), Mapping) else {}
    out = dict(DEFAULT_PATIENT_GROUP_OPTIONS)
    for key in DEFAULT_PATIENT_GROUP_OPTIONS:
        if raw.get(key) is not None:
            out[key] = max(0, int(raw[key]))
    return out


def _org_sources(url: str, name: str, retrieved_at: str) -> list[dict[str, str]]:
    if url.startswith(("http://", "https://")):
        return [{"kind": "url", "id": url, "date": retrieved_at}]
    return [{"kind": "db", "id": f"{PATIENT_GROUP_SOURCE_DB} {name}", "date": retrieved_at}]


def _org_edge(
    org: Mapping[str, Any], target: str, relation: str, conf: float, basis: dict[str, Any], labels: Sequence[str] = (), *, evidence_class: str = "observed"
) -> dict[str, Any]:
    """An Org -> ``target`` edge sourced from the organisation's verified URL: observed for an exact
    match; for a rule-derived route (``evidence_class="inferred"``) the confidence is capped at
    :data:`conf_rubric.INFERRED_CAP` and :data:`conf_rubric.INFERRED_LABEL` joins the labels."""
    props = org.get("props") or {}
    attributes: dict[str, Any] = {"scope": props["scope"]} if props.get("scope") else {}
    edge_labels = list(labels)
    if evidence_class == "inferred":
        conf = conf_rubric.cap_inferred(conf)
        if conf_rubric.INFERRED_LABEL not in edge_labels:
            edge_labels.append(conf_rubric.INFERRED_LABEL)
    if edge_labels:
        attributes["labels"] = edge_labels
    return {
        "source": org["id"],
        "target": target,
        "relation": relation,
        "source_db": PATIENT_GROUP_SOURCE_DB,
        "evidence_class": evidence_class,
        "confidence": _round(conf),
        "confidence_basis": basis,
        "status": "supported",
        "sources": _org_sources(str(props.get("url") or ""), str(org.get("label") or ""), str(props.get("verified_retrieved_at") or "")),
        "supporting": [],
        "contradicting": [],
        "attributes": attributes,
    }


def _org_relation(org: Mapping[str, Any]) -> str:
    return REL_COVERS if org.get("type") == NodeType.Registry.value else REL_SUPPORTS_COMMUNITY


def _mechanism_index(nodes: Mapping[str, Mapping[str, Any]]) -> dict[str, list[tuple[str, str, str]]]:
    """normalised label -> [(mechanism id, label as written, field)] over the Mechanism nodes."""
    index: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for n in nodes.values():
        if n.get("type") != NodeType.Mechanism.value:
            continue
        for field_name, text in (("label", n.get("label")), *(("synonyms", s) for s in n.get("synonyms") or [])):
            key = normalize_mechanism_term(text)
            if key:
                index[key].append((str(n["id"]), str(text), field_name))
    return index


def build_patient_groups(seeds_pg: Mapping[str, Any] | None, nodes: Mapping[str, Mapping[str, Any]], *, today: str) -> PatientGroupResult:
    """Nodes, edges, coverage rows and the meta source record for the verified patient-group /
    registry seed file (see the module docstring, step 5). ``nodes`` supplies the Disease nodes
    the seed terms are resolved against and the Mechanism nodes a term that matches no disease is
    tried against by normalised label (:func:`normalize_mechanism_term`). The disease edges that
    follow from a mechanism match or from an umbrella disease's descendants are added by
    :func:`propagate_patient_groups` once the export is pruned."""
    out = PatientGroupResult()
    if not isinstance(seeds_pg, Mapping) or not any(k in seeds_pg for k in ("groups", "searches")):
        out.report = {"status": "no patient_groups seeds"}
        return out
    meta = seeds_pg.get("meta") if isinstance(seeds_pg.get("meta"), Mapping) else {}
    seed_date = str(meta.get("date") or "").strip() or today
    diseases = [n for n in nodes.values() if n.get("type") == NodeType.Disease.value]
    mech_index = _mechanism_index(nodes)
    by_kind: Counter[str] = Counter()
    resolved_terms = unresolved_terms = mechanism_terms = 0
    for row in seeds_pg.get("groups") or []:
        if not isinstance(row, Mapping) or not str(row.get("name") or "").strip():
            continue
        name = str(row["name"]).strip()
        node_id = f"org:{slugify(name)}"
        kind = str(row.get("kind") or "patient_group").strip()
        node_type = NodeType.Registry.value if kind == "registry" else NodeType.PatientGroup.value
        verified = row.get("verified") if isinstance(row.get("verified"), Mapping) else {}
        retrieved_at = str(verified.get("retrieved_at") or seed_date)
        url = str(row.get("url") or "").strip()
        props: dict[str, Any] = {
            "url": url,
            "kind": kind,
            "scope": row.get("scope"),
            "verified_retrieved_at": retrieved_at,
            "method": verified.get("method"),
            "note": _truncate(_normalize_ws(str(verified.get("note") or "")), NOTE_MAX_CHARS),
            "source_of_claim": _normalize_ws(str(row.get("source_of_claim") or "")),
        }
        org = {"id": node_id, "type": node_type, "label": name, "synonyms": [], "props": props}
        out.nodes[node_id] = org
        relation = _org_relation(org)
        by_kind[node_type] += 1
        for raw_term in row.get("diseases") or []:
            for term in _seed_terms(raw_term):
                hits: list[tuple[str, str]] = []
                matched = term
                for candidate in _term_candidates(term):
                    hits = mechanisms.resolve_terms(diseases, [candidate])[candidate]
                    if hits:
                        matched = candidate
                        break
                out.coverage.append({"entity_id": node_id, "source": PATIENT_GROUP_TERM_SOURCE, "query": term, "date": retrieved_at, "hits": len(hits)})
                out.reached.setdefault((node_id, term), set())
                if hits:
                    resolved_terms += 1
                    for disease_id, how in hits:
                        out.reached[(node_id, term)].add(disease_id)
                        existing = next((e for e in out.edges if e["source"] == node_id and e["target"] == disease_id), None)
                        if existing is not None:  # two seed terms naming the same disease: one edge, both terms recorded
                            existing["confidence_basis"]["seed_terms"] = _dedupe([*existing["confidence_basis"]["seed_terms"], term])
                            continue
                        conf, basis = conf_rubric.community_edge(matched, url, retrieved_at)
                        basis["matched_field"] = how
                        basis["seed_terms"] = [term]
                        out.edges.append(_org_edge(org, disease_id, relation, conf, basis))
                    continue
                # no Disease carries the term: a Mechanism label in the same normalised form?
                mech_hits = _dedupe(mech_index.get(normalize_mechanism_term(term), []))
                if not mech_hits:
                    unresolved_terms += 1
                    continue
                mechanism_terms += 1
                # filled in by propagate_patient_groups: diseases reached through the mechanism
                out.coverage.append({"entity_id": node_id, "source": PATIENT_GROUP_TERM_SOURCE, "query": f"{term} [resolution: {RESOLUTION_MECHANISM_LABEL}]", "date": retrieved_at, "hits": 0})
                out.reached_by.setdefault((node_id, term, RESOLUTION_MECHANISM_LABEL), set())
                for mech_id, label, how in mech_hits:
                    existing = next((e for e in out.edges if e["source"] == node_id and e["target"] == mech_id), None)
                    if existing is not None:
                        existing["confidence_basis"]["seed_terms"] = _dedupe([*existing["confidence_basis"]["seed_terms"], term])
                        continue
                    basis = {
                        "rule": "community_edge",
                        "matched_term": term,
                        "matched_label": label,
                        "matched_field": how,
                        "resolution": RESOLUTION_MECHANISM_LABEL,
                        "normalized": normalize_mechanism_term(term),
                        "url": url,
                        "retrieved_at": retrieved_at,
                        "seed_terms": [term],
                    }
                    out.edges.append(_org_edge(org, mech_id, relation, COMMUNITY_MECHANISM_CONFIDENCE, basis))
    searches = 0
    for s in seeds_pg.get("searches") or []:
        if not isinstance(s, Mapping):
            continue
        directory = str(s.get("directory") or "directory").strip()
        query = str(s.get("query") or "").strip() or "(no query recorded)"
        url = str(s.get("url") or "").strip()
        hits = s.get("hits")
        text = f"{query} ({url})" if url else query
        if hits is None:
            text += " [not executable]"
            hits = 0
        out.coverage.append({"entity_id": f"{PATIENT_GROUP_SEARCH_PREFIX}{directory}", "source": directory, "query": text, "date": str(s.get("date") or seed_date), "hits": int(hits)})
        searches += 1
    out.source = {"name": "patient group seeds: pipeline/seeds/patient_groups.yaml", "version": "", "retrieved_at": seed_date, "license": PATIENT_GROUP_SEED_LICENSE}
    out.report = {
        "groups": len(out.nodes),
        "by_type": dict(sorted(by_kind.items())),
        "edges": len(out.edges),
        "edges_by_resolution": _edges_by_resolution(out.edges, nodes),
        "terms_resolved": resolved_terms,
        "terms_resolved_via_mechanism": mechanism_terms,
        "terms_unresolved": unresolved_terms,
        "searches_logged": searches,
        "zero_hit_searches": sum(1 for r in out.coverage if r["entity_id"].startswith(PATIENT_GROUP_SEARCH_PREFIX) and r["hits"] == 0),
    }
    return out


def _edges_by_resolution(edges: Iterable[Mapping[str, Any]], nodes: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """Org -> Disease edges by route (``direct`` / ``via_mechanism`` / ``via_ancestor``) plus the
    Org -> Mechanism edges (``mechanism``)."""
    counts: Counter[str] = Counter({"direct": 0, "mechanism": 0, VIA_MECHANISM_LABEL: 0, VIA_ANCESTOR_LABEL: 0})
    for e in edges:
        labels = (e.get("attributes") or {}).get("labels") or []
        if nodes.get(e["target"], {}).get("type") == NodeType.Mechanism.value:
            counts["mechanism"] += 1
        elif VIA_MECHANISM_LABEL in labels:
            counts[VIA_MECHANISM_LABEL] += 1
        elif VIA_ANCESTOR_LABEL in labels:
            counts[VIA_ANCESTOR_LABEL] += 1
        else:
            counts["direct"] += 1
    return dict(counts)


def _descendants(roots: Iterable[str], children_of: Mapping[str, Sequence[str]], levels: int) -> dict[str, tuple[int, str]]:
    """Breadth-first down the ``is_a`` tree from ``roots``: ``{descendant: (depth, root)}`` with
    the shortest depth (ties: the alphabetically first root), roots themselves excluded."""
    roots = sorted(set(roots))
    seen: dict[str, tuple[int, str]] = {r: (0, r) for r in roots}
    frontier = [(r, r) for r in roots]
    for depth in range(1, max(0, levels) + 1):
        nxt: list[tuple[str, str]] = []
        for node, root in frontier:
            for child in sorted(children_of.get(node, ())):
                if child not in seen:
                    seen[child] = (depth, root)
                    nxt.append((child, root))
        frontier = nxt
        if not frontier:
            break
    return {d: v for d, v in seen.items() if v[0] > 0}


def propagate_patient_groups(
    result: PatientGroupResult,
    nodes: Mapping[str, Mapping[str, Any]],
    edges: dict[tuple[str, str, str], dict[str, Any]],
    *,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Module docstring step 5b: link every organisation to the diseases that carry a mechanism it
    names (``via_mechanism``) and to the descendants of every disease it names (``via_ancestor``),
    over the EXPORTED ``nodes`` / ``edges`` (call after :func:`prune_graph`; propagation never
    pulls a disease into the export). New edges go into ``edges`` and ``result.edges``; the seed
    terms' coverage rows are rewritten with the diseases reached and a row per route is added;
    ``result.report`` gains ``edges_by_resolution`` and the cap report. Returns the report."""
    opts = {**DEFAULT_PATIENT_GROUP_OPTIONS, **{k: v for k, v in (options or {}).items() if v is not None}}
    depth_max = int(opts["descendant_depth"])
    cap = int(opts["max_descendants_per_term"])
    orgs = {oid: org for oid, org in result.nodes.items() if oid in nodes}
    disease_type, mech_type = NodeType.Disease.value, NodeType.Mechanism.value

    # the exported Disease -> Mechanism edges and the Disease is_a tree (child -> parent)
    mech_diseases: dict[str, list[tuple[str, float]]] = defaultdict(list)
    children_of: dict[str, list[str]] = defaultdict(list)
    for (s, t, rel), e in edges.items():
        if rel == REL_HAS_MECHANISM and nodes.get(s, {}).get("type") == disease_type and nodes.get(t, {}).get("type") == mech_type:
            mech_diseases[t].append((s, float(e["confidence"])))
        elif rel == REL_IS_A and nodes.get(s, {}).get("type") == disease_type and nodes.get(t, {}).get("type") == disease_type:
            children_of[t].append(s)

    def org_edges(target_type: str) -> list[dict[str, Any]]:
        return [e for e in result.edges if e["source"] in orgs and (e["source"], e["target"], e["relation"]) in edges and nodes.get(e["target"], {}).get("type") == target_type]

    def add(org: Mapping[str, Any], disease_id: str, relation: str, conf: float, basis: dict[str, Any], label: str, route: str) -> None:
        # both routes are rule-derived: inferred, capped at 0.5 (see COMMUNITY_RULE_EVIDENCE_CLASS)
        basis["rule_evidence_class_note"] = COMMUNITY_RULE_EVIDENCE_CLASS_NOTE
        new_edge = _org_edge(org, disease_id, relation, conf, basis, labels=[label], evidence_class=COMMUNITY_RULE_EVIDENCE_CLASS)
        _merge_edge(edges, new_edge)
        result.edges.append(new_edge)
        for term in basis.get("seed_terms") or []:
            result.reached[(org["id"], term)].add(disease_id)
            result.reached_by[(org["id"], term, route)].add(disease_id)

    # R-A: the diseases that carry a mechanism the organisation names
    for e in sorted(org_edges(mech_type), key=lambda e: (e["source"], e["target"])):
        org = orgs[e["source"]]
        for disease_id, mech_conf in sorted(mech_diseases.get(e["target"], [])):
            if (org["id"], disease_id, e["relation"]) in edges:
                continue  # the organisation already names this disease
            basis = {
                "rule": "community_edge",
                "matched_term": e["confidence_basis"]["matched_term"],
                "matched_label": e["confidence_basis"]["matched_label"],
                "resolution": RESOLUTION_MECHANISM_LABEL,
                "via_mechanism": e["target"],
                "disease_mechanism_edge_confidence": mech_conf,
                "url": e["confidence_basis"]["url"],
                "retrieved_at": e["confidence_basis"]["retrieved_at"],
                "seed_terms": list(e["confidence_basis"].get("seed_terms") or [e["confidence_basis"]["matched_term"]]),
            }
            add(org, disease_id, e["relation"], COMMUNITY_VIA_MECHANISM_CONFIDENCE, basis, VIA_MECHANISM_LABEL, RESOLUTION_MECHANISM_LABEL)

    # R-B: the descendants of every disease the organisation names, grouped per (org, matched term)
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in org_edges(disease_type):
        if VIA_ANCESTOR_LABEL in ((e.get("attributes") or {}).get("labels") or []):
            continue
        key = (e["source"], e["relation"], str(e["confidence_basis"].get("matched_term") or ""))
        g = groups.setdefault(key, {"roots": set(), "seed_terms": [], "url": e["confidence_basis"].get("url", ""), "retrieved_at": e["confidence_basis"].get("retrieved_at", "")})
        g["roots"].add(e["target"])
        g["seed_terms"] = _dedupe([*g["seed_terms"], *(e["confidence_basis"].get("seed_terms") or [])])
    cap_hits: list[dict[str, Any]] = []
    for (org_id, relation, matched_term), g in sorted(groups.items()):
        org = orgs[org_id]
        found = _descendants(g["roots"], children_of, depth_max)
        candidates = [(d, depth, root) for d, (depth, root) in sorted(found.items(), key=lambda x: (x[1][0], x[0])) if (org_id, d, relation) not in edges]
        if len(candidates) > cap:
            cap_hits.append({"org": org_id, "matched_term": matched_term, "candidates": len(candidates), "kept": cap})
            candidates = candidates[:cap]
        for disease_id, depth, root in candidates:
            basis = {
                "rule": "community_edge",
                "matched_term": matched_term,
                "resolution": RESOLUTION_ANCESTOR,
                "via_ancestor": root,
                "depth": depth,
                "url": g["url"],
                "retrieved_at": g["retrieved_at"],
                "seed_terms": list(g["seed_terms"]),
            }
            add(org, disease_id, relation, COMMUNITY_VIA_ANCESTOR_CONFIDENCE - COMMUNITY_VIA_ANCESTOR_DEPTH_STEP * (depth - 1), basis, VIA_ANCESTOR_LABEL, RESOLUTION_ANCESTOR)

    # coverage: each seed term counts every disease it reached; one row per route it was linked by
    rows = {(r["entity_id"], r["query"]): r for r in result.coverage if r["source"] == PATIENT_GROUP_TERM_SOURCE}
    for (org_id, term), diseases in result.reached.items():
        if (org_id, term) in rows:
            rows[(org_id, term)]["hits"] = len(diseases)
    for (org_id, term, route), diseases in sorted(result.reached_by.items()):
        query = f"{term} [resolution: {route}]"
        row = rows.get((org_id, query))
        if row is not None:
            row["hits"] = len(diseases)
        elif diseases and (org_id, term) in rows:
            result.coverage.append({**rows[(org_id, term)], "query": query, "hits": len(diseases)})
    result.report.update(
        {
            "edges": len(result.edges),
            "edges_by_resolution": _edges_by_resolution(result.edges, nodes),
            "descendant_depth": depth_max,
            "max_descendants_per_term": cap,
            "descendant_cap_hits": cap_hits,
        }
    )
    return result.report


# --------------------------------------------------------------------------------------
# pruning (design item G / H)
# --------------------------------------------------------------------------------------


def _ancestors(start: Iterable[str], parents_of: Mapping[str, Sequence[str]], levels: int) -> set[str]:
    seen = set(start)
    frontier = set(start)
    out: set[str] = set()
    for _ in range(max(0, levels)):
        nxt: set[str] = set()
        for node in frontier:
            for parent in parents_of.get(node, ()):
                if parent not in seen:
                    seen.add(parent)
                    nxt.add(parent)
        out |= nxt
        frontier = nxt
        if not frontier:
            break
    return out


def _closure(start: Iterable[str], parents_of: Mapping[str, Sequence[str]]) -> set[str]:
    seen = set(start)
    frontier = list(start)
    while frontier:
        node = frontier.pop()
        for parent in parents_of.get(node, ()):
            if parent not in seen:
                seen.add(parent)
                frontier.append(parent)
    return seen


def _assoc_props(edge: Mapping[str, Any]) -> dict[str, Any]:
    """The raw inputs the inclusion rule reads, recovered from the edge's confidence basis."""
    basis = edge.get("confidence_basis") or {}
    return {"evidence_score": basis.get("evidence_score"), "sources": {"direct": list(basis.get("sources_direct") or [])}}


def _anchors(edge: Mapping[str, Any]) -> bool:
    """Does this edge count as evidence that keeps its endpoints in the export?"""
    if edge.get("evidence_class") == "inferred":
        return False
    if edge.get("evidence_class") == "extracted" or edge.get("supporting") or edge.get("contradicting"):
        return True
    return edge["relation"] not in BACKBONE_RELATIONS


def prune_graph(
    nodes: dict[str, dict[str, Any]],
    edges: dict[tuple[str, str, str], dict[str, Any]],
    seeds: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    *,
    claim_refs: set[str],
    today: str,
) -> PruneResult:
    """Reduce ``nodes`` / ``edges`` in place to the compact export (module docstring, step 6)."""
    t = {**DEFAULT_EXPORT_THRESHOLDS, **{k: v for k, v in thresholds.items() if v is not None}}
    if "max_genes_per_disease" not in thresholds and thresholds.get("max_other_genes_per_disease") is not None:
        t["max_genes_per_disease"] = thresholds["max_other_genes_per_disease"]
    rule_text = optimuskg.inclusion_rule_text(t)
    before_nodes = Counter(n["type"] for n in nodes.values())
    before_edges = Counter(k[2] for k in edges)

    def typed(node_type: str) -> set[str]:
        return {nid for nid, n in nodes.items() if n["type"] == node_type}

    diseases, genes, drugs = typed("Disease"), typed("Gene"), typed("Drug")
    variants, phenotypes, pathways = typed("Variant"), typed("Phenotype"), typed("Pathway")
    mechs, investigators = typed("Mechanism"), typed("Investigator")
    seed_symbols = {str(s) for s in seeds.get("genes") or []}
    seed_genes = {g for g in genes if (nodes[g]["props"].get("symbol") or nodes[g]["label"]) in seed_symbols}

    # 1. the disease-gene inclusion rule on every backbone associated_with edge
    assoc_ok: dict[tuple[str, str, str], bool] = {}
    below: Counter[str] = Counter()
    for key, e in edges.items():
        if key[2] != REL_ASSOCIATED_WITH or key[0] not in diseases or key[1] not in genes:
            continue
        if key[1] in seed_genes or _anchors(e):
            assoc_ok[key] = True
            continue
        ok = optimuskg.disease_gene_included(_assoc_props(e), t)
        assoc_ok[key] = ok
        if not ok:
            below[key[0]] += 1

    # 2. diseases anchored by a seed gene: rule + per-gene cap by score
    by_seed_gene: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (d, g, _rel), e in edges.items():
        if g in seed_genes and d in diseases and _rel == REL_ASSOCIATED_WITH and optimuskg.disease_gene_included(_assoc_props(e), t):
            by_seed_gene[g].append((_as_float((e.get("confidence_basis") or {}).get("evidence_score")) or 0.0, d))
    cap_d = int(t["max_diseases_per_seed_gene"])
    direct: set[str] = set()
    capped_away = 0
    for g, ranked in by_seed_gene.items():
        ranked.sort(key=lambda x: (-x[0], x[1]))
        direct |= {d for _, d in ranked[:cap_d]}
        capped_away += max(0, len(ranked) - cap_d)
    # 3. seed terms, 4. observed / extracted evidence and asset edges anchor their endpoints
    term_hits = mechanisms.resolve_terms((nodes[d] for d in diseases), seeds.get("disease_terms") or [])
    term_diseases = {d for hits in term_hits.values() for d, _ in hits}
    # Evidence anchors its backbone endpoints (Disease, Gene, Drug, Phenotype, Pathway); a Variant
    # is anchored only by a claim (item H), never by its own ClinVar record edges; Study, Paper,
    # Investigator and patient-group nodes follow their own rules below.
    referenced: set[str] = set()
    claim_variants: set[str] = set()
    for (s, tg, _rel), e in edges.items():
        if not _anchors(e):
            continue
        for endpoint in (s, tg):
            node_type = nodes[endpoint]["type"]
            if node_type in ANCHORABLE_TYPES:
                referenced.add(endpoint)
            elif node_type == NodeType.Variant.value and (e["evidence_class"] == "extracted" or e.get("supporting") or e.get("contradicting")):
                claim_variants.add(endpoint)
    # A backbone node a claim RESOLVED to is referenced by evidence even when the claim attached to
    # no edge (its other side unresolved): claims[] must never name a node that is not in nodes[]
    # (design items G / H; ``claim_refs`` holds the raw and the resolved endpoints).
    claim_backbone = {x for x in claim_refs if x in nodes and nodes[x]["type"] in ANCHORABLE_TYPES}
    referenced |= claim_backbone
    anchors = direct | term_diseases | (referenced & diseases)
    # 5. ontology parents
    parents_of: dict[str, list[str]] = defaultdict(list)
    for (child, parent, rel) in edges:
        if rel == REL_IS_A and child in diseases and parent in diseases:
            parents_of[child].append(parent)
    keep_diseases = anchors | _ancestors(anchors, parents_of, int(t["include_parent_levels"]))
    # 6. genes: seed genes, evidence-referenced genes (a claim-bearing edge or a resolved claim
    # endpoint, like Variants in step 8), top N other genes per kept disease, and the gap probe
    # (module docstring step 6: the one node exported without edges, so its coverage stays visible)
    gap_probes = {g for g in genes if nodes[g]["props"].get(GAP_PROBE_PROP) is True}
    keep_genes = set(seed_genes) | (referenced & genes) | gap_probes
    cap_g = int(t["max_genes_per_disease"])
    others: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for key, ok in assoc_ok.items():
        d, g, _ = key
        if ok and d in keep_diseases and g not in seed_genes:
            others[d].append((_as_float((edges[key].get("confidence_basis") or {}).get("evidence_score")) or 0.0, g))
    genes_capped_away = 0
    for d, ranked in others.items():
        ranked.sort(key=lambda x: (-x[0], x[1]))
        keep_genes |= {g for _, g in ranked[:cap_g]}
        genes_capped_away += max(0, len(ranked) - cap_g)
    # 7. drugs: act on a seed gene, named by a seed term, or referenced by evidence (a claim-bearing
    # edge or a resolved claim endpoint, design item G "drugs referenced by claims")
    keep_drugs = referenced & drugs
    for (s, tg, _rel) in edges:
        if s in drugs and tg in seed_genes:
            keep_drugs.add(s)
    drug_hits = mechanisms.resolve_terms((nodes[d] for d in drugs), seeds.get("drug_terms") or [])
    keep_drugs |= {d for hits in drug_hits.values() for d, _ in hits}
    # 8. variants
    keep_variants = {v for v in variants if nodes[v]["props"].get("keep_in_graph") or nodes[v]["props"].get("has_somatic_block") or v in claim_refs}
    keep_variants |= claim_variants
    # 9. phenotypes annotated to kept diseases (any HPO aspect), referenced by evidence, plus ancestors
    keep_phenotypes = referenced & phenotypes
    phe_parents: dict[str, list[str]] = defaultdict(list)
    for (s, tg, rel) in edges:
        if rel in HPO_ANNOTATION_RELATIONS and s in keep_diseases and tg in phenotypes:
            keep_phenotypes.add(tg)
        elif rel == REL_IS_A and s in phenotypes and tg in phenotypes:
            phe_parents[s].append(tg)
    keep_phenotypes = _closure(keep_phenotypes, phe_parents)
    # 10. pathways of kept genes (GO processes only for seed genes)
    keep_pathways: set[str] = referenced & pathways
    go_dropped = 0
    for (s, tg, rel) in list(edges):
        if rel == REL_IN_PATHWAY and tg in pathways and s in keep_genes:
            if (nodes[tg]["props"].get("ontology") == GO_ONTOLOGY) and s not in seed_genes:
                go_dropped += 1
                del edges[(s, tg, rel)]
                continue
            keep_pathways.add(tg)
    # 11./12. everything else: Study, Paper, PatientGroup, Registry, Claim nodes stay; Mechanism and
    # Investigator nodes stay when an edge survives.
    always = {nid for nid, n in nodes.items() if n["type"] in {"Study", "Paper", "PatientGroup", "Registry", "Claim"}}
    keep = keep_diseases | keep_genes | keep_drugs | keep_variants | keep_phenotypes | keep_pathways | always
    min_papers = int(t["min_papers_per_investigator"])
    keep_orcid = bool(t.get("keep_orcid_investigators", True))
    few_papers = {
        i
        for i in investigators
        if isinstance(nodes[i]["props"].get("n_papers"), int)
        and nodes[i]["props"]["n_papers"] < min_papers
        and not (keep_orcid and str(nodes[i]["props"].get("orcid") or "").strip())
    }
    candidate = keep | mechs | (investigators - few_papers)
    survivors: dict[tuple[str, str, str], dict[str, Any]] = {}
    dropped_edges: Counter[str] = Counter()
    for key, e in edges.items():
        s, tg, rel = key
        if s not in candidate or tg not in candidate:
            dropped_edges[rel] += 1
            continue
        if rel == REL_ASSOCIATED_WITH and key in assoc_ok and not assoc_ok[key]:
            dropped_edges[rel] += 1
            continue
        survivors[key] = e
    touched = {x for key in survivors for x in key[:2]}
    keep |= (mechs | investigators) & touched
    final_edges = {k: e for k, e in survivors.items() if k[0] in keep and k[1] in keep}
    for key in survivors:
        if key not in final_edges:
            dropped_edges[key[2]] += 1

    reasons: dict[str, str] = {
        "Disease": "not anchored by a seed gene (inclusion rule / max_diseases_per_seed_gene), a seed term, an evidence edge or a claim",
        "Gene": "not a seed gene, not the gap probe, not referenced by evidence or a claim, beyond max_genes_per_disease or below the inclusion rule",
        "Drug": "no edge to a seed gene, no seed drug term, not referenced by evidence or a claim",
        "Variant": "not keep_in_graph, no somatic block, not referenced by a claim",
        "Phenotype": "not annotated to an exported disease (nor an ancestor of one)",
        "Pathway": "no exported member gene",
        "Mechanism": "no surviving edge",
        "Investigator": f"no surviving edge, or fewer than min_papers_per_investigator ({min_papers}) papers" + (" and no ORCID" if keep_orcid else ""),
    }
    dropped_nodes: Counter[str] = Counter()
    for nid in list(nodes):
        if nid not in keep:
            dropped_nodes[nodes[nid]["type"]] += 1
            del nodes[nid]
    edges.clear()
    edges.update(final_edges)

    coverage: list[dict[str, Any]] = []
    for d in sorted(below):
        if d in nodes:
            coverage.append({"entity_id": d, "source": PRUNE_SOURCE, "query": f"associated_with edges below the disease-gene inclusion rule ({rule_text}) not exported", "date": today, "hits": below[d]})
    for node_type, n in sorted(dropped_nodes.items()):
        coverage.append({"entity_id": PRUNE_ENTITY, "source": PRUNE_SOURCE, "query": f"{node_type} nodes not exported: {reasons.get(node_type, 'no surviving edge')}", "date": today, "hits": n})
    report = {
        "thresholds": {k: t[k] for k in DEFAULT_EXPORT_THRESHOLDS},
        "inclusion_rule": rule_text,
        "nodes_before": dict(sorted(before_nodes.items())),
        "nodes_after": dict(sorted(Counter(n["type"] for n in nodes.values()).items())),
        "nodes_dropped": {k: {"n": v, "reason": reasons.get(k, "no surviving edge")} for k, v in sorted(dropped_nodes.items())},
        "edges_before": dict(sorted(before_edges.items())),
        "edges_after": dict(sorted(Counter(k[2] for k in edges).items())),
        "edges_dropped_by_relation": dict(sorted(dropped_edges.items())),
        "associated_with_below_inclusion_rule": sum(below.values()),
        "diseases": {"direct_via_seed_genes": len(direct), "capped_away_per_seed_gene": capped_away, "term_matched": len(term_diseases), "anchored_by_evidence": len(referenced & diseases), "claim_referenced": len(claim_backbone & diseases), "kept": len(keep_diseases)},
        "genes": {"seed": len(seed_genes), "anchored_by_evidence": len(referenced & genes), "claim_referenced": len(claim_backbone & genes), "gap_probe": sorted(gap_probes), "capped_away_per_disease": genes_capped_away, "kept": len(keep_genes)},
        "drugs": {"kept": len(keep_drugs), "anchored_by_evidence": len(referenced & drugs), "claim_referenced": len(claim_backbone & drugs)},
        "variants": {"kept": len(keep_variants), "claim_referenced": len(claim_refs & variants)},
        "investigators": {"before": len(investigators), "below_min_papers": len(few_papers), "kept": len(investigators & keep)},
        "go_in_pathway_edges_dropped_non_seed_gene": go_dropped,
    }
    return PruneResult(coverage, report)


# --------------------------------------------------------------------------------------
# slimming
# --------------------------------------------------------------------------------------


def _author_names(authors: Any) -> list[str]:
    out: list[str] = []
    for a in authors if isinstance(authors, list) else []:
        name = a.get("name") if isinstance(a, Mapping) else a
        if isinstance(name, str) and name.strip() and name.strip() not in out:
            out.append(name.strip())
    return out


def _slim_project(project: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: project.get(k) for k in PROJECT_KEEP if k in project}
    agency = project.get("agency_ic_admin")
    if isinstance(agency, Mapping):
        out["agency"] = agency.get("abbreviation") or agency.get("name")
    pmids = project.get("pmids")
    if isinstance(pmids, list):
        out["pmids"] = pmids[:MAX_LIST_IDS]
    return out


def slim_node(node: dict[str, Any], scrub: Callable[[str], str] = scrub_text) -> dict[str, Any]:
    """Reduce ``node["props"]`` to :data:`EXPORT_PROPS_KEEP`, cap synonyms, scrub every string
    (label, synonyms, props). Mutates and returns ``node``."""
    node_type = str(node.get("type"))
    props: dict[str, Any] = dict(node.get("props") or {})
    keep = EXPORT_PROPS_KEEP.get(node_type)
    if keep is not None:
        if node_type == "Drug":
            for new_key, old_key in DRUG_PROP_ALIASES.items():
                if new_key not in props and old_key in props:
                    props[new_key] = props[old_key]
        if node_type == "Paper":
            if "authors" in props:  # the contract test wants plain names; the structured list fed the Investigator nodes
                props["authors"] = _author_names(props.get("authors"))
            if "pmid" not in props and str(node.get("id", "")).startswith("pmid:"):
                props["pmid"] = str(node["id"])[len("pmid:") :]
        if node_type == "Variant":
            symbols = props.get("gene_symbols")
            if "gene_symbol" not in props and isinstance(symbols, list) and len(symbols) == 1:
                props["gene_symbol"] = symbols[0]
        slim: dict[str, Any] = {}
        for key, limit in keep.items():
            if key not in props:
                continue
            value = scrub_tree(props[key], scrub)  # scrub BEFORE truncating, so a cut can never leave half an address
            if value is None:
                continue
            if isinstance(value, str) and limit:
                value = _truncate(value, limit)
            slim[key] = value
        if node_type == "Gene" and isinstance(slim.get("xrefs"), list):
            slim["xrefs"] = [x for x in slim["xrefs"] if isinstance(x, Mapping) and str(x.get("source") or "").lower() in GENE_XREF_SOURCES]
        if node_type == "Study" and isinstance(slim.get("eligibility"), Mapping):
            elig = dict(slim["eligibility"])
            criteria = elig.get("criteria")
            if isinstance(criteria, str) and len(criteria) > ELIGIBILITY_CRITERIA_MAX:
                elig["criteria"] = _truncate(criteria, ELIGIBILITY_CRITERIA_MAX)
                elig["truncated"] = True
            slim["eligibility"] = elig
        if node_type == "Investigator":
            if isinstance(slim.get("projects"), list):
                slim["projects"] = [_slim_project(p) for p in slim["projects"] if isinstance(p, Mapping)]
            for key in ("pmids", "papers"):
                if isinstance(slim.get(key), list):
                    slim[key] = slim[key][:MAX_LIST_IDS]
            if isinstance(slim.get("affiliations"), list):
                slim["affiliations"] = slim["affiliations"][:5]
        props = slim
    node["props"] = scrub_tree(props, scrub)
    node["label"] = scrub(str(node.get("label") or "")) or str(node.get("label") or "")
    synonyms = [scrub(s) for s in (node.get("synonyms") or []) if isinstance(s, str)]
    node["synonyms"] = _dedupe(s for s in synonyms if s.strip())[:MAX_SYNONYMS]
    return node


def _scrub_edge(edge: dict[str, Any], scrub: Callable[[str], str] = scrub_text) -> None:
    edge["attributes"] = scrub_tree(edge.get("attributes") or {}, scrub)
    edge["confidence_basis"] = scrub_tree(edge.get("confidence_basis") or {}, scrub)


def _round_floats(value: Any, decimals: int = BASIS_FLOAT_DECIMALS) -> Any:
    if isinstance(value, float):
        return round(value, decimals)
    if isinstance(value, Mapping):
        return {k: _round_floats(v, decimals) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_floats(v, decimals) for v in value]
    return value


def compact_edge(edge: dict[str, Any]) -> dict[str, Any]:
    """Byte hygiene that drops no evidence (the 8 MB budget of ``web/public/graph.json``):
    ``sources[].date`` keeps the retrieval DATE of a timestamp (the exact timestamps stay in
    ``meta.sources`` and in the build report), top-level ``confidence_basis`` entries whose value
    is ``None`` or empty (``[]``, ``{}``, ``""``) are removed (an absent key means the same), and
    basis floats are rounded to :data:`BASIS_FLOAT_DECIMALS` decimals (a raw ``evidence_score``
    keeps six decimals; ``confidence`` itself is rounded by :func:`finalize_edges`). Mutates and
    returns ``edge``."""
    for s in edge.get("sources") or []:
        m = _ISO_TIMESTAMP.match(str(s.get("date", "")))
        if m:
            s["date"] = m.group(1)
    basis = edge.get("confidence_basis") or {}
    edge["confidence_basis"] = {k: _round_floats(v) for k, v in basis.items() if v is not None and v != [] and v != {} and v != ""}
    return edge


# --------------------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------------------


def build(store: Any, seeds: Mapping[str, Any], claims_bundle: Any = None, *, now: str | None = None) -> BuildResult:
    """Build and validate the graph from ``store`` (see the module docstring).

    ``seeds`` is the cluster seed dict (``thresholds`` is used); it may carry ``"mechanisms"``
    (the mechanisms.yaml dict) for the curated fallback and ``"patient_groups"`` (the
    patient_groups.yaml dict). ``now`` (ISO timestamp) is injectable so tests get a fixed
    ``built_at``.
    """
    stamp = _now_iso(now)
    day = stamp[:10]
    thresholds = {**mechanisms.DEFAULT_THRESHOLDS, **DEFAULT_EXPORT_THRESHOLDS, **dict(seeds.get("thresholds") or {})}
    report: dict[str, Any] = {"built_at": stamp, "thresholds": thresholds}

    # -- nodes ---------------------------------------------------------------------
    store_nodes = {n["id"]: n for n in store.iter_nodes()}
    ic = store.hpo_ic()
    nodes: dict[str, dict[str, Any]] = {}
    for nid, n in store_nodes.items():
        props = dict(n.get("props") or {})
        if n["type"] == NodeType.Phenotype.value and nid in ic:
            props["ic"] = float(ic[nid]["ic"])
            props["ic_n_diseases"] = int(ic[nid]["n_diseases"])
            props["ic_n_total"] = int(ic[nid]["n_total"])
        nodes[nid] = {"id": nid, "type": n["type"], "label": n["label"], "synonyms": list(n.get("synonyms") or []), "props": props}

    # -- mechanisms ----------------------------------------------------------------
    mech = mechanisms.derive(store, seeds, claims_bundle, now=stamp)
    for m in mech.nodes:
        existing = nodes.get(m["id"])
        if existing is None:
            nodes[m["id"]] = m
        else:  # a loader already stored this mechanism node: keep its label, merge props
            existing["props"] = {**m["props"], **existing["props"]}
    report["mechanisms"] = mech.report

    # -- store edges ---------------------------------------------------------------
    srange = score_range(store)
    report["score_range"] = {"min": srange[0], "max": srange[1], "how": srange[2]}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    dangling: list[dict[str, str]] = []
    below_threshold = 0
    conflicts = 0
    for row in store.iter_edges():
        if row["source"] not in nodes or row["target"] not in nodes:
            dangling.append({"source": row["source"], "target": row["target"], "relation": row["relation"], "source_db": row["source_db"]})
            continue
        conf, basis, labels, evidence_class = _score_edge(row, nodes, srange[:2], thresholds)
        below_threshold += BELOW_THRESHOLD_LABEL in labels
        conflicts += conf_rubric.RELATION_CONFLICT_LABEL in labels
        attributes: dict[str, Any] = {}
        if row["relation"] == REL_ASSOCIATED_WITH:
            origin = mech.origins.get((row["source"], row["target"]))
            if origin:
                attributes["origin"] = origin
        if labels:
            attributes["labels"] = labels
        _merge_edge(
            edges,
            {
                "source": row["source"],
                "target": row["target"],
                "relation": row["relation"],
                "source_db": row["source_db"],
                "evidence_class": evidence_class,
                "confidence": conf,
                "confidence_basis": basis,
                "status": "supported",
                "sources": _edge_sources(row, nodes),
                "supporting": [],
                "contradicting": [],
                "attributes": attributes,
            },
        )
    for e in mech.edges:
        _merge_edge(edges, dict(e))

    # -- claims (design item A) ----------------------------------------------------
    claims: list[dict[str, Any]] = []
    evidence_report: dict[str, Any] = {"how": "no claims bundle"}
    evidence_coverage: list[dict[str, Any]] = []
    claim_refs: set[str] = set()
    if claims_bundle is not None:
        for c in claims_bundle.claims:
            claim_refs.update(x for x in (c.subject, c.object, c.variant) if x)
        # Paper-derived Investigators: the evidence module applies the export cap itself when the
        # ORCID exemption is off (so its report counts what is exported); with the exemption on,
        # prune_graph applies the cap and the exemption together.
        min_papers = 1 if thresholds.get("keep_orcid_investigators", True) else int(thresholds.get("min_papers_per_investigator") or 1)
        claims, evidence_report, evidence_coverage = _integrate_claims(nodes, edges, claims_bundle, stamp, investigator_min_papers=min_papers)
        # the RESOLVED endpoints too (a bare "CHEMBL<n>" subject became "CHEMBL:CHEMBL<n>"): a node a
        # claim names stays exported even when the claim could not attach (design item G / H)
        for c in claims:
            claim_refs.update(str(x) for x in (c.get("subject"), c.get("object"), c.get("variant")) if x)
    variant_unresolved = _resolve_claim_variants(claims, nodes)
    report["evidence"] = evidence_report

    # -- patient groups and registries ---------------------------------------------
    groups = build_patient_groups(seeds.get("patient_groups"), nodes, today=day)
    for nid, n in groups.nodes.items():
        nodes.setdefault(nid, n)
    for e in groups.edges:
        _merge_edge(edges, e)
    report["patient_groups"] = groups.report

    # -- prune ---------------------------------------------------------------------
    pruned = prune_graph(nodes, edges, seeds, thresholds, claim_refs=claim_refs, today=day)
    report["prune"] = pruned.report
    # -- patient groups, step 5b: mechanism and umbrella routes over the exported graph -----
    if groups.nodes:
        report["patient_groups"] = propagate_patient_groups(groups, nodes, edges, options=patient_group_options(seeds))

    # -- slim + scrub, then make the claim-bearing edges consistent ----------------
    for n in nodes.values():
        slim_node(n)
    for c in claims:
        c["quote"] = scrub_text(str(c.get("quote") or ""))
    mismatched: dict[str, str] = {}
    for c in claims:
        paper = nodes.get(str(c.get("paper") or ""))
        abstract = (paper or {}).get("props", {}).get("abstract")
        if not isinstance(abstract, str) or _normalize_ws(c["quote"]) not in _normalize_ws(abstract):
            mismatched[c["id"]] = "quote does not match the (scrubbed) abstract of its paper"
    dropped_claims = _drop_claims(edges, claims, mismatched)
    claims_by_id = {c["id"]: c for c in claims}
    reconcile = _reconcile_claim_edges(edges, claims_by_id)
    for e in edges.values():
        _scrub_edge(e)
        compact_edge(e)
        if e["attributes"].get("labels") == []:  # an empty display set is the same as none (bytes)
            del e["attributes"]["labels"]
    final_edges = finalize_edges(edges.values())

    # -- meta ----------------------------------------------------------------------
    sources: list[dict[str, str]] = []
    for s in store.sources_rows():
        sources.append({"name": s["name"], "version": s.get("version") or "", "retrieved_at": s["retrieved_at"], "license": s["license"]})
        upstream = (s.get("notes") or {}).get("upstream_sources") or {}
        for name, up in sorted(upstream.items()):  # one record per upstream database (README section 5 license table)
            up = up if isinstance(up, Mapping) else {}
            sources.append({"name": f"{s['name']} upstream: {name}", "version": str(up.get("version") or ""), "retrieved_at": s["retrieved_at"], "license": str(up.get("license") or "UNVERIFIED")})
    if mech.report.get("curated_edges"):
        seed_source = mechanisms._seed_rows(seeds)[0] or "pipeline/seeds/mechanisms.yaml"
        # The seed file's own transcription date keeps two builds of one store byte-identical
        # (apart from built_at); without it the build time is the best available date.
        seed_meta = seeds.get("mechanisms") if isinstance(seeds.get("mechanisms"), Mapping) else {}
        seed_date = str(seed_meta.get("transcribed_at") or "").strip() or stamp
        sources.append({"name": f"curated mechanism seeds: {seed_source}", "version": "", "retrieved_at": seed_date, "license": CURATED_SEED_LICENSE})
    if claims_bundle is not None:
        sources.append({"name": "claims bundle (data/build/claims.json)", "version": claims_bundle.model or "", "retrieved_at": claims_bundle.extracted_at, "license": CLAIMS_BUNDLE_LICENSE})
    if groups.source is not None:
        sources.append(groups.source)

    coverage_rows: list[dict[str, Any]] = [dict(r) for r in store.coverage_rows()] + [dict(r) for r in mech.coverage]
    if claims_bundle is not None:
        for entity, cov in claims_bundle.coverage.items():
            coverage_rows += [{"entity_id": entity, **s.model_dump(mode="json")} for s in cov.searched]
    coverage_rows += evidence_coverage + groups.coverage + pruned.coverage
    exported_ids = set(nodes)
    kept_rows = [
        r
        for r in coverage_rows
        if str(r["entity_id"]) in exported_ids or str(r["entity_id"]).startswith(COVERAGE_KEEP_PREFIXES) or str(r["source"]) == CLAIMS_COVERAGE_SOURCE
    ]
    coverage_dropped = len(coverage_rows) - len(kept_rows)

    graph_dict = {
        "meta": {"built_at": stamp, "sources": sources, "coverage": _group_coverage(kept_rows)},
        "nodes": [nodes[k] for k in sorted(nodes, key=lambda k: (nodes[k]["type"], k))],
        "edges": final_edges,
        "claims": claims,
        "clusters": [],
    }
    graph = Graph.model_validate(graph_dict)
    # the file-level contract is strict: make sure what we would write loads back unchanged
    graph = Graph.model_validate_json(graph.model_dump_json(), strict=True)

    attached_claim_ids = {cid for e in graph.edges for cid in (*e.supporting, *e.contradicting)}
    report["counts"] = summarize(graph)
    report["sizes"] = sizes(graph)
    report["edges_dropped_dangling"] = dangling
    report["edges_below_threshold"] = below_threshold
    report["edges_with_relation_conflict"] = conflicts
    report["claim_edges_reconciled"] = reconcile
    report["coverage_rows_dropped_for_pruned_entities"] = coverage_dropped
    report["scrub_placeholders"] = scrub_placeholders(graph)
    report["claims"] = {
        "in_bundle": len(claims_bundle.claims) if claims_bundle is not None else 0,
        "in_graph": len(claims),
        "attached_to_edges": len(attached_claim_ids),
        "unattached": sorted({c["id"] for c in claims} - attached_claim_ids),
        "variant_unresolved": variant_unresolved,
        "dropped": dropped_claims,
    }
    report["seed_resolution"] = seed_resolution(store, seeds)
    return BuildResult(graph, report)


def build_graph(store: Any, seeds: Mapping[str, Any], claims_bundle: Any = None, *, now: str | None = None) -> Graph:
    """The validated :class:`pipeline.models.Graph` for ``store`` (see :func:`build`)."""
    return build(store, seeds, claims_bundle, now=now).graph


def scrub_placeholders(graph: Graph) -> dict[str, int]:
    """How many e-mail / phone / dose placeholders the EXPORTED graph carries (counted on the
    final JSON, so the report says what reached graph.json whichever scrubber ran first)."""
    text = graph.model_dump_json()
    return {
        "emails_removed": text.count(EMAIL_PLACEHOLDER),
        "phones_removed": text.count(PHONE_PLACEHOLDER),
        "doses_removed": text.count(DOSE_PLACEHOLDER),
    }


def sizes(graph: Graph) -> dict[str, Any]:
    """Bytes of compact JSON per node type, per edge relation, claims and meta (the size budget)."""
    data = graph.model_dump(mode="json")

    def nbytes(obj: Any) -> int:
        return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

    by_type: Counter[str] = Counter()
    for n in data["nodes"]:
        by_type[n["type"]] += nbytes(n)
    by_relation: Counter[str] = Counter()
    for e in data["edges"]:
        by_relation[e["relation"]] += nbytes(e)
    total = nbytes(data)
    return {
        "total_bytes": total,
        "total_mb": round(total / 1_000_000, 2),
        "nodes_by_type": dict(sorted(by_type.items())),
        "edges_by_relation": dict(sorted(by_relation.items())),
        "claims": nbytes(data["claims"]),
        "meta": nbytes(data["meta"]),
    }


def summarize(graph: Graph) -> dict[str, Any]:
    """Counts for the build report: nodes by type, edges by relation / evidence class / status /
    label, claims, clusters and coverage (entities and zero-hit searches)."""
    labels: Counter[str] = Counter()
    for e in graph.edges:
        for l in e.attributes.get("labels", []) or []:
            labels[str(l)] += 1
    searched = [s for cov in graph.meta.coverage.values() for s in cov.searched]
    # Information content audit: Phenotype nodes without props.ic (outside the "Phenotypic
    # abnormality" subtree) and phenotype is_a edges whose child is LESS specific than its parent.
    phenotypes = {n.id: n for n in graph.nodes if n.type is NodeType.Phenotype}
    ic = {nid: _as_float(n.props.get("ic")) for nid, n in phenotypes.items()}
    phe_is_a = [e for e in graph.edges if e.relation == REL_IS_A and e.source in phenotypes and e.target in phenotypes]
    scored = [e for e in phe_is_a if ic.get(e.source) is not None and ic.get(e.target) is not None]
    return {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "claims": len(graph.claims),
        "clusters": len(graph.clusters),
        "nodes_by_type": dict(sorted(Counter(n.type.value for n in graph.nodes).items())),
        "edges_by_relation": dict(sorted(Counter(e.relation for e in graph.edges).items())),
        "edges_by_evidence_class": dict(sorted(Counter(e.evidence_class.value for e in graph.edges).items())),
        "edges_by_status": dict(sorted(Counter(e.status.value for e in graph.edges).items())),
        "edges_by_label": dict(sorted(labels.items())),
        "edges_with_origin": dict(sorted(Counter(str(e.attributes["origin"]) for e in graph.edges if "origin" in e.attributes).items())),
        "phenotype_ic": {
            "nodes": len(phenotypes),
            "nodes_without_ic": sum(1 for v in ic.values() if v is None),
            "is_a_edges": len(phe_is_a),
            "is_a_edges_with_ic_on_both_ends": len(scored),
            "is_a_edges_child_ic_below_parent": sum(1 for e in scored if ic[e.source] < ic[e.target] - 1e-9),
        },
        "coverage": {
            "entities": len(graph.meta.coverage),
            "searches": len(searched),
            "zero_hit_searches": sum(1 for s in searched if s.hits == 0),
            "by_source": dict(sorted(Counter(s.source for s in searched).items())),
        },
        "sources": [s.name for s in graph.meta.sources],
    }
