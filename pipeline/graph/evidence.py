"""Literature claims -> graph edges, Paper nodes and Investigator nodes.

This is the evidence-pipeline -> graph-build seam.

:func:`integrate_claims` takes the build's DRAFT nodes and edges (plain dicts shaped like
:class:`pipeline.models.Node` / ``Edge`` inputs; edges carry no ``id`` and are keyed by
``(source, target, relation)``) plus the validated :class:`pipeline.models.ClaimsBundle` and

* MUTATES ``nodes`` / ``edges`` in place for upgrades: existing edges gain supporting /
  contradicting claim ids, a claim-aware confidence, status and ``attributes.labels``; an
  ``inferred`` edge that gains a supporting claim becomes ``extracted``; Mechanism nodes named by
  a ``mech:`` endpoint that does not exist yet are created directly in ``nodes``;
* RETURNS the additions separately in an :class:`EvidenceResult`: the claims to export, the Paper
  nodes, the Investigator nodes, the new edges, the coverage rows for every failed link (hard rule
  3) and a report dict for ``build_report.json``.

Nothing here reads the store or the network, and no identifier is hand-typed: endpoints are
resolved by exact id, by :func:`pipeline.ids.normalize_curie` with a hint inferred from the id
SHAPE, or by the ``mech:`` id grammar.

Endpoint resolution (shared design B)
-------------------------------------
``text:<mention>`` endpoints are unresolved by definition. Otherwise: the exact node id; else
``normalize_curie(raw, hint)`` where the hint comes from the shape of a bare id (``CHEMBL<digits>``
-> ``CHEMBL``, ``DB<5 digits>`` -> ``DRUGBANK``; ``ENSG...`` and ``PREFIX_digits`` need no hint)
and the normalised id must be a node; a ``mech:<GENE>:<GoF|LoF|DN>[:somatic]`` id is valid even
when the node does not exist yet (the node is created from the id: label via
:func:`pipeline.graph.mechanisms.mechanism_label`, props ``gene_symbol`` / ``direction`` /
``origin`` (somatic only) / ``gene`` when a Gene node carries the symbol / ``source: "claims"``).
Every unresolved endpoint becomes a coverage row ``(entity, "unresolved literature mention", <raw
endpoint>, today, 0)`` where ``entity`` is the resolved other side when there is one, else
``claims:unresolved``. A ``claim.variant`` that is not a Variant node is cleared on the exported
claim (the contract forbids it) and recorded the same way.

Protein-change mentions (general rule, :class:`_MentionResolver`)
-------------------------------------------------------------------
A ``text:<mention>`` subject, object or ``variant`` that names a variant by its protein change is
resolved against the Variant nodes before it is given up on. The mention must carry a
protein-change token (:data:`PROTEIN_CHANGE_TOKEN`: ``[A-Z]<digits>[A-Z*]`` such as ``R274W`` /
``K119*``, or ``p.<Xxx><digits><Xxx>`` such as ``p.Arg274Trp``) and must name nothing but the
variant: every other word is a gene symbol, a zygosity / direction / variant word
(:data:`VARIANT_MENTION_WORDS`) or another protein-change token (a phrase such as "<variant> allele
burden" or "<variant>-mutated <disease>" is not the variant and stays unresolved). The token is
compared verbatim with each Variant node's ``synonyms``, ``props.protein_change`` and
``props.hgvs_p``, restricted to the variants of the genes the claim names: a gene symbol
(word-bounded, case-sensitive; only genes that carry Variant nodes) in the claim's mentions or
quote, the gene of a resolved Gene / Mechanism / Variant endpoint, or the mechanism gene(s) of a
resolved Disease endpoint. With no gene in the claim the token must be unique across ALL Variant
nodes. Exactly one candidate (and every token in the mention agreeing on it) resolves the mention:
the claim's field becomes the Variant node id, the endpoint counts as ``protein_change_token``
in the resolution report, every edge the claim attaches to records
``confidence_basis.resolved_mentions[] = {claim, field, resolved_from, resolved_to, method:
"protein-change token vs ClinVar synonyms"}`` and the normal routes (the variant-specific
Variant -> Mechanism ``has_mechanism`` route included) run as if reconcile had resolved it.
Ambiguous, unmatched or over-long mentions stay unresolved with the coverage rows above; the report
(``claims.variant_mentions``) counts each outcome and lists the resolutions.

Predicate -> edge mapping (shared design C)
-------------------------------------------
=================  ============================  =======================================================
Predicate          Endpoint types                Edge
=================  ============================  =======================================================
``treats``         Drug -> Disease               ``treats`` (create or upgrade)
``has_mechanism``  Disease | Variant -> Mech.    ``has_mechanism`` (create or upgrade); when the claim
                                                 names a Variant, ALSO Variant -> Mechanism
                                                 ``has_mechanism`` (label ``variant_specific``)
``has_phenotype``  Disease | Mechanism -> Phen.  attach to the observed ``has_phenotype`` edge, else create
``causes`` /       {Gene, Disease}               the ``associated_with`` edge in either orientation, else
``associated_with`` {Mechanism, Disease}         create Disease -> Gene; {Mechanism, Disease}: the
                   {Variant, Disease}            ``has_mechanism`` edge, else create Disease -> Mechanism;
                                                 {Variant, Disease}: the ``has_condition`` edge, else
                                                 create Variant -> Disease
``modulates``      Drug -> Gene|Pathway|Mech.    ``modulates`` (create or upgrade)
=================  ============================  =======================================================

Any other combination is a mapping gap: the claim stays in ``claims[]`` unattached (listed with
its reason in the report) and a coverage row names the pair. A CONTRADICTING claim reaches a
``has_mechanism`` edge only through the ``has_mechanism`` predicate with that Mechanism node as
its object: a contradicting ``causes`` / ``associated_with`` claim between a Mechanism and a
Disease does not land on the disease's mechanism edge (it says nothing about the mechanism
assignment); it stays unattached, with its reason in the report and a coverage row, counted under
``claims.contradicting_not_on_mechanism_edge``. A NEW edge needs at least one
supporting claim; contradicting-only claims against a pair with no edge are unattached too (there
is no status for "refuted" in the contract). Created edges: ``evidence_class: extracted``,
``source_db: claims``, one source per distinct paper (``kind pmid`` / ``doi``, dated by the
paper's year, else the build day), ``attributes.preprint: true`` when a claim comes from a
``doi:`` paper, ``attributes.origin: somatic`` for a somatic mechanism.

Claim-driven confidence and status (shared design D)
----------------------------------------------------
All the arithmetic lives in :mod:`pipeline.graph.confidence`, one definition shared by this
module, the mechanism rules and the build: :func:`pipeline.graph.confidence.edge_confidence_from_claims`
(per-claim weights from ``claim_confidence``, aggregated by ``aggregate_claim_confidence``)::

    base   = min(1, max supporting weight + 0.05 * (n_supporting - 1))        (0 without support)
    conf   = base * (1 - 0.3 * min(1, n_contradicting / n_supporting))

and :func:`pipeline.graph.confidence.status_from_claims`. ``extracted`` edges take ``conf``;
``observed`` edges take ``max(observed confidence, conf)`` (``observed * 0.7`` when every claim
contradicts the database fact) and stay observed; an ``inferred`` edge with >= 1 supporting claim
becomes ``extracted`` at ``conf`` (its ``curated_unverified`` label is dropped, the old basis kept
under ``prior``; a curated R4 edge also loses its seed-only ``reason`` / ``origin`` and its seed
record moves from ``sources`` to ``prior.seed_record``,
:func:`pipeline.graph.mechanisms.strip_curated_provenance`, hard rule 10), while an inferred edge with only contradicting claims stays inferred and merely
lists them. Status and labels: ``contested`` dominates; ``small_n`` when every PATIENT-LEVEL claim
with a KNOWN ``n`` has fewer than 10 patients (in-vitro and review claims never count, an unknown
``n`` neither blocks nor grants it, no known ``n`` means no ``small_n``); ``promising`` with >= 1
supporting patient-level claim; ``preprint`` when a claim's paper id starts with ``doi:``. The
aggregate goes to ``confidence_basis.claims`` (with the ``per_claim`` weights), the status inputs
to ``confidence_basis.claim_status``; ``attributes.labels`` always lists the full set on every
edge this module touches. :func:`claim_status` and :func:`aggregate_confidence` remain as thin
aliases of the rubric functions for callers. One more rubric rule applies to claim-derived
(``extracted``) ``has_mechanism`` edges: when every supporting claim is ``in_vitro`` or ``review``
the edge is labelled ``in_vitro_only`` and capped at 0.5
(:func:`pipeline.graph.confidence.apply_in_vitro_only`; the uncapped value stays in
``confidence_basis.in_vitro_only``), so a mechanism asserted only by bench work or a review never
outranks a patient-level one.

Investigators (shared design E) and Papers (F)
----------------------------------------------
Every ``props.authors`` entry (a string or ``{name, affiliation, orcid}``) becomes an Investigator
``orcid:<id>`` when an ORCID is present, else ``person:<slug(name)>`` (lower-case ASCII, hyphens),
merged across papers. PubMed supplies an ORCID on some of a person's papers only, so a name-only
record whose exact name slug was seen with exactly ONE ORCID folds into that ORCID record
(``props.papers_by_match = {"orcid": [...], "name": [...]}`` says which papers were matched by
name alone, ``match_confidence`` becomes ``mixed``); a name seen with two or more ORCIDs stays
split and is listed under ``report.investigators.ambiguous_names``. Props: ``name``,
``affiliations`` (scrubbed, deduplicated, at most 5), ``orcid``, ``match_confidence`` (``high``
with an ORCID on every paper, ``mixed``, else ``low``), ``n_papers``, ``papers``,
``papers_by_match``, ``role_label`` ("author of N paper(s) on <top two disease / mechanism labels
among the claims of those papers>", never "leader"). Edges: Investigator -> Paper ``author_of``
(observed, 0.9, basis ``pubmed_record`` and ``matched_by``) and Investigator -> Disease |
Mechanism ``publishes_on`` (observed aggregation,
confidence ``min(0.9, 0.3 + 0.1 * n_papers)``, basis ``n_papers`` / ``paper_ids`` /
``via_claims``). Only investigators with >= 1 ``publishes_on`` edge are exported; the rest are
counted. RePORTER investigators already in ``nodes`` are separate unless the id is identical.
Paper nodes: every bundle paper with ``title``, ``abstract`` (the quote check needs it),
``year``, ``journal``, ``pmid``, ``doi``, ``authors``, ``pub_types``, ``preprint``.

Scrubbing (hard rule 7, README section 5 "never guess emails")
--------------------------------------------------------------
``scrub`` (the shared dose scrubber; default :func:`pipeline.sources.clinvar.scrub_doses`) plus
this module's e-mail / phone scrubber run over EVERY free-text value that leaves here: paper
titles, abstracts, journals, author names and affiliations, claim quotes and ``text:`` mentions,
investigator props, coverage queries. Abstract and quote are scrubbed with the same function and
the quote is re-checked against the scrubbed abstract; a claim whose quote no longer matches is
dropped and counted (hard rule 5 over hard rule 7, never the reverse).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pipeline.graph import confidence as conf_rubric
from pipeline.graph.mechanisms import CLAIMS_SOURCE, REL_ASSOCIATED_WITH, REL_HAS_MECHANISM, VARIANT_SPECIFIC_LABEL, mechanism_label, strip_curated_provenance
from pipeline.ids import normalize_curie, parse_mechanism_id
from pipeline.models import NodeType, Polarity

__all__ = [
    "AUTHOR_OF_CONFIDENCE",
    "CLAIM_LABELS",
    "CONTRADICTION_PENALTY",
    "COVERAGE_SOURCE",
    "COVERAGE_UNRESOLVED_ENTITY",
    "EMAIL_PLACEHOLDER",
    "EXTRA_SUPPORT_BONUS",
    "EvidenceResult",
    "MAX_AFFILIATIONS",
    "PAPER_PROPS_KEEP",
    "PHONE_PLACEHOLDER",
    "PROTEIN_CHANGE_TOKEN",
    "PUBLISHES_ON_BASE",
    "PUBLISHES_ON_CAP",
    "PUBLISHES_ON_PER_PAPER",
    "REL_AUTHOR_OF",
    "REL_HAS_CONDITION",
    "REL_HAS_PHENOTYPE",
    "REL_MODULATES",
    "REL_PUBLISHES_ON",
    "REL_TREATS",
    "Scrubber",
    "VARIANT_MENTION_HOW",
    "VARIANT_MENTION_METHOD",
    "VARIANT_MENTION_WORDS",
    "aggregate_confidence",
    "claim_sources",
    "claim_status",
    "default_scrub",
    "integrate_claims",
    "scrub_contacts",
    "slugify",
]

# -- relations and predicates -------------------------------------------------------------
REL_TREATS = "treats"
REL_HAS_PHENOTYPE = "has_phenotype"
REL_HAS_CONDITION = "has_condition"  # Variant -> Disease, as pipeline.sources.clinvar writes it
REL_MODULATES = "modulates"
REL_AUTHOR_OF = "author_of"
REL_PUBLISHES_ON = "publishes_on"

PRED_TREATS = "treats"
PRED_HAS_MECHANISM = "has_mechanism"
PRED_HAS_PHENOTYPE = "has_phenotype"
PRED_CAUSES = "causes"
PRED_ASSOCIATED_WITH = "associated_with"
PRED_MODULATES = "modulates"
# Predicates whose claim may be stated from either end of the pair.
SYMMETRIC_PREDICATES = frozenset({PRED_CAUSES, PRED_ASSOCIATED_WITH})

# -- documented numbers (the claim arithmetic itself is pipeline.graph.confidence's) --------
EXTRA_SUPPORT_BONUS = conf_rubric.CLAIM_SUPPORT_BONUS  # per supporting claim beyond the strongest one
CONTRADICTION_PENALTY = conf_rubric.CONTRADICTION_PENALTY  # full penalty when contradicting claims >= supporting claims
AUTHOR_OF_CONFIDENCE = 0.9  # a PubMed author list is a database fact
PUBLISHES_ON_BASE = 0.3
PUBLISHES_ON_PER_PAPER = 0.1
PUBLISHES_ON_CAP = 0.9
MAX_AFFILIATIONS = 5
ROLE_LABEL_TOPICS = 2

COVERAGE_SOURCE = "unresolved literature mention"  # pipeline.graph.build.CLAIMS_COVERAGE_SOURCE repeats it
COVERAGE_UNRESOLVED_ENTITY = "claims:unresolved"
# Protein-change mentions (module docstring): one-letter "R274W" / "K119*" or three-letter
# "p.Arg274Trp", bounded by non-alphanumerics so "CD14+" or "JAK2V617F" never yield a token.
PROTEIN_CHANGE_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]\d+(?:[A-Z]|\*)|p\.[A-Z][a-z]{2}\d+[A-Z][a-z]{2})(?![A-Za-z0-9])")
VARIANT_MENTION_METHOD = "protein-change token vs ClinVar synonyms"
VARIANT_MENTION_HOW = "protein_change_token"  # _Endpoint.how / report bucket for a mention resolved this way
# Words a variant mention may carry besides the token and a gene symbol (lower-case).
VARIANT_MENTION_WORDS = frozenset(
    {
        "variant", "variants", "mutation", "mutations", "mutant", "mutants", "allele", "alleles", "substitution", "missense",
        "homozygous", "heterozygous", "homozygosity", "heterozygosity", "germline", "somatic",
        "gof", "lof", "dn", "gain", "loss", "of", "function", "dominant", "negative", "the", "a", "an",
    }
)
_MENTION_WORD_SPLIT = re.compile(r"[\s,;:/()\[\]\-]+")
INVESTIGATOR_SOURCE = "pubmed author lists"
CLAIM_LABELS = frozenset({"promising", "contested", "small_n", "preprint"})
CURATED_LABEL = "curated_unverified"  # pipeline.graph.mechanisms.CURATED_LABEL; dropped on upgrade
PAPER_PROPS_KEEP = ("title", "abstract", "year", "journal", "pmid", "doi", "authors", "pub_types", "preprint")

EMAIL_PLACEHOLDER = "[email removed]"
PHONE_PLACEHOLDER = "[phone removed]"
# "Electronic address: x@y.org." is how PubMed affiliations carry the corresponding author's e-mail.
_EMAIL = re.compile(r"(?:\b(?:Electronic address|E-?mail)\s*:?\s*)?[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.I)
# Three separated digit groups (optionally a country code / parentheses), an international
# "+<cc><digits>" run, or anything after a tel/phone/fax marker. Postal codes such as a ZIP+4
# (two groups) or a UK postcode (letters inside) do not match.
_PHONE = re.compile(
    r"(?:\b(?:tel|telephone|phone|fax|mobile)\b\.?\s*:?\s*[+(]?\d[\d\s().-]{6,}\d)"
    r"|(?:(?<![\w.-])\+\d{1,3}[\s.-]?(?:\(\d{1,4}\)[\s.-]?)?\d(?:[\s.-]?\d){6,12}(?![\w-]))"
    r"|(?:(?<![\w.-])(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{3,4}(?![\w-]))",
    re.I,
)

_DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")
_PMID_SHAPE = re.compile(r"^\d+$")
_ORCID_URL = re.compile(r"^(?:https?://)?(?:www\.)?orcid\.org/", re.I)
# Hints for bare ids by SHAPE (never a value): the backbone spells drugs CHEMBL:CHEMBL<n>, DRUGBANK:DB<n>.
_BARE_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^CHEMBL\d+$"), "CHEMBL"),
    (re.compile(r"^DB\d{5}$"), "DRUGBANK"),
)
_OBO_UNDERSCORE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)_\d+$")
_ALPHA_PREFIX = re.compile(r"^([A-Za-z]+)")

TEXT_PREFIX = "text:"
MECH_PREFIX = "mech:"
DOI_PREFIX = "doi:"
PMID_PREFIX = "pmid:"


# --------------------------------------------------------------------------------------
# scrubbing
# --------------------------------------------------------------------------------------


def default_scrub() -> Callable[[str], str]:
    """The shared dose scrubber (imported lazily so this module stays import-light)."""
    from pipeline.sources.clinvar import scrub_doses

    return scrub_doses


def scrub_contacts(text: str) -> tuple[str, int, int]:
    """``(scrubbed, n_emails, n_phones)``: e-mail addresses and phone numbers replaced."""
    out, n_emails = _EMAIL.subn(EMAIL_PLACEHOLDER, text)
    out, n_phones = _PHONE.subn(PHONE_PLACEHOLDER, out)
    return out, n_emails, n_phones


class Scrubber:
    """Dose scrubber (``scrub``) followed by the e-mail / phone scrubber, with counters for the
    report. Non-string values pass through unchanged; strings keep their type."""

    def __init__(self, scrub: Callable[[str], Any] | None = None) -> None:
        self._scrub = scrub if scrub is not None else default_scrub()
        self.emails = 0
        self.phones = 0
        self.dose_fields = 0

    def __call__(self, value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        dosed = self._scrub(value)
        if not isinstance(dosed, str):
            dosed = value
        if dosed != value:
            self.dose_fields += 1
        out, n_emails, n_phones = scrub_contacts(dosed)
        self.emails += n_emails
        self.phones += n_phones
        return out

    def summary(self) -> dict[str, int]:
        return {"emails": self.emails, "phones": self.phones, "dose_fields_changed": self.dose_fields}


def slugify(text: str | None) -> str:
    """ASCII, lower-case, ``[a-z0-9]`` runs joined by single hyphens; accents stripped."""
    normalised = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


def _normalize_ws(s: str) -> str:
    return " ".join(s.split())


def _today_iso(today: str | None) -> str:
    return today or datetime.now(timezone.utc).date().isoformat()


# --------------------------------------------------------------------------------------
# result
# --------------------------------------------------------------------------------------


@dataclass
class EvidenceResult:
    """What :func:`integrate_claims` adds; upgrades were applied to the caller's dicts in place.

    * ``claims``: Claim inputs to export (subject / object / variant rewritten to the resolved
      node ids, quotes scrubbed); each is attached to >= 1 edge or listed in ``unattached``.
    * ``paper_nodes`` / ``investigator_nodes`` / ``mechanism_nodes``: Node inputs keyed by id.
      Mechanism nodes were ALSO inserted into ``nodes`` (edges may point at them); merging them
      again is harmless.
    * ``new_edges``: Edge inputs without ``id``, keyed by ``(source, target, relation)``, each with
      ``source_db`` for the build's deterministic ordering.
    * ``coverage_rows``: ``(entity_id, source, query, date, hits)`` tuples (hard rule 3).
    * ``unattached``: ``[{"id", "reason"}]``; ``report``: counts for ``build_report.json``.
    """

    claims: list[dict[str, Any]] = field(default_factory=list)
    paper_nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    investigator_nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    mechanism_nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    new_edges: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    coverage_rows: list[tuple[str, str, str, str, int]] = field(default_factory=list)
    unattached: list[dict[str, str]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# endpoint resolution
# --------------------------------------------------------------------------------------


@dataclass
class _Endpoint:
    raw: str
    node_id: str | None  # a node id (existing, or a mech: id to be created) when resolved
    node_type: str | None
    how: str  # exact | normalized | mechanism | text | missing | unnormalisable | invalid_mechanism | empty
    prefix: str

    @property
    def resolved(self) -> bool:
        return self.node_id is not None


def _prefix_of(raw: str) -> str:
    """Report bucket for an endpoint spelling: ``text``, ``mech``, ``MONDO``, ``CHEMBL (bare)``..."""
    if raw.startswith(TEXT_PREFIX):
        return "text"
    if raw.startswith(MECH_PREFIX):
        return "mech"
    if ":" in raw:
        return raw.split(":", 1)[0]
    obo = _OBO_UNDERSCORE.match(raw)
    if obo:
        return f"{obo.group(1)} (underscore)"
    alpha = _ALPHA_PREFIX.match(raw)
    if alpha:
        return f"{alpha.group(1)} (bare)"
    return "other"


class _Resolver:
    def __init__(self, nodes: Mapping[str, Mapping[str, Any]]) -> None:
        self.nodes = nodes
        self.kinds: Counter[str] = Counter()

    def __call__(self, raw: Any) -> _Endpoint:
        value = str(raw or "").strip()
        prefix = _prefix_of(value)
        if not value:
            return self._done(_Endpoint(value, None, None, "empty", prefix))
        node = self.nodes.get(value)
        if node is not None:
            return self._done(_Endpoint(value, value, str(node.get("type")), "exact", prefix))
        if value.startswith(TEXT_PREFIX):
            return self._done(_Endpoint(value, None, None, "text", prefix))
        if value.startswith(MECH_PREFIX):
            if parse_mechanism_id(value) is None:
                return self._done(_Endpoint(value, None, None, "invalid_mechanism", prefix))
            return self._done(_Endpoint(value, value, NodeType.Mechanism.value, "mechanism", prefix))
        hint = next((h for pat, h in _BARE_HINTS if pat.match(value)), None)
        try:
            normalised = normalize_curie(value, hint)
        except ValueError:
            return self._done(_Endpoint(value, None, None, "unnormalisable", prefix))
        node = self.nodes.get(normalised)
        if node is not None:
            return self._done(_Endpoint(value, normalised, str(node.get("type")), "normalized", prefix))
        return self._done(_Endpoint(value, None, None, "missing", prefix))

    def _done(self, ep: _Endpoint) -> _Endpoint:
        self.kinds[ep.how] += 1
        return ep

    def recount(self, ep: _Endpoint, how: str) -> None:
        """An endpoint resolved after the fact (a protein-change mention): move its tally."""
        self.kinds[ep.how] -= 1
        if self.kinds[ep.how] <= 0:
            del self.kinds[ep.how]
        ep.how = how
        self.kinds[how] += 1


def _symbols_of(node: Mapping[str, Any] | None) -> set[str]:
    """Gene symbols a Variant / Mechanism / Gene node carries (``props.gene_symbol``,
    ``props.gene_symbols``, a Gene's ``props.symbol`` or label)."""
    if node is None:
        return set()
    props = node.get("props") or {}
    out: set[str] = set()
    for key in ("gene_symbol", "symbol"):
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            out.add(value.strip())
    for value in props.get("gene_symbols") or []:
        if isinstance(value, str) and value.strip():
            out.add(value.strip())
    if str(node.get("type")) == NodeType.Gene.value and isinstance(node.get("label"), str) and node["label"].strip():
        out.add(node["label"].strip())
    return out


@dataclass
class _MentionOutcome:
    variant_id: str | None
    reason: str  # resolved | no_protein_change_token | mention_names_more_than_the_variant | no_matching_variant | ambiguous
    tokens: list[str]
    genes: list[str]
    candidates: list[str]


class _MentionResolver:
    """Module docstring, "Protein-change mentions": resolve a ``text:`` mention to the ONE Variant
    node whose synonyms / protein change carry its protein-change token, restricted to the genes
    the claim names. Nothing here names a gene or a variant; the vocabulary is read from the
    Variant and Gene nodes."""

    def __init__(self, nodes: Mapping[str, Mapping[str, Any]], edges: Mapping[tuple[str, str, str], Any]) -> None:
        self.nodes = nodes
        self.by_token: dict[str, list[str]] = defaultdict(list)  # synonym / protein change -> variant ids
        self.variant_genes: dict[str, set[str]] = {}
        gene_vocab: set[str] = set()
        for nid, n in nodes.items():
            if str(n.get("type")) != NodeType.Variant.value:
                continue
            props = n.get("props") or {}
            pool = {s for s in (n.get("synonyms") or []) if isinstance(s, str)} | {props.get("protein_change"), props.get("hgvs_p")}
            for text in pool:
                if isinstance(text, str) and text.strip():
                    self.by_token[text.strip()].append(nid)
            self.variant_genes[nid] = _symbols_of(n)
            gene_vocab |= self.variant_genes[nid]
        # gene symbols that carry Variant nodes, word-bounded and case-sensitive (HGNC spelling)
        self.gene_vocab = gene_vocab
        self.gene_pattern = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(g) for g in sorted(gene_vocab, key=len, reverse=True)) + r")(?![A-Za-z0-9])") if gene_vocab else None
        # a Disease endpoint names the genes of its mechanism edges (mech:<GENE>:... targets)
        self.disease_genes: dict[str, set[str]] = defaultdict(set)
        for (s, t, rel) in edges:
            if rel == REL_HAS_MECHANISM and t.startswith(MECH_PREFIX):
                parsed = parse_mechanism_id(t)
                if parsed is not None and str((nodes.get(s) or {}).get("type")) == NodeType.Disease.value:
                    self.disease_genes[s].add(parsed[0])
        self.outcomes: Counter[str] = Counter()
        self.resolved: list[dict[str, Any]] = []

    def genes_in(self, texts: Iterable[str], endpoints: Iterable[_Endpoint]) -> set[str]:
        genes: set[str] = set()
        if self.gene_pattern is not None:
            for text in texts:
                genes.update(self.gene_pattern.findall(text or ""))
        for ep in endpoints:
            if not ep.resolved:
                continue
            node = self.nodes.get(str(ep.node_id))
            if ep.node_type in (NodeType.Gene.value, NodeType.Mechanism.value, NodeType.Variant.value):
                genes |= _symbols_of(node)
                if ep.node_type == NodeType.Mechanism.value:
                    parsed = parse_mechanism_id(str(ep.node_id))
                    if parsed is not None:
                        genes.add(parsed[0])
            elif ep.node_type == NodeType.Disease.value:
                genes |= self.disease_genes.get(str(ep.node_id), set())
        return genes & self.gene_vocab if self.gene_vocab else set()

    def resolve(self, mention: str, genes: set[str]) -> _MentionOutcome:
        text = mention[len(TEXT_PREFIX):] if mention.startswith(TEXT_PREFIX) else mention
        tokens = sorted(set(PROTEIN_CHANGE_TOKEN.findall(text)))
        if not tokens:
            return self._done(_MentionOutcome(None, "no_protein_change_token", tokens, sorted(genes), []))
        for word in _MENTION_WORD_SPLIT.split(text):
            w = word.strip().rstrip(".")
            if not w or w in tokens or w in self.gene_vocab or w.lower() in VARIANT_MENTION_WORDS:
                continue
            return self._done(_MentionOutcome(None, "mention_names_more_than_the_variant", tokens, sorted(genes), []))
        candidates: set[str] = set()
        for token in tokens:
            hits = {v for v in self.by_token.get(token, []) if not genes or (self.variant_genes.get(v, set()) & genes)}
            if not hits:
                return self._done(_MentionOutcome(None, "no_matching_variant", tokens, sorted(genes), []))
            candidates |= hits
        if len(candidates) != 1:
            return self._done(_MentionOutcome(None, "ambiguous", tokens, sorted(genes), sorted(candidates)))
        return self._done(_MentionOutcome(next(iter(candidates)), "resolved", tokens, sorted(genes), sorted(candidates)))

    def _done(self, outcome: _MentionOutcome) -> _MentionOutcome:
        self.outcomes[outcome.reason] += 1
        return outcome

    def report(self) -> dict[str, Any]:
        return {"method": VARIANT_MENTION_METHOD, "outcomes": dict(sorted(self.outcomes.items())), "resolved": list(self.resolved)}


