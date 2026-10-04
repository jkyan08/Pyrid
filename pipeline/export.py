"""Build ``data/build/graph.json`` and ``data/build/build_report.json`` (``make data``).

    uv run python -m pipeline.export                 # run the loaders (cached), build, write
    uv run python -m pipeline.export --offline       # ATLAS_OFFLINE=1: cache and raw files only
    uv run python -m pipeline.export --skip-download # build from the existing evidence store
    uv run python -m pipeline.export --only clinvar,ctgov   # run a subset of the loaders
    uv run python -m pipeline.export --claims data/build/claims.json
    uv run python -m pipeline.export --no-claims     # ignore data/build/claims.json
    uv run python -m pipeline.export --indent 2      # pretty-printed graph.json (default: compact)

Steps: load the seeds (``cluster``, ``mechanisms``, ``patient_groups``); run the loaders in
:data:`LOADERS` order (``optimuskg``, ``hpo``, ``clinvar``, ``ctgov``, ``reporter``; each
idempotent and cached; ``--skip-download`` skips them all, ``--only`` runs a subset); run the
**gap probe** (:func:`run_gap_probe`, below) whenever the loaders run; validate the
claims bundle when one exists (``--claims PATH``, default ``data/build/claims.json``, disabled by
``--no-claims``); :func:`pipeline.graph.build.build`; ``pipeline.graph.logic.apply_graph_logic``
(similarity, clustering, rules) when that module is importable, else a log line and the graph
goes out without clusters; write the graph; read it back through the strict file-level contract;
write the report. The report's ``loaders`` entry keeps each loader's status and the summary dict
it returned (set sizes before and after every threshold or cap, dropped edges, per-step timings,
source versions); ``evidence`` and ``prune`` hold the claim integration and pruning summaries;
``sizes`` the bytes per node type and edge relation.

Gap probe (ground-truth test 6, README section 8). When ``cluster.yaml`` has ``gap_probe.enabled``,
:func:`run_gap_probe` asks :func:`pipeline.sources.optimuskg.select_gap_probe` for the
connection-less backbone gene the documented rule picks (no symbol anywhere in code or seeds),
upserts it into the store as a Gene node with ``props.gap_probe = true`` (source ``optimuskg``:
the node is backbone data) after removing any earlier probe node, and records every lookup for it
as a coverage row under the gene's node id: the four backbone edge-file counts (source ``"OptimusKG
<edge file>"``, hits 0 by construction) and three live searches for its SYMBOL through the loaders'
own request helpers, so they share the cache and the rate limiters: the ClinVar esearch count for
``<SYMBOL>[gene]``, the ClinicalTrials.gov ``GET /studies`` ``totalCount`` for
``query.term=EXPANSION[None]<SYMBOL>``, and the NIH RePORTER ``projects/search`` total for the
symbol with the loader's search settings. A failed request is still a coverage row (hits 0, the
failure in the query text) and a report entry; ``ATLAS_OFFLINE=1`` replays all of it from the
cache. The build exports the node without edges (``pipeline.graph.build.prune_graph``), and the
report's ``gap_probe`` entry (``symbol``, ``gene_id``, ``rule``, ``counts``, ``searches``) is what
``tests/test_ground_truth.py`` test 6 reads before calling the API. With ``--skip-download`` the
probe already in the store is reported instead (no lookups).

Exit status: 0 on success, 1 when the graph fails validation, 2 when the claims bundle fails
validation, 3 when a loader fails. A loader module that does not exist yet is reported and skipped
(the build then runs on whatever the store holds).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pipeline import config
from pipeline.cache import ENV_OFFLINE, CachedHttp, DiskCache, default_http
from pipeline.graph.build import build, sizes, summarize
from pipeline.models import ClaimsBundle, Graph, NodeType
from pipeline.sources import clinvar, ctgov, optimuskg, reporter
from pipeline.store import Store

__all__ = [
    "DEFAULT_CLAIMS_PATH",
    "DEFAULT_REPORT_PATH",
    "GAP_PROBE_CTGOV_FIELDS",
    "GAP_PROBE_REPORTER_FIELDS",
    "LOADERS",
    "LOGIC_MODULE",
    "LoaderSpec",
    "apply_graph_logic",
    "gap_probe_from_store",
    "main",
    "run_gap_probe",
    "run_loaders",
]

DEFAULT_CLAIMS_PATH = config.BUILD_DIR / "claims.json"
DEFAULT_REPORT_PATH = config.BUILD_DIR / "build_report.json"
LOGIC_MODULE = "pipeline.graph.logic"
# Gap-probe lookups ask for the smallest page that still carries the total: one study with one
# field (ClinicalTrials.gov ``countTotal``), one RePORTER project with identity fields only.
GAP_PROBE_CTGOV_FIELDS: tuple[str, ...] = ("NCTId",)
GAP_PROBE_REPORTER_FIELDS: tuple[str, ...] = ("ApplId", "ProjectNum", "CoreProjectNum", "ProjectTitle")
GAP_PROBE_REQUEST_FAILED = "[request failed: {}]"


@dataclass(frozen=True)
class LoaderSpec:
    """One source loader: ``module.load(store, <seeds...>, offline=...)``.

    ``seed_files`` names the seed files (under ``pipeline/seeds``) passed positionally after the
    store, in order; ``cluster`` is the cluster seed dict the export already holds.
    """

    name: str
    module: str
    seed_files: tuple[str, ...]


# Run order matters: the backbone first (ClinVar / ClinicalTrials.gov / RePORTER link against the
# Gene and Disease nodes already in the store). Imported lazily so a missing loader does not break
# the build.
LOADERS: tuple[LoaderSpec, ...] = (
    LoaderSpec("optimuskg", "pipeline.sources.optimuskg", ("cluster",)),  # load(store, seeds, offline=...)
    LoaderSpec("hpo", "pipeline.sources.hpo", ()),  # load(store, offline=...)
    LoaderSpec("clinvar", "pipeline.sources.clinvar", ("clinvar", "cluster")),  # load(store, seeds, cluster_seeds, offline=...)
    LoaderSpec("ctgov", "pipeline.sources.ctgov", ("ctgov",)),  # load(store, seeds, offline=...)
    LoaderSpec("reporter", "pipeline.sources.reporter", ("reporter",)),  # load(store, seeds, offline=...)
)

EXIT_OK = 0
EXIT_GRAPH_INVALID = 1
EXIT_CLAIMS_INVALID = 2
EXIT_LOADER_FAILED = 3


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m pipeline.export", description=__doc__.split("\n\n")[0])
    p.add_argument("--offline", action="store_true", help=f"set {ENV_OFFLINE}=1: serve every lookup from the cache / raw files")
    p.add_argument("--skip-download", action="store_true", help="do not run the source loaders; build from the existing store")
    p.add_argument("--only", default=None, help="comma-separated loader names to run (" + ", ".join(s.name for s in LOADERS) + "); the others are skipped")
    p.add_argument("--claims", type=Path, default=None, help=f"claims bundle to merge (default: {DEFAULT_CLAIMS_PATH} when it exists)")
    p.add_argument("--no-claims", action="store_true", help="build without a claims bundle even when the default file exists")
    p.add_argument("--store", type=Path, default=None, help=f"evidence store (default {config.STORE_PATH})")
    p.add_argument("--out", type=Path, default=None, help=f"graph.json path (default {config.GRAPH_PATH})")
    p.add_argument("--report", type=Path, default=None, help=f"build report path (default {DEFAULT_REPORT_PATH})")
    p.add_argument("--indent", type=int, default=None, help="pretty-print graph.json with this indent (default: compact)")
    return p


def _parse_only(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    names = {n.strip() for n in raw.split(",") if n.strip()}
    known = {s.name for s in LOADERS}
    unknown = sorted(names - known)
    if unknown:
        raise SystemExit(f"[export] --only: unknown loader(s) {unknown}; known: {sorted(known)}")
    return names


def run_loaders(
    store: Store,
    seeds: dict[str, Any],
    *,
    offline: bool,
    timings: dict[str, float],
    only: set[str] | None = None,
    seed_loader: Any = config.load_seeds,
) -> dict[str, dict[str, Any]]:
    """Run every loader in :data:`LOADERS` (or the ``only`` subset); returns
    ``{module: {"status": ..., "summary": ...}}`` where ``summary`` is the dict the loader returned
    (what its thresholds removed, per-step timings, seed resolution, ...), so the build report
    makes the safeguards auditable. Raises on a loader error."""
    outcome: dict[str, dict[str, Any]] = {}
    for spec in LOADERS:
        if only is not None and spec.name not in only:
            outcome[spec.module] = {"status": "skipped (--only)"}
            continue
        t0 = time.perf_counter()
        try:
            module = importlib.import_module(spec.module)
        except ImportError as exc:
            outcome[spec.module] = {"status": f"missing: {exc}"}
            print(f"[export] loader {spec.module} not available, skipped ({exc})", file=sys.stderr)
            continue
        args = [seeds if name == "cluster" else seed_loader(name) for name in spec.seed_files]
        summary = module.load(store, *args, offline=offline)
        timings[spec.module] = round(time.perf_counter() - t0, 3)
        outcome[spec.module] = {"status": "ok", "summary": summary}
    return outcome


# --------------------------------------------------------------------------------------
# gap probe (ground-truth test 6)
# --------------------------------------------------------------------------------------


def _gap_probe_enabled(seeds: Mapping[str, Any]) -> bool:
    cfg = seeds.get("gap_probe")
    return isinstance(cfg, Mapping) and bool(cfg.get("enabled"))


def _coverage(store: Store, entity_id: str, source: str, query: str, date: str, hits: int) -> dict[str, Any]:
    """Write one coverage row and return it in the ``meta.coverage`` search shape."""
    store.add_coverage(entity_id, source, query, date, int(hits))
    return {"source": source, "query": query, "date": date, "hits": int(hits)}


def _clear_gap_probe_nodes(store: Store, keep: str) -> list[str]:
    """Remove every Gene node marked ``props.gap_probe`` other than ``keep`` (a changed rule must
    not leave two probes in the store); returns the removed ids."""
    stale = [n["id"] for n in store.iter_nodes(NodeType.Gene) if (n.get("props") or {}).get(optimuskg.GAP_PROBE_PROP) is True and n["id"] != keep]
    if stale:
        with store.conn:
            store.conn.executemany("DELETE FROM nodes WHERE id = ?", [(nid,) for nid in stale])
    return stale


def _gap_probe_searches(store: Store, http: Any, entity_id: str, symbol: str, *, reporter_search: Mapping[str, Any], errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The three live lookups for the probe's SYMBOL (module docstring "Gap probe"), each through
    the loader's own request helper and written as a coverage row under ``entity_id``."""
    rows: list[dict[str, Any]] = []

    # ClinVar: the loader's esearch for "<SYMBOL>[gene]" (count only, no history keys).
    run = clinvar._Run()
    es = clinvar._esearch(http, run, f"{symbol}[gene]", history=False)
    query = es.query if not es.error else f"{es.query} {GAP_PROBE_REQUEST_FAILED.format(es.error)}"
    rows.append(_coverage(store, entity_id, clinvar.COVERAGE_ESEARCH, query, es.date, 0 if es.error else es.count))
    if es.error:
        errors.append({"source": clinvar.COVERAGE_ESEARCH, "query": es.query, "error": es.error})

    # ClinicalTrials.gov: one GET /studies page with countTotal for query.term=EXPANSION[None]<SYMBOL>
    # (the loader's query form for a gene symbol; the context operator prevents fuzzy expansion).
    ctgov._throttle(http)
    seed_query = ctgov.SeedQuery(label=f"gap probe {symbol}", term=f"EXPANSION[None]{symbol}")
    try:
        result = ctgov.search(http, seed_query, page_size=1, max_studies=1, fields=GAP_PROBE_CTGOV_FIELDS)
        rows.append(_coverage(store, entity_id, ctgov.COVERAGE_SOURCE, result.params_string, result.retrieved_date, result.total_count))
    except ctgov.CtgovHttpError as exc:
        record = exc.to_record()
        query = f"{ctgov.params_string(exc.params)} {GAP_PROBE_REQUEST_FAILED.format('HTTP ' + str(record['status']))}"
        rows.append(_coverage(store, entity_id, ctgov.COVERAGE_SOURCE, query, record["date"], 0))
        errors.append({"source": ctgov.COVERAGE_SOURCE, **record})

    # NIH RePORTER: projects/search total for the symbol, the loader's operator / fields / fiscal years.
    reporter._ensure_rate_limit(http)
    body = reporter.build_projects_body(
        symbol,
        reporter.fiscal_years_for(None, int(reporter_search["fiscal_years_back"])),
        operator=str(reporter_search["operator"]),
        search_field=str(reporter_search["search_field"]),
        limit=1,
        exclude_subprojects=bool(reporter_search["exclude_subprojects"]),
        include_fields=GAP_PROBE_REPORTER_FIELDS,
    )
    resp = reporter._post(http, reporter.PROJECTS_URL, body)
    if resp.ok:
        hits, _records = reporter._results(resp.json())
        rows.append(_coverage(store, entity_id, reporter.COVERAGE_PROJECTS_SOURCE, symbol, resp.retrieved_date, hits))
    else:
        message = reporter.error_message(resp)
        rows.append(_coverage(store, entity_id, reporter.COVERAGE_PROJECTS_SOURCE, f"{symbol} {GAP_PROBE_REQUEST_FAILED.format('HTTP ' + str(resp.status))}", resp.retrieved_date, 0))
        errors.append({"source": reporter.COVERAGE_PROJECTS_SOURCE, "query": symbol, "status": resp.status, "error": message})
    return rows


