"""GET /search — label, synonym, free-text matching, empty query."""

from __future__ import annotations


def test_label_match(client):
    body = client.get("/search", params={"q": "Gene Alpha (mock)"}).json()
    assert body["query"] == "Gene Alpha (mock)"
    top = body["results"][0]
    assert top["id"] == "mock:gene:alpha"
    assert top["type"] == "Gene"
    assert top["match_kind"] == "label"
    assert top["matched_on"] == "Gene Alpha (mock)"
    assert isinstance(top["score"], float)
    assert "resolution" in top and top["resolution"]


def test_synonym_match_resolves_cmc(client):
    body = client.get("/search", params={"q": "chronic mock candidiasis"}).json()
    top = body["results"][0]
    assert top["id"] == "mock:disease:gof"
    assert top["match_kind"] == "synonym"
    assert top["matched_on"] == "chronic mock candidiasis"
    # human-readable resolution string naming both the synonym and the label
    assert "chronic mock candidiasis" in top["resolution"]
    assert "Mock immune dysregulation syndrome" in top["resolution"]


def test_free_text_phrase_hits_phenotype(client):
    body = client.get("/search", params={"q": "recurring candida"}).json()
    ids = [r["id"] for r in body["results"]]
    assert "mock:phenotype:1" in ids


def test_free_text_multi_token_hits_phenotype(client):
    # not a substring of any label/synonym -> token-overlap free_text match
    body = client.get("/search", params={"q": "candida recurring thyroid"}).json()
    ids = [r["id"] for r in body["results"]]
    assert "mock:phenotype:1" in ids
    hit = next(r for r in body["results"] if r["id"] == "mock:phenotype:1")
    assert hit["match_kind"] == "free_text"
    # the phenotype with 2 overlapping tokens outranks the one with 1
    assert ids.index("mock:phenotype:1") < ids.index("mock:phenotype:2")


def test_empty_query_returns_empty_results(client):
    # documented choice (api/README.md): empty q -> 200 with empty results
    resp = client.get("/search", params={"q": ""})
    assert resp.status_code == 200
    assert resp.json() == {"query": "", "results": []}


def test_no_hits_returns_empty_list(client):
    body = client.get("/search", params={"q": "zzzz qqqq wwww"}).json()
    assert body["results"] == []