# --------------------------------------------------------------------------------------
# claim-level helpers
# --------------------------------------------------------------------------------------


def _paper_year(paper: Mapping[str, Any] | None) -> str | None:
    props = (paper or {}).get("props") or {}
    year = props.get("year")
    if isinstance(year, bool):
        return None
    if isinstance(year, int) and year > 0:
        return str(year)
    if isinstance(year, str) and re.fullmatch(r"\d{4}(?:-(?:0[1-9]|1[0-2]))?", year.strip()):
        return year.strip()
    return None


def _paper_source(paper_id: str, pmid: str, paper: Mapping[str, Any] | None, fallback_date: str) -> dict[str, str]:
    """One ``Edge.sources`` entry for a paper: ``pmid`` / ``doi`` when the id has that shape, else
    ``db`` with the paper node id (mock papers)."""
    date = _paper_year(paper) or fallback_date
    if pmid and _PMID_SHAPE.match(pmid):
        return {"kind": "pmid", "id": pmid, "date": date}
    if paper_id.startswith(PMID_PREFIX) and _PMID_SHAPE.match(paper_id[len(PMID_PREFIX):]):
        return {"kind": "pmid", "id": paper_id[len(PMID_PREFIX):], "date": date}
    if paper_id.startswith(DOI_PREFIX) and _DOI_SHAPE.match(paper_id[len(DOI_PREFIX):]):
        return {"kind": "doi", "id": paper_id[len(DOI_PREFIX):], "date": date}
    return {"kind": "db", "id": paper_id, "date": date}