def run_gap_probe(
    store: Store,
    seeds: Mapping[str, Any],
    *,
    offline: bool,
    raw_dir: Path | None = None,
    http: Any = None,
    files: Mapping[str, Any] | None = None,
    seed_loader: Any = config.load_seeds,
) -> dict[str, Any]:
    """Choose, store and search the gap probe (module docstring "Gap probe"); returns the report
    entry. Disabled (``seeds["gap_probe"]["enabled"]`` not true) -> ``{"enabled": False, ...}``.

    ``files`` (path -> LocalFile) skips :func:`pipeline.sources.optimuskg.resolve_files` (tests);
    ``http`` is any client with the loaders' ``get_json`` / ``post_json`` interface (default: the
    process-wide cached client, or a cache-only client when ``offline``). Raises on a missing raw
    file or, offline, on a cache miss, like a loader would.
    """
    if not _gap_probe_enabled(seeds):
        return {"enabled": False, "status": "disabled (cluster.yaml gap_probe.enabled is not true)"}
    cfg = seeds["gap_probe"]
    t0 = time.perf_counter()
    if files is None:
        _info, files = optimuskg.resolve_files(optimuskg.GAP_PROBE_FILES, raw_dir=raw_dir, offline=offline)
    probe = optimuskg.select_gap_probe(files, cfg)
    node = probe["node"]
    stale = _clear_gap_probe_nodes(store, keep=node["id"])
    store.upsert_nodes([node])
    searches = [
        _coverage(store, node["id"], optimuskg.GAP_PROBE_COVERAGE[key], probe["symbol"], probe["file_dates"][key], probe["counts"][key])
        for key in optimuskg.GAP_PROBE_EDGE_FILES
    ]
    try:
        reporter_search = {**reporter.DEFAULT_SEARCH, **((seed_loader("reporter") or {}).get("search") or {})}
    except FileNotFoundError:
        reporter_search = dict(reporter.DEFAULT_SEARCH)
    own_http = http is None
    client = http if http is not None else (CachedHttp(DiskCache(offline=True), headers={"User-Agent": config.USER_AGENT}) if offline else default_http())
    errors: list[dict[str, Any]] = []
    try:
        searches += _gap_probe_searches(store, client, node["id"], probe["symbol"], reporter_search=reporter_search, errors=errors)
    finally:
        if own_http and offline:
            client.close()
    return {
        "enabled": True,
        "status": "ok" if not errors else f"ok with {len(errors)} failed lookup(s)",
        "symbol": probe["symbol"],
        "gene_id": probe["gene_id"],
        "backbone_id": probe["backbone_id"],
        "name": probe["name"],
        "biotype": probe["biotype"],
        "rule": probe["rule"],
        "candidates": probe["candidates"],
        "counts": probe["counts"],
        "searches": searches,
        "zero_hit_searches": sum(1 for s in searches if s["hits"] == 0),
        "errors": errors,
        "stale_probe_nodes_removed": stale,
        "seconds": round(time.perf_counter() - t0, 3),
    }


