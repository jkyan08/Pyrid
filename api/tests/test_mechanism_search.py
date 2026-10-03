"""GET /mechanism-search — direction-aware ranking with excluded-with-reason."""

from __future__ import annotations


def test_dampen_ranks_gof_and_excludes_lof(client):
    resp = client.get("/mechanism-search", params={"direction": "dampen"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["direction"] == "dampen"

    result_ids = [r["node"]["id"] for r in body["results"]]
    assert result_ids == ["mock:disease:gof"]
    hit = body["results"][0]
    assert hit["mechanism"]["id"] == "mech:ALPHA:GoF"
    assert hit["evidence_level"] == "observed"
    communities = {c["id"] for c in hit["communities"]}
    assert communities == {"mock:registry:1", "mock:group:umbrella"}
    assert "study_type" in hit["ranked_by"]

    excluded = {e["node"]["id"]: e["reason"] for e in body["excluded"]}
    assert set(excluded) == {"mock:disease:lof", "mock:disease:beta-lof"}
    for reason in excluded.values():
        assert "loss-of-function" in reason
        assert "wrong direction" in reason


def test_boost_mirrors_dampen(client):
    body = client.get("/mechanism-search", params={"direction": "boost"}).json()
    result_ids = {r["node"]["id"] for r in body["results"]}
    assert result_ids == {"mock:disease:lof", "mock:disease:beta-lof"}
    excluded = {e["node"]["id"]: e["reason"] for e in body["excluded"]}
    assert "mock:disease:gof" in excluded
    assert "gain-of-function" in excluded["mock:disease:gof"]


def test_invalid_direction_422(client):
    assert client.get("/mechanism-search", params={"direction": "foo"}).status_code == 422


def test_missing_direction_422(client):
    assert client.get("/mechanism-search").status_code == 422
