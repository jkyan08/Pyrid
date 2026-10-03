"""GET /cluster/{id} and GET /edge/{id}."""

from __future__ import annotations


def test_cluster_members_resolved(client):
    body = client.get("/cluster/cl_mock_gof").json()
    assert body["cluster"]["id"] == "cl_mock_gof"
    member_ids = [m["id"] for m in body["members"]]
    assert member_ids == ["mock:disease:gof", "mock:disease:gamma-gof"]
    assert body["members"][0]["type"] == "Disease"
    assert body["why"] == {"shared": "same pathway step, same direction (mock)"}


def test_cluster_unknown_404(client):
    resp = client.get("/cluster/cl_nope")
    assert resp.status_code == 404
    assert "detail" in resp.json()


def test_edge_contested_resolves_claims_and_labels(client, fixture_graph):
    body = client.get("/edge/e_000011").json()
    assert body["edge"]["id"] == "e_000011"
    assert body["source_node"]["id"] == "mock:drug:x"
    assert body["target_node"]["id"] == "mock:disease:gof"

    claims = {c["id"]: c for c in fixture_graph["claims"]}
    assert body["supporting_claims"] == [claims["c_000001"]]
    assert body["contradicting_claims"] == [claims["c_000002"]]
    assert body["labels"] == ["promising", "contested", "small_n"]


def test_edge_without_labels_falls_back_to_status(client):
    body = client.get("/edge/e_000012").json()
    assert body["labels"] == ["inferred_unsupported"]
    assert body["supporting_claims"] == []
    assert body["contradicting_claims"] == []


def test_edge_unknown_404(client):
    assert client.get("/edge/e_999999").status_code == 404
