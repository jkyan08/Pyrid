"""Disease-disease similarity (README section 6 "Similarity"): ``similar_to`` edge drafts.

::

    sim = w_m * mechanism_score + w_p * phenotype_overlap - w_c * conflict_penalty

Every number below is a GENERAL rule over the graph's own structure: mechanism ids
(``mech:<GENE>:<GoF|LoF|DN>[:somatic]``, parsed by :func:`pipeline.ids.parse_mechanism_id`),
``attributes.origin`` on ``has_mechanism`` edges, ``in_pathway`` edges of the diseases' associated
genes, ``has_phenotype`` edges closed upward through Phenotype ``is_a`` edges and weighted by
``props.ic``. No gene, disease, drug or identifier is named here or in ``pipeline/seeds/logic.yaml``;
the weights live in that file and every emitted edge carries the full breakdown (weights
included) in ``confidence_basis`` so the UI can say *why* two diseases are neighbours.

Definitions (``cfg`` is the ``logic.yaml`` mapping; the ``similarity`` and ``conflict`` sections
are read, missing keys fall back to :data:`DEFAULT_SIMILARITY` / :data:`DEFAULT_CONFLICT`):

* **direction / origin of a disease**: the set of ``(direction, origin)`` from its ``has_mechanism``
  edges; the origin is ``attributes.origin`` when it is ``germline`` or ``somatic``, else
  ``somatic`` for a ``:somatic`` mechanism id, else unknown.
* ``same_direction``: 1 when the two direction sets intersect, else 0.
* ``same_origin``: 1 when both known-origin sets are non-empty and intersect, 0 when both are
  non-empty and disjoint, ``unknown_origin_score`` when either is empty (default 0: an unknown
  origin is no evidence of a shared one, just as a missing mechanism gives ``same_direction`` 0).
* ``shared_pathway``: Jaccard of the two pathway sets (``associated_with`` -> ``in_pathway``,
  associations carrying an ``association_exclude_labels`` label skipped); 0 when either is empty.
* ``mechanism_score``: mean of the ``mechanism_components`` listed in the config.
* ``phenotype_overlap``: IC-weighted Jaccard over the ancestor-closed phenotype sets
  (``sum(ic over intersection) / sum(ic over union)``; a phenotype without ``props.ic`` weighs
  ``ic_missing_weight``); 0 when either set is empty.
* ``conflict_penalty``: ``sum(conflict[k] * component[k])`` over
  ``opposite_direction_same_gene`` (a gain direction on one disease and a loss direction on the
  other for the SAME gene), ``opposite_direction_same_pathway`` (opposite directions on any genes
  and ``shared_pathway > 0``) and ``germline_vs_somatic`` (both origins known and disjoint).
  Each component is 0 or 1.

Pairs are formed over the **candidate diseases** (:func:`candidate_diseases`): Disease nodes with
>= 1 ``has_mechanism`` edge or >= 1 ``has_phenotype`` edge to a Phenotype with ``props.ic``, capped
at ``candidates.max_diseases`` (mechanism-bearing diseases first, then by degree, then by id).
An edge is kept when ``score >= min_score`` and it is among the ``top_k_per_disease`` best edges of
either endpoint (``0`` disables the cap). ``min_score`` (default 0.2) sits above the score two diseases reach by sharing a
pathway set alone (``w_m * 1/3``), so an edge always needs mechanism agreement or phenotype
overlap on top of a shared pathway. Output is deterministic: candidates and drafts are sorted by
id, ``source < target`` lexicographically, no randomness.

The signed side of the score is kept too: :func:`compute_repulsions` lists the candidate pairs whose
conflict penalty outweighs their similarity (``score < 0``). They are never exported as edges;
:mod:`pipeline.graph.cluster` feeds them to Leiden as a negative layer so that penalised pairs
repel instead of merely losing their edge. With the conflict weights at 0 (the ablation) no pair
scores below 0 and the list is empty.

Each draft is an :class:`pipeline.models.Edge` input without ``id`` (the orchestrator assigns
ids): relation ``similar_to``, ``evidence_class`` inferred, ``confidence = 0.5 * clip(score, 0, 1)``
(so never above the inferred cap), ``status`` supported, ``sources`` = one ``db`` record
``similarity:<weighting name>`` dated ``today``, ``attributes.labels == ["similarity"]``.

The helpers :func:`disease_mechanisms`, :func:`disease_phenotype_closure`, :func:`pathways_of`,
:func:`candidate_diseases`, :func:`build_features` and :func:`score_pair` are public so
``cluster.py`` and ``logic.py`` reuse one definition of every feature.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pipeline.ids import parse_mechanism_id
from pipeline.models import EvidenceClass, Graph, Node, NodeType

__all__ = [
    "DEFAULT_CANDIDATES",
    "DEFAULT_CONFLICT",
    "DEFAULT_SIMILARITY",
    "DEFAULT_WEIGHTING",
    "DiseaseFeatures",
    "Features",
    "INFERRED_CAP",
    "LABEL",
    "MODULE",
    "MechanismRef",
    "REL_ASSOCIATED_WITH",
    "REL_HAS_MECHANISM",
    "REL_HAS_PHENOTYPE",
    "REL_IN_PATHWAY",
    "REL_IS_A",
    "REL_SIMILAR_TO",
    "build_features",
    "candidate_diseases",
    "candidates_cfg",
    "compute_repulsions",
    "compute_similarity",
    "conflict_cfg",
    "disease_mechanisms",
    "disease_phenotype_closure",
    "jaccard",
    "pathways_of",
    "phenotype_ic",
    "phenotype_parents",
    "score_pair",
    "similarity_cfg",
    "weighted_jaccard",
]

# Relation vocabulary (README section 4; tests/conftest.py REL_* constants).
REL_HAS_MECHANISM = "has_mechanism"
REL_ASSOCIATED_WITH = "associated_with"
REL_HAS_PHENOTYPE = "has_phenotype"
REL_IN_PATHWAY = "in_pathway"
REL_IS_A = "is_a"
REL_SIMILAR_TO = "similar_to"

MODULE = "similarity"  # sources[].id prefix: "<module>:<weighting name>"
LABEL = "similarity"  # attributes.labels of every emitted edge
INFERRED_CAP = 0.5  # pipeline/models.py: inferred edges never exceed 0.5
KNOWN_ORIGINS = ("germline", "somatic")
ROUND = 6  # decimals kept in confidence and confidence_basis (stable JSON, no float noise)

# Mirrors pipeline/seeds/logic.yaml; the yaml is the documented source, these are the fallbacks.
DEFAULT_WEIGHTING: dict[str, Any] = {"name": "balanced", "w_m": 0.5, "w_p": 0.4, "w_c": 0.6}
DEFAULT_SIMILARITY: dict[str, Any] = {
    "min_score": 0.2,
    "top_k_per_disease": 10,
    "mechanism_components": ["same_direction", "same_origin", "shared_pathway"],
    "unknown_origin_score": 0.0,
    "phenotype_closure": "ancestors",
    "ic_missing_weight": 0.0,
    "gain_directions": ["GoF"],
    "loss_directions": ["LoF", "DN"],
    "association_exclude_labels": ["below_score_threshold"],
    "top_shared_phenotypes": 5,
}
DEFAULT_CONFLICT: dict[str, float] = {
    "opposite_direction_same_gene": 1.0,
    "opposite_direction_same_pathway": 0.5,
    "germline_vs_somatic": 1.0,
}
DEFAULT_CANDIDATES: dict[str, Any] = {"require_mechanism_or_phenotype": True, "max_diseases": 400}
MECHANISM_COMPONENT_NAMES = ("same_direction", "same_origin", "shared_pathway")


# --------------------------------------------------------------------------------------
# config access
# --------------------------------------------------------------------------------------


def _section(cfg: Mapping[str, Any] | None, name: str, defaults: Mapping[str, Any], marker: str) -> dict[str, Any]:
    """``defaults`` updated with ``cfg[name]``; a ``cfg`` that IS the section (carries ``marker``)
    is accepted too, so callers may pass either the whole logic.yaml or one section. A key set to
    ``null`` in the yaml keeps its default (``0`` is the documented way to disable a cap)."""
    section: Mapping[str, Any] = {}
    if isinstance(cfg, Mapping):
        raw = cfg.get(name)
        if isinstance(raw, Mapping):
            section = raw
        elif marker in cfg:
            section = cfg
    return {**defaults, **{k: v for k, v in section.items() if v is not None}}


def similarity_cfg(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    return _section(cfg, "similarity", DEFAULT_SIMILARITY, "min_score")


def conflict_cfg(cfg: Mapping[str, Any] | None) -> dict[str, float]:
    section = _section(cfg, "conflict", DEFAULT_CONFLICT, "opposite_direction_same_gene")
    return {k: float(section.get(k, 0.0)) for k in DEFAULT_CONFLICT}


def candidates_cfg(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    return _section(cfg, "candidates", DEFAULT_CANDIDATES, "require_mechanism_or_phenotype")


def _weighting(weighting: Mapping[str, Any] | None) -> dict[str, Any]:
    w = {**DEFAULT_WEIGHTING, **(dict(weighting) if isinstance(weighting, Mapping) else {})}
    name = str(w.get("name") or "").strip()
    if not name:
        raise ValueError("weighting needs a non-empty 'name' (it becomes the edge source id)")
    try:
        return {"name": name, "w_m": float(w["w_m"]), "w_p": float(w["w_p"]), "w_c": float(w["w_c"])}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"weighting {name!r} needs numeric w_m, w_p, w_c: {exc}") from None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _r(value: float) -> float:
    return round(float(value), ROUND)


# --------------------------------------------------------------------------------------
# set helpers
# --------------------------------------------------------------------------------------


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Plain Jaccard; 0 when either set is empty (hence when the union is empty)."""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def weighted_jaccard(a: Mapping[str, float], b: Mapping[str, float]) -> tuple[float, float, float]:
    """``(overlap, intersection_weight, union_weight)`` of two ``id -> weight`` maps; 0 when
    either map is empty or the union weight is 0 (all weights missing)."""
    if not a or not b:
        return 0.0, 0.0, 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    inter = sum(w for pid, w in small.items() if pid in large)
    union = sum(a.values()) + sum(b.values()) - inter
    if union <= 0:
        return 0.0, inter, union
    return inter / union, inter, union