def gap_probe_from_store(store: Store) -> dict[str, Any]:
    """The probe an earlier run left in the store (``--skip-download``): the marked Gene node and
    its coverage rows, in the same report shape, or a status line when there is none."""
    probes = [n for n in store.iter_nodes(NodeType.Gene) if (n.get("props") or {}).get(optimuskg.GAP_PROBE_PROP) is True]
    if not probes:
        return {"enabled": True, "status": "not in store (--skip-download; run the export once without it)"}
    node = sorted(probes, key=lambda n: n["id"])[0]
    props = node.get("props") or {}
    searches = [
        {"source": r["source"], "query": r["query"], "date": r["date"], "hits": int(r["hits"])}
        for r in store.coverage_rows()
        if r["entity_id"] == node["id"]
    ]
    return {
        "enabled": True,
        "status": "from store (--skip-download)",
        "symbol": props.get("symbol") or node["label"],
        "gene_id": node["id"],
        "backbone_id": props.get("backbone_id"),
        "name": props.get("name"),
        "biotype": props.get("biotype"),
        "rule": props.get(f"{optimuskg.GAP_PROBE_PROP}_rule"),
        "counts": props.get(f"{optimuskg.GAP_PROBE_PROP}_counts"),
        "searches": searches,
        "zero_hit_searches": sum(1 for s in searches if s["hits"] == 0),
        "errors": [],
    }