def claim_sources(claims: Iterable[Mapping[str, Any]], papers: Mapping[str, Mapping[str, Any]], fallback_date: str) -> list[dict[str, str]]:
    """One dated source per distinct paper among ``claims`` (claim dicts)."""
    out: list[dict[str, str]] = []
    for c in claims:
        src = _paper_source(str(c.get("paper", "")), str(c.get("pmid", "")), papers.get(str(c.get("paper", ""))), fallback_date)
        if src not in out:
            out.append(src)
    return out


def aggregate_confidence(supporting_weights: Iterable[float], n_contradicting: int) -> tuple[float, dict[str, Any]]:
    """Alias of :func:`pipeline.graph.confidence.aggregate_claim_confidence` (shared design D):
    ``min(1, max weight + 0.05 * (n_sup - 1)) * (1 - 0.3 * min(1, n_con / n_sup))``; no supporting
    claim gives 0 (and the full penalty ratio when anything contradicts)."""
    return conf_rubric.aggregate_claim_confidence(supporting_weights, n_contradicting)


def claim_status(sup: list[dict[str, Any]], con: list[dict[str, Any]]) -> tuple[str, list[str], dict[str, Any]]:
    """Alias of :func:`pipeline.graph.confidence.status_from_claims` for claim dicts (shared
    design D, README section 6 table): ``contested`` iff both polarities; ``small_n`` only from
    PATIENT-LEVEL claims with a KNOWN ``n`` (a cohort without a reported n neither blocks nor
    grants it; no known n, no ``small_n``); ``promising`` with a supporting patient-level claim;
    ``preprint`` when the paper id starts with ``doi:``."""
    return conf_rubric.status_from_claims(sup, con)