# --------------------------------------------------------------------------------------
# features read from the graph
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class MechanismRef:
    """One ``has_mechanism`` edge of a disease, decoded."""

    mechanism: str  # Mechanism node id
    gene: str  # gene symbol parsed from the mechanism id
    direction: str  # GoF | LoF | DN
    origin: str | None  # germline | somatic | None (unknown)


def _typed_ends(index: Mapping[str, Node], source: str, target: str, a: NodeType, b: NodeType) -> tuple[Node, Node] | None:
    """``(node of type a, node of type b)`` for an edge in either direction, else ``None``."""
    s, t = index.get(source), index.get(target)
    if s is None or t is None:
        return None
    if s.type == a and t.type == b:
        return s, t
    if t.type == a and s.type == b:
        return t, s
    return None


def disease_mechanisms(graph: Graph) -> dict[str, list[MechanismRef]]:
    """Disease id -> its decoded ``has_mechanism`` edges (sorted, deduplicated).

    The origin is ``attributes.origin`` when it is ``germline``/``somatic``, else ``somatic`` for a
    ``:somatic`` mechanism id, else ``None`` (unknown). Edges whose target is not a ``mech:`` id
    are ignored.
    """
    index = graph.node_index()
    out: dict[str, set[MechanismRef]] = defaultdict(set)
    for e in graph.edges:
        if e.relation != REL_HAS_MECHANISM:
            continue
        ends = _typed_ends(index, e.source, e.target, NodeType.Disease, NodeType.Mechanism)
        if ends is None:
            continue
        disease, mech = ends
        parsed = parse_mechanism_id(mech.id)
        if parsed is None:
            continue
        gene, direction, somatic = parsed
        origin = e.attributes.get("origin")
        if origin not in KNOWN_ORIGINS:
            origin = "somatic" if somatic else None
        out[disease.id].add(MechanismRef(mech.id, gene, direction, origin))
    return {d: sorted(refs) for d, refs in sorted(out.items())}


