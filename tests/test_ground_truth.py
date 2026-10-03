"""Ground-truth tests 1-7 (README section 8; docs/test-cluster-desc.md section 8).

All seven run against the BUILT graph at ``$ATLAS_GRAPH_PATH`` (default
``data/build/graph.json``) and, for test 6, the FastAPI app in ``api.main``. They are marked
``xfail(strict=False)`` until the milestone named in each reason lands; remove a marker when
its test passes. With no built graph the ``built_graph`` fixture fails (never skips), so each
test shows as an expected failure. ``XPASS`` means the graph already satisfies the test.

No external identifier is hand-typed here. Genes are located by ``props.symbol`` (HGNC
symbols), mechanisms by the internal ``mech:<GENE>:<GoF|LoF|DN>[:somatic]`` id convention,
and everything else by node type, label regex or by following edges.

Relation contract (what Lane A's export must emit; the helpers in tests/conftest.py rely on it)
----------------------------------------------------------------------------------------------

Same vocabulary as ``data/build/graph.mock.json`` (which the frontend builds against):

=================  ===============================  ===============================================
relation           canonical direction              notes
=================  ===============================  ===============================================
``has_mechanism``  Disease -> Mechanism             ``attributes.origin``: ``"germline"`` | ``"somatic"``
``has_mechanism``  Variant -> Mechanism             variant-specific route claims live on this edge
``associated_with``  Disease -> Gene                backbone disease-gene edge; ``attributes.origin`` as above
``has_phenotype``  Disease -> Phenotype             backbone disease-phenotype edge
``in_pathway``     Gene -> Pathway                  backbone pathway-gene edge
``inhibits``       Drug -> Gene | Drug -> Pathway   backbone drug-target edge
``treats``         Drug -> Disease                  treatment edges, including the rule-generated
                                                    ``inferred_unsupported`` ones (reason ``"direction mismatch"``)
``similar_to``     Disease -> Disease               inferred similarity edges (README section 4)
``variant_of``     Variant -> Gene                  Variant.props.gene (gene node id) / props.gene_symbol also accepted
=================  ===============================  ===============================================

The helpers accept a relation's edge in either direction. Other conventions assumed:

* ``Gene.props.symbol`` holds the HGNC symbol; a Variant names its gene through
  ``props.gene`` (Gene node id), ``props.gene_symbol`` or a ``variant_of`` edge.
* ``clusters[].members`` include the Disease node ids (they may also hold mechanism and
  variant nodes); tests 1-3 compare the clusters of Disease nodes.
* A "JAK inhibitor" is a Drug with an ``inhibits`` edge (or ``props.target``) to a Gene whose
  symbol matches ``^(JAK[123]|TYK2)$`` or to a Pathway whose label matches ``JAK-STAT`` (any
  separator), or a Drug -> Disease edge tagged ``"jak_inhibitor"`` in ``attributes.labels``.
* A "LoF disease" is a Disease with a ``has_mechanism`` edge to a ``mech:*:LoF`` node.
* The direction-mismatch rule (README section 6) emits a Drug -> Disease edge for each LoF
  disease; the test also accepts Drug -> Mechanism(LoF) edges as the rule's output.
* A variant-specific route claim sets ``claim.variant`` to the Variant node id (canonical) or
  names the variant (id, label, synonym or ``props.protein_change``) in its subject/object/quote.
* ``/search?q=<gene>`` answers ``{"status": "no_supported_route", "coverage": [{"source", "query",
  "date", ...}], "next_question": "..."}`` for a gene with no supported route; the gene symbol
  comes from ``$ATLAS_TEST6_GENE`` once it has been chosen from the built graph.
"""

from __future__ import annotations

import os

import pytest

from pipeline.models import EdgeStatus, NodeType
from tests.conftest import (
    JAK_INHIBITOR_LABEL,
    REL_HAS_MECHANISM,
    REL_INHIBITS,
    REL_SIMILAR_TO,
    REL_TREATS,
    claim_mentions_variant,
    cluster_of,
    disease_mechanism_edges,
    diseases_for_mechanism,
    drug_edges_to,
    edge_labels,
    edges_between,
    find_gene,
    genes_of_disease,
    is_jak_inhibitor,
    is_jak_inhibitor_edge,
    jaccard,
    mechanism,
    node,
    nodes_of_type,
    parse_mechanism_id,
    phenotypes_of,
    require_cluster,
    variants_of_gene,
)

# Phenotype profiles of STAT1 GoF and STAT1 LoF disease(s) must differ at least this much.
PHENOTYPE_JACCARD_MAX = 0.8

