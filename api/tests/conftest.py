"""Shared fixtures for the API tests.

All tests run against the committed dev fixture (api/fixtures/*.mock.json)
plus throwaway graphs generated inside tmp dirs. Nothing here ever writes
into data/ (M0-owned).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_GRAPH = REPO_ROOT / "api" / "fixtures" / "graph.mock.json"
FIXTURE_EXPLANATIONS = REPO_ROOT / "api" / "fixtures" / "explanations.mock.json"


def write_minimal_graph(path: Path, built_at: str = "2026-10-03T12:00:00Z") -> Path:
    """Write a tiny contract-shaped graph into a tmp location (never data/)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "meta": {"built_at": built_at, "sources": [], "coverage": {}},
                "nodes": [],
                "edges": [],
                "claims": [],
                "clusters": [],
            }
        )
    )
    return path


@pytest.fixture()
def fixture_graph() -> dict:
    return json.loads(FIXTURE_GRAPH.read_text())


@pytest.fixture()
def fixture_explanations() -> dict:
    return json.loads(FIXTURE_EXPLANATIONS.read_text())


@pytest.fixture(scope="session")
def client():
    """TestClient pinned explicitly to the dev fixture (deterministic)."""
    from fastapi.testclient import TestClient

    from api.main import create_app

    app = create_app(
        graph_path=FIXTURE_GRAPH, explanations_path=FIXTURE_EXPLANATIONS
    )
    with TestClient(app) as c:
        yield c
