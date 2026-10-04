"""The ``graph.json`` contract (README section 4), as Pydantic v2 models.

``docs/graph.schema.json`` is generated from :class:`Graph` by ``python -m pipeline.schema``.
Frozen at the end of M0: changing anything here needs team sign-off.

Two levels of checking
----------------------
* The JSON Schema is the floor: shapes, enums, numeric ranges and ``minLength: 1`` on every
  field that must carry a value. The TypeScript frontend and any ``jsonschema`` run see only
  this level.
* The Pydantic models add the cross-field and cross-list rules listed under "Validation rules"
  that JSON Schema cannot express. The **file-level contract is strict**: :meth:`Graph.load`
  validates with ``strict=True``, so a ``"0.5"`` confidence, a ``"3"`` hit count or a ``true``
  ``n_patients`` is rejected exactly as the JSON Schema rejects it. ``Graph.model_validate(dict)``
  stays lax for in-memory construction; anything written to disk must pass :meth:`Graph.load`.

Top-level shape
---------------
::

    {"meta": {...}, "nodes": [...], "edges": [...], "claims": [...], "clusters": [...]}

Extensions live ONLY inside the free-form dicts ``Node.props``, ``Edge.attributes``,
``Edge.confidence_basis`` and ``Cluster.why``. Every structured model is ``extra="forbid"``.

ID conventions
--------------
The pipeline resolves every external identifier; nothing below is ever hand-typed (README hard
rule 2). These are formats, not values.

=============  =====================================================================
Node type      ID format
=============  =====================================================================
Gene           ``ensembl:ENSG...`` (``props.symbol`` holds the HGNC symbol)
Disease        ``MONDO:...`` or another CURIE provided by the backbone
Phenotype      ``HP:...``
Pathway        ``REACT:R-HSA-...``
Drug           whatever CURIE the backbone provides
Variant        ``clinvar:<variation id>``
Mechanism      ``mech:<GENE>:<GoF|LoF|DN>[:somatic]`` (internal; GENE is the HGNC symbol)
Paper          ``pmid:<n>`` or ``doi:<doi>`` (preprints, PMC-only papers)
Study          ``nct:NCT...``
Investigator   ``orcid:<id>`` or internal ``person:<slug>``
PatientGroup   internal ``org:<slug>``
Registry       internal ``org:<slug>``
Claim          ``c_000001`` (the same id as the entry under ``Graph.claims``; see "Claim nodes")
=============  =====================================================================

Disease/Phenotype/Pathway ids are normalised to colon CURIEs (``MONDO:0013599``, ``HP:0001234``,
``EFO:0002429``, ``Orphanet:35078``) even when the backbone spells them with underscores; the
backbone's original id is kept in ``props.backbone_id``.

Edges are ``e_000001``, clusters ``cl_001``. Mock and test data use ``mock:<type>:<n>`` ids
and labels starting with ``"Mock "`` so nothing fabricated can be mistaken for a resolved record.

Id namespaces: node ids and edge ids never collide (Cytoscape.js requires element ids to be
unique across nodes and edges), edge ids and claim ids never collide (``/explain`` cites both
in one namespace), and a node id equals a claim id only when that node is a ``Claim`` node.

Dates
-----
``meta.built_at`` is an ISO 8601 timestamp (``datetime.fromisoformat`` must parse it).
``sources[].date``, ``coverage[].searched[].date`` and ``meta.sources[].retrieved_at`` are
ISO 8601 dates or timestamps; reduced precision (``YYYY`` or ``YYYY-MM``) is accepted because
publication dates are often month- or year-only and a day must never be invented.

Edge sources
------------
``Edge.sources[].id`` must have the shape of its ``kind``: ``pmid`` is digits, ``pmc`` is
``PMC<digits>``, ``doi`` is ``10.<registrant>/<suffix>``, ``nct`` is ``NCT<8 digits>``, ``url``
starts with ``http://`` or ``https://``. ``db`` ids (database record ids) are unconstrained,
so **mock sources use kind ``"db"`` with a ``mock:`` id** (the shape check is a tripwire for a
hand-typed or malformed identifier reaching graph.json, not a resolver).

``meta.sources`` has at least one record and every record has a non-empty ``retrieved_at``
(ISO 8601) and ``license`` (README section 5 license table; hard rule 6 reproducibility).
``version`` may be ``""`` when a source has no meaningful version. The mock uses the date the
mock was written and ``license: "mock"``.

Paper nodes
-----------
``props.title``, ``props.abstract``, ``props.year``, ``props.journal`` and ``props.authors``
(list of strings) when known. The abstract is stored so that a claim's quote can be checked
against it from ``graph.json`` alone (README hard rule 5): every Paper node referenced by a
claim MUST carry a non-empty ``props.abstract``, and the claim's ``quote`` must occur in it
after whitespace normalization (runs of whitespace collapsed to one space, ends stripped).
Paper nodes with no claims (digest-only papers, author-list sources) may omit the abstract.

Claims
------
A :class:`Claim` keeps the README fields and adds the required ``paper`` field: the id of the
Paper node it comes from (must be a ``Paper`` node). ``pmid`` stays a string and is ``""`` when
the paper has no PMID; when ``paper`` is ``pmid:<n>`` then ``pmid`` must be ``<n>``, and when
``pmid`` is non-empty then ``paper`` must be ``pmid:<pmid>``. ``variant``, when non-empty, is
the id of a ``Variant`` node.

Claim nodes
-----------
``NodeType.Claim`` exists so a claim can be an edge endpoint (e.g. Claim -> Paper or
Claim -> Investigator per README section 4). The pipeline emits a Claim node **only** for a
claim that is an edge endpoint, never one per claim; the ``claims[]`` entry stays the single
source of truth (quote, polarity, study type), and the node carries only ``label``. Every
Claim node id must exist in ``claims[]``; a node of any other type never reuses a claim id.

Status conventions
------------------
``status`` is the single dominant value; ``attributes.labels`` carries the full display set.

* Supporting and contradicting claims both present => ``contested``. **Contested dominates
  small_n**: when every study is also < 10 patients, ``status`` is ``contested`` and ``"small_n"``
  is carried in ``attributes.labels``.
* ``small_n`` requires at least one claim (it describes the studies behind the edge).
* ``extracted`` edges need at least one claim; an extracted edge with no claims has no evidence.
* ``observed`` (database) edges MAY carry claims: a backbone edge enriched by papers keeps
  ``evidence_class: observed`` and takes the status its claims imply.
* ``inferred`` edges (similarity, pathway inference, rules) that are NOT flagged by a rule use
  ``status: "supported"`` and MUST be rendered with the ``evidence_class`` badge, never as a
  bare "supported". Only rule violations (e.g. "direction mismatch") use
  ``inferred_unsupported``, which never lists supporting claims. An inferred edge therefore
  never carries both polarities; evidence on both sides makes the edge ``extracted`` or
  ``observed`` and ``contested``.

Clusters
--------
Leiden produces a partition and ``robust`` is defined per membership, so clusters are disjoint:
a node is in at most one cluster, every cluster has at least one member, no member is repeated.

Standard optional ``Edge.attributes`` keys
-------------------------------------------
Type-checked only as a free dict; these are the agreed names and value shapes.

=================  ===========================================================================
Key                Value
=================  ===========================================================================
``origin``         ``"germline"`` | ``"somatic"`` (disease-gene and disease-mechanism edges)
``labels``         ``list[str]``, the full display label set, e.g. ``["promising", "contested", "small_n"]``
``reason``         ``str``, why an edge carries its status, e.g. ``"direction mismatch"``
``verdict``        ``"reusable_now"`` | ``"adaptable_needs_review"`` | ``"not_transferable"`` (asset edges)
``differs``        ``list[str]``, what differs between the two diseases for this asset
``needs_review``   list of ``"biological"`` | ``"eligibility"``
``preprint``       ``bool``, the evidence comes from a preprint
``contact_source`` ``str`` URL of the public page a contact route was taken from (never a guessed address)
=================  ===========================================================================

Validation rules enforced here
------------------------------
Field level (also visible in the JSON Schema):

* Every id, label, relation, quote, subject/predicate/object, source id/date, coverage
  source/query/date, ``built_at``, ``retrieved_at`` and ``license`` is a non-empty string
  (``minLength: 1``); the models additionally reject whitespace-only values.
  ``pmid``, ``variant`` and ``version`` may be ``""`` (meaningful: no PMID, not
  variant-specific, no version).
* ``confidence`` in [0, 1]; ``confidence_basis`` non-empty (raw inputs are always kept);
  ``n_patients`` and ``hits`` >= 0; every edge has at least one source; every cluster has at
  least one member; ``meta.sources`` has at least one record.

Edge level:

* ``evidence_class == inferred`` implies ``confidence <= 0.5`` and
  ``status in {supported, inferred_unsupported}``.
* ``status == inferred_unsupported`` implies ``evidence_class == inferred`` and no supporting claims.
* ``status == contested`` iff ``supporting`` and ``contradicting`` are both non-empty.
* ``status == small_n`` implies at least one claim; ``evidence_class == extracted`` implies at
  least one claim.
* ``source != target`` (no self-loops); no duplicate claim ids within ``supporting`` or
  ``contradicting``; the two lists are disjoint.
* ``sources[].id`` matches the shape of its ``kind``; dates are ISO 8601.

Graph level (referential integrity):

* ``edge.source``/``target`` are node ids; ``edge.supporting``/``contradicting`` are claim ids
  with polarity ``supports``/``contradicts`` respectively; ``cluster.members`` are node ids.
* ``claim.paper`` is a ``Paper`` node with a non-empty ``props.abstract`` containing the quote
  (whitespace-normalized); ``claim.pmid`` agrees with ``claim.paper``; ``claim.variant`` is a
  ``Variant`` node when non-empty.
* Ids are unique within nodes, edges, claims and clusters; node ids and edge ids are disjoint;
  edge ids and claim ids are disjoint; a node id equals a claim id only for a ``Claim`` node,
  and every ``Claim`` node has a ``claims[]`` entry.
* Clusters are disjoint and have no repeated members.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

__all__ = [
    "Claim",
    "ClaimsBundle",
    "Cluster",
    "Coverage",
    "CoverageSearch",
    "Edge",
    "EdgeSource",
    "EdgeStatus",
    "EvidenceClass",
    "Graph",
    "IsoDate",
    "IsoTimestamp",
    "Meta",
    "Node",
    "NodeType",
    "NonEmptyStr",
    "Polarity",
    "SourceKind",
    "SourceRecord",
    "StudyType",
]


# --------------------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------------------
# StrEnum, not (str, Enum): f"{node.type}" and str(node.type) give "Gene", not "NodeType.Gene",
# so ids, labels, cache keys and log lines built from members are correct. JSON Schema is identical.


class NodeType(StrEnum):
    Gene = "Gene"
    Variant = "Variant"
    Mechanism = "Mechanism"
    Disease = "Disease"
    Phenotype = "Phenotype"
    Pathway = "Pathway"
    Drug = "Drug"
    Claim = "Claim"
    Paper = "Paper"
    Investigator = "Investigator"
    PatientGroup = "PatientGroup"
    Registry = "Registry"
    Study = "Study"


class EvidenceClass(StrEnum):
    """observed: read from a database; extracted: quote-verified claim from a paper;
    inferred: produced by a rule or similarity score (never above 0.5 confidence)."""

    observed = "observed"
    extracted = "extracted"
    inferred = "inferred"


class EdgeStatus(StrEnum):
    supported = "supported"
    contested = "contested"
    small_n = "small_n"
    inferred_unsupported = "inferred_unsupported"


class SourceKind(StrEnum):
    pmid = "pmid"
    pmc = "pmc"
    doi = "doi"
    db = "db"
    url = "url"
    nct = "nct"


class Polarity(StrEnum):
    supports = "supports"
    contradicts = "contradicts"


class StudyType(StrEnum):
    trial = "trial"
    cohort = "cohort"
    case_series = "case_series"
    case_report = "case_report"
    in_vitro = "in_vitro"
    review = "review"


# --------------------------------------------------------------------------------------
# Field types
# --------------------------------------------------------------------------------------


def _reject_blank(v: str) -> str:
    if not v.strip():
        raise ValueError("must not be blank (whitespace only)")
    return v


# JSON Schema sees minLength: 1; the model additionally rejects whitespace-only strings, so an
# empty or blank quote can never string-match every abstract (hard rule 5) and an edge source
# can never satisfy "sources, date" (hard rule 4) with nothing in it.
NonEmptyStr = Annotated[str, StringConstraints(min_length=1), AfterValidator(_reject_blank)]

_YEAR_OR_MONTH = re.compile(r"^\d{4}(?:-(?:0[1-9]|1[0-2]))?$")


def _check_iso_timestamp(v: str) -> str:
    try:
        datetime.fromisoformat(v)
    except ValueError:
        raise ValueError(f"not an ISO 8601 date or timestamp: {v!r}") from None
    return v


def _check_iso_date(v: str) -> str:
    """ISO 8601 date or timestamp; YYYY and YYYY-MM are accepted (publication dates are often
    month- or year-only and a day must never be invented)."""
    if _YEAR_OR_MONTH.match(v):
        return v
    return _check_iso_timestamp(v)


IsoTimestamp = Annotated[NonEmptyStr, AfterValidator(_check_iso_timestamp)]
IsoDate = Annotated[NonEmptyStr, AfterValidator(_check_iso_date)]


def _normalize_ws(s: str) -> str:
    """Whitespace normalization used by the quote check: runs collapse to one space, ends stripped."""
    return " ".join(s.split())


# --------------------------------------------------------------------------------------
# Base
# --------------------------------------------------------------------------------------


class _Strict(BaseModel):
    """Every structured model forbids unknown keys; free-form dicts stay free."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------------------