DIRECTION_MISMATCH = "direction mismatch"

M3 = "M3 (graph logic: confidence, similarity, Leiden clustering, rules) has not landed yet"
M4 = "M4 (API: /search with no_supported_route, coverage and next question) has not landed yet"


def _describe(graph, edge) -> str:
    idx = graph.node_index()
    s = idx.get(edge.source)
    t = idx.get(edge.target)
    return f"{edge.id} ({s.label if s else edge.source} -> {t.label if t else edge.target})"


# --------------------------------------------------------------------------------------
# 1. STAT1 GoF vs LoF: different clusters, different phenotype profiles
# --------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, reason=f"test 1 (STAT1 GoF vs LoF): {M3}")
def test_1_stat1_gof_and_lof_land_in_different_clusters(built_graph):
    g = built_graph
    gof = diseases_for_mechanism(g, mechanism(g, "STAT1", "GoF").id)
    lof = diseases_for_mechanism(g, mechanism(g, "STAT1", "LoF").id)
    assert gof, "no Disease has a has_mechanism edge to mech:STAT1:GoF"
    assert lof, "no Disease has a has_mechanism edge to mech:STAT1:LoF"

    both = {d.id for d in gof} & {d.id for d in lof}
    assert not both, f"disease(s) attached to both STAT1 GoF and STAT1 LoF: {sorted(both)}"

    gof_clusters = {require_cluster(g, d).id for d in gof}
    lof_clusters = {require_cluster(g, d).id for d in lof}
    shared = gof_clusters & lof_clusters
    assert not shared, (
        f"STAT1 GoF {[d.label for d in gof]} and STAT1 LoF {[d.label for d in lof]} "
        f"share cluster(s) {sorted(shared)}"
    )

    gof_ph = set().union(*(phenotypes_of(g, d.id) for d in gof))
    lof_ph = set().union(*(phenotypes_of(g, d.id) for d in lof))
    assert gof_ph, "STAT1 GoF disease(s) have no has_phenotype edges"
    assert lof_ph, "STAT1 LoF disease(s) have no has_phenotype edges"
    j = jaccard(gof_ph, lof_ph)
    assert j < PHENOTYPE_JACCARD_MAX, (
        f"STAT1 GoF and LoF phenotype profiles are too similar: Jaccard {j:.2f} "
        f">= {PHENOTYPE_JACCARD_MAX} ({len(gof_ph)} vs {len(lof_ph)} phenotypes, {len(gof_ph & lof_ph)} shared)"
    )


# --------------------------------------------------------------------------------------
# 2. JAK3 SCID and IL2RG SCID cluster together
# --------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, reason=f"test 2 (JAK3 and IL2RG SCID together): {M3}")
def test_2_jak3_and_il2rg_lof_diseases_share_a_cluster(built_graph):
    g = built_graph
    jak3 = diseases_for_mechanism(g, mechanism(g, "JAK3", "LoF").id)
    il2rg = diseases_for_mechanism(g, mechanism(g, "IL2RG", "LoF").id)
    assert jak3, "no Disease has a has_mechanism edge to mech:JAK3:LoF"
    assert il2rg, "no Disease has a has_mechanism edge to mech:IL2RG:LoF"

    jak3_clusters = {d.label: require_cluster(g, d).id for d in jak3}
    il2rg_clusters = {d.label: require_cluster(g, d).id for d in il2rg}
    assert set(jak3_clusters.values()) & set(il2rg_clusters.values()), (
        f"no shared cluster: JAK3 LoF -> {jak3_clusters}, IL2RG LoF -> {il2rg_clusters}"
    )


# --------------------------------------------------------------------------------------
# 3. Somatic JAK2 disease is not clustered with germline diseases
# --------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, reason=f"test 3 (somatic JAK2 apart from germline): {M3}")
def test_3_somatic_jak2_diseases_share_no_cluster_with_germline_diseases(built_graph):
    g = built_graph
    somatic = diseases_for_mechanism(g, mechanism(g, "JAK2", "GoF", somatic=True).id)
    assert somatic, "no Disease has a has_mechanism edge to mech:JAK2:GoF:somatic"

    germline = {
        d.id: d.label
        for e, d, _m in disease_mechanism_edges(g)
        if e.attributes.get("origin") == "germline"
    }
    assert germline, "no Disease -> Mechanism edge carries attributes.origin == 'germline'"

    for d in somatic:
        assert d.id not in germline, (
            f"{d.id} ({d.label}) hangs off the somatic JAK2 mechanism but also has a germline-origin edge"
        )
        cl = require_cluster(g, d)
        shared = sorted(set(cl.members) & set(germline))
        assert not shared, (
            f"{d.id} ({d.label}) shares cluster {cl.id} with germline disease(s) "
            f"{[(i, germline[i]) for i in shared]}"
        )