def phenotype_parents(graph: Graph) -> dict[str, set[str]]:
    """Phenotype id -> parent Phenotype ids from ``is_a`` edges (child -> parent) between
    Phenotype nodes only (``is_a`` also links diseases; those are not phenotype ancestors)."""
    index = graph.node_index()
    parents: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.relation != REL_IS_A:
            continue
        s, t = index.get(e.source), index.get(e.target)
        if s is None or t is None or s.type != NodeType.Phenotype or t.type != NodeType.Phenotype:
            continue
        parents[e.source].add(e.target)
    return dict(parents)


def phenotype_ic(graph: Graph) -> dict[str, float | None]:
    """Phenotype id -> ``props.ic`` as a float, ``None`` when missing or not numeric."""
    return {n.id: _as_float(n.props.get("ic")) for n in graph.nodes if n.type == NodeType.Phenotype}


def _ancestors(pid: str, parents: Mapping[str, set[str]], memo: dict[str, frozenset[str]]) -> frozenset[str]:
    """``pid`` plus all its ancestors; iterative, cycle-safe, memoised in ``memo``."""
    cached = memo.get(pid)
    if cached is not None:
        return cached
    seen: set[str] = {pid}
    stack = list(parents.get(pid, ()))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        done = memo.get(cur)
        if done is not None:
            seen |= done
            continue
        stack.extend(parents.get(cur, ()))
    result = frozenset(seen)
    memo[pid] = result
    return result


