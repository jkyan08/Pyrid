"""Shared pytest fixtures and graph helpers.

Fixtures
--------
``built_graph``
    The BUILT graph, read from ``$ATLAS_GRAPH_PATH`` (default ``data/build/graph.json``;
    a relative path resolves against the repo root) through the strict file-level contract
    :meth:`pipeline.models.Graph.load`. When the file is missing or does not satisfy the
    contract the fixture calls ``pytest.fail`` (never ``skip``), so an ``xfail``-marked
    ground-truth test shows up as an expected failure instead of a silent skip.
``mock_graph_file``
    ``data/build/graph.mock.json`` (``$ATLAS_MOCK_GRAPH_PATH``) when it exists, else skip.
``inline_mock_graph``
    A tiny in-memory graph (``mock:`` ids, ``"Mock "`` labels) that follows the relation
    contract documented at the top of ``tests/test_ground_truth.py`` and satisfies ground-truth
    tests 1-5 and 7 by construction. It exists so the helpers below are exercised today and so
    the ground-truth tests' own logic can be checked (dump :func:`inline_mock_graph_dict` to a
    file and point ``ATLAS_GRAPH_PATH`` at it). It carries no external identifiers, no real
    papers and no dosing information.

Helpers
-------
Pure functions over :class:`pipeline.models.Graph`. They never hand-type external ids:
genes are located by ``props.symbol`` (HGNC), mechanisms by the internal
``mech:<GENE>:<GoF|LoF|DN>[:somatic]`` convention, everything else by node type, label regex
or by following edges. Relation names are the ``REL_*`` constants below, taken from
``data/build/graph.mock.json`` (the vocabulary the frontend builds against); helpers accept a
relation's edge in either direction.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pipeline.models import Claim, Cluster, Edge, Graph, Node, NodeType

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_GRAPH_PATH = "data/build/graph.json"
DEFAULT_MOCK_GRAPH_PATH = "data/build/graph.mock.json"

# --------------------------------------------------------------------------------------
# Relation names (the contract the graph build emits; same vocabulary as data/build/graph.mock.json)
# --------------------------------------------------------------------------------------

REL_HAS_MECHANISM = "has_mechanism"  # Disease -> Mechanism (attributes.origin); Variant -> Mechanism
REL_DISEASE_GENE = "associated_with"  # Disease -> Gene (attributes.origin)
REL_HAS_PHENOTYPE = "has_phenotype"  # Disease -> Phenotype
REL_GENE_PATHWAY = "in_pathway"  # Gene -> Pathway
REL_INHIBITS = "inhibits"  # Drug -> Gene | Drug -> Pathway
REL_TREATS = "treats"  # Drug -> Disease
REL_SIMILAR_TO = "similar_to"  # Disease -> Disease (inferred)
REL_VARIANT_OF = "variant_of"  # Variant -> Gene

MECHANISM_DIRECTIONS = ("GoF", "LoF", "DN")
_MECHANISM_ID = re.compile(r"^mech:(?P<gene>[^:]+):(?P<direction>GoF|LoF|DN)(?P<somatic>:somatic)?$")

# "JAK inhibitor" is a drug class defined by its targets (the Janus kinases), so matching the
# target gene's HGNC symbol or a pathway label is a naming convention, not a hand-typed id.
JAK_KINASE_SYMBOL = re.compile(r"^(JAK[123]|TYK2)$")
JAK_STAT_PATHWAY_LABEL = re.compile(r"\bJAK\W{0,3}STAT\b", re.IGNORECASE)
JAK_INHIBITOR_LABEL = "jak_inhibitor"  # optional explicit tag in Drug->Disease edge attributes.labels

# Variant props that name the variant (besides label and synonyms); Variant.props.gene holds a
# Gene node id and is deliberately not a naming prop.
VARIANT_NAME_PROPS = ("protein_change", "hgvs_p", "hgvs_c", "hgvs", "name", "cdna_change", "aa_change")


# --------------------------------------------------------------------------------------
# Paths and loading
# --------------------------------------------------------------------------------------


def _resolve(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def built_graph_path() -> Path:
    return _resolve(os.environ.get("ATLAS_GRAPH_PATH") or DEFAULT_GRAPH_PATH)


def mock_graph_path() -> Path:
    return _resolve(os.environ.get("ATLAS_MOCK_GRAPH_PATH") or DEFAULT_MOCK_GRAPH_PATH)


def load_graph_or_fail(path: Path, what: str) -> Graph:
    """Strict load; ``pytest.fail`` (never skip) when the file is missing or off-contract."""
    if not path.is_file():
        pytest.fail(
            f"{what} not found at {path} (set ATLAS_GRAPH_PATH or run `make data`); "
            "the ground-truth tests need the built graph"
        )
    try:
        return Graph.load(path)
    except ValidationError as exc:
        pytest.fail(f"{what} at {path} does not satisfy the graph.json contract:\n{exc}")


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def built_graph() -> Graph:
    """The built graph.json; fails (does not skip) when it is absent."""
    return load_graph_or_fail(built_graph_path(), "built graph")


@pytest.fixture(scope="session")
def mock_graph_file() -> Graph:
    """data/build/graph.mock.json when it exists (written by the M0 scaffold)."""
    path = mock_graph_path()
    if not path.is_file():
        pytest.skip(f"mock graph not present at {path}")
    return Graph.load(path)


@pytest.fixture(scope="session")
def inline_mock_graph() -> Graph:
    """In-memory mock that satisfies the relation contract; validated as strictly as a file."""
    return Graph.model_validate_json(json.dumps(inline_mock_graph_dict()), strict=True)


# --------------------------------------------------------------------------------------
# Node lookup
# --------------------------------------------------------------------------------------


def nodes_of_type(graph: Graph, node_type: NodeType | str) -> list[Node]:
    return [n for n in graph.nodes if n.type == node_type]


def node(graph: Graph, node_id: str) -> Node:
    """Node by id; ``pytest.fail`` when it does not exist."""
    found = graph.node_index().get(node_id)
    if found is None:
        pytest.fail(f"no node with id {node_id!r}")
    return found


def gene_symbol(gene: Node) -> str:
    return str(gene.props.get("symbol") or gene.label)


def find_gene(graph: Graph, symbol: str) -> Node:
    """The single Gene node whose ``props.symbol`` is ``symbol`` (fallback: label == symbol)."""
    genes = nodes_of_type(graph, NodeType.Gene)
    hits = [n for n in genes if n.props.get("symbol") == symbol]
    if not hits:
        hits = [n for n in genes if n.label == symbol]
    if not hits:
        pytest.fail(f"no Gene node with props.symbol == {symbol!r}")
    if len(hits) > 1:
        pytest.fail(f"{len(hits)} Gene nodes carry symbol {symbol!r}: {[n.id for n in hits]}")
    return hits[0]


def mechanism_id(gene: str, direction: str, somatic: bool = False) -> str:
    if direction not in MECHANISM_DIRECTIONS:
        raise ValueError(f"direction must be one of {MECHANISM_DIRECTIONS}, got {direction!r}")
    return f"mech:{gene}:{direction}" + (":somatic" if somatic else "")


def parse_mechanism_id(node_id: str) -> tuple[str, str, bool] | None:
    """``(gene_symbol, direction, somatic)`` for a ``mech:`` id, else ``None``."""
    m = _MECHANISM_ID.match(node_id)
    if m is None:
        return None
    return m.group("gene"), m.group("direction"), bool(m.group("somatic"))


def mechanism(graph: Graph, gene: str, direction: str, somatic: bool = False) -> Node:
    """The Mechanism node ``mech:<gene>:<direction>[:somatic]``; ``pytest.fail`` when absent."""
    wanted = mechanism_id(gene, direction, somatic)
    found = graph.node_index().get(wanted)
    if found is None:
        available = sorted(
            n.id for n in nodes_of_type(graph, NodeType.Mechanism) if n.id.startswith(f"mech:{gene}:")
        )
        pytest.fail(f"no Mechanism node {wanted!r}; mechanisms for {gene}: {available or 'none'}")
    if found.type != NodeType.Mechanism:
        pytest.fail(f"{wanted!r} is a {found.type} node, not a Mechanism")
    return found


# --------------------------------------------------------------------------------------
# Edge traversal
# --------------------------------------------------------------------------------------


def edges_touching(graph: Graph, node_id: str, relation: str | None = None) -> list[Edge]:
    """Edges with ``node_id`` at either end, optionally restricted to one relation."""
    return [
        e
        for e in graph.edges
        if (e.source == node_id or e.target == node_id) and (relation is None or e.relation == relation)
    ]


def edges_between(graph: Graph, a: str, b: str, relation: str | None = None) -> list[Edge]:
    """Edges joining ``a`` and ``b`` in either direction."""
    return [
        e
        for e in graph.edges
        if {e.source, e.target} == {a, b} and (relation is None or e.relation == relation)
    ]


def neighbors(
    graph: Graph,
    node_id: str,
    relation: str | None = None,
    node_type: NodeType | str | None = None,
) -> list[Node]:
    """Nodes joined to ``node_id`` (either direction), deduplicated, in edge order."""
    index = graph.node_index()
    seen: set[str] = set()
    out: list[Node] = []
    for e in edges_touching(graph, node_id, relation):
        other_id = e.target if e.source == node_id else e.source
        if other_id in seen:
            continue
        other = index.get(other_id)
        if other is None or (node_type is not None and other.type != node_type):
            continue
        seen.add(other_id)
        out.append(other)
    return out


def diseases_for_mechanism(graph: Graph, mech_id: str) -> list[Node]:
    """Disease nodes joined to the mechanism by a ``has_mechanism`` edge."""
    return neighbors(graph, mech_id, REL_HAS_MECHANISM, NodeType.Disease)


def disease_mechanism_edges(graph: Graph) -> list[tuple[Edge, Node, Node]]:
    """Every ``has_mechanism`` edge between a Disease and a Mechanism as ``(edge, disease, mechanism)``."""
    index = graph.node_index()
    out: list[tuple[Edge, Node, Node]] = []
    for e in graph.edges:
        if e.relation != REL_HAS_MECHANISM:
            continue
        s, t = index.get(e.source), index.get(e.target)
        if s is None or t is None:
            continue
        if s.type == NodeType.Disease and t.type == NodeType.Mechanism:
            out.append((e, s, t))
        elif t.type == NodeType.Disease and s.type == NodeType.Mechanism:
            out.append((e, t, s))
    return out


def phenotypes_of(graph: Graph, disease_id: str) -> set[str]:
    """Phenotype node ids joined to the disease by ``has_phenotype`` edges."""
    return {n.id for n in neighbors(graph, disease_id, REL_HAS_PHENOTYPE, NodeType.Phenotype)}


def genes_of_disease(graph: Graph, disease_id: str) -> list[Node]:
    """Gene nodes joined to the disease by ``associated_with`` edges."""
    return neighbors(graph, disease_id, REL_DISEASE_GENE, NodeType.Gene)


def drug_edges_to(graph: Graph, disease_id: str) -> list[Edge]:
    """Edges from a Drug node to the disease (any relation; treatment edges use ``treats``)."""
    index = graph.node_index()
    return [
        e
        for e in graph.edges
        if e.target == disease_id and (src := index.get(e.source)) is not None and src.type == NodeType.Drug
    ]


def edge_labels(edge: Edge) -> list[str]:
    labels = edge.attributes.get("labels", [])
    return [str(x) for x in labels] if isinstance(labels, list) else []


def _is_jak_target(target: Node) -> bool:
    if target.type == NodeType.Gene:
        return bool(JAK_KINASE_SYMBOL.match(gene_symbol(target)))
    if target.type == NodeType.Pathway:
        return bool(JAK_STAT_PATHWAY_LABEL.search(target.label))
    return False


def is_jak_inhibitor(graph: Graph, drug: Node) -> bool:
    """A Drug with an ``inhibits`` edge (or ``props.target``) to a Janus-kinase Gene or a
    JAK-STAT-labelled Pathway."""
    targets = neighbors(graph, drug.id, REL_INHIBITS)
    prop_target = graph.node_index().get(str(drug.props.get("target", "")))
    if prop_target is not None and drug.props.get("action", REL_INHIBITS) == REL_INHIBITS:
        targets.append(prop_target)
    return any(_is_jak_target(t) for t in targets)


def is_jak_inhibitor_edge(graph: Graph, edge: Edge) -> bool:
    """Drug-sourced edge whose drug is a JAK inhibitor, or that is tagged ``jak_inhibitor``."""
    if JAK_INHIBITOR_LABEL in edge_labels(edge):
        return True
    src = graph.node_index().get(edge.source)
    return src is not None and src.type == NodeType.Drug and is_jak_inhibitor(graph, src)


# --------------------------------------------------------------------------------------
# Clusters
# --------------------------------------------------------------------------------------


def cluster_of(graph: Graph, node_id: str) -> Cluster | None:
    """The cluster containing ``node_id`` (clusters are a partition), or ``None``."""
    for cl in graph.clusters:
        if node_id in cl.members:
            return cl
    return None


def require_cluster(graph: Graph, n: Node) -> Cluster:
    cl = cluster_of(graph, n.id)
    if cl is None:
        pytest.fail(f"{n.type} {n.id!r} ({n.label}) is in no cluster")
    return cl


# --------------------------------------------------------------------------------------
# Variants and claims
# --------------------------------------------------------------------------------------


def variants_of_gene(graph: Graph, gene: Node) -> list[Node]:
    """Variant nodes whose ``props.gene`` is the gene's id, whose ``props.gene_symbol`` is its
    symbol, or that have an edge (``variant_of``) to the gene."""
    symbol = gene_symbol(gene)
    linked = {n.id for n in neighbors(graph, gene.id, node_type=NodeType.Variant)}
    return [
        v
        for v in nodes_of_type(graph, NodeType.Variant)
        if v.props.get("gene") == gene.id or v.props.get("gene_symbol") == symbol or v.id in linked
    ]


_ALNUM_TOKEN = re.compile(r"[A-Za-z]*\d+[A-Za-z>]*")


def variant_terms(variant: Node, exclude: Iterable[str] = ()) -> set[str]:
    """Lower-cased strings that name the variant: label, synonyms, naming props and their
    digit-bearing tokens (e.g. ``R274W``), minus ``exclude`` (typically the gene symbol)."""
    raw = [variant.label, *variant.synonyms]
    raw += [str(variant.props[k]) for k in VARIANT_NAME_PROPS if isinstance(variant.props.get(k), str)]
    terms = {t.strip() for t in raw if t and t.strip()}
    for t in list(terms):
        terms.update(tok for tok in _ALNUM_TOKEN.findall(t) if len(tok) >= 3)
    excluded = {x.lower() for x in exclude if x}
    return {t.lower() for t in terms} - excluded


def claim_mentions_variant(claim: Claim, variant: Node, gene: Node | None = None) -> bool:
    """``claim.variant`` points at the variant, or the variant's id or one of its names appears
    in the claim's subject, object or quote. The gene's symbol and id never count as a mention."""
    if claim.variant == variant.id:
        return True
    text = " ".join((claim.subject, claim.object, claim.quote)).lower()
    if variant.id.lower() in text:
        return True
    exclude: list[str] = [str(variant.props.get("gene_symbol", ""))]
    if gene is not None:
        exclude += [gene.id, gene.label, gene_symbol(gene), *gene.synonyms]
    return any(term in text for term in variant_terms(variant, exclude))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


# --------------------------------------------------------------------------------------
# Inline mock graph
# --------------------------------------------------------------------------------------

_MOCK_DATE = "2026-10-03"
_MOCK_SOURCE = {"kind": "db", "id": "mock:source:1", "date": _MOCK_DATE}


def _n(node_id: str, node_type: str, label: str, synonyms: Iterable[str] = (), **props: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "label": label, "synonyms": list(synonyms), "props": props}


def _e(
    edge_id: str,
    source: str,
    target: str,
    relation: str,
    *,
    evidence_class: str = "observed",
    confidence: float = 0.9,
    status: str = "supported",
    supporting: Iterable[str] = (),
    contradicting: Iterable[str] = (),
    **attributes: Any,
) -> dict[str, Any]:
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "relation": relation,
        "evidence_class": evidence_class,
        "confidence": confidence,
        "confidence_basis": {"mock": True},
        "status": status,
        "sources": [dict(_MOCK_SOURCE)],
        "supporting": list(supporting),
        "contradicting": list(contradicting),
        "attributes": attributes,
    }


def inline_mock_graph_dict() -> dict[str, Any]:
    """A small graph that satisfies ground-truth tests 1-5 and 7 by construction.

    Everything is a placeholder: ``mock:`` ids, ``"Mock "`` labels, one mock paper whose
    abstract is made up of the mock claim quotes. Gene symbols are the cluster's HGNC symbols
    (needed because the helpers look mechanisms up as ``mech:<SYMBOL>:...``); nothing else
    about these nodes is real.
    """
    abstract = (
        "Mock abstract. Mock supporting sentence about the mock inhibitor. "
        "Mock contradicting sentence about the mock inhibitor. "
        "Mock variant one showed a mock mechanism route."
    )
    nodes = [
        _n("mock:gene:1", "Gene", "Mock STAT1", symbol="STAT1"),
        _n("mock:gene:2", "Gene", "Mock JAK3", symbol="JAK3"),
        _n("mock:gene:3", "Gene", "Mock IL2RG", symbol="IL2RG"),
        _n("mock:gene:4", "Gene", "Mock JAK2", symbol="JAK2"),
        _n("mech:STAT1:GoF", "Mechanism", "Mock STAT1 gain of function", gene="mock:gene:1", direction="GoF"),
        _n("mech:STAT1:LoF", "Mechanism", "Mock STAT1 loss of function", gene="mock:gene:1", direction="LoF"),
        _n("mech:JAK3:LoF", "Mechanism", "Mock JAK3 loss of function", gene="mock:gene:2", direction="LoF"),
        _n("mech:IL2RG:LoF", "Mechanism", "Mock IL2RG loss of function", gene="mock:gene:3", direction="LoF"),
        _n(
            "mech:JAK2:GoF:somatic",
            "Mechanism",
            "Mock JAK2 somatic gain of function",
            gene="mock:gene:4",
            direction="GoF",
            origin="somatic",
        ),
        _n("mock:disease:1", "Disease", "Mock STAT1 GoF disease"),
        _n("mock:disease:2", "Disease", "Mock STAT1 LoF disease"),
        _n("mock:disease:3", "Disease", "Mock JAK3 LoF disease"),
        _n("mock:disease:4", "Disease", "Mock IL2RG LoF disease"),
        _n("mock:disease:5", "Disease", "Mock JAK2 somatic disease"),
        _n("mock:phenotype:1", "Phenotype", "Mock phenotype A"),
        _n("mock:phenotype:2", "Phenotype", "Mock phenotype B"),
        _n("mock:phenotype:3", "Phenotype", "Mock phenotype C"),
        _n("mock:phenotype:4", "Phenotype", "Mock phenotype D"),
        _n("mock:phenotype:5", "Phenotype", "Mock phenotype E"),
        _n("mock:pathway:1", "Pathway", "Mock JAK-STAT signaling"),
        _n("mock:drug:1", "Drug", "Mock JAK inhibitor", action="inhibits", target="mock:pathway:1"),
        # variant one is linked to its gene by props.gene and a variant_of edge, variant two
        # only by props.gene_symbol, so both lookup routes are exercised
        _n("mock:variant:1", "Variant", "Mock STAT1 variant one", gene="mock:gene:1", protein_change="Mock change one"),
        _n("mock:variant:2", "Variant", "Mock STAT1 variant two", gene_symbol="STAT1", protein_change="Mock change two"),
        _n("mock:paper:1", "Paper", "Mock paper", title="Mock paper", abstract=abstract, year=2026),
    ]
    edges = [
        # Disease -> Mechanism, with origin
        _e("e_000001", "mock:disease:1", "mech:STAT1:GoF", REL_HAS_MECHANISM, origin="germline"),
        _e("e_000002", "mock:disease:2", "mech:STAT1:LoF", REL_HAS_MECHANISM, origin="germline"),
        _e("e_000003", "mock:disease:3", "mech:JAK3:LoF", REL_HAS_MECHANISM, origin="germline"),
        _e("e_000004", "mock:disease:4", "mech:IL2RG:LoF", REL_HAS_MECHANISM, origin="germline"),
        _e("e_000005", "mock:disease:5", "mech:JAK2:GoF:somatic", REL_HAS_MECHANISM, origin="somatic"),
        # Disease -> Gene
        _e("e_000006", "mock:disease:1", "mock:gene:1", REL_DISEASE_GENE, origin="germline"),
        _e("e_000007", "mock:disease:2", "mock:gene:1", REL_DISEASE_GENE, origin="germline"),
        _e("e_000008", "mock:disease:3", "mock:gene:2", REL_DISEASE_GENE, origin="germline"),
        _e("e_000009", "mock:disease:4", "mock:gene:3", REL_DISEASE_GENE, origin="germline"),
        _e("e_000010", "mock:disease:5", "mock:gene:4", REL_DISEASE_GENE, origin="somatic"),
        # Disease -> Phenotype
        _e("e_000011", "mock:disease:1", "mock:phenotype:1", REL_HAS_PHENOTYPE),
        _e("e_000012", "mock:disease:1", "mock:phenotype:2", REL_HAS_PHENOTYPE),
        _e("e_000013", "mock:disease:2", "mock:phenotype:3", REL_HAS_PHENOTYPE),
        _e("e_000014", "mock:disease:3", "mock:phenotype:4", REL_HAS_PHENOTYPE),
        _e("e_000015", "mock:disease:4", "mock:phenotype:4", REL_HAS_PHENOTYPE),
        _e("e_000016", "mock:disease:5", "mock:phenotype:5", REL_HAS_PHENOTYPE),
        # Gene -> Pathway, Drug -> Pathway / Gene
        _e("e_000017", "mock:gene:1", "mock:pathway:1", REL_GENE_PATHWAY),
        _e("e_000018", "mock:drug:1", "mock:pathway:1", REL_INHIBITS),
        _e("e_000019", "mock:drug:1", "mock:gene:4", REL_INHIBITS),
        # Drug -> Disease: mixed small-n evidence on the GoF disease, direction mismatch on LoF
        _e(
            "e_000020",
            "mock:drug:1",
            "mock:disease:1",
            REL_TREATS,
            evidence_class="extracted",
            confidence=0.4,
            status="contested",
            supporting=["c_000001"],
            contradicting=["c_000002"],
            labels=["promising", "contested", "small_n"],
        ),
        _e(
            "e_000021",
            "mock:drug:1",
            "mock:disease:2",
            REL_TREATS,
            evidence_class="inferred",
            confidence=0.1,
            status="inferred_unsupported",
            reason="direction mismatch",
            labels=["inferred", "unsupported"],
        ),
        _e(
            "e_000022",
            "mock:drug:1",
            "mock:disease:3",
            REL_TREATS,
            evidence_class="inferred",
            confidence=0.1,
            status="inferred_unsupported",
            reason="direction mismatch",
            labels=["inferred", "unsupported"],
        ),
        # Variant -> Mechanism (one with a variant-specific route claim), Variant -> Gene
        _e(
            "e_000023",
            "mock:variant:1",
            "mech:STAT1:GoF",
            REL_HAS_MECHANISM,
            evidence_class="extracted",
            confidence=0.6,
            supporting=["c_000003"],
            origin="germline",
            labels=["variant_specific"],
        ),
        _e("e_000024", "mock:variant:2", "mech:STAT1:GoF", REL_HAS_MECHANISM, origin="germline"),
        _e("e_000025", "mock:variant:1", "mock:gene:1", REL_VARIANT_OF),
        # Disease -> Disease similarity
        _e(
            "e_000026",
            "mock:disease:3",
            "mock:disease:4",
            REL_SIMILAR_TO,
            evidence_class="inferred",
            confidence=0.5,
            labels=["inferred"],
        ),
    ]
    claims = [
        {
            "id": "c_000001",
            "pmid": "",
            "paper": "mock:paper:1",
            "quote": "Mock supporting sentence about the mock inhibitor.",
            "polarity": "supports",
            "subject": "mock:drug:1",
            "predicate": "improves",
            "object": "mock:disease:1",
            "variant": "",
            "study_type": "case_report",
            "n_patients": 1,
        },
        {
            "id": "c_000002",
            "pmid": "",
            "paper": "mock:paper:1",
            "quote": "Mock contradicting sentence about the mock inhibitor.",
            "polarity": "contradicts",
            "subject": "mock:drug:1",
            "predicate": "improves",
            "object": "mock:disease:1",
            "variant": "",
            "study_type": "case_series",
            "n_patients": 2,
        },
        {
            "id": "c_000003",
            "pmid": "",
            "paper": "mock:paper:1",
            "quote": "Mock variant one showed a mock mechanism route.",
            "polarity": "supports",
            "subject": "mock:variant:1",
            "predicate": "produces",
            "object": "mech:STAT1:GoF",
            "variant": "mock:variant:1",
            "study_type": "in_vitro",
            "n_patients": None,
        },
    ]
    clusters = [
        {"id": "cl_001", "members": ["mock:disease:1", "mech:STAT1:GoF"], "robust": True, "why": {"mock": True}},
        {"id": "cl_002", "members": ["mock:disease:2", "mech:STAT1:LoF"], "robust": True, "why": {"mock": True}},
        {
            "id": "cl_003",
            "members": ["mock:disease:3", "mock:disease:4", "mech:JAK3:LoF", "mech:IL2RG:LoF"],
            "robust": True,
            "why": {"mock": True},
        },
        {"id": "cl_004", "members": ["mock:disease:5", "mech:JAK2:GoF:somatic"], "robust": True, "why": {"mock": True}},
    ]
    return {
        "meta": {
            "built_at": f"{_MOCK_DATE}T00:00:00+00:00",
            "sources": [{"name": "mock", "version": "", "retrieved_at": _MOCK_DATE, "license": "mock"}],
            "coverage": {},
        },
        "nodes": nodes,
        "edges": edges,
        "claims": claims,
        "clusters": clusters,
    }