# --------------------------------------------------------------------------------------
# 4. JAK inhibitor -> STAT1 GoF: mixed evidence, contested, small-n
# --------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, reason=f"test 4 (contested JAK inhibitor -> STAT1 GoF edge): {M3}")
def test_4_jak_inhibitor_to_stat1_gof_shows_mixed_small_n_evidence(built_graph):
    g = built_graph
    gof = diseases_for_mechanism(g, mechanism(g, "STAT1", "GoF").id)
    assert gof, "no Disease has a has_mechanism edge to mech:STAT1:GoF"

    candidates = [e for d in gof for e in drug_edges_to(g, d.id) if is_jak_inhibitor_edge(g, e)]
    assert candidates, (
        "no JAK-inhibitor Drug -> Disease edge reaches a STAT1 GoF disease "
        f"(drug edges seen: {[_describe(g, e) for d in gof for e in drug_edges_to(g, d.id)]})"
    )

    problems = []
    for e in candidates:
        labels = edge_labels(e)
        checks = {
            "supporting non-empty": bool(e.supporting),
            "contradicting non-empty": bool(e.contradicting),
            "contested": e.status == EdgeStatus.contested or "contested" in labels,
            "small_n label": "small_n" in labels,
        }
        if all(checks.values()):
            return
        failed = [name for name, ok in checks.items() if not ok]
        problems.append(f"{_describe(g, e)}: status={e.status}, labels={labels}, missing {failed}")
    pytest.fail("no JAK-inhibitor -> STAT1 GoF edge shows contested small-n evidence:\n  " + "\n  ".join(problems))


# --------------------------------------------------------------------------------------
# 5. JAK inhibitor -> LoF disease: inferred_unsupported, "direction mismatch"
# --------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, reason=f"test 5 (JAK inhibitor -> LoF disease flagged): {M3}")
def test_5_jak_inhibitor_to_lof_disease_is_inferred_unsupported(built_graph):
    g = built_graph
    idx = g.node_index()
    lof_mechs = [
        n for n in nodes_of_type(g, NodeType.Mechanism)
        if (parsed := parse_mechanism_id(n.id)) is not None and parsed[1] == "LoF"
    ]
    assert lof_mechs, "no mech:*:LoF Mechanism node in the graph"
    lof_mech_ids = {m.id for m in lof_mechs}
    lof_diseases = {d.id for m in lof_mechs for d in diseases_for_mechanism(g, m.id)}
    assert lof_diseases, "no Disease has a has_mechanism edge to any LoF mechanism"

    edges = [e for did in sorted(lof_diseases) for e in drug_edges_to(g, did) if is_jak_inhibitor_edge(g, e)]
    edges += [
        e
        for e in g.edges
        if e.target in lof_mech_ids
        and (src := idx.get(e.source)) is not None
        and src.type == NodeType.Drug
        and is_jak_inhibitor_edge(g, e)
    ]
    assert edges, (
        "the direction-mismatch rule emitted no JAK-inhibitor edge to a LoF disease or LoF mechanism "
        f"(LoF diseases: {sorted(lof_diseases)})"
    )

    bad = [
        f"{_describe(g, e)}: status={e.status}, evidence_class={e.evidence_class}, reason={e.attributes.get('reason')!r}"
        for e in edges
        if not (e.status == EdgeStatus.inferred_unsupported and e.attributes.get("reason") == DIRECTION_MISMATCH)
    ]
    assert not bad, (
        f"JAK-inhibitor edges to LoF targets must be status 'inferred_unsupported' with "
        f"attributes.reason == {DIRECTION_MISMATCH!r}:\n  " + "\n  ".join(bad)
    )