def _node_label(nodes: Mapping[str, Mapping[str, Any]], node_id: str) -> str:
    node = nodes.get(node_id) or {}
    return str(node.get("label") or node_id)


# --------------------------------------------------------------------------------------
# predicate -> edge planning
# --------------------------------------------------------------------------------------


@dataclass
class _Plan:
    key: tuple[str, str, str]  # the edge the claim attaches to (existing or to be created)
    create: bool  # True when no edge exists yet and this key is the canonical one to create
    variant_specific: bool = False


def _lookup(edges: Mapping[tuple[str, str, str], Any], a: str, b: str, relation: str, either: bool) -> tuple[str, str, str] | None:
    if (a, b, relation) in edges:
        return (a, b, relation)
    if either and (b, a, relation) in edges:
        return (b, a, relation)
    return None


def _attach_or_create(edges: Mapping[tuple[str, str, str], Any], a: str, b: str, relation: str, either: bool = False) -> _Plan:
    found = _lookup(edges, a, b, relation, either)
    if found is not None:
        return _Plan(found, False)
    return _Plan((a, b, relation), True)


CONTRADICTING_MECHANISM_PROBLEM = "contradicting {predicate} claim does not attach to the has_mechanism edge (only a has_mechanism claim whose object is the Mechanism node does)"


