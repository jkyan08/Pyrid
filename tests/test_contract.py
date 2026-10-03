"""Contract tests for ``graph.json`` (README section 8; hard rules 4 and 5).

Every test runs against every graph file this module can find:

* ``$ATLAS_GRAPH_PATH`` when it names an existing file, otherwise ``data/build/graph.mock.json``;
* ``data/build/graph.json`` (the real build) **in addition**, whenever it exists.

Two layers are checked. The JSON Schema in ``docs/graph.schema.json`` is applied to the raw
JSON with the ``jsonschema`` package when it is installed and with the small validator in
this file otherwise (the generated schema uses a fixed, known set of keywords; the test fails
loudly if a keyword this validator does not understand ever appears). The Pydantic contract
is applied with :meth:`Graph.load`, which is the strict file-level contract. The remaining
tests restate the hard rules explicitly so a failure names the rule, not a model internal.

Tests whose names start with ``test_mock_`` run only against the mock: they pin down what the
fixture must keep exercising for the frontend (every node type, every status, ...) and that
nothing in it could be mistaken for a resolved record.
"""

from __future__ import annotations

import copy
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from pipeline.models import EdgeStatus, EvidenceClass, Graph, NodeType, Polarity

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_PATH = REPO_ROOT / "data" / "build" / "graph.mock.json"
REAL_PATH = REPO_ROOT / "data" / "build" / "graph.json"
SCHEMA_PATH = REPO_ROOT / "docs" / "graph.schema.json"

MECHANISM_ID = re.compile(r"^mech:[A-Z0-9-]+:(GoF|LoF|DN)(:somatic)?$")
EDGE_ID = re.compile(r"^e_\d{6}$")
CLAIM_ID = re.compile(r"^c_\d{6}$")
CLUSTER_ID = re.compile(r"^cl_\d{3}$")

# README hard rule 4: sources (each with a date), relation, evidence class, confidence 0-1
# with its basis, supporting citations, contradicting citations. Plus the identity fields.
EDGE_REQUIRED_FIELDS = (
    "id", "source", "target", "relation", "evidence_class", "confidence",
    "confidence_basis", "status", "sources", "supporting", "contradicting",
)
SOURCE_REQUIRED_FIELDS = ("kind", "id", "date")
TOP_LEVEL_KEYS = ("meta", "nodes", "edges", "claims", "clusters")
INFERRED_CAP = 0.5
SMALL_N = 10


def normalize_ws(s: str) -> str:
    """Hard rule 5 normalization: runs of whitespace collapse to one space, ends stripped."""
    return " ".join(s.split())


# --------------------------------------------------------------------------------------
# Which graphs to test
# --------------------------------------------------------------------------------------


def graph_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("ATLAS_GRAPH_PATH")
    if env and Path(env).is_file():
        paths.append(Path(env).resolve())
    else:
        paths.append(MOCK_PATH.resolve())
    if REAL_PATH.is_file():
        paths.append(REAL_PATH.resolve())
    unique: list[Path] = []
    for p in paths:
        if p not in unique:
            unique.append(p)
    return unique


GRAPH_PATHS = graph_paths()


@pytest.fixture(params=GRAPH_PATHS, ids=[p.name for p in GRAPH_PATHS])
def graph_path(request: pytest.FixtureRequest) -> Path:
    return request.param


