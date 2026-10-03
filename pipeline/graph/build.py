"""Assemble ``graph.json`` (:class:`pipeline.models.Graph`) from the evidence store.

:func:`build` reads ONLY the store tables (nodes, edges, coverage, sources, hpo_ic), the seeds and
an optional claims bundle; it never touches the network. :func:`build_graph` returns the graph,
:func:`build` also returns the report dict ``pipeline.export`` writes to ``build_report.json``.

What the build does
-------------------
* Nodes: every store node, ``props`` verbatim. Phenotype nodes gain ``props.ic`` (plus
  ``ic_n_diseases`` / ``ic_n_total``) from the ``hpo_ic`` table when present. Pathway nodes keep
  ``props.ontology``. Mechanism nodes come from :mod:`pipeline.graph.mechanisms`; Paper nodes from
  the claims bundle. Nodes are sorted by (type, id).
* Edges: every store edge, scored with :mod:`pipeline.graph.confidence` by relation
  (``associated_with`` -> disease_gene over the observed ``evidence_score`` range, ``has_phenotype``
  and the other HPO-aspect relations the backbone loader writes (``has_inheritance``,
  ``has_clinical_course``, ...) -> disease_phenotype, ``is_a`` -> ontology_is_a, ``in_pathway`` ->
  go_annotation when the pathway is a GO term or the edge carries GO codes else reactome_pathway,
  drug relations -> drug_edge, ``variant_of`` -> :data:`VARIANT_OF_CONFIDENCE`, anything else ->
  :data:`UNSCORED_CONFIDENCE`), downgraded and labelled ``conflicting_assertions`` when the backbone
  flags a relation conflict. ``evidence_class`` is ``observed``, ``status`` ``supported`` unless
  claims attach; the ``associated_with`` edges carry ``attributes.origin`` when the mechanism rules
  read one from the disease's own database text or from a claim (never from the curated seed file:
  that origin stays on the inferred, ``curated_unverified`` has_mechanism edge, hard rule 10) and
  ``attributes.labels ["below_score_threshold"]`` when the edge fails the seed threshold (kept, so
  the UI can show it as weak rather than hide it). Edge ids ``e_000001...`` are assigned after
  sorting by (relation, source, target, source_db), so two builds of one store give identical ids.
* Edge sources: ``kind: db`` with id ``"<source_db> <relation> <raw source id>-><raw target id>"``
  (the loader name plus the record ids as the backbone spelled them) and ``date: retrieved_at``;
  ``PMID:`` entries in ``props.references`` are added as ``kind: pmid`` sources.
* Claims: with a bundle, its Paper nodes and claims are added; a claim whose (subject, predicate,
  object) equals an edge's (source, relation, target) attaches to that edge, which then takes the
  status its claims imply (:func:`pipeline.graph.confidence.status_from_claims`). Claims that
  attach to nothing stay in ``claims[]`` and are counted in the report. A claim whose ``variant``
  is not a Variant node in the graph is dropped and counted (the contract forbids it).
* Meta: ``sources`` from the store's sources table, plus one record per upstream database a
  loader listed under ``notes.upstream_sources`` (``"<source> upstream: <NAME>"`` with the
  version and license the loader recorded, UNVERIFIED when the terms were not checked, so
  ``meta.sources`` matches the README section 5 license table), a record for the curated seed
  file when R4 fired and one for the claims bundle when given; ``coverage`` from the store's
  coverage rows, the mechanism rules' rows and the bundle's rows, grouped by entity id;
  ``built_at`` now (UTC).
* ``clusters`` is empty in v0 (M3 fills it).
* The report's ``counts.phenotype_ic`` records how many Phenotype nodes carry no ``props.ic``
  (HP terms outside the "Phenotypic abnormality" subtree) and how many phenotype ``is_a`` edges
  have a child with LOWER information content than its parent, which happens when the backbone's
  embedded HPO release differs from the one the IC was computed on (release drift is reported,
  not hidden).

Edges whose endpoints are not nodes cannot be exported; they are dropped and listed in the report
(``edges_dropped_dangling``), never silently.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pipeline.graph import confidence as conf_rubric
from pipeline.graph import mechanisms
from pipeline.models import Graph, NodeType
from pipeline.sources.optimuskg import ASPECT_RELATIONS

__all__ = [
    "BELOW_THRESHOLD_LABEL",
    "BuildResult",
    "HPO_ANNOTATION_RELATIONS",
    "UNSCORED_CONFIDENCE",
    "VARIANT_OF_CONFIDENCE",
    "build",
    "build_graph",
    "finalize_edges",
    "score_range",
    "seed_resolution",
    "summarize",
]

BELOW_THRESHOLD_LABEL = "below_score_threshold"
# Database-asserted identity facts (ClinVar: variant X lies in gene Y) that carry no per-edge score.
VARIANT_OF_CONFIDENCE = 0.9
# Relations the rubric does not cover: scored at the midpoint so the gap is visible in the basis.
UNSCORED_CONFIDENCE = 0.5
CONFIDENCE_DECIMALS = 4

REL_ASSOCIATED_WITH = mechanisms.REL_ASSOCIATED_WITH
REL_HAS_PHENOTYPE = "has_phenotype"
# HPO annotation edges by aspect (has_phenotype for "P", has_inheritance, has_clinical_course, ...):
# all scored with the disease_phenotype rubric, since every row carries an HPO evidence code.
HPO_ANNOTATION_RELATIONS = frozenset({REL_HAS_PHENOTYPE, *ASPECT_RELATIONS.values()})
REL_IS_A = "is_a"
REL_IN_PATHWAY = "in_pathway"
REL_VARIANT_OF = "variant_of"
DRUG_RELATIONS = frozenset({"inhibits", "targets", "indicated_for"})
GO_ONTOLOGY = "GO"

_PMID_REF = re.compile(r"^PMID:(\d+)$", re.I)
_SCORE_RANGE_NOTE_KEYS = ("evidence_score_min", "evidence_score_max")

CURATED_SEED_LICENSE = "team-authored project document (docs/test-cluster-desc.md); not a licensed database"
CLAIMS_BUNDLE_LICENSE = "PubMed abstracts: public (NCBI / NLM terms), see README section 5"


@dataclass
class BuildResult:
    graph: Graph
    report: dict[str, Any]


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
    out = [{"kind": "db", "id": f"{row['source_db']} {row['relation']} {src}->{tgt}", "date": date}]
    refs = props.get("references")
    for ref in refs if isinstance(refs, list) else ([refs] if isinstance(refs, str) else []):
        m = _PMID_REF.match(str(ref).strip())
        if m:
            s = {"kind": "pmid", "id": m.group(1), "date": date}
            if s not in out:
                out.append(s)
    return out


def _score_edge(
    row: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
    srange: tuple[float, float],
    thresholds: Mapping[str, Any],
) -> tuple[float, dict[str, Any], list[str]]:
    """``(confidence, basis, labels)`` for a store edge by relation."""
    relation = row["relation"]
    props = row.get("props") or {}
    labels: list[str] = []
    if relation == REL_ASSOCIATED_WITH:
        conf, basis = conf_rubric.disease_gene(props, srange[0], srange[1])
        if not mechanisms.association_passes(props, thresholds):
            labels.append(BELOW_THRESHOLD_LABEL)
            basis["below_threshold"] = dict(thresholds)
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
    elif relation in DRUG_RELATIONS or (nodes.get(row["source"]) or {}).get("type") == NodeType.Drug.value:
        conf, basis = conf_rubric.drug_edge(props)
    elif relation == REL_VARIANT_OF:
        conf, basis = VARIANT_OF_CONFIDENCE, {"rule": "variant_of", "source_db": row["source_db"]}
    else:
        conf, basis = UNSCORED_CONFIDENCE, {"rule": "unscored_relation", "relation": relation, "source_db": row["source_db"]}
    basis.setdefault("backbone_relation", props.get("backbone_relation"))
    basis["source_db"] = row["source_db"]
    conf, basis = conf_rubric.apply_relation_conflict(conf, basis, props)
    if conf_rubric.has_relation_conflict(props):
        labels.append(conf_rubric.RELATION_CONFLICT_LABEL)
    return conf, basis, labels


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


def _group_coverage(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        grouped[str(r["entity_id"])].append({"source": r["source"], "query": r["query"], "date": r["date"], "hits": int(r["hits"])})
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
# build
# --------------------------------------------------------------------------------------


def build(store: Any, seeds: Mapping[str, Any], claims_bundle: Any = None, *, now: str | None = None) -> BuildResult:
    """Build and validate the graph from ``store`` (see the module docstring).

    ``seeds`` is the cluster seed dict (``thresholds`` is used); it may carry ``"mechanisms"``
    (the mechanisms.yaml dict) for the curated fallback. ``now`` (ISO timestamp) is injectable so
    tests get a fixed ``built_at``.
    """
    stamp = _now_iso(now)
    thresholds = {**mechanisms.DEFAULT_THRESHOLDS, **dict(seeds.get("thresholds") or {})}
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

    # -- claims bundle: papers and claims ------------------------------------------
    claims: list[dict[str, Any]] = []
    claims_by_triple: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    dropped_claims: list[dict[str, str]] = []
    if claims_bundle is not None:
        for p in claims_bundle.papers:
            if p.id not in nodes:
                nodes[p.id] = p.model_dump(mode="json")
        for c in claims_bundle.claims:
            if c.variant and (nodes.get(c.variant) or {}).get("type") != NodeType.Variant.value:
                dropped_claims.append({"id": c.id, "reason": f"variant {c.variant!r} is not a Variant node"})
                continue
            claims.append(c.model_dump(mode="json"))
            claims_by_triple[(c.subject, c.predicate, c.object)].append(c)
    attached_claim_ids: set[str] = set()

    # -- store edges ---------------------------------------------------------------
    srange = score_range(store)
    report["score_range"] = {"min": srange[0], "max": srange[1], "how": srange[2]}
    edges: list[dict[str, Any]] = []
    dangling: list[dict[str, str]] = []
    below_threshold = 0
    conflicts = 0
    for row in store.iter_edges():
        if row["source"] not in nodes or row["target"] not in nodes:
            dangling.append({"source": row["source"], "target": row["target"], "relation": row["relation"], "source_db": row["source_db"]})
            continue
        conf, basis, labels = _score_edge(row, nodes, srange[:2], thresholds)
        below_threshold += BELOW_THRESHOLD_LABEL in labels
        conflicts += conf_rubric.RELATION_CONFLICT_LABEL in labels
        attributes: dict[str, Any] = {}
        if row["relation"] == REL_ASSOCIATED_WITH:
            origin = mech.origins.get((row["source"], row["target"]))
            if origin:
                attributes["origin"] = origin
        edge = {
            "source": row["source"],
            "target": row["target"],
            "relation": row["relation"],
            "source_db": row["source_db"],
            "evidence_class": "observed",
            "confidence": conf,
            "confidence_basis": basis,
            "status": "supported",
            "sources": _edge_sources(row, nodes),
            "supporting": [],
            "contradicting": [],
            "attributes": attributes,
        }
        matching = claims_by_triple.get((row["source"], row["relation"], row["target"]), [])
        if matching:
            sup = [c for c in matching if c.polarity.value == "supports"]
            con = [c for c in matching if c.polarity.value == "contradicts"]
            status, claim_labels, status_basis = conf_rubric.status_from_claims(sup, con)
            edge["supporting"] = [c.id for c in sup]
            edge["contradicting"] = [c.id for c in con]
            edge["status"] = status
            edge["confidence_basis"] = {**basis, "claims": status_basis}
            labels += [l for l in claim_labels if l not in labels]
            if "preprint" in claim_labels:
                attributes["preprint"] = True
            papers = {p.id: p for p in claims_bundle.papers}
            for s in mechanisms._claim_sources(matching, papers, claims_bundle.extracted_at):
                if s not in edge["sources"]:
                    edge["sources"].append(s)
            attached_claim_ids.update(c.id for c in matching)
        if labels:
            attributes["labels"] = labels
        edges.append(edge)
    for e in mech.edges:
        attached_claim_ids.update(e["supporting"])
        attached_claim_ids.update(e["contradicting"])
    edges += mech.edges
    final_edges = finalize_edges(edges)

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
    coverage_rows: list[Mapping[str, Any]] = list(store.coverage_rows()) + list(mech.coverage)
    if claims_bundle is not None:
        for entity, cov in claims_bundle.coverage.items():
            coverage_rows += [{"entity_id": entity, **s.model_dump(mode="json")} for s in cov.searched]

    graph_dict = {
        "meta": {"built_at": stamp, "sources": sources, "coverage": _group_coverage(coverage_rows)},
        "nodes": [nodes[k] for k in sorted(nodes, key=lambda k: (nodes[k]["type"], k))],
        "edges": final_edges,
        "claims": claims,
        "clusters": [],
    }
    graph = Graph.model_validate(graph_dict)
    # the file-level contract is strict: make sure what we would write loads back unchanged
    graph = Graph.model_validate_json(graph.model_dump_json(), strict=True)

    report["counts"] = summarize(graph)
    report["edges_dropped_dangling"] = dangling
    report["edges_below_threshold"] = below_threshold
    report["edges_with_relation_conflict"] = conflicts
    report["claims"] = {
        "in_bundle": len(claims_bundle.claims) if claims_bundle is not None else 0,
        "in_graph": len(claims),
        "attached_to_edges": len(attached_claim_ids),
        "unattached": sorted({c["id"] for c in claims} - attached_claim_ids),
        "dropped": dropped_claims,
    }
    report["seed_resolution"] = seed_resolution(store, seeds)
    return BuildResult(graph, report)


def build_graph(store: Any, seeds: Mapping[str, Any], claims_bundle: Any = None, *, now: str | None = None) -> Graph:
    """The validated :class:`pipeline.models.Graph` for ``store`` (see :func:`build`)."""
    return build(store, seeds, claims_bundle, now=now).graph


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