def _plan(predicate: str, s: _Endpoint, o: _Endpoint, edges: Mapping[tuple[str, str, str], Any], polarity: str = Polarity.supports.value) -> tuple[list[_Plan], str | None]:
    """``(plans, problem)`` for a claim whose endpoints both resolved. ``polarity`` matters for one
    rule (module docstring, predicate mapping): a contradicting claim lands on a ``has_mechanism``
    edge only through the ``has_mechanism`` predicate with the Mechanism node as its object."""
    st, ot = s.node_type, o.node_type
    sid, oid = str(s.node_id), str(o.node_id)
    T = NodeType
    if sid == oid:
        return [], f"self-loop on {sid}"
    if predicate == PRED_TREATS and st == T.Drug and ot == T.Disease:
        return [_attach_or_create(edges, sid, oid, REL_TREATS)], None
    if predicate == PRED_HAS_MECHANISM and st in (T.Disease, T.Variant) and ot == T.Mechanism:
        return [_attach_or_create(edges, sid, oid, REL_HAS_MECHANISM)], None
    if predicate == PRED_HAS_PHENOTYPE and st in (T.Disease, T.Mechanism) and ot == T.Phenotype:
        return [_attach_or_create(edges, sid, oid, REL_HAS_PHENOTYPE)], None
    if predicate in SYMMETRIC_PREDICATES:
        by_type = {st: sid, ot: oid}
        if st != ot and T.Disease in by_type:
            disease = by_type[T.Disease]
            if T.Gene in by_type:
                return [_attach_or_create(edges, disease, by_type[T.Gene], REL_ASSOCIATED_WITH, either=True)], None
            if T.Mechanism in by_type:
                if polarity == Polarity.contradicts.value:
                    return [], CONTRADICTING_MECHANISM_PROBLEM.format(predicate=predicate)
                return [_attach_or_create(edges, disease, by_type[T.Mechanism], REL_HAS_MECHANISM, either=True)], None
            if T.Variant in by_type:
                return [_attach_or_create(edges, by_type[T.Variant], disease, REL_HAS_CONDITION, either=True)], None
    if predicate == PRED_MODULATES and st == T.Drug and ot in (T.Gene, T.Pathway, T.Mechanism):
        return [_attach_or_create(edges, sid, oid, REL_MODULATES)], None
    return [], f"no edge mapping for {predicate} between {st} and {ot}"


# --------------------------------------------------------------------------------------
# edge creation / upgrade
# --------------------------------------------------------------------------------------


def _new_edge(key: tuple[str, str, str]) -> dict[str, Any]:
    source, target, relation = key
    attributes: dict[str, Any] = {}
    if relation == REL_HAS_MECHANISM:
        parsed = parse_mechanism_id(target)
        if parsed is not None and parsed[2]:
            attributes["origin"] = "somatic"
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "source_db": CLAIMS_SOURCE,
        "evidence_class": "extracted",
        "confidence": 0.0,
        "confidence_basis": {"rule": "claims"},
        "status": "supported",
        "sources": [],
        "supporting": [],
        "contradicting": [],
        "attributes": attributes,
    }


def _apply_claims(
    edge: dict[str, Any],
    claims_here: list[dict[str, Any]],
    claim_by_id: Mapping[str, dict[str, Any]],
    papers: Mapping[str, Mapping[str, Any]],
    today: str,
    *,
    variant_specific: bool,
) -> str:
    """Attach ``claims_here`` to ``edge`` (in place) and recompute confidence / status / labels over
    the UNION of the edge's claims. Returns what happened to the evidence class."""
    sup_ids = list(edge.get("supporting") or [])
    con_ids = list(edge.get("contradicting") or [])
    for c in claims_here:
        target = sup_ids if c["polarity"] == Polarity.supports.value else con_ids
        if c["id"] not in target:
            target.append(c["id"])
    sup = [claim_by_id[i] for i in sup_ids if i in claim_by_id]
    con = [claim_by_id[i] for i in con_ids if i in claim_by_id]
    unknown = [i for i in sup_ids + con_ids if i not in claim_by_id]

    prior_class = str(edge.get("evidence_class"))
    prior_conf = float(edge.get("confidence") or 0.0)
    old_basis = dict(edge.get("confidence_basis") or {})
    old_basis.pop("claims", None)
    old_basis.pop("claim_status", None)
    observed = prior_class == "observed"
    # the one definition of the claim arithmetic (shared design D): pipeline.graph.confidence
    agg_conf, claims_basis = conf_rubric.edge_confidence_from_claims(sup, con, observed_confidence=prior_conf if observed else None)
    status, labels, status_basis = conf_rubric.status_from_claims(sup, con)
    claims_basis["per_claim"] = claims_basis.pop("claims")
    if unknown:
        claims_basis["unknown_claim_ids"] = unknown
    outcome = prior_class
    if observed:  # max(observed, literature), or observed * 0.7 when only contradicted; stays observed
        edge["confidence"] = agg_conf
        edge["status"] = status
    elif prior_class == "inferred":
        if sup:
            edge["evidence_class"] = "extracted"
            edge["confidence"] = agg_conf
            edge["status"] = status
            # a curated (R4) seed verified by a claim: its reason / seed-only origin / seed record
            # must not surface as sourced (hard rule 10); the seed record moves into the prior basis
            strip_curated_provenance(edge, old_basis)
            old_basis = {"prior": {**old_basis, "evidence_class": prior_class, "confidence": prior_conf}, "upgraded_from": prior_class}
            outcome = "inferred->extracted"
        else:  # only contradicting claims: an inferred edge cannot become extracted without support
            edge["confidence"] = conf_rubric.cap_inferred(prior_conf)
            outcome = "inferred (contradicting claims listed)"
            labels = [l for l in labels if l not in ("small_n",)]
    else:
        edge["confidence"] = agg_conf
        edge["status"] = status
    edge["supporting"] = sup_ids
    edge["contradicting"] = con_ids
    edge["confidence_basis"] = {**old_basis, "claims": claims_basis, "claim_status": status_basis}

    for s in claim_sources(sup + con, papers, today):
        if s not in edge["sources"]:
            edge["sources"].append(s)

    attributes = edge.setdefault("attributes", {})
    # a supporting claim verifies a curated seed: an extracted edge is no longer curated_unverified
    verified = bool(sup) and edge["evidence_class"] == "extracted"
    kept = [l for l in (attributes.get("labels") or []) if l not in CLAIM_LABELS and not (verified and l == CURATED_LABEL)]
    merged = kept + [l for l in labels if l not in kept]
    if variant_specific and VARIANT_SPECIFIC_LABEL not in merged:
        merged.append(VARIANT_SPECIFIC_LABEL)
    attributes["labels"] = merged
    if "preprint" in labels:
        attributes["preprint"] = True
    # bench work or reviews alone never carry a claim-derived mechanism edge above 0.5
    conf_rubric.apply_in_vitro_only(edge, sup)
    return outcome


# --------------------------------------------------------------------------------------
# papers and investigators
# --------------------------------------------------------------------------------------