# --------------------------------------------------------------------------------------
# 6. Obscure gene: the API answers "no supported route" with coverage and a next question
# --------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, reason=f"test 6 (no_supported_route from /search): {M4}")
def test_6_obscure_gene_returns_no_supported_route():
    try:
        from fastapi.testclient import TestClient

        from api.main import app
    except Exception as exc:  # noqa: BLE001 - any import problem is the finding
        pytest.fail(f"cannot import the API (api.main:app with fastapi.testclient): {exc!r}")

    gene = os.environ.get("ATLAS_TEST6_GENE", "").strip()
    if not gene:
        pytest.fail(
            "set ATLAS_TEST6_GENE after choosing the obscure gene "
            "(README section 8: 'Test 6 gene: to choose from the built graph and document here')"
        )

    with TestClient(app) as client:
        resp = client.get("/search", params={"q": gene})
    assert resp.status_code == 200, f"/search?q={gene} returned HTTP {resp.status_code}: {resp.text[:500]}"
    body = resp.json()
    assert isinstance(body, dict), f"/search did not return a JSON object: {body!r}"

    assert body.get("status") == "no_supported_route", f"status is {body.get('status')!r}"

    coverage = body.get("coverage")
    assert isinstance(coverage, list) and coverage, f"coverage must be a non-empty list, got {coverage!r}"
    for entry in coverage:
        assert isinstance(entry, dict), f"coverage entry is not an object: {entry!r}"
        for key in ("source", "query", "date"):
            value = entry.get(key)
            assert isinstance(value, str) and value.strip(), f"coverage entry lacks a non-empty {key!r}: {entry!r}"

    next_question = body.get("next_question")
    assert isinstance(next_question, str) and next_question.strip(), (
        f"next_question must be a non-empty string, got {next_question!r}"
    )


# --------------------------------------------------------------------------------------
# 7. STAT1 GoF variants stay distinct and carry variant-specific route claims
# --------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, reason=f"test 7 (distinct STAT1 variants with variant-specific claims): {M3}")
def test_7_stat1_variants_are_distinct_nodes_with_variant_specific_claims(built_graph):
    g = built_graph
    stat1 = find_gene(g, "STAT1")
    gof = mechanism(g, "STAT1", "GoF")
    variants = variants_of_gene(g, stat1)
    assert len(variants) >= 2, (
        f"expected at least two STAT1 Variant nodes (props.gene == {stat1.id!r}, props.gene_symbol == 'STAT1' "
        f"or an edge to the gene), found {[(v.id, v.label) for v in variants]}"
    )

    routed = {v.id: edges_between(g, v.id, gof.id, REL_HAS_MECHANISM) for v in variants}
    routed = {vid: es for vid, es in routed.items() if es}
    assert len(routed) >= 2, (
        f"need at least two distinct STAT1 variants each with its own has_mechanism edge to {gof.id}; "
        f"variants with such an edge: {sorted(routed)}; all STAT1 variants: {[v.id for v in variants]}"
    )
    labels = {node(g, vid).label for vid in routed}
    assert len(labels) == len(routed), f"distinct variant nodes share a label (merged variants?): {sorted(labels)}"

    claims = g.claim_index()
    specific = [
        (vid, e.id, cid)
        for vid, es in routed.items()
        for e in es
        for cid in e.supporting
        if claim_mentions_variant(claims[cid], node(g, vid), gene=stat1)
    ]
    assert specific, (
        f"no Variant -> {gof.id} edge carries a supporting claim that names its variant "
        f"(claim.variant == variant id, or the variant's id/label/synonyms/protein_change appear in the claim's "
        f"subject, object or quote); edges checked: {[(vid, [e.id for e in es]) for vid, es in routed.items()]}"
    )


# --------------------------------------------------------------------------------------
# Smoke tests (not xfail): the helpers work on mock data today
# --------------------------------------------------------------------------------------