class SourceRecord(_Strict):
    """A data source the build read from, with its version, retrieval date and license.
    ``retrieved_at`` and ``license`` are required (README section 5 license table)."""

    name: NonEmptyStr
    version: str = Field(default="", description='"" when the source has no meaningful version')
    retrieved_at: IsoDate = Field(description="ISO 8601 date or timestamp")
    license: NonEmptyStr


class CoverageSearch(_Strict):
    """One search the pipeline ran for an entity. Zero hits are recorded, not dropped
    (README hard rule 3: failed lookups are data)."""

    source: NonEmptyStr
    query: NonEmptyStr
    date: IsoDate = Field(description="ISO 8601 date or timestamp")
    hits: int = Field(ge=0)


class Coverage(_Strict):
    searched: list[CoverageSearch] = Field(default_factory=list)


class Meta(_Strict):
    built_at: IsoTimestamp = Field(description="ISO 8601 timestamp of the build")
    sources: list[SourceRecord] = Field(min_length=1)
    coverage: dict[str, Coverage] = Field(
        description="Keyed by entity id: every search run for that entity, including empty ones"
    )


# --------------------------------------------------------------------------------------
# nodes
# --------------------------------------------------------------------------------------


class Node(_Strict):
    id: NonEmptyStr = Field(description="Resolved CURIE or internal id; see the module docstring")
    type: NodeType
    label: NonEmptyStr
    synonyms: list[str] = Field(default_factory=list)
    props: dict[str, Any] = Field(default_factory=dict, description="Free-form, type-specific")


