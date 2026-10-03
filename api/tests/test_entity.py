"""GET /entity/{id} — summary cards, neighbors, honest no-supported-route."""

from __future__ import annotations

import re

CITED = re.compile(r"\[([^\]]+)\]")


def test_entity_gof_full_card(client, fixture_graph):
    body = client.get("/entity/mock:disease:gof").json()
    assert body["node"]["id"] == "mock:disease:gof"

    # neighbors: all 9 fixture edges touching the disease, with direction
    assert len(body["neighbors"]) == 9
    by_edge = {n["edge"]["id"]: n for n in body["neighbors"]}
    assert by_edge["e_000003"]["direction"] == "in"
    assert by_edge["e_000003"]["node"]["id"] == "mech:ALPHA:GoF"
    assert by_edge["e_000005"]["direction"] == "out"
    assert by_edge["e_000005"]["node"]["id"] == "mock:phenotype:1"

    assert body["cluster_ids"] == ["cl_mock_gof"]

    # summary: three plain lines, each citing at least one known edge/claim id
    assert len(body["summary"]) == 3
    known = {e["id"] for e in fixture_graph["edges"]} | {
        c["id"] for c in fixture_graph["claims"]
    }
    for line in body["summary"]:
        groups = CITED.findall(line)
        assert groups, f"summary line has no [id] citation: {line}"
        for group in groups:
            for cited_id in [p.strip() for p in group.split(",")]:
                assert cited_id in known, f"unknown cited id {cited_id!r} in: {line}"

    # assets contain the registry (plus group/study neighbors)
    asset_ids = {a["node"]["id"] for a in body["assets"]}
    assert "mock:registry:1" in asset_ids
    assert {"mock:group:umbrella", "mock:study:1"} <= asset_ids

    investigator_ids = [i["node"]["id"] for i in body["investigators"]]
    assert investigator_ids == ["mock:investigator:1"]

    assert body["no_supported_route"] is False
    assert body["coverage"] == fixture_graph["meta"]["coverage"]["mock:disease:gof"]
    assert body["next_question"] is None


def test_entity_orphan_no_supported_route(client, fixture_graph):
    body = client.get("/entity/mock:gene:orphan").json()
    assert body["no_supported_route"] is True
    assert body["neighbors"] == []
    # coverage comes straight from meta.coverage (ground-truth test 6 shape)
    assert body["coverage"] == fixture_graph["meta"]["coverage"]["mock:gene:orphan"]
    assert len(body["coverage"]["searched"]) == 2
    assert isinstance(body["next_question"], str)
    assert body["next_question"].strip()
    assert "Gene Orphan" in body["next_question"]


def test_entity_unknown_404(client):
    resp = client.get("/entity/unknown")
    assert resp.status_code == 404
    assert "detail" in resp.json()
