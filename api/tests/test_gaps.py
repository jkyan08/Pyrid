"""GET /gaps/{id} — honest gaps, zero-hit searches, contested/small_n evidence."""

from __future__ import annotations


def test_gaps_orphan_honest_gap(client, fixture_graph):
    body = client.get("/gaps/mock:gene:orphan").json()
    assert body["entity_id"] == "mock:gene:orphan"
    assert body["honest_gap"] is True
    assert body["coverage"] == fixture_graph["meta"]["coverage"]["mock:gene:orphan"]
    joined = "\n".join(body["missing_evidence"])
    # both zero-hit searches from meta.coverage are surfaced
    assert "placeholder orphan gene (mock)" in joined
    assert "placeholder orphan gene interventional (mock)" in joined
    assert "0 hits" in joined
    assert body["what_would_change"], "must say what would change the answer"


def test_gaps_gof_surfaces_contested_and_small_n(client):
    body = client.get("/gaps/mock:disease:gof").json()
    assert body["honest_gap"] is False
    joined = "\n".join(body["missing_evidence"])
    assert "e_000011" in joined
    assert "contested" in joined
    assert "small_n" in joined
    # the zero-hit trials search for this disease is also surfaced
    assert "0 hits" in joined
    assert body["what_would_change"]


def test_gaps_unknown_404(client):
    assert client.get("/gaps/not:a:node").status_code == 404