# --------------------------------------------------------------------------------------
# edges
# --------------------------------------------------------------------------------------

# Shape of ``EdgeSource.id`` per kind. ``db`` is unconstrained (mock data uses it).
_SOURCE_ID_SHAPE: dict[SourceKind, re.Pattern[str]] = {
    SourceKind.pmid: re.compile(r"^\d+$"),
    SourceKind.pmc: re.compile(r"^PMC\d+$"),
    SourceKind.doi: re.compile(r"^10\.\d{4,9}/\S+$"),
    SourceKind.nct: re.compile(r"^NCT\d{8}$"),
    SourceKind.url: re.compile(r"^https?://"),
}


class EdgeSource(_Strict):
    kind: SourceKind
    id: NonEmptyStr = Field(description="PMID, PMC id, DOI, database record id, URL or NCT number")
    date: IsoDate = Field(description="ISO 8601 publication or retrieval date")

    @model_validator(mode="after")
    def _check_id_shape(self) -> EdgeSource:
        shape = _SOURCE_ID_SHAPE.get(self.kind)
        if shape is not None and not shape.search(self.id):
            raise ValueError(
                f"source id {self.id!r} does not have the shape of a {self.kind.value} "
                f"({shape.pattern}); mock sources use kind 'db'"
            )
        return self