def disease_phenotype_closure(
    graph: Graph,
    *,
    closure: str = "ancestors",
    ic_missing_weight: float = 0.0,
) -> dict[str, dict[str, float]]:
    """Disease id -> ``{phenotype id: ic weight}`` over its ``has_phenotype`` targets, closed
    upward through Phenotype ``is_a`` edges when ``closure == "ancestors"`` (``"none"`` keeps
    the annotated terms only). A phenotype without ``props.ic`` weighs ``ic_missing_weight``.
    Diseases without ``has_phenotype`` edges are absent from the result.
    """
    if closure not in ("ancestors", "none"):
        raise ValueError(f"phenotype_closure must be 'ancestors' or 'none', got {closure!r}")
    index = graph.node_index()
    ic = phenotype_ic(graph)
    missing = float(ic_missing_weight)
    parents = phenotype_parents(graph) if closure == "ancestors" else {}
    memo: dict[str, frozenset[str]] = {}

    direct: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.relation != REL_HAS_PHENOTYPE:
            continue
        ends = _typed_ends(index, e.source, e.target, NodeType.Disease, NodeType.Phenotype)
        if ends is None:
            continue
        disease, phenotype = ends
        direct[disease.id].add(phenotype.id)

    out: dict[str, dict[str, float]] = {}
    for disease_id in sorted(direct):
        closed: set[str] = set()
        for pid in direct[disease_id]:
            closed |= _ancestors(pid, parents, memo) if parents else {pid}
        weights: dict[str, float] = {}
        for pid in sorted(closed):
            value = ic.get(pid)
            weights[pid] = float(value) if value is not None else missing
        out[disease_id] = weights
    return out


