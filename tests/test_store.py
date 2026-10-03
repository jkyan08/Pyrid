"""pipeline/store.py (SQLite evidence store), pipeline/config.py and the seed files.

Fully offline, against a store in ``tmp_path``. Ids are ``mock:`` placeholders and labels start
with "Mock " so nothing here can be mistaken for a resolved record (pipeline/models.py).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from pipeline import config
from pipeline.ids import MECHANISM_DIRECTIONS
from pipeline.store import EDGE_COLUMNS, NODE_COLUMNS, Store

MOCK_TS = "2026-10-03T00:00:00+00:00"
MOCK_DATE = "2026-10-03"


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "evidence.sqlite") as s:
        yield s


def node(node_id: str, node_type: str = "Gene", label: str | None = None, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": node_id,
        "type": node_type,
        "label": label or f"Mock {node_id}",
        "synonyms": [],
        "props": {},
        "source": "mock",
        "retrieved_at": MOCK_TS,
    }
    row.update(overrides)
    return row


def edge(source: str, target: str, relation: str = "associated_with", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source": source,
        "target": target,
        "relation": relation,
        "source_db": "mock",
        "props": {},
        "retrieved_at": MOCK_TS,
    }
    row.update(overrides)
    return row


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


# --------------------------------------------------------------------------------------
# schema and lifecycle
# --------------------------------------------------------------------------------------


def test_schema_is_created_on_open_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "dir" / "evidence.sqlite"  # parents are created
    with Store(path) as s:
        assert table_names(s.conn) == {"nodes", "edges", "coverage", "sources", "hpo_ic"}
        s.ensure_schema()
        s.ensure_schema()
        assert s.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"  # WAL off
        assert repr(s) == f"Store({str(path)!r})"
    assert path.is_file()


def test_data_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "evidence.sqlite"
    with Store(path) as s:
        s.upsert_nodes([node("mock:gene:1", synonyms=["Mock synonym"], props={"symbol": "MOCK1"})])
    with Store(path) as s:
        got = s.get_node("mock:gene:1")
        assert got is not None
        assert got["synonyms"] == ["Mock synonym"]
        assert got["props"] == {"symbol": "MOCK1"}


def test_memory_store() -> None:
    with Store(":memory:") as s:
        assert s.path == ":memory:"
        assert s.upsert_nodes([node("mock:gene:1")]) == 1
        assert s.counts()["nodes"] == 1


def test_default_path_is_the_configured_store_path() -> None:
    assert config.STORE_PATH == config.BUILD_DIR / "evidence.sqlite"
    assert config.BUILD_DIR == config.REPO_ROOT / "data" / "build"
    # Not opened here: Store() with no argument would create the real build file.


def test_close_releases_the_connection(tmp_path: Path) -> None:
    s = Store(tmp_path / "evidence.sqlite")
    s.close()
    with pytest.raises(sqlite3.ProgrammingError):
        s.conn.execute("SELECT 1")


# --------------------------------------------------------------------------------------
# nodes
# --------------------------------------------------------------------------------------


def test_node_columns_constant() -> None:
    assert NODE_COLUMNS == ("id", "type", "label", "synonyms", "props", "source", "retrieved_at")
    assert EDGE_COLUMNS == ("source", "target", "relation", "source_db", "props", "retrieved_at")


def test_upsert_nodes_round_trips_json_columns(store: Store) -> None:
    props = {
        "backbone_id": "MOCK_0",
        "source_type": "GEN",
        "symbol": "MOCK1",
        "xrefs": [{"id": "0", "source": "mock"}],
        "nested": {"a": [1, 2.5, None, True], "ü": "ünïcode"},
    }
    n = store.upsert_nodes([node("mock:gene:1", synonyms=["Mock A", "Mock B"], props=props)])
    assert n == 1
    got = store.get_node("mock:gene:1")
    assert got is not None
    assert got["id"] == "mock:gene:1"
    assert got["type"] == "Gene"
    assert got["label"] == "Mock mock:gene:1"
    assert got["synonyms"] == ["Mock A", "Mock B"]
    assert got["props"] == props
    assert got["source"] == "mock"
    assert got["retrieved_at"] == MOCK_TS
    # Stored as JSON text, decoded by the reader.
    raw = store.conn.execute("SELECT synonyms, props FROM nodes").fetchone()
    assert json.loads(raw["synonyms"]) == ["Mock A", "Mock B"]
    assert json.loads(raw["props"]) == props


def test_upsert_nodes_defaults_and_replace(store: Store) -> None:
    minimal = {"id": "mock:disease:1", "type": "Disease", "label": "Mock disease", "source": "mock", "retrieved_at": MOCK_TS}
    store.upsert_nodes([minimal])
    got = store.get_node("mock:disease:1")
    assert got is not None
    assert got["synonyms"] == [] and got["props"] == {}

    store.upsert_nodes([node("mock:disease:1", "Disease", "Mock disease renamed", props={"v": 2}, synonyms=None)])
    got = store.get_node("mock:disease:1")
    assert got is not None
    assert got["label"] == "Mock disease renamed"
    assert got["props"] == {"v": 2}
    assert got["synonyms"] == []
    assert store.counts()["nodes"] == 1


def test_upsert_nodes_accepts_the_node_type_enum_and_props_from_odd_types(store: Store) -> None:
    from datetime import date, datetime, timezone

    from pipeline.models import NodeType

    store.upsert_nodes(
        [
            node(
                "mock:phenotype:1",
                NodeType.Phenotype,
                props={"when": datetime(2026, 10, 3, tzinfo=timezone.utc), "day": date(2026, 10, 3), "s": {"b", "a"}, "t": (1, 2)},
                synonyms=("Mock tuple synonym",),
            )
        ]
    )
    got = store.get_node("mock:phenotype:1")
    assert got is not None
    assert got["type"] == "Phenotype"
    assert got["props"] == {"when": "2026-10-03T00:00:00+00:00", "day": "2026-10-03", "s": ["a", "b"], "t": [1, 2]}
    assert got["synonyms"] == ["Mock tuple synonym"]


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "Gene", "label": "Mock", "source": "mock", "retrieved_at": MOCK_TS},  # no id
        node("", "Gene"),
        node("mock:gene:1", "Protein"),  # not a NodeType
        node("mock:gene:1", "gene"),  # case matters
        node("mock:gene:1", "Gene", label="   "),
        node("mock:gene:1", "Gene", source=""),
        node("mock:gene:1", "Gene", retrieved_at=None),
        node("mock:gene:1", "Gene", synonyms="Mock not a list"),
        node("mock:gene:1", "Gene", synonyms=["ok", 3]),
        node("mock:gene:1", "Gene", props=["not", "a", "mapping"]),
    ],
)
def test_upsert_nodes_rejects_bad_rows_before_writing(store: Store, bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        store.upsert_nodes([node("mock:gene:0"), bad])
    # Validation happens for the whole batch first, so the good row was not written either.
    assert store.counts()["nodes"] == 0


def test_iter_nodes_filters_by_type_and_is_ordered(store: Store) -> None:
    store.upsert_nodes(
        [
            node("mock:gene:2", "Gene"),
            node("mock:disease:1", "Disease"),
            node("mock:gene:1", "Gene"),
            node("mock:phenotype:1", "Phenotype"),
        ]
    )
    assert [n["id"] for n in store.iter_nodes()] == ["mock:disease:1", "mock:gene:1", "mock:gene:2", "mock:phenotype:1"]
    assert [n["id"] for n in store.iter_nodes(type="Gene")] == ["mock:gene:1", "mock:gene:2"]
    assert list(store.iter_nodes(type="Drug")) == []
    assert store.nodes_by_type() == {"Disease": 1, "Gene": 2, "Phenotype": 1}
    assert store.get_node("mock:missing") is None


# --------------------------------------------------------------------------------------
# edges
# --------------------------------------------------------------------------------------


def test_upsert_edges_primary_key_and_props(store: Store) -> None:
    props = {"evidence_score": 0.5, "sources": {"direct": ["MOCK"]}, "backbone_relation": "MOCK_REL", "relation_conflict": False}
    assert store.upsert_edges([edge("mock:disease:1", "mock:gene:1", props=props)]) == 1
    # Same (source, target, relation, source_db) replaces ...
    store.upsert_edges([edge("mock:disease:1", "mock:gene:1", props={"evidence_score": 0.9})])
    # ... a different source_db is a second row, as is a different relation.
    store.upsert_edges([edge("mock:disease:1", "mock:gene:1", source_db="other"), edge("mock:disease:1", "mock:gene:1", relation="has_mechanism")])
    rows = list(store.iter_edges())
    assert len(rows) == 3
    assert [(r["relation"], r["source_db"]) for r in rows] == [
        ("associated_with", "mock"),
        ("associated_with", "other"),
        ("has_mechanism", "mock"),
    ]
    assert rows[0]["props"] == {"evidence_score": 0.9}
    assert rows[0]["retrieved_at"] == MOCK_TS
    assert store.edges_by_relation() == {"associated_with": 2, "has_mechanism": 1}


def test_iter_edges_filters_and_order(store: Store) -> None:
    store.upsert_edges(
        [
            edge("mock:disease:2", "mock:phenotype:1", "has_phenotype", source_db="hpo"),
            edge("mock:disease:1", "mock:phenotype:1", "has_phenotype", source_db="optimuskg"),
            edge("mock:disease:1", "mock:phenotype:1", "has_phenotype", source_db="hpo"),
            edge("mock:disease:1", "mock:gene:1", "associated_with", source_db="optimuskg"),
        ]
    )
    assert [(e["source"], e["source_db"]) for e in store.iter_edges(relation="has_phenotype")] == [
        ("mock:disease:1", "hpo"),
        ("mock:disease:1", "optimuskg"),
        ("mock:disease:2", "hpo"),
    ]
    assert [e["relation"] for e in store.iter_edges(source_db="optimuskg")] == ["associated_with", "has_phenotype"]
    assert len(list(store.iter_edges(relation="has_phenotype", source_db="hpo"))) == 2
    assert list(store.iter_edges(relation="treats")) == []


@pytest.mark.parametrize(
    "bad",
    [
        edge("mock:a", "mock:a"),  # self-loop
        edge("", "mock:b"),
        edge("mock:a", "mock:b", relation=""),
        edge("mock:a", "mock:b", source_db=None),
        edge("mock:a", "mock:b", retrieved_at=" "),
        edge("mock:a", "mock:b", props="not a mapping"),
    ],
)
def test_upsert_edges_rejects_bad_rows_before_writing(store: Store, bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        store.upsert_edges([edge("mock:x", "mock:y"), bad])
    assert store.counts()["edges"] == 0


# --------------------------------------------------------------------------------------
# coverage, sources, hpo_ic
# --------------------------------------------------------------------------------------


def test_coverage_records_zero_hits_and_collapses_reruns(store: Store) -> None:
    store.add_coverage("seed:gene:MOCK1", "mock-db", "symbol = MOCK1", MOCK_DATE, 0)
    store.add_coverage("mock:disease:1", "mock-db", "phenotypes", MOCK_DATE, 7)
    # Same lookup again on the same date: one row, latest hits.
    store.add_coverage("seed:gene:MOCK1", "mock-db", "symbol = MOCK1", MOCK_DATE, 1)
    # A different query, or a different date, is another row.
    store.add_coverage("seed:gene:MOCK1", "mock-db", "synonym = MOCK1", MOCK_DATE, 0)
    store.add_coverage("seed:gene:MOCK1", "mock-db", "symbol = MOCK1", "2026-10-04", 0)
    rows = store.coverage_rows()
    assert rows == [
        {"entity_id": "mock:disease:1", "source": "mock-db", "query": "phenotypes", "date": MOCK_DATE, "hits": 7},
        {"entity_id": "seed:gene:MOCK1", "source": "mock-db", "query": "symbol = MOCK1", "date": MOCK_DATE, "hits": 1},
        {"entity_id": "seed:gene:MOCK1", "source": "mock-db", "query": "symbol = MOCK1", "date": "2026-10-04", "hits": 0},
        {"entity_id": "seed:gene:MOCK1", "source": "mock-db", "query": "synonym = MOCK1", "date": MOCK_DATE, "hits": 0},
    ]
    assert store.counts()["coverage"] == 4


@pytest.mark.parametrize("hits", [-1, 1.5, "3", True, None])
def test_coverage_rejects_bad_hits(store: Store, hits: Any) -> None:
    with pytest.raises(ValueError):
        store.add_coverage("mock:x", "mock-db", "q", MOCK_DATE, hits)


def test_coverage_rejects_blank_fields(store: Store) -> None:
    with pytest.raises(ValueError):
        store.add_coverage("", "mock-db", "q", MOCK_DATE, 0)
    with pytest.raises(ValueError):
        store.add_coverage("mock:x", "mock-db", "q", "", 0)
    with pytest.raises(ValueError):
        store.add_coverage("mock:x", "mock-db", "   ", MOCK_DATE, 0)


def test_sources_table(store: Store) -> None:
    store.upsert_source("mock", retrieved_at=MOCK_DATE, license="mock")
    store.upsert_source(
        "mock-versioned",
        version="3.2",
        retrieved_at=MOCK_TS,
        license="CC BY-NC-SA 4.0",
        url="https://mock.example/dataset",
        notes={"files": ["mock.parquet"], "md5_checked": True},
    )
    assert store.sources_rows() == [
        {"name": "mock", "version": "", "retrieved_at": MOCK_DATE, "license": "mock", "url": "", "notes": {}},
        {
            "name": "mock-versioned",
            "version": "3.2",
            "retrieved_at": MOCK_TS,
            "license": "CC BY-NC-SA 4.0",
            "url": "https://mock.example/dataset",
            "notes": {"files": ["mock.parquet"], "md5_checked": True},
        },
    ]
    store.upsert_source("mock", retrieved_at="2026-10-04", license="mock2")
    assert store.sources_rows()[0]["retrieved_at"] == "2026-10-04"
    assert store.counts()["sources"] == 2
    with pytest.raises(ValueError):
        store.upsert_source("mock-bad", retrieved_at="", license="mock")
    with pytest.raises(ValueError):
        store.upsert_source("mock-bad", retrieved_at=MOCK_DATE, license="")


def test_hpo_ic_accepts_dicts_and_tuples(store: Store) -> None:
    n = store.set_hpo_ic({"mock:phenotype:1": {"ic": 2.5, "n_diseases": 3, "n_total": 100}, "mock:phenotype:2": (0.0, 100, 100)})
    assert n == 2
    assert store.hpo_ic() == {
        "mock:phenotype:1": {"ic": 2.5, "n_diseases": 3, "n_total": 100},
        "mock:phenotype:2": {"ic": 0.0, "n_diseases": 100, "n_total": 100},
    }
    store.set_hpo_ic({"mock:phenotype:1": (3.0, 2, 100)})
    assert store.hpo_ic()["mock:phenotype:1"] == {"ic": 3.0, "n_diseases": 2, "n_total": 100}
    assert store.counts()["hpo_ic"] == 2
    with pytest.raises(ValueError):
        store.set_hpo_ic({"": (1.0, 1, 1)})
    with pytest.raises(KeyError):
        store.set_hpo_ic({"mock:phenotype:3": {"ic": 1.0}})


def test_counts_shape(store: Store) -> None:
    assert store.counts() == {
        "nodes": 0,
        "edges": 0,
        "coverage": 0,
        "sources": 0,
        "hpo_ic": 0,
        "nodes_by_type": {},
        "edges_by_relation": {},
    }
    store.upsert_nodes([node("mock:gene:1"), node("mock:disease:1", "Disease")])
    store.upsert_edges([edge("mock:disease:1", "mock:gene:1")])
    c = store.counts()
    assert (c["nodes"], c["edges"]) == (2, 1)
    assert c["nodes_by_type"] == {"Disease": 1, "Gene": 1}
    assert c["edges_by_relation"] == {"associated_with": 1}


# --------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------


def test_config_paths_are_absolute_and_resolvers_honour_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for p in (config.REPO_ROOT, config.RAW_DIR, config.CACHE_DIR, config.BUILD_DIR, config.GRAPH_PATH, config.STORE_PATH, config.SEEDS_DIR):
        assert p.is_absolute()
    assert config.SEEDS_DIR == config.REPO_ROOT / "pipeline" / "seeds"
    assert (config.REPO_ROOT / "pipeline" / "models.py").is_file()

    monkeypatch.delenv(config.ENV_RAW_DIR, raising=False)
    monkeypatch.delenv(config.ENV_GRAPH_PATH, raising=False)
    assert config.resolve_raw_dir() == config.REPO_ROOT / "data" / "raw"
    assert config.resolve_graph_path() == config.REPO_ROOT / "data" / "build" / "graph.json"
    monkeypatch.setenv(config.ENV_RAW_DIR, "relative/raw")
    monkeypatch.setenv(config.ENV_GRAPH_PATH, str(tmp_path / "g.json"))
    assert config.resolve_raw_dir() == config.REPO_ROOT / "relative" / "raw"
    assert config.resolve_graph_path() == tmp_path / "g.json"
    assert config.resolve_raw_dir(tmp_path) == tmp_path


def test_user_agent_is_not_a_default_python_agent() -> None:
    assert config.USER_AGENT.startswith("pyrid-atlas/")
    assert "python" not in config.USER_AGENT.lower()


# --------------------------------------------------------------------------------------
# seeds: names only, no hand-typed identifiers
# --------------------------------------------------------------------------------------

# Tripwire for a hand-typed external identifier in a seed file (README hard rule 2). Formats, not
# values: ontology / database prefixes followed by a run of digits, PMIDs, NCT numbers, Ensembl ids.
_ID_LIKE = re.compile(
    r"\b(?:MONDO|HP|EFO|Orphanet|ORPHA|OMIM|DOID|GO|CHEMBL|HGNC|MedGen|UMLS|PMC|PMID|NCBIGene)[:_ ]?\d{3,}\b"
    r"|\bNCT\d{6,}\b|\bENSG\d{6,}\b|\bVCV\d{6,}\b|\brs\d{4,}\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("name", ["cluster", "mechanisms"])
def test_seed_files_contain_no_identifiers(name: str) -> None:
    text = (config.SEEDS_DIR / f"{name}.yaml").read_text(encoding="utf-8")
    assert _ID_LIKE.search(text) is None, _ID_LIKE.search(text)


def test_load_seeds_accepts_suffix_and_reports_missing_files() -> None:
    assert config.load_seeds("cluster") == config.load_seeds("cluster.yaml")
    with pytest.raises(FileNotFoundError):
        config.load_seeds("mock-no-such-seed")


def test_cluster_seeds_shape() -> None:
    seeds = config.load_seeds("cluster")
    assert seeds["genes"] == ["STAT1", "STAT3", "JAK1", "JAK2", "JAK3", "TYK2", "IL2RG"]
    for key in ("disease_terms", "drug_terms"):
        terms = seeds[key]
        assert terms and all(isinstance(t, str) and t.strip() for t in terms), key
        assert len(set(t.lower() for t in terms)) == len(terms), f"duplicate {key}"
    assert set(seeds["drug_terms"]) >= {"ruxolitinib", "baricitinib", "tofacitinib", "upadacitinib", "itacitinib"}
    assert seeds["thresholds"] == {
        "disease_gene_min_score": 0.3,
        "keep_disgenet_direct": True,
        "max_pathway_members_to_expand": 0,
        "include_parent_levels": 1,
        # OptimusKG loader density safeguards, spelled out so build_report.json shows them
        "disease_gene_most_specific": True,
        "max_other_genes_per_disease": 25,
        "max_drugs_per_disease": 25,
    }
    family = re.compile(seeds["pathway_family_label_regex"], re.IGNORECASE)
    assert family.search("Interleukin-6 signaling")
    assert family.search("Mock JAK-STAT signaling")
    assert family.search("Mock unrelated pathway") is None


def test_mechanism_seeds_shape() -> None:
    genes = set(config.load_seeds("cluster")["genes"])
    seeds = config.load_seeds("mechanisms")
    rows = seeds["mechanisms"]
    assert isinstance(seeds["source"], str) and "test-cluster-desc" in seeds["source"]
    assert rows
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        assert set(row) == {"gene", "direction", "origin", "disease_terms", "cluster_doc_confidence", "note"}, row
        assert row["gene"] in genes, row
        assert row["direction"] in MECHANISM_DIRECTIONS, row
        assert row["origin"] in {"germline", "somatic"}, row
        assert row["cluster_doc_confidence"] in {"Retrieved", "Background"}, row
        # a term is a bare name (table wording) or {term, from} when the wording comes from elsewhere
        assert row["disease_terms"], row
        for t in row["disease_terms"]:
            if isinstance(t, dict):
                assert set(t) == {"term", "from"} and all(isinstance(v, str) and v.strip() for v in t.values()), row
            else:
                assert isinstance(t, str) and t.strip(), row
        assert isinstance(row["note"], str) and row["note"].strip(), row
        key = (row["gene"], row["direction"], row["origin"])
        assert key not in seen, f"duplicate mechanism row {key}"
        seen.add(key)
    # Every direction in the vocabulary is exercised by the cluster doc's table.
    assert {r["direction"] for r in rows} == set(MECHANISM_DIRECTIONS)
    assert {r["origin"] for r in rows} == {"germline", "somatic"}
