"""The gap probe (ground-truth test 6): ``pipeline.sources.optimuskg.select_gap_probe``,
``pipeline.export.run_gap_probe`` / ``gap_probe_from_store`` and the ``prune_graph`` exemption in
``pipeline.graph.build``.

Fully offline. The backbone is the tiny synthetic Parquet world of ``tests/test_optimuskg.py``
(real column names and nested types; ``ENSG99…`` / ``MOCK…`` placeholders, nothing resolvable)
extended with a few more edgeless genes, and every HTTP call goes through ``httpx.MockTransport``
over a :class:`pipeline.cache.CachedHttp` in ``tmp_path`` so the cache replay is exercised too.
No gene symbol here is a real HGNC symbol (README hard rule 2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from pipeline import export
from pipeline.cache import CachedHttp, DiskCache
from pipeline.graph import build as b
from pipeline.models import NodeType
from pipeline.sources import clinvar, ctgov, optimuskg, reporter
from pipeline.sources.optimuskg import (
    DRUG_GENE,
    GAP_PROBE_COVERAGE,
    GAP_PROBE_EDGE_FILES,
    GAP_PROBE_FILES,
    GAP_PROBE_PROP,
    GENE_NODES,
    PROCESS_GENE,
    GapProbeError,
    select_gap_probe,
)
from pipeline.store import Store
from tests.test_build import NOW, SEEDS as BUILD_SEEDS, node, populate
from tests.test_optimuskg import (
    DRUG_1,
    G_DUP1,
    G_DUP2,
    GO,
    MOCK_TS,
    drg_gen,
    edge,
    gene,
    world_frames,
    write_world,
)

HGNC = [{"id": "9", "source": "HGNC"}]
# Edgeless genes added to the synthetic world (ids are FORMAT exemplars, symbols placeholders).
G_P, G_Q, G_AA, G_AB, G_AC, G_AD, G_NOSYM = (
    "ENSG99000000091",
    "ENSG99000000090",
    "ENSG99000000092",
    "ENSG99000000093",
    "ENSG99000000094",
    "ENSG99000000095",
    "ENSG99000000096",
)
RULE = "mock rule text recorded with the result"
CFG: dict[str, Any] = {"enabled": True, "rule": RULE, "biotype": "protein_coding"}


def probe_frames(*, drop: tuple[str, ...] = (), extra_drug_gene: bool = True) -> dict[str, tuple[list[dict[str, Any]], dict[str, Any]]]:
    """The test_optimuskg world plus edgeless genes that probe each clause of the rule."""
    frames = world_frames()
    rows, schema = frames[GENE_NODES]
    rows = [r for r in rows if r["id"] not in (G_DUP1, G_DUP2)]
    rows += [
        gene(G_DUP1, "DUPL", xrefs=HGNC),  # shared symbol (alphabetically first): not a resolvable HGNC symbol
        gene(G_DUP2, "DUPL", xrefs=HGNC),
        gene(G_Q, "MOCKQ", xrefs=HGNC),  # candidate
        gene(G_P, "MOCKP", xrefs=HGNC),  # candidate, alphabetically before MOCKQ: the probe
        gene(G_AA, "MOCKAA", biotype="lncRNA", xrefs=HGNC),  # wrong biotype (would otherwise win)
        gene(G_AB, "MOCKAB", xrefs=HGNC),  # one drug_gene row (below): connected
        gene(G_AC, "MOCKAC", xrefs=HGNC),  # one biological_process_gene row (below): connected
        gene(G_AD, "MOCKAD", xrefs=[]),  # no HGNC xref: not an HGNC symbol
        gene(G_NOSYM, G_NOSYM, xrefs=HGNC),  # symbol is the Ensembl id: no symbol at all
    ]
    frames[GENE_NODES] = ([r for r in rows if r["id"] not in drop], schema)
    dg_rows, dg_schema = frames[DRUG_GENE]
    if extra_drug_gene:
        dg_rows = [*dg_rows, drg_gen(DRUG_1, G_AB, "TARGET", None)]
    frames[DRUG_GENE] = (dg_rows, dg_schema)
    bg_rows, bg_schema = frames[PROCESS_GENE]
    bg_rows = [
        *bg_rows,
        edge(GO, G_AC, "BPO-GEN", "INTERACTS_WITH", True, {"sources": {"direct": ["OPEN_TARGETS"], "indirect": []}, "evidence": ["IEA"], "gene_product": ["MOCK2"], "eco_ids": ["ECO:0000501"]}),
    ]
    frames[PROCESS_GENE] = (bg_rows, bg_schema)
    return frames


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Any]:
    _info, local = write_world(tmp_path / "raw", probe_frames())
    return local


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "evidence.sqlite") as s:
        yield s


# --------------------------------------------------------------------------------------
# select_gap_probe
# --------------------------------------------------------------------------------------


def test_select_picks_the_alphabetically_first_edgeless_protein_coding_hgnc_symbol(files: dict[str, Any]) -> None:
    probe = select_gap_probe(files, CFG)
    assert probe["symbol"] == "MOCKP", "MOCKP < MOCKQ; DUPL is shared, MOCKAA is lncRNA, MOCKAB/MOCKAC have an edge row, MOCKAD has no HGNC xref"
    assert probe["gene_id"] == f"ensembl:{G_P}" and probe["backbone_id"] == G_P
    assert probe["name"] == "Mock gene MOCKP" and probe["biotype"] == "protein_coding"
    assert probe["counts"] == {"disease_gene": 0, "drug_gene": 0, "pathway_gene": 0, "biological_process_gene": 0}
    assert set(probe["counts"]) == set(GAP_PROBE_EDGE_FILES) == set(GAP_PROBE_COVERAGE)
    assert probe["file_dates"] == {k: MOCK_TS[:10] for k in GAP_PROBE_EDGE_FILES}
    assert probe["rule"] == RULE and probe["candidates"] == 2
    n = probe["node"]
    assert n["id"] == f"ensembl:{G_P}" and n["type"] == NodeType.Gene.value and n["label"] == "MOCKP"
    assert n["source"] == optimuskg.SOURCE_DB and n["retrieved_at"] == MOCK_TS
    assert n["props"][GAP_PROBE_PROP] is True and n["props"]["gap_probe_rule"] == RULE and n["props"]["gap_probe_counts"] == probe["counts"]
    assert n["props"]["backbone_id"] == G_P and n["props"]["symbol"] == "MOCKP" and n["props"]["source_type"] == "GEN"


def test_select_is_deterministic_and_uses_default_rule_text(files: dict[str, Any]) -> None:
    first = select_gap_probe(files, {"enabled": True})
    second = select_gap_probe(files, {})
    assert first == second
    assert first["rule"] == optimuskg.DEFAULT_GAP_PROBE_RULE and first["biotype"] == optimuskg.DEFAULT_GAP_PROBE_BIOTYPE


def test_biotype_comes_from_the_config(files: dict[str, Any]) -> None:
    probe = select_gap_probe(files, {**CFG, "biotype": "lncRNA"})
    assert probe["symbol"] == "MOCKAA" and probe["biotype"] == "lncRNA" and probe["candidates"] == 1


def test_a_single_row_in_any_edge_file_disqualifies(tmp_path: Path) -> None:
    # without MOCKP and MOCKQ the only edgeless-looking HGNC genes carry one drug_gene (MOCKAB) or
    # one biological_process_gene (MOCKAC) row each: nothing qualifies
    _info, files = write_world(tmp_path / "raw_none", probe_frames(drop=(G_P, G_Q)))
    with pytest.raises(GapProbeError):
        select_gap_probe(files, CFG)
    # remove MOCKAB's drug_gene row and it becomes the probe (MOCKAC still has its GO row)
    _info, files = write_world(tmp_path / "raw_one", probe_frames(drop=(G_P, G_Q), extra_drug_gene=False))
    probe = select_gap_probe(files, CFG)
    assert probe["symbol"] == "MOCKAB" and probe["candidates"] == 1


def test_select_needs_every_probe_file(files: dict[str, Any]) -> None:
    partial = {k: v for k, v in files.items() if k != DRUG_GENE}
    with pytest.raises(KeyError, match=DRUG_GENE):
        select_gap_probe(partial, CFG)
    assert set(GAP_PROBE_FILES) == {GENE_NODES, *GAP_PROBE_EDGE_FILES.values()}


# --------------------------------------------------------------------------------------
# run_gap_probe / gap_probe_from_store (export) over a fake network
# --------------------------------------------------------------------------------------


def _transport(calls: list[str], *, clinvar_count: str = "3", ctgov_total: int = 2, reporter_total: int = 1, fail: str | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.host}{request.url.path}")
        host = request.url.host
        if fail == host:
            return httpx.Response(500, text="upstream unavailable", headers={"content-type": "text/plain"})
        if host == "eutils.ncbi.nlm.nih.gov":
            assert request.url.params["db"] == clinvar.DB and request.url.params["retmax"] == "0"
            return httpx.Response(200, json={"header": {"type": "esearch"}, "esearchresult": {"count": clinvar_count, "idlist": []}})
        if host == ctgov.HOST:
            assert request.url.params["countTotal"] == "true" and request.url.params["pageSize"] == "1"
            return httpx.Response(200, json={"totalCount": ctgov_total, "studies": []})
        if host == reporter.REPORTER_HOST:
            body = json.loads(request.content)
            assert body["limit"] == 1 and body["criteria"]["advanced_text_search"]["search_text"] == "MOCKP"
            return httpx.Response(200, json={"meta": {"total": reporter_total}, "results": []})
        return httpx.Response(404, text="unexpected host")

    return httpx.MockTransport(handler)


def _http(tmp_path: Path, calls: list[str], *, offline: bool, **kw: Any) -> CachedHttp:
    return CachedHttp(DiskCache(tmp_path / "cache", offline=offline), transport=_transport(calls, **kw), sleep=lambda s: None)


def _rows(store: Store, entity_id: str) -> dict[str, int]:
    return {r["source"]: r["hits"] for r in store.coverage_rows() if r["entity_id"] == entity_id}


SEEDS = {"genes": ["MOCKA", "MOCKB"], "gap_probe": CFG}


def test_run_gap_probe_stores_node_and_coverage_then_replays_from_cache(store: Store, files: dict[str, Any], tmp_path: Path) -> None:
    gene_id = f"ensembl:{G_P}"
    store.upsert_nodes([node("mock:gene:old", "Gene", "MOCKOLD", symbol="MOCKOLD", **{GAP_PROBE_PROP: True})])  # an earlier run's probe
    calls: list[str] = []
    report = export.run_gap_probe(store, SEEDS, offline=False, files=files, http=_http(tmp_path, calls, offline=False), seed_loader=lambda name: {})
    assert report["enabled"] is True and report["status"] == "ok" and report["errors"] == []
    assert report["symbol"] == "MOCKP" and report["gene_id"] == gene_id and report["backbone_id"] == G_P and report["rule"] == RULE
    assert report["counts"] == {k: 0 for k in GAP_PROBE_EDGE_FILES} and report["candidates"] == 2
    assert report["stale_probe_nodes_removed"] == ["mock:gene:old"] and store.get_node("mock:gene:old") is None
    stored = store.get_node(gene_id)
    assert stored is not None and stored["props"][GAP_PROBE_PROP] is True and stored["source"] == optimuskg.SOURCE_DB
    assert sorted(calls) == [f"GET {ctgov.HOST}/api/v2/studies", "GET eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", f"POST {reporter.REPORTER_HOST}/v2/projects/search"]

    expected = {**{GAP_PROBE_COVERAGE[k]: 0 for k in GAP_PROBE_EDGE_FILES}, clinvar.COVERAGE_ESEARCH: 3, ctgov.COVERAGE_SOURCE: 2, reporter.COVERAGE_PROJECTS_SOURCE: 1}
    assert _rows(store, gene_id) == expected
    assert {s["source"]: s["hits"] for s in report["searches"]} == expected and report["zero_hit_searches"] == 4
    by_source = {s["source"]: s for s in report["searches"]}
    assert by_source[clinvar.COVERAGE_ESEARCH]["query"] == "MOCKP[gene]"
    assert by_source[ctgov.COVERAGE_SOURCE]["query"].startswith("query.term=EXPANSION%5BNone%5DMOCKP&countTotal=true")
    assert by_source[reporter.COVERAGE_PROJECTS_SOURCE]["query"] == "MOCKP"
    for s in report["searches"]:
        assert s["date"] and isinstance(s["hits"], int)
    assert by_source[GAP_PROBE_COVERAGE["disease_gene"]]["date"] == MOCK_TS[:10]

    # offline replay: same cache root, a transport that must never be reached, identical rows
    replay_calls: list[str] = []
    again = export.run_gap_probe(store, SEEDS, offline=True, files=files, http=_http(tmp_path, replay_calls, offline=True, fail="eutils.ncbi.nlm.nih.gov"), seed_loader=lambda name: {})
    assert replay_calls == []
    volatile = {"seconds", "stale_probe_nodes_removed"}  # the stale node was removed by the first run
    assert again["stale_probe_nodes_removed"] == []
    assert {k: v for k, v in again.items() if k not in volatile} == {k: v for k, v in report.items() if k not in volatile}
    assert _rows(store, gene_id) == expected and sum(1 for r in store.coverage_rows() if r["entity_id"] == gene_id) == 7

    # the store-only view (--skip-download) reports the same probe and searches
    from_store = export.gap_probe_from_store(store)
    assert from_store["status"].startswith("from store") and from_store["symbol"] == "MOCKP" and from_store["gene_id"] == gene_id
    assert from_store["rule"] == RULE and from_store["counts"] == report["counts"]
    assert {s["source"]: s["hits"] for s in from_store["searches"]} == expected


def test_run_gap_probe_records_failed_lookups_as_zero_hit_rows(store: Store, files: dict[str, Any], tmp_path: Path) -> None:
    gene_id = f"ensembl:{G_P}"
    report = export.run_gap_probe(store, SEEDS, offline=False, files=files, http=_http(tmp_path, [], offline=False, fail=ctgov.HOST), seed_loader=lambda name: {})
    assert report["status"] == "ok with 1 failed lookup(s)" and len(report["errors"]) == 1 and report["errors"][0]["source"] == ctgov.COVERAGE_SOURCE
    rows = {r["source"]: r for r in store.coverage_rows() if r["entity_id"] == gene_id}
    assert rows[ctgov.COVERAGE_SOURCE]["hits"] == 0 and "[request failed: HTTP 500]" in rows[ctgov.COVERAGE_SOURCE]["query"]
    assert rows[clinvar.COVERAGE_ESEARCH]["hits"] == 3 and rows[reporter.COVERAGE_PROJECTS_SOURCE]["hits"] == 1


def test_run_gap_probe_disabled_touches_nothing(store: Store, files: dict[str, Any], tmp_path: Path) -> None:
    calls: list[str] = []
    for seeds in ({"genes": []}, {"genes": [], "gap_probe": {"enabled": False}}, {"genes": [], "gap_probe": "yes"}):
        report = export.run_gap_probe(store, seeds, offline=False, files=files, http=_http(tmp_path, calls, offline=False))
        assert report["enabled"] is False and "symbol" not in report
    assert calls == [] and store.counts()["nodes"] == 0 and store.coverage_rows() == []
    assert export.gap_probe_from_store(store)["status"].startswith("not in store")


# --------------------------------------------------------------------------------------
# build: the probe survives pruning without edges and keeps its coverage
# --------------------------------------------------------------------------------------


def test_gap_probe_node_is_exported_without_edges_and_keeps_coverage(store: Store) -> None:
    populate(store)
    counts = {k: 0 for k in GAP_PROBE_EDGE_FILES}
    store.upsert_nodes(
        [
            node("mock:gene:probe", "Gene", "MOCKPROBE", ["MOCKPROBE-OLD"], symbol="MOCKPROBE", name="mock probe gene", biotype="protein_coding", backbone_id="MOCKENSG9", function_descriptions=["bulk"], **{GAP_PROBE_PROP: True, "gap_probe_rule": RULE, "gap_probe_counts": counts}),
            node("mock:gene:lonely", "Gene", "MOCKLONELY", symbol="MOCKLONELY", backbone_id="MOCKENSG8"),  # edgeless but not the probe
        ]
    )
    for key, source in GAP_PROBE_COVERAGE.items():
        store.add_coverage("mock:gene:probe", source, "MOCKPROBE", "2026-10-03", counts[key])
    store.add_coverage("mock:gene:probe", clinvar.COVERAGE_ESEARCH, "MOCKPROBE[gene]", "2026-10-03", 0)
    store.add_coverage("mock:gene:lonely", clinvar.COVERAGE_ESEARCH, "MOCKLONELY[gene]", "2026-10-03", 0)

    result = b.build(store, BUILD_SEEDS, now=NOW)
    graph = result.graph
    idx = graph.node_index()
    assert "mock:gene:probe" in idx and "mock:gene:lonely" not in idx, "only the marked probe is exported without edges"
    assert not any("mock:gene:probe" in (e.source, e.target) for e in graph.edges)
    probe = idx["mock:gene:probe"]
    assert probe.props[GAP_PROBE_PROP] is True and probe.props["gap_probe_rule"] == RULE and probe.props["gap_probe_counts"] == counts
    assert set(probe.props) <= set(b.EXPORT_PROPS_KEEP["Gene"]) and "function_descriptions" not in probe.props
    searched = graph.meta.coverage["mock:gene:probe"].searched
    assert {s.source for s in searched} == {*GAP_PROBE_COVERAGE.values(), clinvar.COVERAGE_ESEARCH} and all(s.hits == 0 for s in searched)
    assert "mock:gene:lonely" not in graph.meta.coverage
    assert result.report["prune"]["genes"]["gap_probe"] == ["mock:gene:probe"]
    assert result.report["prune"]["nodes_dropped"]["Gene"]["n"] == 1