class Edge(_Strict):
    id: NonEmptyStr
    source: NonEmptyStr = Field(description="Node id")
    target: NonEmptyStr = Field(description="Node id")
    relation: NonEmptyStr
    evidence_class: EvidenceClass
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_basis: dict[str, Any] = Field(
        min_length=1, description="Raw inputs the confidence was computed from; never empty"
    )
    status: EdgeStatus
    sources: list[EdgeSource] = Field(min_length=1)
    supporting: list[str] = Field(description="Claim ids (polarity 'supports')")
    contradicting: list[str] = Field(description="Claim ids (polarity 'contradicts')")
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Free-form; standard keys in the module docstring"
    )

    @model_validator(mode="after")
    def _check_consistency(self) -> Edge:
        problems: list[str] = []
        has_claims = bool(self.supporting or self.contradicting)

        if self.source == self.target:
            problems.append(f"self-loop on {self.source!r}")
        if len(set(self.supporting)) != len(self.supporting):
            problems.append("duplicate claim ids in 'supporting'")
        if len(set(self.contradicting)) != len(self.contradicting):
            problems.append("duplicate claim ids in 'contradicting'")
        both = set(self.supporting) & set(self.contradicting)
        if both:
            problems.append(
                f"claims listed as both supporting and contradicting: {sorted(both)}"
            )

        if self.evidence_class is EvidenceClass.inferred:
            if self.confidence > 0.5:
                problems.append(
                    f"inferred edges are capped at confidence 0.5 (got {self.confidence})"
                )
            if self.status not in (EdgeStatus.supported, EdgeStatus.inferred_unsupported):
                problems.append(
                    "inferred edges carry status 'supported' (rendered with the evidence_class "
                    f"badge) or 'inferred_unsupported' (got {self.status.value!r})"
                )
        if self.evidence_class is EvidenceClass.extracted and not has_claims:
            problems.append("extracted edges need at least one supporting or contradicting claim")

        if self.status is EdgeStatus.inferred_unsupported:
            if self.evidence_class is not EvidenceClass.inferred:
                problems.append(
                    "status 'inferred_unsupported' requires evidence_class 'inferred' "
                    f"(got {self.evidence_class.value!r})"
                )
            if self.supporting:
                problems.append("status 'inferred_unsupported' cannot list supporting claims")
        if self.status is EdgeStatus.contested and not (self.supporting and self.contradicting):
            problems.append(
                "status 'contested' requires both supporting and contradicting claim ids"
            )
        if self.supporting and self.contradicting and self.status is not EdgeStatus.contested:
            problems.append(
                "edges with both supporting and contradicting claims must have status "
                f"'contested' (got {self.status.value!r}); contested dominates small_n, "
                "carry 'small_n' in attributes.labels"
            )
        if self.status is EdgeStatus.small_n and not has_claims:
            problems.append("status 'small_n' requires at least one claim")

        if problems:
            raise ValueError(f"edge {self.id!r}: " + "; ".join(problems))
        return self


