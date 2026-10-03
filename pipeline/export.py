"""Build ``data/build/graph.json`` and ``data/build/build_report.json`` (``make data``).

    uv run python -m pipeline.export                 # load sources (cached), build, write
    uv run python -m pipeline.export --offline       # ATLAS_OFFLINE=1: cache and raw files only
    uv run python -m pipeline.export --skip-download # build from the existing evidence store
    uv run python -m pipeline.export --claims data/build/claims.json
    uv run python -m pipeline.export --indent 2     # pretty-printed graph.json (default: compact)

Steps: load the seeds; run ``pipeline.sources.optimuskg.load(store, seeds, offline=...)`` and
``pipeline.sources.hpo.load(store, offline=...)`` (both idempotent and cached; skipped with
``--skip-download``); validate the claims bundle when one exists; :func:`pipeline.graph.build.build`;
write the graph; read it back through the strict file-level contract; write the report. The
report's ``loaders`` entry keeps each loader's status and the summary dict it returned (set sizes
before and after every threshold or cap, dropped edges, per-step timings, source versions).

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
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pipeline import config
from pipeline.cache import ENV_OFFLINE
from pipeline.graph.build import build
from pipeline.models import ClaimsBundle, Graph
from pipeline.store import Store

__all__ = ["DEFAULT_CLAIMS_PATH", "DEFAULT_REPORT_PATH", "LOADERS", "main", "run_loaders"]

DEFAULT_CLAIMS_PATH = config.BUILD_DIR / "claims.json"
DEFAULT_REPORT_PATH = config.BUILD_DIR / "build_report.json"

# (module, how it is called); imported lazily so a missing loader does not break the build.
LOADERS: tuple[tuple[str, bool], ...] = (
    ("pipeline.sources.optimuskg", True),  # load(store, seeds, offline=...)
    ("pipeline.sources.hpo", False),  # load(store, offline=...)
)

EXIT_OK = 0
EXIT_GRAPH_INVALID = 1
EXIT_CLAIMS_INVALID = 2
EXIT_LOADER_FAILED = 3


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m pipeline.export", description=__doc__.split("\n\n")[0])
    p.add_argument("--offline", action="store_true", help=f"set {ENV_OFFLINE}=1: serve every lookup from the cache / raw files")
    p.add_argument("--skip-download", action="store_true", help="do not run the source loaders; build from the existing store")
    p.add_argument("--claims", type=Path, default=None, help=f"claims bundle to merge (default: {DEFAULT_CLAIMS_PATH} when it exists)")
    p.add_argument("--store", type=Path, default=None, help=f"evidence store (default {config.STORE_PATH})")
    p.add_argument("--out", type=Path, default=None, help=f"graph.json path (default {config.GRAPH_PATH})")
    p.add_argument("--report", type=Path, default=None, help=f"build report path (default {DEFAULT_REPORT_PATH})")
    p.add_argument("--indent", type=int, default=None, help="pretty-print graph.json with this indent (default: compact; the real graph is ~40 MB compact vs ~65 MB at indent 2)")
    return p


def run_loaders(store: Store, seeds: dict[str, Any], *, offline: bool, timings: dict[str, float]) -> dict[str, dict[str, Any]]:
    """Run every loader in :data:`LOADERS`; returns ``{module: {"status": ..., "summary": ...}}``
    where ``summary`` is the dict the loader returned (what its thresholds removed, per-step
    timings, seed resolution, ...), so the build report makes the safeguards auditable. Raises on a
    loader error."""
    outcome: dict[str, dict[str, Any]] = {}
    for module_name, with_seeds in LOADERS:
        t0 = time.perf_counter()
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            outcome[module_name] = {"status": f"missing: {exc}"}
            print(f"[export] loader {module_name} not available, skipped ({exc})", file=sys.stderr)
            continue
        if with_seeds:
            summary = module.load(store, seeds, offline=offline)
        else:
            summary = module.load(store, offline=offline)
        timings[module_name] = round(time.perf_counter() - t0, 3)
        outcome[module_name] = {"status": "ok", "summary": summary}
    return outcome


def _load_claims(path: Path | None) -> tuple[ClaimsBundle | None, str]:
    if path is None:
        if not DEFAULT_CLAIMS_PATH.is_file():
            return None, "none"
        path = DEFAULT_CLAIMS_PATH
    return ClaimsBundle.load(path), str(path)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.offline:
        os.environ[ENV_OFFLINE] = "1"
    offline = os.environ.get(ENV_OFFLINE, "").strip().lower() in {"1", "true", "yes", "on"}
    out_path = Path(args.out) if args.out else config.GRAPH_PATH
    report_path = Path(args.report) if args.report else DEFAULT_REPORT_PATH
    store_path = Path(args.store) if args.store else config.STORE_PATH
    timings: dict[str, float] = {}
    t_start = time.perf_counter()

    seeds = config.load_seeds("cluster")
    seeds["mechanisms"] = config.load_seeds("mechanisms")

    try:
        bundle, claims_source = _load_claims(args.claims)
    except (ValidationError, ValueError, OSError) as exc:
        print(f"[export] claims bundle invalid: {exc}", file=sys.stderr)
        return EXIT_CLAIMS_INVALID

    with Store(store_path) as store:
        loaders: dict[str, dict[str, Any]] = {}
        if not args.skip_download:
            try:
                loaders = run_loaders(store, seeds, offline=offline, timings=timings)
            except Exception as exc:  # noqa: BLE001 - the loader's own message is the finding
                print(f"[export] loader failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                return EXIT_LOADER_FAILED
        else:
            loaders = {name: {"status": "skipped (--skip-download)"} for name, _ in LOADERS}

        t0 = time.perf_counter()
        try:
            result = build(store, seeds, bundle)
        except ValidationError as exc:
            print(f"[export] graph failed validation:\n{exc}", file=sys.stderr)
            return EXIT_GRAPH_INVALID
        timings["build"] = round(time.perf_counter() - t0, 3)
        store_counts = store.counts()

    t0 = time.perf_counter()
    result.graph.dump(out_path, indent=args.indent)
    try:
        Graph.load(out_path)
    except ValidationError as exc:
        print(f"[export] written graph does not load back through the strict contract:\n{exc}", file=sys.stderr)
        return EXIT_GRAPH_INVALID
    timings["write_and_reload"] = round(time.perf_counter() - t0, 3)
    timings["total"] = round(time.perf_counter() - t_start, 3)

    report = {
        **result.report,
        "graph_path": str(out_path),
        "store_path": str(store_path),
        "claims_bundle": claims_source,
        "offline": offline,
        "loaders": loaders,
        "store_counts": store_counts,
        "source_versions": [s.model_dump(mode="json") for s in result.graph.meta.sources],
        "timings_seconds": timings,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")

    counts = result.report["counts"]
    print(f"[export] wrote {out_path}")
    print(f"[export] nodes {counts['nodes']} {counts['nodes_by_type']}")
    print(f"[export] edges {counts['edges']} {counts['edges_by_relation']}")
    print(f"[export] claims {counts['claims']}, coverage entities {counts['coverage']['entities']} ({counts['coverage']['zero_hit_searches']} zero-hit searches)")
    print(f"[export] mechanisms: {result.report['mechanisms']['by_rule']}")
    print(f"[export] report {report_path} ({timings['total']} s)")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