@lru_cache(maxsize=None)
def _raw(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _graph(path: Path) -> Graph:
    return Graph.load(path)


@pytest.fixture
def raw(graph_path: Path) -> dict[str, Any]:
    """The file as plain JSON, before any model touches it."""
    return _raw(graph_path)


@pytest.fixture
def graph(graph_path: Path) -> Graph:
    """The file through the strict Pydantic contract. Shared between tests: do not mutate."""
    return _graph(graph_path)


@pytest.fixture
def is_mock(graph_path: Path) -> bool:
    return graph_path == MOCK_PATH.resolve()


# --------------------------------------------------------------------------------------
# Minimal JSON Schema validator (used when the jsonschema package is not installed)
# --------------------------------------------------------------------------------------

_SCHEMA_KEYWORDS_HANDLED = {
    "$schema", "$defs", "$ref", "title", "description", "default",
    "type", "enum", "anyOf", "properties", "required", "additionalProperties",
    "items", "minLength", "minItems", "minProperties", "minimum", "maximum",
}

_JSON_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool))
    or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def _schema_keywords(schema: Any, out: set[str]) -> set[str]:
    """Every keyword used anywhere in the schema (property names under 'properties' and
    '$defs' are data, not keywords, and are skipped)."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            out.add(key)
            if key in ("properties", "$defs"):
                for sub in value.values():
                    _schema_keywords(sub, out)
            else:
                _schema_keywords(value, out)
    elif isinstance(schema, list):
        for item in schema:
            _schema_keywords(item, out)
    return out


def _resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    assert ref.startswith("#/"), f"only local $ref is supported, got {ref!r}"
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def schema_errors(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> list[str]:
    """Validate ``value`` against the subset of JSON Schema that docs/graph.schema.json uses."""
    if "$ref" in schema:
        return schema_errors(value, _resolve_ref(schema["$ref"], root), root, path)
    errors: list[str] = []

    if "anyOf" in schema and not any(not schema_errors(value, sub, root, path) for sub in schema["anyOf"]):
        errors.append(f"{path}: {value!r} matches none of the anyOf alternatives")

    types = schema.get("type")
    if types is not None:
        allowed = [types] if isinstance(types, str) else list(types)
        if not any(_JSON_TYPES[t](value) for t in allowed):
            errors.append(f"{path}: expected {'/'.join(allowed)}, got {type(value).__name__} {value!r}")
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")

    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: string shorter than minLength {schema['minLength']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum {schema['maximum']}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(value):
                errors += schema_errors(item, schema["items"], root, f"{path}[{i}]")

    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{path}: fewer than minProperties {schema['minProperties']}")
        for key in schema.get("required", ()):
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                errors += schema_errors(value[key], sub, root, f"{path}.{key}")
        extra = schema.get("additionalProperties", True)
        for key in value:
            if key in props:
                continue
            if extra is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(extra, dict):
                errors += schema_errors(value[key], extra, root, f"{path}.{key}")
    return errors


def validate_against_schema(instance: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """jsonschema when available, otherwise the validator above."""
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError:
        unknown = _schema_keywords(schema, set()) - _SCHEMA_KEYWORDS_HANDLED
        assert not unknown, (
            f"docs/graph.schema.json uses keywords this minimal validator does not handle: "
            f"{sorted(unknown)}; extend schema_errors() or install jsonschema"
        )
        return schema_errors(instance, schema, schema)
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in err.absolute_path) or '$'}: {err.message}" for err in validator.iter_errors(instance)]


@lru_cache(maxsize=None)
def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# Schema and model
# --------------------------------------------------------------------------------------


def test_graph_paths_exist() -> None:
    for p in GRAPH_PATHS:
        assert p.is_file(), f"graph file missing: {p}"


def test_committed_schema_matches_models() -> None:
    """docs/graph.schema.json is generated from pipeline/models.py; a stale copy would let
    the frontend and this test disagree with the pipeline. Regenerate with `make schema`."""
    assert _schema() == Graph.json_schema(), "docs/graph.schema.json is stale: run `make schema`"


def test_loads_with_graph_model(graph: Graph) -> None:
    assert isinstance(graph, Graph)
    assert graph.nodes, "a graph with no nodes is not a graph"
    assert graph.edges, "a graph with no edges has nothing to explain"


def test_raw_json_has_top_level_shape(raw: dict[str, Any]) -> None:
    assert set(raw) == set(TOP_LEVEL_KEYS), f"top-level keys must be exactly {TOP_LEVEL_KEYS}, got {sorted(raw)}"
    for key in ("nodes", "edges", "claims", "clusters"):
        assert isinstance(raw[key], list), f"{key} must be a list"
    assert isinstance(raw["meta"], dict)
    for key in ("built_at", "sources", "coverage"):
        assert key in raw["meta"], f"meta.{key} is required"


def test_raw_json_validates_against_schema(raw: dict[str, Any]) -> None:
    errors = validate_against_schema(raw, _schema())
    assert not errors, "schema violations:\n  " + "\n  ".join(errors[:30])


def test_schema_check_rejects_a_broken_edge() -> None:
    """The schema check must not be vacuous: a mangled copy of the mock has to fail it."""
    broken = copy.deepcopy(_raw(MOCK_PATH.resolve()))
    edge = broken["edges"][0]
    del edge["sources"]
    edge["confidence"] = 1.5
    edge["evidence_class"] = "guessed"
    edge["unexpected_key"] = True
    broken["meta"]["coverage"]["x"] = {"searched": [{"source": "", "query": "q", "date": "2026-10-03", "hits": -1}]}
    errors = validate_against_schema(broken, _schema())
    text = "\n".join(errors)
    for needle in ("sources", "confidence", "evidence_class", "unexpected_key", "hits"):
        assert needle in text, f"schema check did not report the broken {needle!r}:\n{text}"
    # jsonschema says "'' should be non-empty"; the built-in fallback says "minLength".
    assert "minLength" in text or "non-empty" in text, f"schema check did not report the empty string:\n{text}"


# --------------------------------------------------------------------------------------
# Edges: hard rule 4
# --------------------------------------------------------------------------------------


def test_every_raw_edge_has_required_fields(raw: dict[str, Any]) -> None:
    problems: list[str] = []
    for i, edge in enumerate(raw["edges"]):
        where = f"edges[{i}] ({edge.get('id', '?')})"
        missing = [f for f in EDGE_REQUIRED_FIELDS if f not in edge]
        if missing:
            problems.append(f"{where}: missing {missing}")
            continue
        if not isinstance(edge["sources"], list) or not edge["sources"]:
            problems.append(f"{where}: sources must be a non-empty list")
        else:
            for j, src in enumerate(edge["sources"]):
                src_missing = [f for f in SOURCE_REQUIRED_FIELDS if f not in src]
                if src_missing:
                    problems.append(f"{where}: sources[{j}] missing {src_missing}")
                elif not str(src["date"]).strip():
                    problems.append(f"{where}: sources[{j}] has an empty date")
        if edge["evidence_class"] not in {e.value for e in EvidenceClass}:
            problems.append(f"{where}: evidence_class {edge['evidence_class']!r} is not observed|extracted|inferred")
        conf = edge["confidence"]
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            problems.append(f"{where}: confidence {conf!r} is not a number in [0, 1]")
        if not isinstance(edge["confidence_basis"], dict) or not edge["confidence_basis"]:
            problems.append(f"{where}: confidence_basis must be a non-empty object (raw inputs are kept)")
        for key in ("supporting", "contradicting"):
            if not isinstance(edge[key], list):
                problems.append(f"{where}: {key} must be a list of claim ids")
    assert not problems, "\n".join(problems[:30])


def test_every_edge_has_evidence_fields(graph: Graph) -> None:
    for edge in graph.edges:
        assert edge.sources, f"{edge.id}: no sources"
        assert all(s.date.strip() for s in edge.sources), f"{edge.id}: a source has no date"
        assert isinstance(edge.evidence_class, EvidenceClass), f"{edge.id}: bad evidence_class"
        assert 0.0 <= edge.confidence <= 1.0, f"{edge.id}: confidence {edge.confidence} outside [0, 1]"
        assert edge.confidence_basis, f"{edge.id}: empty confidence_basis"
        assert isinstance(edge.supporting, list) and isinstance(edge.contradicting, list), f"{edge.id}: claim lists"
        assert edge.relation.strip(), f"{edge.id}: empty relation"


def test_edge_endpoints_are_nodes(graph: Graph) -> None:
    node_ids = {n.id for n in graph.nodes}
    for edge in graph.edges:
        assert edge.source in node_ids, f"{edge.id}: source {edge.source!r} is not a node"
        assert edge.target in node_ids, f"{edge.id}: target {edge.target!r} is not a node"
        assert edge.source != edge.target, f"{edge.id}: self-loop"


def test_every_claim_referenced_by_an_edge_exists(graph: Graph) -> None:
    claims = graph.claim_index()
    for edge in graph.edges:
        for cid in edge.supporting:
            assert cid in claims, f"{edge.id}: supporting claim {cid!r} does not exist"
            assert claims[cid].polarity is Polarity.supports, f"{edge.id}: supporting claim {cid!r} does not have polarity 'supports'"
        for cid in edge.contradicting:
            assert cid in claims, f"{edge.id}: contradicting claim {cid!r} does not exist"
            assert claims[cid].polarity is Polarity.contradicts, f"{edge.id}: contradicting claim {cid!r} does not have polarity 'contradicts'"


# --------------------------------------------------------------------------------------
# Claims: hard rule 5
# --------------------------------------------------------------------------------------


def test_every_claim_paper_is_a_paper_node(graph: Graph) -> None:
    nodes = graph.node_index()
    for claim in graph.claims:
        paper = nodes.get(claim.paper)
        assert paper is not None, f"{claim.id}: paper {claim.paper!r} is not a node"
        assert paper.type is NodeType.Paper, f"{claim.id}: paper {claim.paper!r} is a {paper.type} node, not a Paper"
        if claim.paper.startswith("pmid:"):
            assert claim.pmid == claim.paper[len("pmid:"):], f"{claim.id}: pmid {claim.pmid!r} disagrees with paper {claim.paper!r}"
        else:
            assert claim.pmid == "", f"{claim.id}: pmid {claim.pmid!r} set but paper {claim.paper!r} is not a pmid: id"


def test_every_claim_quote_matches_its_abstract(graph: Graph) -> None:
    nodes = graph.node_index()
    for claim in graph.claims:
        paper = nodes[claim.paper]
        abstract = paper.props.get("abstract")
        assert isinstance(abstract, str) and abstract.strip(), (
            f"{claim.id}: Paper {claim.paper!r} has no props.abstract; a claim cannot exist "
            "without the text its quote is checked against (hard rule 5)"
        )
        quote = normalize_ws(claim.quote)
        assert quote, f"{claim.id}: empty quote"
        assert quote in normalize_ws(abstract), (
            f"{claim.id}: quote is not a substring of the abstract of {claim.paper!r} after "
            f"whitespace normalization:\n  quote: {quote!r}"
        )


def test_claim_variants_are_variant_nodes(graph: Graph) -> None:
    nodes = graph.node_index()
    for claim in graph.claims:
        if claim.variant:
            assert claim.variant in nodes, f"{claim.id}: variant {claim.variant!r} is not a node"
            assert nodes[claim.variant].type is NodeType.Variant, f"{claim.id}: variant {claim.variant!r} is not a Variant node"


# --------------------------------------------------------------------------------------
# Status and evidence-class rules (README section 6)
# --------------------------------------------------------------------------------------


def test_inferred_edges_are_capped(graph: Graph) -> None:
    for edge in graph.edges:
        if edge.evidence_class is EvidenceClass.inferred:
            assert edge.confidence <= INFERRED_CAP, f"{edge.id}: inferred edge with confidence {edge.confidence} > {INFERRED_CAP}"


def test_contested_edges_have_both_sides(graph: Graph) -> None:
    for edge in graph.edges:
        both = bool(edge.supporting and edge.contradicting)
        if edge.status is EdgeStatus.contested:
            assert both, f"{edge.id}: contested without both supporting and contradicting claims"
        if both:
            assert edge.status is EdgeStatus.contested, f"{edge.id}: has both polarities but status is {edge.status}"


def test_inferred_unsupported_edges_carry_a_reason(graph: Graph) -> None:
    for edge in graph.edges:
        if edge.status is EdgeStatus.inferred_unsupported:
            reason = edge.attributes.get("reason")
            assert isinstance(reason, str) and reason.strip(), f"{edge.id}: inferred_unsupported without attributes.reason"
            assert edge.evidence_class is EvidenceClass.inferred, f"{edge.id}: inferred_unsupported but evidence_class {edge.evidence_class}"
            assert not edge.supporting, f"{edge.id}: inferred_unsupported cannot list supporting claims"
            assert edge.confidence <= INFERRED_CAP, f"{edge.id}: inferred_unsupported with confidence {edge.confidence}"


def test_small_n_edges_have_claims(graph: Graph) -> None:
    for edge in graph.edges:
        if edge.status is EdgeStatus.small_n:
            assert edge.supporting or edge.contradicting, f"{edge.id}: small_n describes studies, but there are no claims"


def test_edge_origin_attribute_is_germline_or_somatic(graph: Graph) -> None:
    for edge in graph.edges:
        if "origin" in edge.attributes:
            assert edge.attributes["origin"] in ("germline", "somatic"), f"{edge.id}: origin {edge.attributes['origin']!r}"


# --------------------------------------------------------------------------------------
# Nodes, clusters, meta
# --------------------------------------------------------------------------------------


def test_mechanism_node_ids(graph: Graph) -> None:
    for node in graph.nodes:
        if node.type is NodeType.Mechanism:
            assert MECHANISM_ID.match(node.id), f"Mechanism node id {node.id!r} does not match {MECHANISM_ID.pattern}"


def test_claim_nodes_have_claim_entries(graph: Graph) -> None:
    claim_ids = {c.id for c in graph.claims}
    for node in graph.nodes:
        if node.type is NodeType.Claim:
            assert node.id in claim_ids, f"Claim node {node.id!r} has no claims[] entry"
        else:
            assert node.id not in claim_ids, f"{node.type} node {node.id!r} reuses a claim id"


def test_ids_are_unique(graph: Graph) -> None:
    for name, items in (("node", graph.nodes), ("edge", graph.edges), ("claim", graph.claims), ("cluster", graph.clusters)):
        ids = [item.id for item in items]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        assert not dupes, f"duplicate {name} ids: {dupes}"
    node_ids = {n.id for n in graph.nodes}
    edge_ids = {e.id for e in graph.edges}
    assert not (node_ids & edge_ids), "node ids and edge ids must not collide (Cytoscape element ids)"
    assert not (edge_ids & {c.id for c in graph.claims}), "edge ids and claim ids must not collide (/explain cites both)"


def test_cluster_members_are_nodes_and_why_is_explained(graph: Graph) -> None:
    node_ids = {n.id for n in graph.nodes}
    seen: dict[str, str] = {}
    for cluster in graph.clusters:
        assert cluster.members, f"{cluster.id}: no members"
        for member in cluster.members:
            assert member in node_ids, f"{cluster.id}: member {member!r} is not a node"
            assert seen.setdefault(member, cluster.id) == cluster.id, f"{member!r} is in clusters {seen[member]!r} and {cluster.id!r}"
        assert cluster.why, f"{cluster.id}: why is empty; every cluster must say why its members belong together"


def test_meta_sources_and_coverage(graph: Graph) -> None:
    assert graph.meta.sources, "meta.sources is empty"
    for src in graph.meta.sources:
        assert src.license.strip(), f"meta.sources[{src.name!r}] has no license"
        assert src.retrieved_at.strip(), f"meta.sources[{src.name!r}] has no retrieved_at"
    for entity, cov in graph.meta.coverage.items():
        for run in cov.searched:
            assert run.hits >= 0, f"coverage[{entity!r}]: negative hit count"
            assert run.source.strip() and run.query.strip() and run.date.strip(), f"coverage[{entity!r}]: incomplete search record"


def test_paper_nodes_with_claims_store_abstract_text(graph: Graph) -> None:
    cited = {c.paper for c in graph.claims}
    for node in graph.nodes:
        if node.type is NodeType.Paper and node.id in cited:
            abstract = node.props.get("abstract")
            assert isinstance(abstract, str) and abstract.strip(), f"Paper {node.id!r} is cited by a claim but has no abstract"
            authors = node.props.get("authors")
            if authors is not None:
                assert isinstance(authors, list) and all(isinstance(a, str) for a in authors), f"Paper {node.id!r}: props.authors must be a list of strings"


# --------------------------------------------------------------------------------------
# Mock-only: the fixture must stay obviously fake and keep exercising the whole contract
# --------------------------------------------------------------------------------------

_FORBIDDEN_IN_MOCK = ("http://", "https://", "pmid:", "doi:", "orcid:", "NCT", "MONDO:", "HP:", "ENSG", "clinvar:")


def test_mock_uses_only_placeholder_ids_and_labels(graph: Graph, graph_path: Path, is_mock: bool) -> None:
    if not is_mock:
        pytest.skip("mock-only check")
    claim_ids = {c.id for c in graph.claims}
    for node in graph.nodes:
        if node.type is NodeType.Mechanism:
            assert node.id.startswith("mech:MOCK"), f"mock Mechanism id {node.id!r} must use a MOCK gene symbol"
        elif node.type is NodeType.Claim:
            assert node.id in claim_ids
        else:
            assert node.id.startswith("mock:"), f"mock node id {node.id!r} must start with 'mock:'"
        assert node.label.startswith("Mock "), f"mock label {node.label!r} must start with 'Mock '"
        if node.type is NodeType.Gene:
            assert str(node.props.get("symbol", "")).startswith("MOCK"), f"{node.id}: props.symbol must be a MOCK symbol"
    for edge in graph.edges:
        assert EDGE_ID.match(edge.id), f"edge id {edge.id!r}"
        for src in edge.sources:
            assert src.kind.value == "db" and src.id.startswith("mock:"), f"{edge.id}: mock sources are kind 'db' with a 'mock:' id, got {src.kind}:{src.id}"
    for claim in graph.claims:
        assert CLAIM_ID.match(claim.id), f"claim id {claim.id!r}"
        assert claim.pmid == "", f"{claim.id}: a mock claim has no PMID"
    for cluster in graph.clusters:
        assert CLUSTER_ID.match(cluster.id), f"cluster id {cluster.id!r}"
    for src in graph.meta.sources:
        assert src.name.startswith("Mock "), f"meta.sources name {src.name!r}"
        assert src.license == "mock"
    text = graph_path.read_text(encoding="utf-8")
    for needle in _FORBIDDEN_IN_MOCK:
        assert needle not in text, f"mock graph contains {needle!r}, which looks like a real identifier"


def test_mock_exercises_the_whole_contract(graph: Graph, is_mock: bool) -> None:
    if not is_mock:
        pytest.skip("mock-only check")
    assert 25 <= len(graph.nodes) <= 40, f"the mock should have 25-40 nodes for the frontend, has {len(graph.nodes)}"
    assert {n.type for n in graph.nodes} == set(NodeType), "mock must contain every node type"
    assert {e.evidence_class for e in graph.edges} == set(EvidenceClass), "mock must contain every evidence class"
    assert {e.status for e in graph.edges} == set(EdgeStatus), "mock must contain every status"

    claims = graph.claim_index()
    contested = [e for e in graph.edges if e.status is EdgeStatus.contested]
    assert any(
        all((claims[c].n_patients or 0) < SMALL_N for c in e.supporting + e.contradicting) and "small_n" in e.attributes.get("labels", [])
        for e in contested
    ), "mock needs a contested edge whose studies are all < 10 patients, with 'small_n' in attributes.labels"

    mismatch = [e for e in graph.edges if e.status is EdgeStatus.inferred_unsupported]
    assert any(e.attributes.get("reason") == "direction mismatch" and e.confidence <= INFERRED_CAP for e in mismatch), "mock needs an inferred_unsupported 'direction mismatch' edge"

    assert len(graph.clusters) >= 2 and {c.robust for c in graph.clusters} == {True, False}, "mock needs one robust and one non-robust cluster"

    similar = [e for e in graph.edges if e.relation == "similar_to"]
    assert similar, "mock needs a similar_to edge"
    for e in similar:
        assert e.evidence_class is EvidenceClass.inferred, f"{e.id}: similar_to must be inferred"
        assert {"mechanism_score", "phenotype_overlap", "conflict_penalty", "weights"} <= set(e.confidence_basis), f"{e.id}: similarity breakdown incomplete"

    nodes = graph.node_index()
    assert any(nodes[e.source].type is NodeType.Variant and nodes[e.target].type is NodeType.Mechanism for e in graph.edges), "mock needs a variant -> mechanism edge"
    assert any(nodes[e.source].type is NodeType.Variant and nodes[e.target].type is NodeType.Mechanism and e.supporting and claims[e.supporting[0]].variant for e in graph.edges), "mock needs a variant-specific mechanism claim (ground-truth test 7)"

    origins = {e.attributes.get("origin") for e in graph.edges} - {None}
    assert origins == {"germline", "somatic"}, f"mock needs both germline and somatic origin attributes, has {origins}"

    assert any(run.hits == 0 for cov in graph.meta.coverage.values() for run in cov.searched), "mock needs a zero-hit search in meta.coverage (hard rule 3)"
    assert any("verdict" in e.attributes for e in graph.edges), "mock needs an asset edge with a verdict (Act tab)"
    assert any(e.attributes.get("preprint") is True for e in graph.edges), "mock needs a preprint-flagged edge"