# --------------------------------------------------------------------------------------
# claims
# --------------------------------------------------------------------------------------


class Claim(_Strict):
    id: NonEmptyStr
    pmid: str = Field(default="", description='"" when the paper has no PMID (preprints)')
    paper: NonEmptyStr = Field(description="Id of the Paper node this claim was extracted from")
    quote: NonEmptyStr = Field(
        description="Verbatim span that string-matches the source abstract after whitespace normalization"
    )
    polarity: Polarity
    subject: NonEmptyStr
    predicate: NonEmptyStr
    object: NonEmptyStr
    variant: str = Field(default="", description="Variant node id when the claim is variant-specific")
    study_type: StudyType
    n_patients: int | None = Field(default=None, ge=0)


# --------------------------------------------------------------------------------------
# clusters
# --------------------------------------------------------------------------------------


class Cluster(_Strict):
    id: NonEmptyStr
    members: list[str] = Field(min_length=1, description="Node ids; clusters are disjoint")
    robust: bool = Field(description="Membership held across all three Leiden weightings")
    why: dict[str, Any] = Field(default_factory=dict, description="Free-form explanation inputs")


# --------------------------------------------------------------------------------------
# graph
# --------------------------------------------------------------------------------------

_MAX_REPORTED_PROBLEMS = 25
_PMID_PREFIX = "pmid:"