def _has_excluded_label(attributes: Mapping[str, Any], excluded: frozenset[str]) -> bool:
    if not excluded:
        return False
    labels = attributes.get("labels")
    return isinstance(labels, list) and any(str(label) in excluded for label in labels)


def pathways_of(graph: Graph, *, exclude_labels: Iterable[str] = ()) -> dict[str, set[str]]:
    """Disease id -> Pathway ids reached through ``associated_with`` (Disease -> Gene) then
    ``in_pathway`` (Gene -> Pathway). Associations whose ``attributes.labels`` carry a label in
    ``exclude_labels`` are skipped. Diseases reaching no pathway are absent."""
    index = graph.node_index()
    excluded = frozenset(str(x) for x in exclude_labels)
    gene_pathways: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.relation != REL_IN_PATHWAY:
            continue
        ends = _typed_ends(index, e.source, e.target, NodeType.Gene, NodeType.Pathway)
        if ends is not None:
            gene_pathways[ends[0].id].add(ends[1].id)
    out: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.relation != REL_ASSOCIATED_WITH or _has_excluded_label(e.attributes, excluded):
            continue
        ends = _typed_ends(index, e.source, e.target, NodeType.Disease, NodeType.Gene)
        if ends is None:
            continue
        pw = gene_pathways.get(ends[1].id)
        if pw:
            out[ends[0].id] |= pw
    return {d: set(p) for d, p in sorted(out.items())}


# --------------------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------------------


def _diseases_with_ic_phenotype(graph: Graph) -> set[str]:
    index = graph.node_index()
    ic = phenotype_ic(graph)
    out: set[str] = set()
    for e in graph.edges:
        if e.relation != REL_HAS_PHENOTYPE:
            continue
        ends = _typed_ends(index, e.source, e.target, NodeType.Disease, NodeType.Phenotype)
        if ends is not None and ic.get(ends[1].id) is not None:
            out.add(ends[0].id)
    return out


def candidate_diseases(graph: Graph, cfg: Mapping[str, Any] | None = None) -> list[str]:
    """Sorted Disease ids that similarity and clustering run over (module docstring).

    With ``candidates.require_mechanism_or_phenotype`` (default true): diseases having >= 1
    ``has_mechanism`` edge or >= 1 ``has_phenotype`` edge to a Phenotype with ``props.ic``.
    When more than ``candidates.max_diseases`` qualify, mechanism-bearing diseases are kept
    first, then the highest-degree diseases (edges touching the node, any relation), ties by id.
    """
    c = candidates_cfg(cfg)
    diseases = sorted(n.id for n in graph.nodes if n.type == NodeType.Disease)
    with_mechanism = set(disease_mechanisms(graph))
    if c.get("require_mechanism_or_phenotype", True):
        with_ic = _diseases_with_ic_phenotype(graph)
        pool = [d for d in diseases if d in with_mechanism or d in with_ic]
    else:
        pool = diseases
    cap = c.get("max_diseases")
    if cap is not None and int(cap) > 0 and len(pool) > int(cap):
        degree: Counter[str] = Counter()
        for e in graph.edges:
            degree[e.source] += 1
            degree[e.target] += 1
        ranked = sorted(pool, key=lambda d: (d not in with_mechanism, -degree[d], d))
        pool = sorted(ranked[: int(cap)])
    return pool


# --------------------------------------------------------------------------------------
# per-disease features and pair scoring
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DiseaseFeatures:
    id: str
    label: str
    mechanisms: tuple[MechanismRef, ...]
    directions: frozenset[str]
    origins: frozenset[str]  # known origins only; empty = unknown
    gene_directions: Mapping[str, frozenset[str]]  # gene symbol -> directions on that gene
    pathways: frozenset[str]
    phenotypes: Mapping[str, float]  # closed phenotype id -> ic weight
    phenotype_weight: float  # sum of the weights above