def _paper_node(paper: Any, scrub: Scrubber) -> dict[str, Any]:
    raw = paper.model_dump(mode="json") if hasattr(paper, "model_dump") else dict(paper)
    props_in = dict(raw.get("props") or {})
    props: dict[str, Any] = {}
    for key in PAPER_PROPS_KEEP:
        if key in props_in and props_in[key] is not None:
            props[key] = props_in[key]
    for key in ("title", "abstract", "journal"):
        if isinstance(props.get(key), str):
            props[key] = scrub(props[key])
    pid = str(raw["id"])
    if "pmid" not in props and pid.startswith(PMID_PREFIX):
        props["pmid"] = pid[len(PMID_PREFIX):]
    if "doi" not in props and pid.startswith(DOI_PREFIX):
        props["doi"] = pid[len(DOI_PREFIX):]
    props["preprint"] = bool(props.get("preprint")) or pid.startswith(DOI_PREFIX)
    authors_out: list[Any] = []
    for entry in props_in.get("authors") or []:
        if isinstance(entry, Mapping):
            cleaned = {k: (scrub(v) if isinstance(v, str) else v) for k, v in entry.items() if v not in (None, "")}
            if cleaned:
                authors_out.append(cleaned)
        elif isinstance(entry, str) and entry.strip():
            authors_out.append(scrub(entry))
    props["authors"] = authors_out
    return {
        "id": pid,
        "type": NodeType.Paper.value,
        "label": scrub(str(raw.get("label") or props.get("title") or pid)),
        "synonyms": [scrub(s) for s in (raw.get("synonyms") or []) if isinstance(s, str)],
        "props": props,
    }


MATCH_ORCID = "orcid"
MATCH_NAME = "name"


@dataclass
class _Investigator:
    id: str
    name: str
    orcid: str | None
    affiliations: list[str] = field(default_factory=list)
    papers: list[str] = field(default_factory=list)
    papers_by_match: dict[str, list[str]] = field(default_factory=lambda: {MATCH_ORCID: [], MATCH_NAME: []})

    def add(self, paper_id: str, affiliation: str | None, matched_by: str) -> None:
        if paper_id not in self.papers:
            self.papers.append(paper_id)
        if paper_id not in self.papers_by_match[matched_by]:
            self.papers_by_match[matched_by].append(paper_id)
        aff = _normalize_ws(affiliation or "")
        if aff and aff not in self.affiliations and len(self.affiliations) < MAX_AFFILIATIONS:
            self.affiliations.append(aff)

    def absorb(self, other: _Investigator, paper_order: Mapping[str, int]) -> None:
        """Fold a name-only record into this ORCID record: papers in bundle order, affiliations
        capped, the name-matched papers recorded as such."""
        for pid in other.papers:
            if pid not in self.papers:
                self.papers.append(pid)
            if pid not in self.papers_by_match[MATCH_ORCID] and pid not in self.papers_by_match[MATCH_NAME]:
                self.papers_by_match[MATCH_NAME].append(pid)
        self.papers.sort(key=lambda p: paper_order.get(p, len(paper_order)))
        for key in self.papers_by_match:
            self.papers_by_match[key].sort(key=lambda p: paper_order.get(p, len(paper_order)))
        for aff in other.affiliations:
            if aff not in self.affiliations and len(self.affiliations) < MAX_AFFILIATIONS:
                self.affiliations.append(aff)
        if not self.name and other.name:
            self.name = other.name

    @property
    def match_confidence(self) -> str:
        """``high``: an ORCID on every paper; ``mixed``: an ORCID record that also absorbed
        name-only entries; ``low``: no ORCID at all (README section 5)."""
        if not self.orcid:
            return "low"
        return "mixed" if self.papers_by_match[MATCH_NAME] else "high"

    def matched_by(self, paper_id: str) -> str:
        return MATCH_ORCID if paper_id in self.papers_by_match[MATCH_ORCID] else MATCH_NAME


def _author_fields(entry: Any, scrub: Scrubber) -> tuple[str, str | None, str | None]:
    """``(name, affiliation, orcid)`` from a string or ``{name, affiliation, orcid}`` entry, scrubbed."""
    if isinstance(entry, Mapping):
        name = entry.get("name")
        affiliation = entry.get("affiliation")
        orcid = entry.get("orcid")
    else:
        name, affiliation, orcid = entry, None, None
    name = _normalize_ws(scrub(str(name))) if isinstance(name, str) else ""
    affiliation = scrub(affiliation) if isinstance(affiliation, str) else None
    orcid_s = _ORCID_URL.sub("", str(orcid).strip()).strip("/") if isinstance(orcid, str) and orcid.strip() else None
    return name, affiliation, orcid_s


def _role_label(n_papers: int, topics: list[str]) -> str:
    noun = "paper" if n_papers == 1 else "papers"
    label = f"author of {n_papers} {noun}"
    if topics:
        label += " on " + ", ".join(topics[:ROLE_LABEL_TOPICS])
    return label


def _investigators(
    bundle: Any,
    paper_topics: Mapping[str, dict[str, list[str]]],  # paper id -> {topic node id: [claim ids]}
    nodes: Mapping[str, Mapping[str, Any]],
    papers: Mapping[str, Mapping[str, Any]],
    scrub: Scrubber,
    today: str,
    min_papers: int,
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]], dict[str, Any]]:
    people: dict[str, _Investigator] = {}
    skipped_nameless = 0
    paper_order = {p.id: i for i, p in enumerate(bundle.papers)}
    orcids_by_slug: dict[str, set[str]] = defaultdict(set)  # name slug -> ORCIDs seen with that name
    for paper in bundle.papers:
        for entry in (paper.props or {}).get("authors") or []:
            name, affiliation, orcid = _author_fields(entry, scrub)
            slug = slugify(name)
            if orcid:
                inv_id = f"orcid:{orcid}"
                matched_by = MATCH_ORCID
                if slug:
                    orcids_by_slug[slug].add(orcid)
            else:
                if not slug:
                    skipped_nameless += 1
                    continue
                inv_id = f"person:{slug}"
                matched_by = MATCH_NAME
            person = people.get(inv_id)
            if person is None:
                person = people[inv_id] = _Investigator(inv_id, name or inv_id, orcid)
            elif not person.name and name:
                person.name = name
            person.add(paper.id, affiliation, matched_by)

    # PubMed carries an ORCID on some of a person's papers and not on others: a name-only record
    # whose exact name slug was seen with exactly ONE ORCID is the same person and folds into the
    # ORCID record (papers matched by name stay visible in papers_by_match / match_confidence
    # "mixed"); a name seen with two or more ORCIDs is ambiguous and the records stay apart.
    merged_by_name = 0
    ambiguous_names: dict[str, list[str]] = {}
    for slug in sorted(orcids_by_slug):
        person = people.get(f"person:{slug}")
        if person is None:
            continue
        orcids = sorted(orcids_by_slug[slug])
        if len(orcids) == 1:
            people[f"orcid:{orcids[0]}"].absorb(person, paper_order)
            del people[person.id]
            merged_by_name += 1
        else:
            ambiguous_names[slug] = [f"orcid:{o}" for o in orcids]

    inv_nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    merged_existing = 0
    skipped_collision = 0
    below_min = 0
    exported = 0
    for inv_id in sorted(people):
        person = people[inv_id]
        if len(person.papers) < min_papers:
            below_min += 1
            continue
        # topic -> (paper ids, claim ids) across this person's papers
        topics: dict[str, tuple[list[str], list[str]]] = defaultdict(lambda: ([], []))
        for pid in person.papers:
            for topic, claim_ids in (paper_topics.get(pid) or {}).items():
                topics[topic][0].append(pid)
                topics[topic][1].extend(claim_ids)
        if not topics:
            continue
        existing = nodes.get(inv_id)
        if existing is not None and str(existing.get("type")) != NodeType.Investigator.value:
            skipped_collision += 1
            continue
        ranked = sorted(topics.items(), key=lambda kv: (-len(kv[1][0]), _node_label(nodes, kv[0]).lower()))
        props = {
            "name": person.name,
            "affiliations": list(person.affiliations),
            "orcid": person.orcid,
            "match_confidence": person.match_confidence,
            "n_papers": len(person.papers),
            "papers": list(person.papers),
            "papers_by_match": {k: list(v) for k, v in person.papers_by_match.items()},
            "role_label": _role_label(len(person.papers), [_node_label(nodes, t) for t, _ in ranked]),
            "source": INVESTIGATOR_SOURCE,
        }
        if existing is not None:  # the exact same id already in the store (e.g. RePORTER): merge props
            merged_existing += 1
            ex_props = existing.setdefault("props", {})  # type: ignore[union-attr]
            ex_props.update({k: v for k, v in props.items() if k not in ex_props or k in ("n_papers", "papers", "role_label")})
            ex_props["papers"] = sorted(set(ex_props.get("papers") or []) | set(person.papers))
            ex_props["n_papers"] = len(ex_props["papers"])
        else:
            inv_nodes[inv_id] = {"id": inv_id, "type": NodeType.Investigator.value, "label": person.name, "synonyms": [], "props": props}
        exported += 1
        for pid in person.papers:
            edges[(inv_id, pid, REL_AUTHOR_OF)] = {
                "source": inv_id,
                "target": pid,
                "relation": REL_AUTHOR_OF,
                "source_db": CLAIMS_SOURCE,
                "evidence_class": "observed",
                "confidence": AUTHOR_OF_CONFIDENCE,
                "confidence_basis": {"rule": "author_of", "pubmed_record": pid, "match_confidence": props["match_confidence"], "matched_by": person.matched_by(pid)},
                "status": "supported",
                "sources": [_paper_source(pid, "", papers.get(pid), today)],
                "supporting": [],
                "contradicting": [],
                "attributes": {"labels": []},
            }
        for topic, (paper_ids, claim_ids) in ranked:
            uniq_papers = sorted(set(paper_ids))
            n = len(uniq_papers)
            edges[(inv_id, topic, REL_PUBLISHES_ON)] = {
                "source": inv_id,
                "target": topic,
                "relation": REL_PUBLISHES_ON,
                "source_db": CLAIMS_SOURCE,
                "evidence_class": "observed",
                "confidence": min(PUBLISHES_ON_CAP, PUBLISHES_ON_BASE + PUBLISHES_ON_PER_PAPER * n),
                "confidence_basis": {
                    "rule": "publishes_on",
                    "n_papers": n,
                    "paper_ids": uniq_papers,
                    "via_claims": sorted(set(claim_ids)),
                    "base": PUBLISHES_ON_BASE,
                    "per_paper": PUBLISHES_ON_PER_PAPER,
                    "cap": PUBLISHES_ON_CAP,
                },
                "status": "supported",
                "sources": [_paper_source(pid, "", papers.get(pid), today) for pid in uniq_papers],
                "supporting": [],
                "contradicting": [],
                "attributes": {"labels": []},
            }
    report = {
        "total": len(people),
        "exported": exported,
        "with_orcid": sum(1 for p in people.values() if p.orcid),
        "merged_by_name": merged_by_name,
        "ambiguous_names": ambiguous_names,
        "min_papers": int(min_papers),
        "below_min_papers": below_min,
        "merged_into_existing_nodes": merged_existing,
        "skipped_id_collision": skipped_collision,
        "skipped_nameless": skipped_nameless,
        "author_of_edges": sum(1 for k in edges if k[2] == REL_AUTHOR_OF),
        "publishes_on_edges": sum(1 for k in edges if k[2] == REL_PUBLISHES_ON),
    }
    return inv_nodes, edges, report