def _unique_ids(kind: str, ids: Iterable[str], problems: list[str]) -> set[str]:
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            problems.append(f"duplicate {kind} id {item!r}")
        seen.add(item)
    return seen


class Graph(_Strict):
    meta: Meta
    nodes: list[Node]
    edges: list[Edge]
    claims: list[Claim]
    clusters: list[Cluster]

    @model_validator(mode="after")
    def _check_referential_integrity(self) -> Graph:
        problems: list[str] = []
        node_ids = _unique_ids("node", (n.id for n in self.nodes), problems)
        edge_ids = _unique_ids("edge", (e.id for e in self.edges), problems)
        claim_ids = _unique_ids("claim", (c.id for c in self.claims), problems)
        _unique_ids("cluster", (c.id for c in self.clusters), problems)
        node_by_id = {n.id: n for n in self.nodes}
        claim_by_id = {c.id: c for c in self.claims}

        # Id namespaces (see the module docstring).
        for shared in sorted(node_ids & edge_ids):
            problems.append(f"id {shared!r} is both a node id and an edge id")
        for shared in sorted(edge_ids & claim_ids):
            problems.append(f"id {shared!r} is both an edge id and a claim id")
        for n in self.nodes:
            if n.type is NodeType.Claim:
                if n.id not in claim_ids:
                    problems.append(f"Claim node {n.id!r} has no entry in claims[]")
            elif n.id in claim_ids:
                problems.append(f"{n.type.value} node {n.id!r} reuses a claim id")

        for e in self.edges:
            if e.source not in node_ids:
                problems.append(f"edge {e.id!r}: source {e.source!r} is not a node id")
            if e.target not in node_ids:
                problems.append(f"edge {e.id!r}: target {e.target!r} is not a node id")
            for cid in e.supporting:
                c = claim_by_id.get(cid)
                if c is None:
                    problems.append(f"edge {e.id!r}: supporting {cid!r} is not a claim id")
                elif c.polarity is not Polarity.supports:
                    problems.append(
                        f"edge {e.id!r}: supporting claim {cid!r} has polarity {c.polarity.value!r}"
                    )
            for cid in e.contradicting:
                c = claim_by_id.get(cid)
                if c is None:
                    problems.append(f"edge {e.id!r}: contradicting {cid!r} is not a claim id")
                elif c.polarity is not Polarity.contradicts:
                    problems.append(
                        f"edge {e.id!r}: contradicting claim {cid!r} has polarity {c.polarity.value!r}"
                    )

        for c in self.claims:
            paper = node_by_id.get(c.paper)
            if paper is None:
                problems.append(f"claim {c.id!r}: paper {c.paper!r} is not a node id")
            elif paper.type is not NodeType.Paper:
                problems.append(
                    f"claim {c.id!r}: paper {c.paper!r} is a {paper.type.value} node, not a Paper"
                )
            else:
                abstract = paper.props.get("abstract")
                if not (isinstance(abstract, str) and abstract.strip()):
                    problems.append(
                        f"claim {c.id!r}: Paper {c.paper!r} has no props.abstract, "
                        "so the quote cannot be checked (hard rule 5)"
                    )
                elif _normalize_ws(c.quote) not in _normalize_ws(abstract):
                    problems.append(
                        f"claim {c.id!r}: quote does not occur in the abstract of {c.paper!r} "
                        "after whitespace normalization (hard rule 5)"
                    )
            if c.paper.startswith(_PMID_PREFIX):
                if c.pmid != c.paper[len(_PMID_PREFIX) :]:
                    problems.append(
                        f"claim {c.id!r}: pmid {c.pmid!r} disagrees with paper {c.paper!r}"
                    )
            elif c.pmid:
                problems.append(
                    f"claim {c.id!r}: pmid {c.pmid!r} is set but paper {c.paper!r} "
                    f"is not {_PMID_PREFIX + c.pmid!r}"
                )
            if c.variant:
                variant = node_by_id.get(c.variant)
                if variant is None:
                    problems.append(f"claim {c.id!r}: variant {c.variant!r} is not a node id")
                elif variant.type is not NodeType.Variant:
                    problems.append(
                        f"claim {c.id!r}: variant {c.variant!r} is a {variant.type.value} node, "
                        "not a Variant"
                    )

        seen_members: dict[str, str] = {}
        for cl in self.clusters:
            in_this: set[str] = set()
            for m in cl.members:
                if m not in node_ids:
                    problems.append(f"cluster {cl.id!r}: member {m!r} is not a node id")
                if m in in_this:
                    problems.append(f"cluster {cl.id!r}: duplicate member {m!r}")
                    continue
                in_this.add(m)
                first = seen_members.setdefault(m, cl.id)
                if first != cl.id:
                    problems.append(
                        f"node {m!r} is in clusters {first!r} and {cl.id!r}; clusters are a partition"
                    )

        if problems:
            shown = problems[:_MAX_REPORTED_PROBLEMS]
            hidden = len(problems) - len(shown)
            tail = f"; ... and {hidden} more" if hidden else ""
            raise ValueError("referential integrity: " + "; ".join(shown) + tail)
        return self

    # ---- helpers -------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> Graph:
        """Read and validate a graph.json file.

        This is the file-level contract and it is strict: no type coercion, so the result
        is exactly what the JSON Schema (and the TypeScript frontend) accepts. In-memory
        construction via ``Graph.model_validate(dict)`` stays lax.
        """
        return cls.model_validate_json(Path(path).read_bytes(), strict=True)

    def dump(self, path: str | Path, indent: int = 2) -> Path:
        """Write this graph as JSON (enums as their string values), creating parent dirs."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.model_dump_json(indent=indent) + "\n", encoding="utf-8")
        return out

    def node_index(self) -> dict[str, Node]:
        """Node id -> Node."""
        return {n.id: n for n in self.nodes}

    def claim_index(self) -> dict[str, Claim]:
        """Claim id -> Claim."""
        return {c.id: c for c in self.claims}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """The JSON Schema written to docs/graph.schema.json."""
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema"}
        schema.update(cls.model_json_schema())
        return schema


def schema_json(indent: int = 2) -> str:
    return json.dumps(Graph.json_schema(), indent=indent) + "\n"


# --------------------------------------------------------------------------------------
# Evidence-pipeline -> graph-build handoff: data/build/claims.json
# --------------------------------------------------------------------------------------


class ClaimsBundle(_Strict):
    """What the evidence pipeline (PubMed fetch + OpenAI extraction + reconciliation) hands to the
    graph build, written to ``data/build/claims.json``.

    * ``papers``: the ``Paper`` nodes the claims come from. Every paper referenced by a claim
      carries a non-empty ``props.abstract`` (the exact text the quote was matched against).
      ``props.authors`` is a list of strings, or of dicts ``{"name", "affiliation", "orcid"}``
      when PubMed provided them (the build derives Investigator nodes from these).
    * ``claims``: quote-verified :class:`Claim` objects. ``subject``/``object``/``variant`` hold
      resolved node ids when the reconciler was confident; an unresolved mention is kept as
      ``text:<mention>`` so nothing is silently dropped, and the build links only claims whose
      endpoints are node ids present in the graph. ``predicate`` is free text, with the agreed
      vocabulary ``treats``, ``has_mechanism``, ``associated_with``, ``has_phenotype``.
    * ``unresolved``: the low-confidence reconciliations that went to ``data/review_queue.csv``
      (free-form rows), never into the graph (README section 7, "Reconcile").
    * ``coverage``: per-entity PubMed searches run, including zero-hit ones (hard rule 3).
    * ``model`` is the ``OPENAI_MODEL`` the claims were extracted with, recorded, not hard-coded.
    """

    extracted_at: IsoTimestamp
    model: str = Field(default="", description="OPENAI_MODEL used for extraction")
    papers: list[Node]
    claims: list[Claim]
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Coverage] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_bundle(self) -> ClaimsBundle:
        problems: list[str] = []
        paper_ids = _unique_ids("paper", (p.id for p in self.papers), problems)
        _unique_ids("claim", (c.id for c in self.claims), problems)
        paper_by_id = {p.id: p for p in self.papers}
        for p in self.papers:
            if p.type is not NodeType.Paper:
                problems.append(f"papers[] entry {p.id!r} is a {p.type.value} node, not a Paper")
        for c in self.claims:
            paper = paper_by_id.get(c.paper)
            if paper is None:
                problems.append(f"claim {c.id!r}: paper {c.paper!r} is not in papers[]")
            else:
                abstract = paper.props.get("abstract")
                if not (isinstance(abstract, str) and abstract.strip()):
                    problems.append(f"claim {c.id!r}: Paper {c.paper!r} has no props.abstract")
                elif _normalize_ws(c.quote) not in _normalize_ws(abstract):
                    problems.append(
                        f"claim {c.id!r}: quote does not occur in the abstract of {c.paper!r} "
                        "after whitespace normalization (hard rule 5)"
                    )
            if c.paper.startswith(_PMID_PREFIX):
                if c.pmid != c.paper[len(_PMID_PREFIX) :]:
                    problems.append(f"claim {c.id!r}: pmid {c.pmid!r} disagrees with paper {c.paper!r}")
            elif c.pmid:
                problems.append(f"claim {c.id!r}: pmid set but paper {c.paper!r} is not pmid:{c.pmid}")
        if problems:
            shown = problems[:_MAX_REPORTED_PROBLEMS]
            hidden = len(problems) - len(shown)
            tail = f"; ... and {hidden} more" if hidden else ""
            raise ValueError("claims bundle: " + "; ".join(shown) + tail)
        return self

    @classmethod
    def load(cls, path: str | Path) -> ClaimsBundle:
        """Read and strictly validate a claims.json file (same strictness as :meth:`Graph.load`)."""
        return cls.model_validate_json(Path(path).read_bytes(), strict=True)

    def dump(self, path: str | Path, indent: int = 2) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.model_dump_json(indent=indent) + "\n", encoding="utf-8")
        return out