@dataclass(frozen=True)
class Features:
    """Everything :func:`score_pair` needs, computed once per graph."""

    candidates: tuple[str, ...]
    diseases: Mapping[str, DiseaseFeatures]
    labels: Mapping[str, str]  # node id -> label (diseases, phenotypes, pathways)
    n_diseases_total: int  # Disease nodes in the graph (for the run report)


def build_features(graph: Graph, cfg: Mapping[str, Any] | None = None) -> Features:
    """Decode the graph once: candidates plus mechanism, pathway and phenotype features."""
    sim = similarity_cfg(cfg)
    index = graph.node_index()
    candidates = candidate_diseases(graph, cfg)
    mechanisms = disease_mechanisms(graph)
    pathways = pathways_of(graph, exclude_labels=sim.get("association_exclude_labels") or ())
    phenotypes = disease_phenotype_closure(
        graph,
        closure=str(sim.get("phenotype_closure", "ancestors")),
        ic_missing_weight=float(sim.get("ic_missing_weight", 0.0)),
    )
    diseases: dict[str, DiseaseFeatures] = {}
    for d in candidates:
        refs = tuple(mechanisms.get(d, ()))
        gene_dirs: dict[str, set[str]] = defaultdict(set)
        for ref in refs:
            gene_dirs[ref.gene].add(ref.direction)
        ph = phenotypes.get(d, {})
        diseases[d] = DiseaseFeatures(
            id=d,
            label=index[d].label,
            mechanisms=refs,
            directions=frozenset(ref.direction for ref in refs),
            origins=frozenset(ref.origin for ref in refs if ref.origin in KNOWN_ORIGINS),
            gene_directions={g: frozenset(ds) for g, ds in sorted(gene_dirs.items())},
            pathways=frozenset(pathways.get(d, ())),
            phenotypes=dict(ph),
            phenotype_weight=sum(ph.values()),
        )
    labels = {
        n.id: n.label for n in graph.nodes if n.type in (NodeType.Disease, NodeType.Phenotype, NodeType.Pathway)
    }
    n_total = sum(1 for n in graph.nodes if n.type == NodeType.Disease)
    return Features(tuple(candidates), diseases, labels, n_total)


def _opposite(a: DiseaseFeatures, b: DiseaseFeatures, gain: frozenset[str], loss: frozenset[str]) -> bool:
    return bool((a.directions & gain and b.directions & loss) or (b.directions & gain and a.directions & loss))


def _opposite_same_gene(a: DiseaseFeatures, b: DiseaseFeatures, gain: frozenset[str], loss: frozenset[str]) -> list[str]:
    """Genes on which one disease has a gain direction and the other a loss direction."""
    hits = []
    for gene in sorted(set(a.gene_directions) & set(b.gene_directions)):
        da, db = a.gene_directions[gene], b.gene_directions[gene]
        if (da & gain and db & loss) or (db & gain and da & loss):
            hits.append(gene)
    return hits


