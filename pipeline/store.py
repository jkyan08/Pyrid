"""SQLite evidence store: what the source loaders write and ``pipeline.graph.build`` reads.

One file at :data:`pipeline.config.STORE_PATH` (``data/build/evidence.sqlite``, gitignored),
stdlib ``sqlite3``, journal mode DELETE (no WAL), one connection per :class:`Store`. Every write
runs as ``executemany`` inside a transaction, and every upsert is ``INSERT OR REPLACE``, so the
loaders are idempotent: running ``pipeline.export`` twice gives the same store.

Tables (JSON columns are TEXT holding ``json.dumps``; the store encodes and decodes them)::

    nodes(id PK, type, label, synonyms JSON, props JSON, source, retrieved_at)
    edges(source, target, relation, source_db, props JSON, retrieved_at;
          PK (source, target, relation, source_db))
    coverage(entity_id, source, query, date, hits)      unique on (entity_id, source, query, date)
    sources(name PK, version, retrieved_at, license, url, notes JSON)
    hpo_ic(term PK, ic, n_diseases, n_total)

Conventions (what loaders put in, what the build relies on)
-----------------------------------------------------------
* ``nodes.id`` and ``edges.source`` / ``edges.target`` are ALREADY normalised with
  :func:`pipeline.ids.normalize_curie` (``MONDO:…``, ``HP:…``, ``ensembl:ENSG…``, ``REACT:…``,
  ``CHEMBL:…``, ``mech:…``). The store never normalises.
* ``nodes.type`` is a :class:`pipeline.models.NodeType` value (``"Gene"``, ``"Disease"``, ...).
* ``nodes.props`` keeps the source record's properties dict verbatim plus ``backbone_id`` (the raw
  id exactly as the source spelled it) and ``source_type`` (the source's own type code, e.g.
  ``"GEN"``, ``"DIS"``). Gene nodes: ``label`` = HGNC symbol, ``props.symbol`` = symbol,
  ``synonyms`` = symbol synonyms + obsolete symbols. Disease / Phenotype nodes: ``label`` = name,
  ``synonyms`` = exact + related + narrow + broad synonyms (deduplicated, order kept). Pathway
  nodes carry ``props.ontology`` = ``"Reactome"`` or ``"GO"``. Mechanism nodes carry
  ``props.gene_symbol``, ``props.direction`` and ``props.origin``.
* ``edges.relation`` is the README section 4 vocabulary: ``associated_with`` (Disease -> Gene),
  ``has_phenotype`` (Disease -> Phenotype), ``is_a`` (CHILD -> PARENT, for Phenotype -> Phenotype
  and Disease -> Disease), ``in_pathway`` (Gene -> Pathway, Reactome and GO alike), ``inhibits``
  or ``targets`` (Drug -> Gene), ``indicated_for`` (Drug -> Disease; ``treats`` is reserved for
  literature-backed edges), ``has_mechanism`` (Disease / Variant -> Mechanism), ``variant_of``
  (Variant -> Gene).
* ``edges.props`` keeps the source edge's properties verbatim (``evidence_score``,
  ``evidence_count``, ``number_of_pmids``, ``year_initial`` / ``year_final``, ``frequency``,
  ``onset``, ``evidence_type``, ``references``, ``evidence`` (GO codes), ``eco_ids``, ``sources``,
  ``relation_assertions``, ``relation_conflict``, ``mechanisms_of_action``, ``source_ids``, ...)
  plus ``backbone_relation`` (the source's own relation label, e.g. ``"PARENT"``, ``"INHIBITOR"``).
* ``edges.source_db`` names the loader (``"optimuskg"``, ``"hpo"``, ``"clinvar"``, ...); the same
  (source, target, relation) asserted by two loaders is two rows.
* ``coverage`` is README hard rule 3: every lookup, including empty ones. ``entity_id`` is a node
  id or a seed term key such as ``"seed:gene:STAT1"`` / ``"seed:disease:<term>"``. Re-running the
  same query on the same date replaces the earlier row (latest ``hits`` wins).
* ``retrieved_at`` and ``date`` are ISO 8601 strings (``CachedResponse.retrieved_at`` or
  ``.retrieved_date``), never parsed here but required to be non-empty.

Readers return plain dicts with the JSON columns decoded, in a deterministic order, so the build
is reproducible from the same store.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pipeline.config import STORE_PATH
from pipeline.models import NodeType

__all__ = ["EDGE_COLUMNS", "NODE_COLUMNS", "Store"]

NODE_COLUMNS = ("id", "type", "label", "synonyms", "props", "source", "retrieved_at")
EDGE_COLUMNS = ("source", "target", "relation", "source_db", "props", "retrieved_at")
_NODE_TYPES = frozenset(t.value for t in NodeType)
MEMORY = ":memory:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    label        TEXT NOT NULL,
    synonyms     TEXT NOT NULL DEFAULT '[]',
    props        TEXT NOT NULL DEFAULT '{}',
    source       TEXT NOT NULL,
    retrieved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS nodes_type ON nodes(type);

CREATE TABLE IF NOT EXISTS edges (
    source       TEXT NOT NULL,
    target       TEXT NOT NULL,
    relation     TEXT NOT NULL,
    source_db    TEXT NOT NULL,
    props        TEXT NOT NULL DEFAULT '{}',
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (source, target, relation, source_db)
);
CREATE INDEX IF NOT EXISTS edges_target ON edges(target);
CREATE INDEX IF NOT EXISTS edges_relation ON edges(relation);

CREATE TABLE IF NOT EXISTS coverage (
    entity_id TEXT NOT NULL,
    source    TEXT NOT NULL,
    query     TEXT NOT NULL,
    date      TEXT NOT NULL,
    hits      INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS coverage_lookup ON coverage(entity_id, source, query, date);

CREATE TABLE IF NOT EXISTS sources (
    name         TEXT PRIMARY KEY,
    version      TEXT NOT NULL DEFAULT '',
    retrieved_at TEXT NOT NULL,
    license      TEXT NOT NULL,
    url          TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS hpo_ic (
    term       TEXT PRIMARY KEY,
    ic         REAL NOT NULL,
    n_diseases INTEGER NOT NULL,
    n_total    INTEGER NOT NULL
);
"""


