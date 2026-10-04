"""Tests for pipeline/export.py — assembles data/build/graph.json from the
retrieved artifacts that exist today (lane B's claims bundle + papers +
investigators, lane C's verified seeds). Written before the implementation.

Honesty invariants (README hard rules):
* only fully-resolved claims (both endpoints in the backbone node table)
  become edges; nothing is invented to bridge unresolved mentions;
* clusters are empty until lane A's similarity/clustering lands;
* every edge cites real pmid sources or a seed-verified url.
"""

from __future__ import annotations

import pytest

from pipeline.export import build_graph
from pipeline.models import Graph


@pytest.fixture(scope="module")
def graph() -> Graph:
    return build_graph()


def test_graph_validates_against_contract(graph):
    assert isinstance(graph, Graph)
    assert len(graph.nodes) > 150  # 171 backbone + papers + investigators + groups
    assert graph.edges, "aggregation must produce edges from resolved claims"


def test_every_edge_endpoint_is_a_node(graph):
    ids = {n.id for n in graph.nodes}
    for e in graph.edges:
        assert e.source in ids and e.target in ids, e.id


def test_claim_edges_only_from_fully_resolved_claims(graph):
    ids = {n.id for n in graph.nodes}
    claim_ids = {c.id for c in graph.claims}
    for e in graph.edges:
        for cid in list(e.supporting) + list(e.contradicting):
            assert cid in claim_ids, f"{e.id} cites unknown claim {cid}"
    for c in graph.claims:
        assert c.subject in ids and c.object in ids, (
            f"claim {c.id} kept although an endpoint is unresolved"
        )


def test_mixed_polarity_aggregates_to_contested(graph):
    mixed = [e for e in graph.edges if e.supporting and e.contradicting]
    assert mixed, "the ruxolitinib evidence has real contradicting claims"
    for e in mixed:
        assert e.status.value == "contested", e.id


def test_clusters_empty_until_lane_a(graph):
    assert graph.clusters == []


def test_patient_groups_present_from_seeds(graph):
    groups = [n for n in graph.nodes if n.type.value in ("PatientGroup", "Registry")]
    assert len(groups) >= 7
    for g in groups:
        assert "url" in g.props, g.id


def test_investigator_edges_bounded_and_sourced(graph):
    invs = {n.id for n in graph.nodes if n.type.value == "Investigator"}
    assert invs, "investigators with >=2 papers must be included"
    for e in graph.edges:
        if e.source in invs:
            assert e.relation in ("author_of", "works_on")
            assert e.sources, e.id