def _load_claims(path: Path | None, *, disabled: bool) -> tuple[ClaimsBundle | None, str]:
    if disabled:
        return None, "disabled (--no-claims)"
    if path is None:
        if not DEFAULT_CLAIMS_PATH.is_file():
            return None, "none"
        path = DEFAULT_CLAIMS_PATH
    return ClaimsBundle.load(path), str(path)


def apply_graph_logic(graph: Graph, seeds: dict[str, Any]) -> tuple[Graph, dict[str, Any]]:
    """``pipeline.graph.logic.apply_graph_logic(graph, seeds)`` when that module is importable
    (similarity, clustering, rules), else the graph unchanged and a report saying it is absent.
    The result may be a :class:`Graph`, an object with ``.graph`` / ``.report`` or a
    ``(graph, report)`` tuple; it is re-validated through the strict contract."""
    try:
        module = importlib.import_module(LOGIC_MODULE)
    except ImportError as exc:
        print(f"[export] {LOGIC_MODULE} not available, graph exported without clusters ({exc})", file=sys.stderr)
        return graph, {"status": f"absent: {exc}"}
    fn = getattr(module, "apply_graph_logic", None)
    if not callable(fn):
        print(f"[export] {LOGIC_MODULE} has no apply_graph_logic, graph exported without clusters", file=sys.stderr)
        return graph, {"status": "absent: no apply_graph_logic"}
    out = fn(graph, seeds)
    logic_report: Any = None
    if isinstance(out, Graph):
        new_graph = out
    elif isinstance(out, tuple) and out and isinstance(out[0], Graph):
        new_graph = out[0]
        logic_report = out[1] if len(out) > 1 else None
    elif isinstance(getattr(out, "graph", None), Graph):
        new_graph = out.graph
        logic_report = getattr(out, "report", None)
    else:
        raise TypeError(f"{LOGIC_MODULE}.apply_graph_logic returned {type(out).__name__}, expected a Graph")
    new_graph = Graph.model_validate_json(new_graph.model_dump_json(), strict=True)
    return new_graph, {"status": "applied", "report": logic_report}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.offline:
        os.environ[ENV_OFFLINE] = "1"
    offline = os.environ.get(ENV_OFFLINE, "").strip().lower() in {"1", "true", "yes", "on"}
    out_path = Path(args.out) if args.out else config.GRAPH_PATH
    report_path = Path(args.report) if args.report else DEFAULT_REPORT_PATH
    store_path = Path(args.store) if args.store else config.STORE_PATH
    only = _parse_only(args.only)
    timings: dict[str, float] = {}
    t_start = time.perf_counter()

    seeds = config.load_seeds("cluster")
    seeds["mechanisms"] = config.load_seeds("mechanisms")
    # cluster.yaml's ``patient_groups:`` section holds the resolution knobs (descendant_depth,
    # max_descendants_per_term); the seed file's rows join them under the same key for the build.
    pg_options = seeds.get("patient_groups") if isinstance(seeds.get("patient_groups"), dict) else {}
    try:
        seeds["patient_groups"] = {**config.load_seeds("patient_groups"), **pg_options}
    except FileNotFoundError as exc:
        print(f"[export] patient group seeds not found, skipped ({exc})", file=sys.stderr)

    try:
        bundle, claims_source = _load_claims(args.claims, disabled=args.no_claims)
    except (ValidationError, ValueError, OSError) as exc:
        print(f"[export] claims bundle invalid: {exc}", file=sys.stderr)
        return EXIT_CLAIMS_INVALID

    with Store(store_path) as store:
        loaders: dict[str, dict[str, Any]] = {}
        if not args.skip_download:
            try:
                loaders = run_loaders(store, seeds, offline=offline, timings=timings, only=only)
            except Exception as exc:  # noqa: BLE001 - the loader's own message is the finding
                print(f"[export] loader failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                return EXIT_LOADER_FAILED
            t0 = time.perf_counter()
            try:
                gap_probe = run_gap_probe(store, seeds, offline=offline)
            except Exception as exc:  # noqa: BLE001 - same contract as a loader failure
                print(f"[export] gap probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                return EXIT_LOADER_FAILED
            timings["gap_probe"] = round(time.perf_counter() - t0, 3)
        else:
            loaders = {spec.module: {"status": "skipped (--skip-download)"} for spec in LOADERS}
            # no lookups without the loaders: report the probe an earlier run stored (or the disabled status)
            gap_probe = gap_probe_from_store(store) if _gap_probe_enabled(seeds) else run_gap_probe(store, seeds, offline=offline)

        if store.counts()["nodes"] == 0:
            print(f"[export] graph failed validation: the evidence store {store_path} has no nodes (run the loaders first)", file=sys.stderr)
            return EXIT_GRAPH_INVALID
        t0 = time.perf_counter()
        try:
            result = build(store, seeds, bundle)
        except ValidationError as exc:
            print(f"[export] graph failed validation:\n{exc}", file=sys.stderr)
            return EXIT_GRAPH_INVALID
        timings["build"] = round(time.perf_counter() - t0, 3)
        store_counts = store.counts()

    t0 = time.perf_counter()
    try:
        graph, logic_report = apply_graph_logic(result.graph, seeds)
    except ValidationError as exc:
        print(f"[export] graph failed validation after {LOGIC_MODULE}:\n{exc}", file=sys.stderr)
        return EXIT_GRAPH_INVALID
    timings["graph_logic"] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    graph.dump(out_path, indent=args.indent)
    try:
        Graph.load(out_path)
    except ValidationError as exc:
        print(f"[export] written graph does not load back through the strict contract:\n{exc}", file=sys.stderr)
        return EXIT_GRAPH_INVALID
    timings["write_and_reload"] = round(time.perf_counter() - t0, 3)
    timings["total"] = round(time.perf_counter() - t_start, 3)

    counts = summarize(graph)  # the final graph may differ from the build's (clusters, rule edges)
    report = {
        **result.report,
        "counts": counts,
        "sizes": {**sizes(graph), "graph_json_bytes": out_path.stat().st_size},
        "graph_logic": logic_report,
        "gap_probe": gap_probe,
        "graph_path": str(out_path),
        "store_path": str(store_path),
        "claims_bundle": claims_source,
        "offline": offline,
        "loaders": loaders,
        "store_counts": store_counts,
        "source_versions": [s.model_dump(mode="json") for s in graph.meta.sources],
        "timings_seconds": timings,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")

    print(f"[export] wrote {out_path} ({report['sizes']['graph_json_bytes'] / 1_000_000:.2f} MB)")
    print(f"[export] nodes {counts['nodes']} {counts['nodes_by_type']}")
    print(f"[export] edges {counts['edges']} {counts['edges_by_relation']}")
    print(f"[export] claims {counts['claims']}, clusters {counts['clusters']}, coverage entities {counts['coverage']['entities']} ({counts['coverage']['zero_hit_searches']} zero-hit searches)")
    print(f"[export] mechanisms: {result.report['mechanisms']['by_rule']}; evidence: {result.report['evidence'].get('how')}; graph logic: {logic_report.get('status')}")
    if gap_probe.get("symbol"):
        hits = ", ".join(f"{s['source']}={s['hits']}" for s in gap_probe.get("searches") or [])
        print(f"[export] gap probe: {gap_probe['symbol']} ({gap_probe['gene_id']}; {gap_probe['status']}); searches: {hits}")
    else:
        print(f"[export] gap probe: {gap_probe.get('status')}")
    print(f"[export] report {report_path} ({timings['total']} s)")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