# --------------------------------------------------------------------------------------
# JSON columns and row validation
# --------------------------------------------------------------------------------------


def _json_default(obj: Any) -> Any:
    """The few non-JSON types a Parquet or API record can carry into props."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "tolist"):  # numpy arrays
        return obj.tolist()
    if hasattr(obj, "item"):  # numpy scalars
        return obj.item()
    raise TypeError(f"{type(obj).__name__} is not JSON serialisable; store props must be plain data")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=_json_default)


def _text(row: Mapping[str, Any], key: str, what: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what}: {key!r} must be a non-empty string, got {value!r}")
    return value


def _props(row: Mapping[str, Any], what: str) -> str:
    props = row.get("props", {})
    if props is None:
        props = {}
    if not isinstance(props, Mapping):
        raise ValueError(f"{what}: 'props' must be a mapping, got {type(props).__name__}")
    return _dumps(dict(props))


def _node_params(row: Mapping[str, Any]) -> tuple[Any, ...]:
    what = f"node {row.get('id')!r}"
    node_id = _text(row, "id", what)
    node_type = str(row.get("type") or "")
    if node_type not in _NODE_TYPES:
        raise ValueError(f"{what}: 'type' must be a NodeType value, got {row.get('type')!r}")
    synonyms = row.get("synonyms", [])
    if synonyms is None:
        synonyms = []
    if isinstance(synonyms, str) or not isinstance(synonyms, Sequence) or not all(isinstance(s, str) for s in synonyms):
        raise ValueError(f"{what}: 'synonyms' must be a list of strings, got {synonyms!r}")
    return (
        node_id,
        node_type,
        _text(row, "label", what),
        _dumps(list(synonyms)),
        _props(row, what),
        _text(row, "source", what),
        _text(row, "retrieved_at", what),
    )


def _edge_params(row: Mapping[str, Any]) -> tuple[Any, ...]:
    what = f"edge {row.get('source')!r} -> {row.get('target')!r}"
    source = _text(row, "source", what)
    target = _text(row, "target", what)
    if source == target:
        raise ValueError(f"{what}: self-loops cannot be exported (pipeline.models.Edge forbids them)")
    return (
        source,
        target,
        _text(row, "relation", what),
        _text(row, "source_db", what),
        _props(row, what),
        _text(row, "retrieved_at", what),
    )


def _decode_node(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["synonyms"] = json.loads(out["synonyms"])
    out["props"] = json.loads(out["props"])
    return out


def _decode_edge(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["props"] = json.loads(out["props"])
    return out


# --------------------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------------------


class Store:
    """The evidence store (see the module docstring for tables and conventions).

    ``path`` defaults to :data:`pipeline.config.STORE_PATH`; ``":memory:"`` gives a throwaway
    in-memory store. The schema is created on open (``CREATE ... IF NOT EXISTS``), so opening an
    existing store is harmless. Use as a context manager or call :meth:`close`.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        if path is None:
            self.path: Path | str = STORE_PATH
        elif str(path) == MEMORY:
            self.path = MEMORY
        else:
            self.path = Path(path)
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.ensure_schema()

    def __repr__(self) -> str:
        return f"Store({str(self.path)!r})"

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def ensure_schema(self) -> None:
        """Create any missing table or index; safe to call on an existing store."""
        with self.conn:
            self.conn.executescript(_SCHEMA)

    # -- writes -----------------------------------------------------------------------

    def upsert_nodes(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Insert or replace node rows (keys: ``NODE_COLUMNS``; ``synonyms`` and ``props`` are
        Python objects). Every row is validated before anything is written. Returns the count."""
        params = [_node_params(r) for r in rows]
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO nodes(id, type, label, synonyms, props, source, retrieved_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                params,
            )
        return len(params)

    def upsert_edges(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Insert or replace edge rows (keys: ``EDGE_COLUMNS``; ``props`` is a dict). Rows with the
        same (source, target, relation, source_db) replace each other. Returns the count."""
        params = [_edge_params(r) for r in rows]
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO edges(source, target, relation, source_db, props, retrieved_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                params,
            )
        return len(params)

    def add_coverage(self, entity_id: str, source: str, query: str, date: str, hits: int) -> None:
        """Record one lookup for ``entity_id`` (zero hits included; README hard rule 3)."""
        row = {"entity_id": entity_id, "source": source, "query": query, "date": date}
        what = f"coverage for {entity_id!r}"
        values = tuple(_text(row, k, what) for k in ("entity_id", "source", "query", "date"))
        if isinstance(hits, bool) or not isinstance(hits, int) or hits < 0:
            raise ValueError(f"{what}: hits must be an int >= 0, got {hits!r}")
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO coverage(entity_id, source, query, date, hits) VALUES (?, ?, ?, ?, ?)",
                (*values, hits),
            )

    def upsert_source(
        self,
        name: str,
        *,
        retrieved_at: str,
        license: str,
        version: str = "",
        url: str = "",
        notes: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a data source for ``meta.sources`` (README section 5 license table)."""
        row = {"name": name, "retrieved_at": retrieved_at, "license": license}
        what = f"source {name!r}"
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO sources(name, version, retrieved_at, license, url, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _text(row, "name", what),
                    version or "",
                    _text(row, "retrieved_at", what),
                    _text(row, "license", what),
                    url or "",
                    _dumps(dict(notes or {})),
                ),
            )

    def set_hpo_ic(self, mapping: Mapping[str, Any]) -> int:
        """Store information content per phenotype term. Values are ``{"ic", "n_diseases",
        "n_total"}`` dicts or ``(ic, n_diseases, n_total)`` sequences. Returns the count."""
        params: list[tuple[str, float, int, int]] = []
        for term, value in mapping.items():
            if not isinstance(term, str) or not term.strip():
                raise ValueError(f"hpo_ic: term must be a non-empty string, got {term!r}")
            if isinstance(value, Mapping):
                ic, n_diseases, n_total = value["ic"], value["n_diseases"], value["n_total"]
            else:
                ic, n_diseases, n_total = value
            params.append((term, float(ic), int(n_diseases), int(n_total)))
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO hpo_ic(term, ic, n_diseases, n_total) VALUES (?, ?, ?, ?)", params
            )
        return len(params)

    # -- reads ------------------------------------------------------------------------

    def iter_nodes(self, type: NodeType | str | None = None) -> Iterator[dict[str, Any]]:
        """Node rows (JSON decoded), ordered by (type, id); optionally one node type."""
        if type is None:
            rows = self.conn.execute("SELECT * FROM nodes ORDER BY type, id").fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM nodes WHERE type = ? ORDER BY id", (str(type),)).fetchall()
        for row in rows:
            yield _decode_node(row)

    def iter_edges(self, relation: str | None = None, source_db: str | None = None) -> Iterator[dict[str, Any]]:
        """Edge rows (JSON decoded), ordered by (relation, source, target, source_db)."""
        clauses: list[str] = []
        params: list[str] = []
        if relation is not None:
            clauses.append("relation = ?")
            params.append(relation)
        if source_db is not None:
            clauses.append("source_db = ?")
            params.append(source_db)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM edges{where} ORDER BY relation, source, target, source_db", params
        ).fetchall()
        for row in rows:
            yield _decode_edge(row)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return None if row is None else _decode_node(row)

    def nodes_by_type(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT type, COUNT(*) AS n FROM nodes GROUP BY type ORDER BY type").fetchall()
        return {r["type"]: r["n"] for r in rows}

    def edges_by_relation(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT relation, COUNT(*) AS n FROM edges GROUP BY relation ORDER BY relation"
        ).fetchall()
        return {r["relation"]: r["n"] for r in rows}

    def coverage_rows(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM coverage ORDER BY entity_id, source, query, date").fetchall()
        return [dict(r) for r in rows]

    def sources_rows(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM sources ORDER BY name").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["notes"] = json.loads(d["notes"])
            out.append(d)
        return out

    def hpo_ic(self) -> dict[str, dict[str, float | int]]:
        """``{term: {"ic", "n_diseases", "n_total"}}``."""
        rows = self.conn.execute("SELECT * FROM hpo_ic ORDER BY term").fetchall()
        return {r["term"]: {"ic": r["ic"], "n_diseases": r["n_diseases"], "n_total": r["n_total"]} for r in rows}

    def counts(self) -> dict[str, Any]:
        """Row counts per table plus the per-type and per-relation breakdowns (for build_report)."""
        def count(table: str) -> int:
            return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        return {
            "nodes": count("nodes"),
            "edges": count("edges"),
            "coverage": count("coverage"),
            "sources": count("sources"),
            "hpo_ic": count("hpo_ic"),
            "nodes_by_type": self.nodes_by_type(),
            "edges_by_relation": self.edges_by_relation(),
        }