# --------------------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------------------


def integrate_claims(
    nodes: dict[str, dict[str, Any]],
    edges: dict[tuple[str, str, str], dict[str, Any]],
    bundle: Any,
    *,
    today: str | None = None,
    scrub: Callable[[str], Any] | None = None,
    investigator_min_papers: int = 1,
) -> EvidenceResult:
    """Integrate a :class:`pipeline.models.ClaimsBundle` into the build's drafts (module docstring).

    ``nodes`` and ``edges`` are mutated in place (upgrades, created Mechanism nodes); the
    additions come back in the :class:`EvidenceResult`. ``today`` dates coverage rows and papers
    without a year (ISO date; default: today, UTC). ``scrub`` is the dose scrubber (default
    :func:`pipeline.sources.clinvar.scrub_doses`); e-mails and phones are always scrubbed too.
    ``investigator_min_papers`` is a general export cap (an author of fewer papers in the bundle
    is counted, not exported); the build reads it from the seeds so the threshold is documented
    there, default 1 = every author with a ``publishes_on`` edge.
    """
    day = _today_iso(today)
    scrubber = Scrubber(scrub)
    result = EvidenceResult()
    coverage: list[tuple[str, str, str, str, int]] = []
    seen_cov: set[tuple[str, str, str, str, int]] = set()

    def cover(entity: str, query: str) -> None:
        row = (entity, COVERAGE_SOURCE, scrubber(query), day, 0)
        if row not in seen_cov:
            seen_cov.add(row)
            coverage.append(row)

    # -- papers --------------------------------------------------------------------------
    for p in bundle.papers:
        result.paper_nodes[p.id] = _paper_node(p, scrubber)
    papers: dict[str, dict[str, Any]] = result.paper_nodes

    # -- claims: scrub, resolve, plan ------------------------------------------------------
    resolve = _Resolver(nodes)
    gene_by_symbol: dict[str, str] = {}
    for nid, n in nodes.items():
        if str(n.get("type")) == NodeType.Gene.value:
            symbol = str((n.get("props") or {}).get("symbol") or n.get("label") or "").strip().upper()
            if symbol:
                gene_by_symbol.setdefault(symbol, nid)

    def ensure_mechanism(mech_id: str) -> None:
        if mech_id in nodes:
            return
        parsed = parse_mechanism_id(mech_id)
        assert parsed is not None
        symbol, direction, somatic = parsed
        props: dict[str, Any] = {"gene_symbol": symbol, "direction": direction, "source": CLAIMS_SOURCE}
        gene_id = gene_by_symbol.get(symbol.upper())
        if gene_id:
            props["gene"] = gene_id
        if somatic:
            props["origin"] = "somatic"
        node = {"id": mech_id, "type": NodeType.Mechanism.value, "label": mechanism_label(symbol, direction, somatic), "synonyms": [], "props": props}
        nodes[mech_id] = node
        result.mechanism_nodes[mech_id] = node

    claim_by_id: dict[str, dict[str, Any]] = {}
    plans_by_key: dict[tuple[str, str, str], list[tuple[dict[str, Any], bool]]] = defaultdict(list)
    paper_topics: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    unattached: dict[str, str] = {}
    dropped_quote: list[str] = []
    variant_cleared: list[str] = []
    resolved_by_prefix: Counter[str] = Counter()
    unresolved_by_prefix: Counter[str] = Counter()
    mapping_gaps: Counter[str] = Counter()
    contradicting_only = 0
    contradicting_off_mechanism = 0
    mentions = _MentionResolver(nodes, edges)
    mention_notes: dict[str, list[dict[str, str]]] = defaultdict(list)  # claim id -> resolved_mentions entries

    def resolve_mention(c: dict[str, Any], field_name: str, ep: _Endpoint, context: Iterable[str], endpoints: Iterable[_Endpoint]) -> _Endpoint:
        """A ``text:`` endpoint retried as a protein-change mention (module docstring)."""
        if ep.resolved or ep.how != "text":
            return ep
        outcome = mentions.resolve(ep.raw, mentions.genes_in(context, endpoints))
        if outcome.variant_id is None:
            return ep
        resolve.recount(ep, VARIANT_MENTION_HOW)
        ep.node_id = outcome.variant_id
        ep.node_type = NodeType.Variant.value
        note = {"claim": c["id"], "field": field_name, "resolved_from": scrubber(ep.raw), "resolved_to": outcome.variant_id, "method": VARIANT_MENTION_METHOD}
        mention_notes[c["id"]].append(note)
        mentions.resolved.append({**note, "tokens": outcome.tokens, "genes": outcome.genes})
        return ep

    for claim in bundle.claims:
        c = claim.model_dump(mode="json")
        # quote and abstract are scrubbed with the same function; the quote must still match
        c["quote"] = scrubber(c["quote"])
        abstract = (papers.get(c["paper"]) or {}).get("props", {}).get("abstract")
        if not (isinstance(abstract, str) and _normalize_ws(c["quote"]) and _normalize_ws(c["quote"]) in _normalize_ws(abstract)):
            dropped_quote.append(c["id"])
            continue
        s, o = resolve(c["subject"]), resolve(c["object"])
        v: _Endpoint | None = resolve(c["variant"]) if c.get("variant") else None
        # a text: subject / object / variant that names a variant by its protein change: the gene
        # context is the claim's own text (mentions and quote) plus its resolved endpoints
        texts = [c["subject"], c["object"], str(c.get("variant") or ""), c["quote"]]
        s = resolve_mention(c, "subject", s, texts, [o, *([v] if v else [])])
        o = resolve_mention(c, "object", o, texts, [s, *([v] if v else [])])
        if v is not None:
            v = resolve_mention(c, "variant", v, texts, [s, o])
        for ep in (s, o):
            (resolved_by_prefix if ep.resolved else unresolved_by_prefix)[ep.prefix] += 1
        c["subject"] = s.node_id if s.resolved else scrubber(c["subject"])
        c["object"] = o.node_id if o.resolved else scrubber(c["object"])
        for ep in (s, o):
            if ep.resolved and ep.node_type in (NodeType.Disease.value, NodeType.Mechanism.value):
                paper_topics[c["paper"]][str(ep.node_id)].append(c["id"])
                if ep.how == "mechanism":
                    ensure_mechanism(str(ep.node_id))
        variant: _Endpoint | None = None
        if v is not None:
            if v.resolved and v.node_type == NodeType.Variant.value:
                c["variant"] = v.node_id
                variant = v
            else:
                entity = str(s.node_id if s.resolved else o.node_id if o.resolved else COVERAGE_UNRESOLVED_ENTITY)
                cover(entity, f"{c['variant']} [claim variant is not a Variant node]")
                variant_cleared.append(c["id"])
                c["variant"] = ""
        claim_by_id[c["id"]] = c
        result.claims.append(c)

        if not (s.resolved and o.resolved):
            # one coverage row per unresolved side, keyed by the resolved side when there is one
            other = s.node_id if s.resolved else o.node_id if o.resolved else None
            for ep in (s, o):
                if not ep.resolved:
                    cover(str(other or COVERAGE_UNRESOLVED_ENTITY), ep.raw)
            reasons = [f"{side} {ep.raw!r} unresolved ({ep.how})" for side, ep in (("subject", s), ("object", o)) if not ep.resolved]
            unattached[c["id"]] = "; ".join(reasons)
            plans: list[_Plan] = []
        else:
            plans, problem = _plan(c["predicate"], s, o, edges, c["polarity"])
            if problem is not None:
                unattached[c["id"]] = problem
                if problem.startswith("contradicting "):
                    contradicting_off_mechanism += 1
                else:
                    mapping_gaps[f"{c['predicate']}: {s.node_type} -> {o.node_type}"] += 1
                cover(str(s.node_id), f"{c['predicate']} {s.raw} -> {o.raw} [{problem}]")
        # variant-specific route: Variant -> Mechanism carries the claim too (test 7)
        if c["predicate"] == PRED_HAS_MECHANISM and variant is not None and o.resolved and o.node_type == NodeType.Mechanism.value:
            if not (s.resolved and s.node_type == NodeType.Variant.value and s.node_id == variant.node_id):
                vplan = _attach_or_create(edges, str(variant.node_id), str(o.node_id), REL_HAS_MECHANISM)
                vplan.variant_specific = True
                plans.append(vplan)
        for plan in plans:
            vs = plan.variant_specific or (s.resolved and s.node_type == NodeType.Variant.value and variant is not None and s.node_id == variant.node_id)
            plans_by_key[plan.key].append((c, bool(vs)))
            if plan.key[2] == REL_HAS_MECHANISM and plan.key[1].startswith(MECH_PREFIX):
                ensure_mechanism(plan.key[1])

    # -- apply: create or upgrade edges ----------------------------------------------------
    created_by_relation: Counter[str] = Counter()
    upgraded_by_relation: Counter[str] = Counter()
    created_by_predicate: Counter[str] = Counter()
    upgraded_by_predicate: Counter[str] = Counter()
    upgrade_outcomes: Counter[str] = Counter()
    attached: set[str] = set()
    for key, entries in plans_by_key.items():
        claims_here = []
        seen_ids: set[str] = set()
        variant_specific = False
        for c, vs in entries:
            if c["id"] not in seen_ids:
                seen_ids.add(c["id"])
                claims_here.append(c)
            variant_specific = variant_specific or vs
        edge = edges.get(key)
        if edge is None:
            if not any(c["polarity"] == Polarity.supports.value for c in claims_here):
                contradicting_only += 1
                for c in claims_here:
                    unattached.setdefault(c["id"], f"only contradicting claims for {key[0]} -{key[2]}-> {key[1]} and no existing edge")
                continue
            edge = result.new_edges[key] = _new_edge(key)
            created_by_relation[key[2]] += 1
            for c in claims_here:
                created_by_predicate[c["predicate"]] += 1
            _apply_claims(edge, claims_here, claim_by_id, papers, day, variant_specific=variant_specific)
        else:
            outcome = _apply_claims(edge, claims_here, claim_by_id, papers, day, variant_specific=variant_specific)
            upgraded_by_relation[key[2]] += 1
            upgrade_outcomes[outcome] += 1
            for c in claims_here:
                upgraded_by_predicate[c["predicate"]] += 1
        notes = [n for c in claims_here for n in mention_notes.get(c["id"], [])]
        if notes:  # how a text: mention of this edge's claims became a Variant node id
            kept_notes = list(edge["confidence_basis"].get("resolved_mentions") or [])
            edge["confidence_basis"]["resolved_mentions"] = kept_notes + [n for n in notes if n not in kept_notes]
        for c in claims_here:
            attached.add(c["id"])
            unattached.pop(c["id"], None)

    # -- investigators ---------------------------------------------------------------------
    inv_nodes, inv_edges, inv_report = _investigators(bundle, paper_topics, nodes, papers, scrubber, day, max(1, int(investigator_min_papers)))
    result.investigator_nodes = inv_nodes
    for key, e in inv_edges.items():
        result.new_edges.setdefault(key, e)

    result.coverage_rows = coverage
    result.unattached = [{"id": cid, "reason": unattached[cid]} for cid in sorted(unattached)]
    mech_with_edges = {k[0] for k in list(edges) + list(result.new_edges)} | {k[1] for k in list(edges) + list(result.new_edges)}
    # which Variants carry a variant-specific mechanism route (claims on a Variant -> Mechanism
    # edge): the report says it per variant so nobody has to infer it from the hero disease
    variant_routes: dict[str, list[str]] = defaultdict(list)
    for (s, t, rel), e in list(edges.items()) + list(result.new_edges.items()):
        if rel == REL_HAS_MECHANISM and str((nodes.get(s) or {}).get("type")) == NodeType.Variant.value and (e.get("supporting") or e.get("contradicting")):
            variant_routes[s].append(t)
    result.report = {
        "claims": {
            "in_bundle": len(bundle.claims),
            "in_graph": len(result.claims),
            "attached": len(attached),
            "unattached": len(result.unattached),
            "unattached_reasons": dict(Counter(re.sub(r"'[^']*'", "'...'", u["reason"]) for u in result.unattached)),
            "dropped_quote_mismatch_after_scrub": dropped_quote,
            "variant_cleared": variant_cleared,
            "variant_mentions": mentions.report(),
            "contradicting_only_no_edge": contradicting_only,
            "contradicting_not_on_mechanism_edge": contradicting_off_mechanism,
        },
        "endpoints": {
            "resolved_by_prefix": dict(sorted(resolved_by_prefix.items())),
            "unresolved_by_prefix": dict(sorted(unresolved_by_prefix.items())),
            "resolution_how": dict(sorted(resolve.kinds.items())),
            "mapping_gaps": dict(sorted(mapping_gaps.items())),
        },
        "edges": {
            "created_by_relation": dict(sorted(created_by_relation.items())),
            "upgraded_by_relation": dict(sorted(upgraded_by_relation.items())),
            "created_by_predicate": dict(sorted(created_by_predicate.items())),
            "upgraded_by_predicate": dict(sorted(upgraded_by_predicate.items())),
            "upgrade_outcomes": dict(sorted(upgrade_outcomes.items())),
            "investigator_edges": inv_report["author_of_edges"] + inv_report["publishes_on_edges"],
        },
        "mechanism_nodes_created": sorted(result.mechanism_nodes),
        "mechanism_nodes_created_without_edges": sorted(m for m in result.mechanism_nodes if m not in mech_with_edges),
        "variant_mechanism_routes": {v: sorted(set(ms)) for v, ms in sorted(variant_routes.items())},
        "papers": {
            "in_bundle": len(bundle.papers),
            "exported": len(result.paper_nodes),
            "preprints": sum(1 for p in result.paper_nodes.values() if p["props"].get("preprint")),
        },
        "investigators": inv_report,
        "scrubbed": scrubber.summary(),
        "scrubbed_emails": scrubber.emails,
        "coverage_rows": len(coverage),
    }
    return result