def test_smoke_helpers_on_inline_mock(inline_mock_graph):
    g = inline_mock_graph

    stat1 = find_gene(g, "STAT1")
    assert stat1.id == "mock:gene:1" and stat1.type == NodeType.Gene

    gof = mechanism(g, "STAT1", "GoF")
    lof = mechanism(g, "STAT1", "LoF")
    somatic = mechanism(g, "JAK2", "GoF", somatic=True)
    assert (gof.id, lof.id, somatic.id) == ("mech:STAT1:GoF", "mech:STAT1:LoF", "mech:JAK2:GoF:somatic")
    assert parse_mechanism_id(somatic.id) == ("JAK2", "GoF", True)
    assert parse_mechanism_id("mock:disease:1") is None

    gof_diseases = diseases_for_mechanism(g, gof.id)
    assert [d.id for d in gof_diseases] == ["mock:disease:1"]
    assert all(d.type == NodeType.Disease for d in gof_diseases)
    assert [d.id for d in diseases_for_mechanism(g, lof.id)] == ["mock:disease:2"]

    dm = disease_mechanism_edges(g)
    assert len(dm) == 5
    assert {d.id for e, d, _m in dm if e.attributes.get("origin") == "somatic"} == {"mock:disease:5"}

    assert phenotypes_of(g, "mock:disease:1") == {"mock:phenotype:1", "mock:phenotype:2"}
    assert jaccard(phenotypes_of(g, "mock:disease:1"), phenotypes_of(g, "mock:disease:2")) == 0.0
    assert jaccard(phenotypes_of(g, "mock:disease:3"), phenotypes_of(g, "mock:disease:4")) == 1.0
    assert [n.id for n in genes_of_disease(g, "mock:disease:3")] == ["mock:gene:2"]

    assert cluster_of(g, "mock:disease:1").id == "cl_001"
    assert cluster_of(g, "mock:disease:3").id == cluster_of(g, "mock:disease:4").id == "cl_003"
    assert cluster_of(g, "mock:gene:1") is None
    assert require_cluster(g, node(g, "mock:disease:5")).id == "cl_004"

    drug_edges = drug_edges_to(g, "mock:disease:1")
    assert [e.id for e in drug_edges] == ["e_000020"]
    assert drug_edges[0].relation == REL_TREATS
    assert edge_labels(drug_edges[0]) == ["promising", "contested", "small_n"]
    assert drug_edges_to(g, "mock:gene:1") == []

    drug = node(g, "mock:drug:1")
    assert drug.type == NodeType.Drug
    assert is_jak_inhibitor(g, drug)
    assert is_jak_inhibitor_edge(g, drug_edges[0])
    assert all(e.relation == REL_INHIBITS for e in g.edges if e.source == drug.id and e.relation != REL_TREATS)
    assert JAK_INHIBITOR_LABEL not in edge_labels(drug_edges[0])  # the mock derives the class from inhibits edges

    variants = variants_of_gene(g, stat1)
    assert {v.id for v in variants} == {"mock:variant:1", "mock:variant:2"}
    v1, v2 = node(g, "mock:variant:1"), node(g, "mock:variant:2")
    assert [e.id for e in edges_between(g, v1.id, gof.id, REL_HAS_MECHANISM)] == ["e_000023"]
    assert [e.id for e in edges_between(g, v2.id, gof.id, REL_HAS_MECHANISM)] == ["e_000024"]
    claims = g.claim_index()
    assert claim_mentions_variant(claims["c_000003"], v1, gene=stat1)
    assert not claim_mentions_variant(claims["c_000003"], v2, gene=stat1)
    assert not claim_mentions_variant(claims["c_000001"], v1, gene=stat1)

    assert [e.id for e in edges_between(g, "mock:disease:3", "mock:disease:4", REL_SIMILAR_TO)] == ["e_000026"]


def test_smoke_helpers_on_mock_file(mock_graph_file):
    """Generic shape checks against data/build/graph.mock.json (skipped while it does not exist)."""
    g = mock_graph_file
    idx = g.node_index()
    claims = g.claim_index()

    for gene in nodes_of_type(g, NodeType.Gene):
        symbol = gene.props.get("symbol")
        if isinstance(symbol, str) and symbol:
            assert find_gene(g, symbol).id == gene.id
        assert all(v.type == NodeType.Variant for v in variants_of_gene(g, gene))

    for mech in nodes_of_type(g, NodeType.Mechanism):
        parsed = parse_mechanism_id(mech.id)
        if parsed is not None:
            assert mechanism(g, *parsed).id == mech.id
        diseases = diseases_for_mechanism(g, mech.id)
        assert all(d.type == NodeType.Disease for d in diseases)

    for e, disease, mech in disease_mechanism_edges(g):
        assert e.relation == REL_HAS_MECHANISM
        assert disease.type == NodeType.Disease and mech.type == NodeType.Mechanism

    for cl in g.clusters:
        for member in cl.members:
            assert cluster_of(g, member) is not None and cluster_of(g, member).id == cl.id
    clustered = {m for cl in g.clusters for m in cl.members}
    for n in g.nodes:
        if n.id not in clustered:
            assert cluster_of(g, n.id) is None

    for disease in nodes_of_type(g, NodeType.Disease):
        for e in drug_edges_to(g, disease.id):
            assert e.target == disease.id and idx[e.source].type == NodeType.Drug
            assert isinstance(is_jak_inhibitor_edge(g, e), bool)
        assert all(idx[p].type == NodeType.Phenotype for p in phenotypes_of(g, disease.id))

    for c in g.claims:
        if c.variant:
            assert claim_mentions_variant(c, idx[c.variant])
    assert claims.keys() == {c.id for c in g.claims}