def score_pair(
    features: Features,
    a: str,
    b: str,
    weighting: Mapping[str, Any] | None = None,
    cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The full score breakdown for one unordered pair of candidate diseases (module docstring).

    Returns the ``confidence_basis`` content: ``weighting``, ``score``, ``mechanism_score``,
    ``mechanism_components``, ``phenotype_overlap``, ``conflict_penalty``, ``conflict_components``,
    ``conflict_weights``, ``n_shared_phenotypes``, ``top_shared_phenotypes`` and per-side detail.
    """
    w = _weighting(weighting)
    sim = similarity_cfg(cfg)
    weights = conflict_cfg(cfg)
    fa, fb = features.diseases[a], features.diseases[b]
    gain = frozenset(str(x) for x in sim.get("gain_directions") or ())
    loss = frozenset(str(x) for x in sim.get("loss_directions") or ())

    # mechanism components
    same_direction = 1.0 if fa.directions & fb.directions else 0.0
    if fa.origins and fb.origins:
        same_origin = 1.0 if fa.origins & fb.origins else 0.0
        origins_known = True
    else:
        same_origin = float(sim["unknown_origin_score"])  # similarity_cfg always fills the key (yaml / DEFAULT_SIMILARITY)
        origins_known = False
    shared_pathway = jaccard(fa.pathways, fb.pathways)
    components = {"same_direction": same_direction, "same_origin": same_origin, "shared_pathway": shared_pathway}
    used = [c for c in (sim.get("mechanism_components") or MECHANISM_COMPONENT_NAMES) if c in components]
    mechanism_score = sum(components[c] for c in used) / len(used) if used else 0.0

    # phenotype overlap
    phenotype_overlap, inter_w, union_w = weighted_jaccard(fa.phenotypes, fb.phenotypes)
    shared = sorted(set(fa.phenotypes) & set(fb.phenotypes), key=lambda p: (-fa.phenotypes[p], p))
    top_n = int(sim.get("top_shared_phenotypes", 5) or 0)
    top_shared = [
        {"id": p, "label": features.labels.get(p, p), "ic": _r(fa.phenotypes[p])} for p in shared[:top_n]
    ]

    # conflicts
    same_gene_hits = _opposite_same_gene(fa, fb, gain, loss)
    opposite = _opposite(fa, fb, gain, loss)
    conflict_components = {
        "opposite_direction_same_gene": 1.0 if same_gene_hits else 0.0,
        "opposite_direction_same_pathway": 1.0 if opposite and shared_pathway > 0 else 0.0,
        "germline_vs_somatic": 1.0 if origins_known and not (fa.origins & fb.origins) else 0.0,
    }
    conflict_penalty = sum(weights[k] * conflict_components[k] for k in DEFAULT_CONFLICT)

    score = w["w_m"] * mechanism_score + w["w_p"] * phenotype_overlap - w["w_c"] * conflict_penalty

    def side(f: DiseaseFeatures) -> dict[str, Any]:
        return {
            "id": f.id,
            "label": f.label,
            "mechanisms": [m.mechanism for m in f.mechanisms],
            "directions": sorted(f.directions),
            "origins": sorted(f.origins),
            "n_pathways": len(f.pathways),
            "n_phenotypes_closed": len(f.phenotypes),
            "phenotype_ic_sum": _r(f.phenotype_weight),
        }

    return {
        "rule": MODULE,
        "formula": "w_m * mechanism_score + w_p * phenotype_overlap - w_c * conflict_penalty",
        "weighting": w,
        "score": _r(score),
        "mechanism_score": _r(mechanism_score),
        "mechanism_components": {k: _r(v) for k, v in components.items()},
        "mechanism_components_used": list(used),
        "phenotype_overlap": _r(phenotype_overlap),
        "phenotype_ic_shared": _r(inter_w),
        "phenotype_ic_union": _r(union_w),
        "conflict_penalty": _r(conflict_penalty),
        "conflict_components": conflict_components,
        "conflict_weights": weights,
        "opposite_direction_genes": same_gene_hits,
        "n_shared_pathways": len(fa.pathways & fb.pathways),
        "n_shared_phenotypes": len(shared),
        "top_shared_phenotypes": top_shared,
        "a": side(fa),
        "b": side(fb),
    }


# --------------------------------------------------------------------------------------
# edge drafts
# --------------------------------------------------------------------------------------


def _draft(a: str, b: str, basis: Mapping[str, Any], weighting_name: str, today: str) -> dict[str, Any]:
    score = float(basis["score"])
    confidence = _r(INFERRED_CAP * min(max(score, 0.0), 1.0))
    return {
        "source": a,
        "target": b,
        "relation": REL_SIMILAR_TO,
        "evidence_class": EvidenceClass.inferred.value,
        "confidence": min(confidence, INFERRED_CAP),
        "confidence_basis": dict(basis),
        "status": "supported",
        "sources": [{"kind": "db", "id": f"{MODULE}:{weighting_name}", "date": today}],
        "supporting": [],
        "contradicting": [],
        "attributes": {"labels": [LABEL]},
    }


def compute_similarity(
    graph: Graph,
    weighting: Mapping[str, Any] | None,
    cfg: Mapping[str, Any] | None,
    *,
    today: str,
    features: Features | None = None,
) -> list[dict[str, Any]]:
    """``similar_to`` edge drafts for one weighting (module docstring).

    ``weighting`` is one entry of ``logic.yaml`` ``weightings`` (``name, w_m, w_p, w_c``); ``cfg``
    is the ``logic.yaml`` mapping (``similarity``, ``conflict`` and ``candidates`` sections are
    read); ``today`` is the build date written into ``sources[].date``. ``features`` may be
    passed to reuse :func:`build_features` across weightings. Drafts carry no ``id`` and are
    sorted by ``(source, target)`` with ``source < target``.
    """
    if not isinstance(today, str) or not today.strip():
        raise ValueError("today must be a non-empty ISO date string")
    w = _weighting(weighting)
    sim = similarity_cfg(cfg)
    min_score = float(sim["min_score"])
    top_k = int(sim["top_k_per_disease"] or 0)  # 0 disables the cap (a yaml null falls back to the default)
    feats = features if features is not None else build_features(graph, cfg)
    cands = list(feats.candidates)

    scored: list[tuple[str, str, dict[str, Any]]] = []
    for i, a in enumerate(cands):
        for b in cands[i + 1 :]:
            basis = score_pair(feats, a, b, w, cfg)
            if basis["score"] >= min_score:
                scored.append((a, b, basis))

    if top_k > 0:
        per: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for a, b, basis in scored:
            per[a].append((-float(basis["score"]), b))
            per[b].append((-float(basis["score"]), a))
        keep: set[tuple[str, str]] = set()
        for d, ranked in per.items():
            ranked.sort()
            for _neg, other in ranked[:top_k]:
                keep.add((d, other) if d < other else (other, d))
        scored = [(a, b, basis) for a, b, basis in scored if (a, b) in keep]

    drafts = [_draft(a, b, basis, w["name"], today) for a, b, basis in scored]
    drafts.sort(key=lambda d: (d["source"], d["target"]))
    return drafts


def compute_repulsions(
    graph: Graph,
    weighting: Mapping[str, Any] | None,
    cfg: Mapping[str, Any] | None,
    *,
    features: Features | None = None,
) -> list[dict[str, Any]]:
    """Candidate pairs whose score is NEGATIVE under ``weighting``: the conflict penalty outweighs
    mechanism and phenotype similarity (module docstring).

    Each row is ``{"source", "target", "weight", "score", "conflict_penalty", "conflict_components",
    "weighting"}`` with ``source < target`` and ``weight = -score > 0``, sorted by ``(source,
    target)``. These are inputs for the clustering's negative layer, not edges: nothing here is
    written to ``graph.json``. Empty when every conflict weight is 0.
    """
    w = _weighting(weighting)
    feats = features if features is not None else build_features(graph, cfg)
    cands = list(feats.candidates)
    out: list[dict[str, Any]] = []
    for i, a in enumerate(cands):
        for b in cands[i + 1 :]:
            basis = score_pair(feats, a, b, w, cfg)
            score = float(basis["score"])
            if score < 0.0:
                out.append(
                    {
                        "source": a,
                        "target": b,
                        "weight": _r(-score),
                        "score": score,
                        "conflict_penalty": basis["conflict_penalty"],
                        "conflict_components": dict(basis["conflict_components"]),
                        "weighting": w["name"],
                    }
                )
    out.sort(key=lambda d: (d["source"], d["target"]))
    return out
