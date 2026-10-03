"""GET /health, GET /graph, CORS."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.tests.conftest import write_minimal_graph


def test_health_on_fixture_is_mock_mode(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mode"] == "mock"
    assert body["graph_path"].endswith("api/fixtures/graph.mock.json")
    assert body["graph_built_at"] == "2026-10-03T00:00:00Z"
    assert body["nodes"] == 21
    assert body["edges"] == 18


def test_health_real_mode_when_graph_json_resolved(tmp_path, monkeypatch):
    from api.main import create_app

    monkeypatch.delenv("ATLAS_GRAPH_PATH", raising=False)
    monkeypatch.delenv("ATLAS_EXPLANATIONS_PATH", raising=False)
    write_minimal_graph(tmp_path / "data" / "build" / "graph.json", built_at="later")
    app = create_app(repo_root=tmp_path)
    with TestClient(app) as c:
        body = c.get("/health").json()
    assert body["mode"] == "real"
    assert body["graph_path"].endswith("data/build/graph.json")
    assert body["graph_built_at"] == "later"
    assert body["nodes"] == 0


def test_health_env_override(tmp_path, monkeypatch):
    from api.main import create_app

    monkeypatch.delenv("ATLAS_EXPLANATIONS_PATH", raising=False)
    override = write_minimal_graph(tmp_path / "anywhere" / "graph.custom.json")
    monkeypatch.setenv("ATLAS_GRAPH_PATH", str(override))
    app = create_app(repo_root=tmp_path)
    with TestClient(app) as c:
        body = c.get("/health").json()
    assert body["graph_path"] == str(override)
    assert body["mode"] == "real"


def test_graph_returns_loaded_graph_verbatim(client, fixture_graph):
    resp = client.get("/graph")
    assert resp.status_code == 200
    assert resp.json() == fixture_graph


def test_cors_header_present(client):
    resp = client.get("/health", headers={"Origin": "http://example.com"})
    assert resp.headers.get("access-control-allow-origin") == "*"
