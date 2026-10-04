"""Leiden clustering of the disease similarity graph (README section 6, "Clustering").

Input is a :class:`pipeline.models.Graph` plus a similarity function; output is a list of
:class:`pipeline.models.Cluster` drafts (plain dicts) that the orchestrator
(``pipeline/graph/logic.py``) writes into ``graph.clusters``. Nothing here reads or writes files.

General rules only
------------------
Nothing in this module names a gene, disease, drug, pathway or phenotype. Every parameter comes
from the ``clustering`` section of ``pipeline/seeds/logic.yaml`` (names below, defaults in
:data:`DEFAULT_CLUSTERING`); the similarity scores that drive the partition come from the
injected ``sim_fn`` (by default :func:`pipeline.graph.similarity.compute_similarity`, imported
lazily so this module and its tests do not depend on that module being finished).

==========================  =====================================================================
``clustering`` key          Meaning
==========================  =====================================================================
``method``                  ``leiden`` (the only method implemented)
``objective``               ``modularity`` (``ModularityVertexPartition``; with ``resolution``
                            != 1 the equivalent ``RBConfigurationVertexPartition``) or ``cpm``
                            (``CPMVertexPartition``)
``resolution``              resolution parameter (ignored by plain modularity, i.e. 1.0)
``seed``                    RNG seed handed to ``leidenalg.find_partition`` (determinism)
``n_iterations``            Leiden iterations; ``-1`` iterates until no further improvement
``robust_min_jaccard``      a primary cluster is ``robust`` when its member set has Jaccard >=
                            this with some cluster in EVERY other weighting's partition
``singleton_clusters``      a disease with no similarity edge becomes its own cluster (``true``)
                            or is left out of the partition (``false``)
``repulsion_weight``        weight of the negative layer built from the penalised pairs
                            (:func:`pipeline.graph.similarity.compute_repulsions`); ``0`` turns the
                            signed layer off and clusters on the positive edges alone
==========================  =====================================================================

Partition
---------
:func:`leiden_partition` builds an undirected ``igraph.Graph`` whose vertices are the disease ids
(sorted, so vertex order is deterministic) and whose edges are the ``similar_to`` drafts with
weight ``2 * confidence`` (similarity emits ``confidence = 0.5 * clip(score, 0, 1)``, so the
weight is the clipped score; non-positive weights are dropped; a duplicated pair keeps its
largest weight; edges to ids outside the disease set and self-loops are ignored). The exact call
is::

    leidenalg.find_partition(ig_graph, leidenalg.ModularityVertexPartition,
                             weights="weight", n_iterations=<n_iterations>, seed=<seed>)

Signed layer
------------
A pair whose conflict penalty outweighs its similarity (``score < 0``: opposite direction on the
same gene or pathway, germline against somatic) carries no edge, but dropping it is not enough:
two such diseases can still be pulled together through third diseases that resemble both. When
``repulsions`` are given (``{"source", "target", "weight" = -score}`` rows) and
``repulsion_weight > 0``, the partition is optimised over two layers with leidenalg's documented
negative-links recipe: the positive graph with layer weight ``+1`` and the repulsion graph with
layer weight ``-repulsion_weight`` (``Optimiser.optimise_partition_multiplex``, seeded), so the
penalised pairs push apart. With the conflict weights at 0 (README section 6 ablation) there are
no repulsions and the single-layer call above is used unchanged.

Communities are renumbered 0, 1, 2, ... by (size desc, smallest member id), so equal inputs give
equal outputs regardless of leidenalg's internal labelling.

Clusters
--------
:func:`build_clusters` recomputes similarity and the partition once per weighting; the FIRST
weighting is primary and defines the clusters. Cluster ids are ``cl_001``, ``cl_002``, ... in
(size desc, smallest member id) order; ``members`` are Disease node ids only, sorted, and form a
partition over the disease set (by default every Disease node of the graph, so
``tests.conftest.require_cluster`` finds every disease). ``robust`` is defined above; ``why``
carries the inputs the UI needs to say *why* the members belong together (see
:func:`build_clusters`).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from pipeline.ids import parse_mechanism_id
from pipeline.models import Graph, NodeType

__all__ = [
    "CLUSTER_ID_FORMAT",
    "DEFAULT_CLUSTERING",
    "MAX_SHARED_PATHWAYS",
    "MAX_TOP_PHENOTYPES",
    "MIN_MEMBERS_FOR_SHARED_PATHWAY",
    "REL_ASSOCIATED_WITH",
    "REL_HAS_MECHANISM",
    "REL_HAS_PHENOTYPE",
    "REL_IN_PATHWAY",
    "REL_SIMILAR_TO",
    "RepulsionFn",
    "SimilarityFn",
    "build_clusters",
    "cluster_report",
    "clustering_config",
    "edge_weight",
    "jaccard",
    "leiden_partition",
    "partition_groups",
    "repulsion_weight",
]

REL_HAS_MECHANISM = "has_mechanism"  # Disease -> Mechanism (attributes.origin)
REL_ASSOCIATED_WITH = "associated_with"  # Disease -> Gene
REL_HAS_PHENOTYPE = "has_phenotype"  # Disease -> Phenotype
REL_IN_PATHWAY = "in_pathway"  # Gene -> Pathway
REL_SIMILAR_TO = "similar_to"  # Disease -> Disease (inferred; what sim_fn emits)

CLUSTER_ID_FORMAT = "cl_{:03d}"
MAX_SHARED_PATHWAYS = 5
MAX_TOP_PHENOTYPES = 8
MIN_MEMBERS_FOR_SHARED_PATHWAY = 2  # a pathway is "shared" when at least two members reach it (singletons: [])
ALGORITHM = "leiden"
_ROUND = 4

# Documented defaults for the ``clustering`` section of pipeline/seeds/logic.yaml. The yaml wins.
DEFAULT_CLUSTERING: dict[str, Any] = {
    "method": "leiden",
    "objective": "modularity",
    "resolution": 1.0,
    "seed": 7,
    "n_iterations": -1,
    "robust_min_jaccard": 0.8,
    "singleton_clusters": True,
    "repulsion_weight": 1.0,
}

# ``sim_fn(graph, weighting, cfg, *, today) -> list[edge draft dict]`` (pipeline.graph.similarity.compute_similarity).
SimilarityFn = Callable[..., list[dict[str, Any]]]
# ``repulsion_fn(graph, weighting, cfg, *, today) -> list[{"source", "target", "weight"}]`` (similarity.compute_repulsions).
RepulsionFn = Callable[..., list[dict[str, Any]]]


# --------------------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------------------


def clustering_config(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    """The effective ``clustering`` parameters: :data:`DEFAULT_CLUSTERING` overlaid with ``cfg``.

    ``cfg`` may be the whole ``logic.yaml`` dict (its ``clustering`` section is used) or that
    section on its own.
    """
    section: Mapping[str, Any] = cfg or {}
    nested = section.get("clustering")
    if isinstance(nested, Mapping):
        section = nested
    out = {**DEFAULT_CLUSTERING, **dict(section)}
    if str(out["method"]).lower() != ALGORITHM:
        raise ValueError(f"clustering.method must be {ALGORITHM!r}, got {out['method']!r}")
    objective = str(out["objective"]).lower()
    if objective not in ("modularity", "cpm"):
        raise ValueError(f"clustering.objective must be 'modularity' or 'cpm', got {out['objective']!r}")
    out["objective"] = objective
    out["resolution"] = float(out["resolution"])
    out["seed"] = int(out["seed"])
    out["n_iterations"] = int(out["n_iterations"])
    out["robust_min_jaccard"] = float(out["robust_min_jaccard"])
    out["singleton_clusters"] = bool(out["singleton_clusters"])
    out["repulsion_weight"] = float(out["repulsion_weight"])
    if out["repulsion_weight"] < 0:
        raise ValueError(f"clustering.repulsion_weight must be >= 0 (0 disables the signed layer), got {out['repulsion_weight']}")
    return out


# --------------------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------------------


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def edge_weight(draft: Mapping[str, Any]) -> float:
    """Leiden weight of a similarity draft: ``2 * confidence`` clipped at 0 (the clipped score).

    Falls back to ``confidence_basis.score`` (clipped) when a draft carries no ``confidence``.
    """
    conf = draft.get("confidence")
    if conf is None:
        basis = draft.get("confidence_basis") or {}
        weight = float(basis.get("score", 0.0) or 0.0)
    else:
        weight = 2.0 * float(conf)
    return max(weight, 0.0)


def repulsion_weight(row: Mapping[str, Any]) -> float:
    """Negative-layer weight of a repulsion row: its ``weight`` (``-score``), else ``-score``,
    clipped at 0."""
    weight = row.get("weight")
    if weight is None:
        weight = -float(row.get("score", 0.0) or 0.0)
    return max(float(weight), 0.0)


def partition_groups(partition: Mapping[str, int]) -> list[list[str]]:
    """Communities as sorted member lists, ordered by (size desc, smallest member id)."""
    groups: dict[int, list[str]] = defaultdict(list)
    for member, community in partition.items():
        groups[community].append(member)
    ordered = [sorted(members) for members in groups.values()]
    ordered.sort(key=lambda members: (-len(members), members[0]))
    return ordered


def _renumber(partition: Mapping[str, int]) -> dict[str, int]:
    """Community labels 0..k-1 in :func:`partition_groups` order (deterministic labelling)."""
    out: dict[str, int] = {}
    for label, members in enumerate(partition_groups(partition)):
        for member in members:
            out[member] = label
    return out


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# --------------------------------------------------------------------------------------
# Leiden
# --------------------------------------------------------------------------------------


def _partition_type(cfg: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    """leidenalg partition class and its extra kwargs for the configured objective/resolution."""
    import leidenalg as la

    kwargs: dict[str, Any] = {}
    if cfg["objective"] == "modularity" and cfg["resolution"] == 1.0:
        partition_type = la.ModularityVertexPartition
    elif cfg["objective"] == "modularity":
        partition_type = la.RBConfigurationVertexPartition  # modularity with a resolution parameter
        kwargs["resolution_parameter"] = cfg["resolution"]
    else:
        partition_type = la.CPMVertexPartition
        kwargs["resolution_parameter"] = cfg["resolution"]
    return partition_type, kwargs


def _run_leiden(
    n_vertices: int,
    edges: Sequence[tuple[int, int]],
    weights: Sequence[float],
    cfg: Mapping[str, Any],
    neg_edges: Sequence[tuple[int, int]] = (),
    neg_weights: Sequence[float] = (),
) -> list[int]:
    """Community label per vertex index. Imported lazily so importing this module is cheap.

    Without ``neg_edges`` (or with ``repulsion_weight`` 0) this is the single ``find_partition``
    call documented in the module docstring. With them, the positive graph and the repulsion graph
    are optimised together with layer weights ``+1`` and ``-repulsion_weight`` (leidenalg's
    negative-links recipe), seeded the same way.
    """
    import igraph as ig
    import leidenalg as la

    ig_graph = ig.Graph(n=n_vertices, edges=list(edges), directed=False)
    ig_graph.es["weight"] = list(weights)
    partition_type, kwargs = _partition_type(cfg)
    if not neg_edges or cfg["repulsion_weight"] <= 0.0:
        partition = la.find_partition(
            ig_graph,
            partition_type,
            weights="weight",
            n_iterations=cfg["n_iterations"],
            seed=cfg["seed"],
            **kwargs,
        )
        return list(partition.membership)

    neg_graph = ig.Graph(n=n_vertices, edges=list(neg_edges), directed=False)
    neg_graph.es["weight"] = list(neg_weights)
    part_pos = partition_type(ig_graph, weights="weight", **kwargs)
    part_neg = partition_type(neg_graph, weights="weight", **kwargs)
    optimiser = la.Optimiser()
    optimiser.set_rng_seed(cfg["seed"])
    optimiser.optimise_partition_multiplex(
        [part_pos, part_neg],
        layer_weights=[1.0, -float(cfg["repulsion_weight"])],
        n_iterations=cfg["n_iterations"],
    )
    return list(part_pos.membership)


def _pair_weights(
    rows: Iterable[Mapping[str, Any]],
    index: Mapping[str, int],
    weight_of: Callable[[Mapping[str, Any]], float],
    *,
    relation: str | None,
) -> dict[tuple[int, int], float]:
    """``{(i, j): weight}`` over rows whose endpoints are both in ``index`` (no self-loops, positive
    weight, largest weight per pair); ``relation`` filters rows that carry another relation."""
    weights: dict[tuple[int, int], float] = {}
    for row in rows:
        if relation is not None and row.get("relation", relation) != relation:
            continue
        s, t = row.get("source"), row.get("target")
        if s not in index or t not in index or s == t:
            continue
        w = weight_of(row)
        if w <= 0.0:
            continue
        key = (min(index[s], index[t]), max(index[s], index[t]))
        weights[key] = max(weights.get(key, 0.0), w)
    return weights


def leiden_partition(
    disease_ids: Iterable[str],
    sim_edges: Iterable[Mapping[str, Any]],
    cfg: Mapping[str, Any] | None,
    repulsions: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, int]:
    """``{disease_id: community}`` from Leiden over the similarity drafts (module docstring).

    ``disease_ids`` is the vertex set (deduplicated, sorted). ``sim_edges`` are ``similar_to``
    edge drafts (``source``, ``target``, ``confidence``); drafts with another ``relation``, an
    endpoint outside ``disease_ids``, a self-loop or a non-positive weight are ignored.
    ``repulsions`` are the penalised pairs (``source``, ``target``, ``weight``), filtered the same
    way and used as the negative layer when ``repulsion_weight > 0``; a repulsion alone does not
    make a disease "clustered". With ``singleton_clusters`` true every edge-less disease is its own
    community; otherwise it is absent from the result. Communities are labelled 0..k-1 by (size
    desc, smallest member id).
    """
    c = clustering_config(cfg)
    ids = sorted(set(disease_ids))
    if not ids:
        return {}
    index = {d: i for i, d in enumerate(ids)}
    weights = _pair_weights(sim_edges, index, edge_weight, relation=REL_SIMILAR_TO)
    negatives = _pair_weights(repulsions or (), index, repulsion_weight, relation=None)

    edges = sorted(weights)
    neg_edges = sorted(negatives)
    if edges:
        membership = _run_leiden(len(ids), edges, [weights[k] for k in edges], c, neg_edges, [negatives[k] for k in neg_edges])
    else:
        membership = list(range(len(ids)))
    with_edge = {i for pair in edges for i in pair}
    raw = {d: membership[i] for i, d in enumerate(ids) if i in with_edge or c["singleton_clusters"]}
    return _renumber(raw)


# --------------------------------------------------------------------------------------
# graph indexes used by ``why``
# --------------------------------------------------------------------------------------


def _association_exclude_labels(cfg: Mapping[str, Any] | None) -> frozenset[str]:
    """``similarity.association_exclude_labels``: the ``associated_with`` labels the similarity
    score skips when it builds pathway sets. ``why.shared_pathways`` skips the same associations
    so it explains the score that was actually computed. Read through
    :func:`pipeline.graph.similarity.similarity_cfg` (its documented default) when that module is
    available, else from ``cfg`` directly (this module must not hard-depend on similarity)."""
    try:
        from pipeline.graph.similarity import similarity_cfg

        labels = similarity_cfg(cfg).get("association_exclude_labels") or ()
    except ImportError:  # similarity.py absent or stubbed: read the section as given, no default
        section = cfg.get("similarity") if isinstance(cfg, Mapping) else None
        labels = section.get("association_exclude_labels") or () if isinstance(section, Mapping) else ()
    return frozenset(str(label) for label in labels)


def _has_excluded_label(attributes: Mapping[str, Any], excluded: frozenset[str]) -> bool:
    if not excluded:
        return False
    labels = attributes.get("labels")
    return isinstance(labels, list) and any(str(label) in excluded for label in labels)


class _Context:
    """Per-graph indexes: everything ``why`` reports is read from the graph's own edges.
    ``associated_with`` edges carrying a label in ``exclude_labels`` are skipped, mirroring
    :func:`pipeline.graph.similarity.pathways_of`."""

    def __init__(self, graph: Graph, exclude_labels: Iterable[str] = ()) -> None:
        self.nodes = graph.node_index()
        self.exclude_labels = frozenset(str(label) for label in exclude_labels)
        self.mechanisms: dict[str, set[tuple[str, str | None]]] = defaultdict(set)  # disease -> {(direction, origin)}
        self.genes: dict[str, set[str]] = defaultdict(set)  # disease -> gene ids
        self.pathways_of_gene: dict[str, set[str]] = defaultdict(set)  # gene -> pathway ids
        self.phenotypes: dict[str, set[str]] = defaultdict(set)  # disease -> phenotype ids
        for e in graph.edges:
            if e.relation == REL_HAS_MECHANISM:
                pair = self._ends(e.source, e.target, NodeType.Disease, NodeType.Mechanism)
                if pair is not None:
                    disease, mech = pair
                    parsed = parse_mechanism_id(mech)
                    if parsed is None:
                        continue
                    _gene, direction, somatic = parsed
                    origin = e.attributes.get("origin")
                    if origin not in ("germline", "somatic"):
                        origin = "somatic" if somatic else None
                    self.mechanisms[disease].add((direction, origin))
            elif e.relation == REL_ASSOCIATED_WITH:
                if _has_excluded_label(e.attributes, self.exclude_labels):
                    continue
                pair = self._ends(e.source, e.target, NodeType.Disease, NodeType.Gene)
                if pair is not None:
                    self.genes[pair[0]].add(pair[1])
            elif e.relation == REL_IN_PATHWAY:
                pair = self._ends(e.source, e.target, NodeType.Gene, NodeType.Pathway)
                if pair is not None:
                    self.pathways_of_gene[pair[0]].add(pair[1])
            elif e.relation == REL_HAS_PHENOTYPE:
                pair = self._ends(e.source, e.target, NodeType.Disease, NodeType.Phenotype)
                if pair is not None:
                    self.phenotypes[pair[0]].add(pair[1])

    def _ends(self, a: str, b: str, type_a: NodeType, type_b: NodeType) -> tuple[str, str] | None:
        """``(id of type_a, id of type_b)`` whichever way the edge is stored, else ``None``."""
        na, nb = self.nodes.get(a), self.nodes.get(b)
        if na is None or nb is None:
            return None
        if na.type == type_a and nb.type == type_b:
            return a, b
        if nb.type == type_a and na.type == type_b:
            return b, a
        return None

    def label(self, node_id: str) -> str:
        n = self.nodes.get(node_id)
        return n.label if n is not None else node_id

    def ic(self, phenotype_id: str) -> float | None:
        n = self.nodes.get(phenotype_id)
        value = n.props.get("ic") if n is not None else None
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def pathways_of_disease(self, disease_id: str) -> set[str]:
        out: set[str] = set()
        for gene in self.genes.get(disease_id, ()):
            out |= self.pathways_of_gene.get(gene, set())
        return out


def _dominant(counter: Counter[str]) -> str | None:
    """Most frequent key, ties broken alphabetically; ``None`` for an empty counter."""
    if not counter:
        return None
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _why(members: Sequence[str], ctx: _Context) -> dict[str, Any]:
    directions: Counter[str] = Counter()
    origins: Counter[str] = Counter()
    for d in members:
        dirs = {direction for direction, _ in ctx.mechanisms.get(d, ())}
        origs = {origin for _, origin in ctx.mechanisms.get(d, ()) if origin}
        directions.update(dirs)
        origins.update(origs)

    pathway_counts: Counter[str] = Counter()
    for d in members:
        pathway_counts.update(ctx.pathways_of_disease(d))
    shared = [p for p, n in pathway_counts.items() if n >= MIN_MEMBERS_FOR_SHARED_PATHWAY]
    shared.sort(key=lambda p: (-pathway_counts[p], ctx.label(p), p))

    phenotype_counts: Counter[str] = Counter()
    for d in members:
        phenotype_counts.update(ctx.phenotypes.get(d, ()))
    ranked = sorted(
        phenotype_counts,
        key=lambda p: (-phenotype_counts[p], -(ctx.ic(p) if ctx.ic(p) is not None else -1.0), p),
    )
    top_phenotypes = [
        {"id": p, "label": ctx.label(p), "ic": ctx.ic(p), "n_members": phenotype_counts[p]}
        for p in ranked[:MAX_TOP_PHENOTYPES]
    ]

    return {
        "size": len(members),
        "dominant_direction": _dominant(directions),
        "direction_counts": dict(sorted(directions.items())),
        "dominant_origin": _dominant(origins),
        "origin_counts": dict(sorted(origins.items())),
        "shared_pathways": [ctx.label(p) for p in shared[:MAX_SHARED_PATHWAYS]],
        "top_phenotypes": top_phenotypes,
        "member_labels": [ctx.label(d) for d in members],
    }


# --------------------------------------------------------------------------------------
# clusters
# --------------------------------------------------------------------------------------


def _weighting_name(weighting: Mapping[str, Any], position: int) -> str:
    name = weighting.get("name")
    return str(name) if name not in (None, "") else f"weighting_{position}"


def _versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for module_name in ("igraph", "leidenalg"):
        try:
            module = __import__(module_name)
            out[module_name] = str(getattr(module, "__version__", ""))
        except ImportError:  # pragma: no cover - both are project dependencies
            out[module_name] = ""
    return out


def _inside(rows: Iterable[Mapping[str, Any]], member_set: set[str], *, relation: str | None) -> int:
    """How many rows join two distinct members of ``member_set`` (optionally of one relation)."""
    return sum(
        1
        for r in rows
        if (relation is None or r.get("relation", relation) == relation)
        and r.get("source") in member_set
        and r.get("target") in member_set
        and r.get("source") != r.get("target")
    )


def build_clusters(
    graph: Graph,
    weightings: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any] | None,
    sim_fn: SimilarityFn | None = None,
    *,
    today: str | None = None,
    disease_ids: Iterable[str] | None = None,
    repulsion_fn: RepulsionFn | None = None,
) -> list[dict[str, Any]]:
    """Cluster drafts (``{"id", "members", "robust", "why"}``) for ``graph``.

    ``weightings`` are the ``weightings`` rows of ``logic.yaml`` (``{name, w_m, w_p, w_c}``);
    the first is primary. ``cfg`` is the loaded ``logic.yaml`` dict (passed through unchanged to
    ``sim_fn``; its ``clustering`` section drives the partition) or just that section when
    ``sim_fn`` needs nothing else. ``sim_fn(graph, weighting, cfg, today=today)`` returns
    ``similar_to`` edge drafts; it defaults to :func:`pipeline.graph.similarity.compute_similarity`.
    ``repulsion_fn(graph, weighting, cfg, today=today)`` returns the penalised pairs for the
    negative layer (``{"source", "target", "weight"}``, see
    :func:`pipeline.graph.similarity.compute_repulsions`); ``None`` means no signed layer.
    ``today`` is the build date (ISO) handed to both; it defaults to the current UTC date.
    ``disease_ids`` is the vertex set; it defaults to every Disease node in the graph.

    ``why`` keys: ``size``, ``robust_jaccard_min`` (the smallest, over the other weightings, of
    the best Jaccard this member set reaches against that weighting's clusters; 1.0 with a single
    weighting), ``robust_by_weighting``, ``robust_threshold``, ``dominant_direction`` and
    ``direction_counts`` (members' ``has_mechanism`` directions), ``dominant_origin`` and
    ``origin_counts`` (``attributes.origin`` or the ``:somatic`` suffix), ``shared_pathways``
    (up to :data:`MAX_SHARED_PATHWAYS` pathway labels reached by the most members through
    ``associated_with`` -> ``in_pathway``, at least :data:`MIN_MEMBERS_FOR_SHARED_PATHWAY` of
    them, so a singleton shares nothing; associations labelled with
    ``similarity.association_exclude_labels`` are skipped exactly as the similarity score skips
    them), ``top_phenotypes`` (up to :data:`MAX_TOP_PHENOTYPES` ``{id, label, ic,
    n_members}`` from ``has_phenotype`` edges, ranked by members then ``props.ic``),
    ``weightings_used``, ``primary_weighting``, ``member_labels``,
    ``internal_similarity_edges`` (primary-weighting edges inside the cluster),
    ``internal_repulsions`` (penalised pairs that still ended up inside the cluster; 0 is the
    goal) and ``method`` (algorithm, objective, resolution, seed, iterations, signed layer and
    its weight, library versions).
    """
    if not weightings:
        raise ValueError("build_clusters needs at least one weighting (the first is primary)")
    c = clustering_config(cfg)
    if sim_fn is None:
        from pipeline.graph.similarity import compute_similarity

        sim_fn = compute_similarity
    build_date = today or _utc_today()
    ids = sorted(set(disease_ids)) if disease_ids is not None else sorted(n.id for n in graph.nodes if n.type == NodeType.Disease)

    names: list[str] = []
    groups_by_weighting: list[list[list[str]]] = []
    primary_drafts: list[dict[str, Any]] = []
    primary_repulsions: list[dict[str, Any]] = []
    for position, weighting in enumerate(weightings):
        drafts = list(sim_fn(graph, weighting, cfg, today=build_date))
        repulsions = list(repulsion_fn(graph, weighting, cfg, today=build_date)) if repulsion_fn is not None else []
        names.append(_weighting_name(weighting, position))
        groups_by_weighting.append(partition_groups(leiden_partition(ids, drafts, c, repulsions)))
        if position == 0:
            primary_drafts, primary_repulsions = drafts, repulsions

    primary_groups = groups_by_weighting[0]
    others = list(zip(names[1:], groups_by_weighting[1:]))
    ctx = _Context(graph, _association_exclude_labels(cfg))
    method = {
        "algorithm": ALGORITHM,
        "objective": c["objective"],
        "resolution": c["resolution"],
        "seed": c["seed"],
        "n_iterations": c["n_iterations"],
        "singleton_clusters": c["singleton_clusters"],
        "signed": bool(primary_repulsions) and c["repulsion_weight"] > 0.0,
        "repulsion_weight": c["repulsion_weight"],
        "repulsions": len(primary_repulsions),
        **_versions(),
    }

    out: list[dict[str, Any]] = []
    for position, members in enumerate(primary_groups, start=1):
        member_set = set(members)
        by_weighting = {
            name: round(max((jaccard(member_set, other) for other in groups), default=0.0), _ROUND)
            for name, groups in others
        }
        robust_min = min(by_weighting.values(), default=1.0)
        why = {
            **_why(members, ctx),
            "robust_jaccard_min": robust_min,
            "robust_by_weighting": by_weighting,
            "robust_threshold": c["robust_min_jaccard"],
            "weightings_used": list(names),
            "primary_weighting": names[0],
            "internal_similarity_edges": _inside(primary_drafts, member_set, relation=REL_SIMILAR_TO),
            "internal_repulsions": _inside(primary_repulsions, member_set, relation=None),
            "method": dict(method),
        }
        out.append(
            {
                "id": CLUSTER_ID_FORMAT.format(position),
                "members": list(members),
                "robust": all(v >= c["robust_min_jaccard"] for v in by_weighting.values()),
                "why": why,
            }
        )
    return out


def cluster_report(clusters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summary counts for ``build_report`` / ``logic_report``: clusters, robust, singletons, sizes."""
    sizes = [len(cl.get("members", [])) for cl in clusters]
    first_why = (clusters[0].get("why") or {}) if clusters else {}
    return {
        "clusters": len(clusters),
        "robust": sum(1 for cl in clusters if cl.get("robust")),
        "sensitive": sum(1 for cl in clusters if not cl.get("robust")),
        "singletons": sum(1 for s in sizes if s == 1),
        "multi_member": sum(1 for s in sizes if s > 1),
        "diseases_clustered": sum(sizes),
        "largest": max(sizes, default=0),
        "sizes": sorted(sizes, reverse=True)[:20],
        "internal_repulsions": sum(int((cl.get("why") or {}).get("internal_repulsions", 0)) for cl in clusters),
        "signed": bool((first_why.get("method") or {}).get("signed", False)),
        "weightings_used": list(first_why.get("weightings_used", [])),
    }
