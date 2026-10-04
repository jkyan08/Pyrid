"""Graph logic orchestrator (README section 6): similarity -> Leiden clustering -> rules.

:func:`apply_graph_logic` takes the BUILT :class:`pipeline.models.Graph` (``pipeline.graph.build``
output: backbone, mechanisms, claims) and returns a new, validated Graph with

* the primary weighting's ``similar_to`` edges (:mod:`pipeline.graph.similarity`),
* ``clusters`` replaced by the Leiden partition over Disease nodes, run at every weighting of
  ``pipeline/seeds/logic.yaml`` (:mod:`pipeline.graph.cluster`; ``robust`` = held across all);
  the penalised pairs (score < 0, :func:`pipeline.graph.similarity.compute_repulsions`) form a
  negative layer so conflicting diseases repel instead of merely losing their edge,
* the rule edges (:mod:`pipeline.graph.rules`: ``direction_mismatch`` -> ``inferred_unsupported``,
  ``direction_match_candidate`` -> labelled hypothesis).

Every emitted edge is ``evidence_class: inferred`` with confidence <= 0.5 and the full breakdown in
``confidence_basis``; ids continue the graph's ``e_NNNNNN`` sequence in sorted draft order
(``relation, source, target, sources[0].id``), so the same input gives byte-identical output.
Nothing in this module names a gene, disease, drug or identifier: the weights come from
``logic.yaml`` and the ground-truth evaluation reuses ``tests/test_ground_truth.py`` itself.

Inputs
------
``seeds``
    The ``logic.yaml`` mapping (``weightings``, ``similarity``, ``conflict``, ``clustering``,
    ``rules``, ``candidates``). ``None``, or a mapping without any of those keys (``export.py``
    passes the *cluster* seeds), loads ``pipeline/seeds/logic.yaml`` through
    :func:`pipeline.config.load_seeds`; a ``logic`` sub-mapping is honoured too.
``today``
    The build date written into ``sources[].date`` of every emitted edge. Defaults to the date of
    ``graph.meta.built_at`` (not the wall clock), so re-running logic on a build is reproducible.
``penalties_enabled``
    ``False`` sets every ``conflict`` weight to 0 (the README section 6 ablation: phenotype and
    pathway only). Nothing else changes.

Idempotence
-----------
Edges emitted by an earlier logic run are recognised by their ``sources[].id`` prefix
(``similarity:`` / ``rules:``) and dropped before the run, and ``clusters`` is replaced, so
``apply_graph_logic(apply_graph_logic(g))`` equals ``apply_graph_logic(g)``.

Evaluation
----------
:func:`evaluate_ground_truth` runs ground-truth tests 1, 2, 3, 5 and 7 of
``tests/test_ground_truth.py`` directly on a Graph (the test functions take the graph as their only
argument; the ``xfail`` marker is inert outside pytest) and reports ``{test_id: {"pass", "detail"}}``.
:func:`ablation` applies the logic with penalties on and off and evaluates tests 1-3 both ways.

CLI::

    uv run python -m pipeline.graph.logic --in data/build/graph.json --out <path> [--ablation] [--today YYYY-MM-DD]

writes the graph to ``--out`` (re-read through the strict file contract), prints the run report,
the ground-truth evaluation and, with ``--ablation``, the ablation as JSON, and also writes
``data/build/logic_report.json`` when ``--out`` lies under ``data/build``.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import re
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline.graph import cluster as cluster_mod
from pipeline.graph import rules as rules_mod
from pipeline.graph import similarity as sim_mod
from pipeline.models import Edge, Graph, NodeType

__all__ = [
    "ABLATION_TEST_IDS",
    "EDGE_ID_FORMAT",
    "GROUND_TRUTH_MODULE",
    "GROUND_TRUTH_TEST_IDS",
    "LOGIC_CONFIG_KEYS",
    "LOGIC_SEED",
    "LOGIC_SOURCE_PREFIXES",
    "REPORT_FILENAME",
    "LogicResult",
    "ablation",
    "apply_graph_logic",
    "assign_edge_ids",
    "build_date",
    "disable_penalties",
    "evaluate_ground_truth",
    "load_logic_config",
    "main",
    "mechanism_disease_clusters",
    "next_edge_number",
    "run_graph_logic",
    "strip_logic_edges",
]

LOGIC_SEED = "logic"  # pipeline/seeds/logic.yaml
LOGIC_CONFIG_KEYS = ("weightings", "similarity", "conflict", "clustering", "rules", "candidates")
EDGE_ID_FORMAT = "e_{:06d}"
_EDGE_ID = re.compile(r"^e_(\d+)$")
# sources[].id prefixes of the edges this module adds (similarity:<weighting>, rules:<rule>).
LOGIC_SOURCE_PREFIXES: tuple[str, ...] = (f"{sim_mod.MODULE}:", f"{rules_mod.SOURCE_MODULE}:")
REPORT_FILENAME = "logic_report.json"

GROUND_TRUTH_MODULE = "tests.test_ground_truth"
GROUND_TRUTH_TEST_IDS: tuple[str, ...] = ("1", "2", "3", "5", "7")  # 4 needs claims, 6 needs the API
ABLATION_TEST_IDS: tuple[str, ...] = ("1", "2", "3")
_TEST_NAME = re.compile(r"^test_(\d+)_")
_DETAIL_LIMIT = 600


# --------------------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------------------


def load_logic_config(seeds: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The effective ``logic.yaml`` mapping (deep-copied, never shared with the caller).

    ``seeds`` is used as-is when it carries any of :data:`LOGIC_CONFIG_KEYS`; a mapping with a
    ``logic`` sub-mapping yields that; anything else (``None``, the cluster seeds) loads
    ``pipeline/seeds/logic.yaml``. ``weightings`` must be a non-empty list of mappings with
    non-empty, pairwise distinct ``name`` values.
    """
    cfg: Mapping[str, Any] | None = None
    if isinstance(seeds, Mapping):
        if any(k in seeds for k in LOGIC_CONFIG_KEYS):
            cfg = seeds
        elif isinstance(seeds.get(LOGIC_SEED), Mapping):
            cfg = seeds[LOGIC_SEED]
    if cfg is None:
        from pipeline.config import load_seeds

        cfg = load_seeds(LOGIC_SEED)
    out = copy.deepcopy(dict(cfg))
    weightings = out.get("weightings")
    if weightings is None:
        weightings = [dict(sim_mod.DEFAULT_WEIGHTING)]
    if not isinstance(weightings, list) or not weightings or not all(isinstance(w, Mapping) for w in weightings):
        raise ValueError("logic config 'weightings' must be a non-empty list of {name, w_m, w_p, w_c} mappings")
    out["weightings"] = [dict(w) for w in weightings]
    # Names key the per-weighting similarity/repulsion caches of run_graph_logic and the edges'
    # sources[].id, so every weighting needs one and no two may share it (a duplicate would make
    # the second weighting silently reuse the first one's edges and `robust` meaningless).
    names = [str(w.get("name") or "").strip() for w in out["weightings"]]
    if any(not name for name in names):
        raise ValueError("every logic config weighting needs a non-empty 'name' (it becomes the similarity edges' source id)")
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise ValueError(f"logic config 'weightings' names must be unique, duplicated: {duplicated}")
    return out


def disable_penalties(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``cfg`` with every ``conflict`` weight set to 0 (the ablation)."""
    out = copy.deepcopy(dict(cfg))
    weights = dict(out.get("conflict") or {})
    for key in set(sim_mod.DEFAULT_CONFLICT) | set(weights):
        weights[key] = 0.0
    out["conflict"] = weights
    return out


def _effective_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """What each module actually used (its section merged over its documented defaults)."""
    return {
        "weightings": [dict(w) for w in cfg["weightings"]],
        "similarity": sim_mod.similarity_cfg(cfg),
        "conflict": sim_mod.conflict_cfg(cfg),
        "candidates": sim_mod.candidates_cfg(cfg),
        "clustering": cluster_mod.clustering_config(cfg),
        "rules": rules_mod.rules_config(cfg),
    }


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------


def build_date(graph: Graph, today: str | None = None) -> str:
    """``today`` when given, else the date of ``graph.meta.built_at`` (ISO ``YYYY-MM-DD``)."""
    if today is not None:
        if not isinstance(today, str) or not today.strip():
            raise ValueError("today must be a non-empty ISO 8601 date string")
        return today.strip()
    return datetime.fromisoformat(graph.meta.built_at).date().isoformat()


def _is_logic_edge(edge: Edge) -> bool:
    return any(src.id.startswith(LOGIC_SOURCE_PREFIXES) for src in edge.sources)


def strip_logic_edges(graph: Graph) -> tuple[list[Edge], int]:
    """``(edges kept, number removed)``: drops edges an earlier logic run emitted (recognised by
    the ``similarity:`` / ``rules:`` prefix of a ``sources[].id``)."""
    kept = [e for e in graph.edges if not _is_logic_edge(e)]
    return kept, len(graph.edges) - len(kept)


def next_edge_number(edges: Sequence[Edge] | Sequence[Mapping[str, Any]]) -> int:
    """1 + the largest ``N`` among ``e_N`` edge ids (1 when no edge has that shape)."""
    highest = 0
    for e in edges:
        edge_id = e.id if isinstance(e, Edge) else str(e.get("id", ""))
        m = _EDGE_ID.match(edge_id)
        if m is not None:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def _draft_key(draft: Mapping[str, Any]) -> tuple[str, str, str, str]:
    sources = draft.get("sources") or [{}]
    return (str(draft.get("relation", "")), str(draft.get("source", "")), str(draft.get("target", "")), str(sources[0].get("id", "")))


def assign_edge_ids(drafts: Sequence[Mapping[str, Any]], start: int) -> list[Edge]:
    """Sorted drafts (:func:`_draft_key`) become :class:`Edge` objects ``e_<start>``, ``e_<start+1>``...
    Each draft is validated on its own, so a bad draft names itself in the error."""
    out: list[Edge] = []
    for offset, draft in enumerate(sorted(drafts, key=_draft_key)):
        payload = {**draft, "id": EDGE_ID_FORMAT.format(start + offset)}
        out.append(Edge.model_validate(payload))
    return out


def _round(value: float, places: int = 6) -> float:
    return round(float(value), places)


# --------------------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------------------


@dataclass
class LogicResult:
    """:func:`run_graph_logic` output: the validated graph plus everything a report needs."""

    graph: Graph
    report: dict[str, Any]
    similarity_edges: list[dict[str, Any]] = field(default_factory=list)  # primary-weighting drafts (no ids)
    rule_edges: list[dict[str, Any]] = field(default_factory=list)  # rule drafts (no ids)
    clusters: list[dict[str, Any]] = field(default_factory=list)  # cluster drafts as written
    repulsions: list[dict[str, Any]] = field(default_factory=list)  # penalised pairs (negative layer), not edges


def _similarity_stats(drafts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scores = sorted(float((d.get("confidence_basis") or {}).get("score", 0.0)) for d in drafts)
    kinds = {"both_mechanism": 0, "one_mechanism": 0, "no_mechanism": 0}
    penalised = 0
    for d in drafts:
        basis = d.get("confidence_basis") or {}
        n_mech = sum(1 for side in ("a", "b") if (basis.get(side) or {}).get("mechanisms"))
        kinds["both_mechanism" if n_mech == 2 else "one_mechanism" if n_mech == 1 else "no_mechanism"] += 1
        if float(basis.get("conflict_penalty", 0.0)) > 0:
            penalised += 1
    return {
        "n_edges": len(drafts),
        "score": {
            "min": _round(scores[0]) if scores else None,
            "median": _round(statistics.median(scores)) if scores else None,
            "max": _round(scores[-1]) if scores else None,
        },
        "confidence_max": _round(max((float(d.get("confidence", 0.0)) for d in drafts), default=0.0)),
        "by_kind": kinds,
        "edges_with_conflict_penalty": penalised,
    }


def mechanism_disease_clusters(graph: Graph) -> list[dict[str, Any]]:
    """One row per Disease -> Mechanism ``has_mechanism`` edge: the disease, its mechanism, the
    edge's origin and the cluster the disease landed in (``None`` when unclustered). Read from
    the graph only; no name is typed here."""
    index = graph.node_index()
    cluster_of: dict[str, tuple[str, int]] = {}
    for cl in graph.clusters:
        for member in cl.members:
            cluster_of[member] = (cl.id, len(cl.members))
    rows: list[dict[str, Any]] = []
    for e in graph.edges:
        if e.relation != sim_mod.REL_HAS_MECHANISM:
            continue
        s, t = index.get(e.source), index.get(e.target)
        if s is None or t is None:
            continue
        if s.type == NodeType.Disease and t.type == NodeType.Mechanism:
            disease, mech = s, t
        elif t.type == NodeType.Disease and s.type == NodeType.Mechanism:
            disease, mech = t, s
        else:
            continue
        cl = cluster_of.get(disease.id)
        rows.append(
            {
                "disease": disease.id,
                "label": disease.label,
                "mechanism": mech.id,
                "origin": e.attributes.get("origin"),
                "cluster": cl[0] if cl else None,
                "cluster_size": cl[1] if cl else None,
            }
        )
    rows.sort(key=lambda r: (r["mechanism"], r["disease"]))
    return rows


def run_graph_logic(
    graph: Graph,
    seeds: Mapping[str, Any] | None = None,
    *,
    today: str | None = None,
    penalties_enabled: bool = True,
) -> LogicResult:
    """Similarity, clustering and rules on ``graph`` (module docstring); the input is not modified.

    Returns a :class:`LogicResult` whose ``graph`` passed ``Graph.model_validate`` AND a strict
    ``model_dump_json`` / ``model_validate_json`` round trip (the file-level contract), and whose
    ``report`` records the effective configuration, counts, score statistics, the rules report,
    the cluster summary, the edge-id range and timings.
    """
    t_start = time.perf_counter()
    timings: dict[str, float] = {}
    cfg = load_logic_config(seeds)
    if not penalties_enabled:
        cfg = disable_penalties(cfg)
    weightings: list[dict[str, Any]] = cfg["weightings"]
    date = build_date(graph, today)

    kept, n_stripped = strip_logic_edges(graph)
    base = graph.model_copy(update={"edges": kept, "clusters": []})

    # similarity: decode the graph once, score every weighting from the same features
    t0 = time.perf_counter()
    features = sim_mod.build_features(base, cfg)
    cache: dict[str, list[dict[str, Any]]] = {}
    repulsion_cache: dict[str, list[dict[str, Any]]] = {}

    def sim_fn(g: Graph, weighting: Mapping[str, Any], c: Mapping[str, Any] | None, *, today: str) -> list[dict[str, Any]]:
        name = str(weighting.get("name", ""))
        if name not in cache:
            cache[name] = sim_mod.compute_similarity(g, weighting, c, today=today, features=features)
        return cache[name]

    def repulsion_fn(g: Graph, weighting: Mapping[str, Any], c: Mapping[str, Any] | None, *, today: str) -> list[dict[str, Any]]:
        name = str(weighting.get("name", ""))
        if name not in repulsion_cache:
            repulsion_cache[name] = sim_mod.compute_repulsions(g, weighting, c, features=features)
        return repulsion_cache[name]

    similarity_edges = sim_fn(base, weightings[0], cfg, today=date)
    repulsions = repulsion_fn(base, weightings[0], cfg, today=date)
    timings["similarity"] = _round(time.perf_counter() - t0, 3)

    # clusters: every weighting, primary first; penalised pairs form the negative layer
    t0 = time.perf_counter()
    clusters = cluster_mod.build_clusters(base, weightings, cfg, sim_fn, today=date, repulsion_fn=repulsion_fn)
    timings["clustering"] = _round(time.perf_counter() - t0, 3)

    # rules
    t0 = time.perf_counter()
    rules_result = rules_mod.run_rules(base, cfg, today=date)
    timings["rules"] = _round(time.perf_counter() - t0, 3)

    # ids, assembly, validation
    t0 = time.perf_counter()
    start = next_edge_number(kept)
    new_edges = assign_edge_ids([*similarity_edges, *rules_result.drafts], start)
    payload = base.model_dump(mode="json")
    payload["edges"] = payload["edges"] + [e.model_dump(mode="json") for e in new_edges]
    payload["clusters"] = [dict(cl) for cl in clusters]
    validated = Graph.model_validate(payload)
    out_graph = Graph.model_validate_json(validated.model_dump_json(), strict=True)
    timings["validate"] = _round(time.perf_counter() - t0, 3)
    timings["total"] = _round(time.perf_counter() - t_start, 3)

    report: dict[str, Any] = {
        "today": date,
        "penalties_enabled": penalties_enabled,
        "config": _effective_config(cfg),
        "edges_stripped_from_input": n_stripped,
        "edge_ids": {
            "continued_from": EDGE_ID_FORMAT.format(start - 1) if start > 1 else None,
            "first": new_edges[0].id if new_edges else None,
            "last": new_edges[-1].id if new_edges else None,
            "n_new": len(new_edges),
        },
        "similarity": {
            "weighting": str(weightings[0].get("name", "")),
            "weightings_run": [str(w.get("name", "")) for w in weightings],
            "candidates": len(features.candidates),
            "diseases_total": features.n_diseases_total,
            **_similarity_stats(similarity_edges),
            "repulsions": {
                "n_pairs": len(repulsions),
                "score_min": _round(min((float(r["score"]) for r in repulsions), default=0.0)),
                "by_component": {
                    k: sum(1 for r in repulsions if float((r.get("conflict_components") or {}).get(k, 0.0)) > 0)
                    for k in sim_mod.DEFAULT_CONFLICT
                },
            },
        },
        "clusters": cluster_mod.cluster_report(clusters),
        "rules": {"report": rules_result.report, "summary": rules_mod.summarize(rules_result.drafts)},
        "mechanism_disease_clusters": mechanism_disease_clusters(out_graph),
        "counts": {
            "nodes": len(out_graph.nodes),
            "edges": len(out_graph.edges),
            "claims": len(out_graph.claims),
            "clusters": len(out_graph.clusters),
            "similar_to_edges": len(similarity_edges),
            "rule_edges": len(rules_result.drafts),
        },
        "timings": timings,
    }
    return LogicResult(out_graph, report, list(similarity_edges), list(rules_result.drafts), [dict(cl) for cl in clusters], list(repulsions))


def apply_graph_logic(
    graph: Graph,
    seeds: Mapping[str, Any] | None = None,
    *,
    today: str | None = None,
    penalties_enabled: bool = True,
) -> Graph:
    """The graph with similarity edges, Leiden clusters and rule edges (see :func:`run_graph_logic`,
    which also returns the report). This is what ``pipeline.export`` calls."""
    return run_graph_logic(graph, seeds, today=today, penalties_enabled=penalties_enabled).graph


# --------------------------------------------------------------------------------------
# ground-truth evaluation (reuses tests/test_ground_truth.py, never copies it)
# --------------------------------------------------------------------------------------


def _ground_truth_tests() -> dict[str, Callable[[Graph], None]]:
    """``{test number: function}`` from ``tests/test_ground_truth.py`` (``test_<n>_...``), imported
    lazily; the repo root is added to ``sys.path`` when ``tests`` is not importable."""
    try:
        module = importlib.import_module(GROUND_TRUTH_MODULE)
    except ImportError:
        from pipeline.config import REPO_ROOT

        root = str(REPO_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        module = importlib.import_module(GROUND_TRUTH_MODULE)
    found: dict[str, Callable[[Graph], None]] = {}
    for name in dir(module):
        m = _TEST_NAME.match(name)
        if m is not None and callable(getattr(module, name)):
            found[m.group(1)] = getattr(module, name)
    return found


def _first_lines(text: str, limit: int = _DETAIL_LIMIT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _outcome_exceptions() -> tuple[type[BaseException], ...]:
    """``pytest.fail`` / ``pytest.skip`` raise ``BaseException`` subclasses; catch exactly those."""
    import pytest

    return (pytest.fail.Exception, pytest.skip.Exception)


def evaluate_ground_truth(graph: Graph, test_ids: Sequence[str] = GROUND_TRUTH_TEST_IDS) -> dict[str, dict[str, Any]]:
    """Run ground-truth tests ``test_ids`` (default 1, 2, 3, 5, 7) of ``tests/test_ground_truth.py``
    on ``graph`` and return ``{test_id: {"pass": bool, "detail": str, "test": <function name>}}``.

    The test functions take the graph as their only parameter, so they are called directly: an
    ``AssertionError`` or ``pytest.fail`` is a failure (its message is the detail), any other
    exception is reported as an error, and a clean return is a pass.
    """
    tests = _ground_truth_tests()
    outcomes = _outcome_exceptions()
    result: dict[str, dict[str, Any]] = {}
    for test_id in test_ids:
        fn = tests.get(str(test_id))
        if fn is None:
            result[str(test_id)] = {"pass": False, "detail": f"no test_{test_id}_* in {GROUND_TRUTH_MODULE}", "test": None}
            continue
        try:
            fn(graph)
        except AssertionError as exc:
            entry = {"pass": False, "detail": "FAIL: " + _first_lines(str(exc) or "assertion failed")}
        except outcomes as exc:  # pytest.fail(...) inside a conftest helper
            entry = {"pass": False, "detail": "FAIL: " + _first_lines(str(exc))}
        except Exception as exc:  # noqa: BLE001 - the evaluation must report, not crash
            entry = {"pass": False, "detail": f"ERROR: {type(exc).__name__}: {_first_lines(str(exc))}"}
        else:
            entry = {"pass": True, "detail": f"PASS: {fn.__name__}"}
        entry["test"] = fn.__name__
        result[str(test_id)] = entry
    return result


def ablation(
    graph_before_logic: Graph,
    seeds: Mapping[str, Any] | None = None,
    *,
    today: str | None = None,
    test_ids: Sequence[str] = ABLATION_TEST_IDS,
) -> dict[str, Any]:
    """README section 6 ablation: apply the logic with the conflict penalties on and off, evaluate
    tests 1-3 each way. ``requires_penalties`` is true for a test that passes only with them."""
    on = run_graph_logic(graph_before_logic, seeds, today=today, penalties_enabled=True)
    off = run_graph_logic(graph_before_logic, seeds, today=today, penalties_enabled=False)
    eval_on = evaluate_ground_truth(on.graph, test_ids)
    eval_off = evaluate_ground_truth(off.graph, test_ids)
    summary = {
        tid: {
            "penalties_on": eval_on[tid]["pass"],
            "penalties_off": eval_off[tid]["pass"],
            "requires_penalties": eval_on[tid]["pass"] and not eval_off[tid]["pass"],
        }
        for tid in map(str, test_ids)
    }
    return {
        "tests": [str(t) for t in test_ids],
        "summary": summary,
        "all_pass_with_penalties": all(v["penalties_on"] for v in summary.values()),
        "all_pass_without_penalties": all(v["penalties_off"] for v in summary.values()),
        "penalties_on": {
            "evaluation": eval_on,
            "conflict_weights": on.report["config"]["conflict"],
            "similarity": on.report["similarity"],
            "clusters": on.report["clusters"],
            "mechanism_disease_clusters": on.report["mechanism_disease_clusters"],
        },
        "penalties_off": {
            "evaluation": eval_off,
            "conflict_weights": off.report["config"]["conflict"],
            "similarity": off.report["similarity"],
            "clusters": off.report["clusters"],
            "mechanism_disease_clusters": off.report["mechanism_disease_clusters"],
        },
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _under(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipeline.graph.logic",
        description="Apply similarity, Leiden clustering and the rule layer to a built graph.json, then evaluate the ground-truth tests.",
    )
    p.add_argument("--in", dest="path", required=True, help="built graph.json to read (pipeline.graph.build output)")
    p.add_argument("--out", required=True, help="where to write the graph with clusters and inferred edges")
    p.add_argument("--ablation", action="store_true", help="also run with the conflict penalties off and compare tests 1-3")
    p.add_argument("--no-penalties", action="store_true", help="write the penalties-off graph instead (ablation variant)")
    p.add_argument("--today", default=None, help="build date (ISO 8601) for sources[].date; default: the graph's meta.built_at date")
    p.add_argument("--indent", type=int, default=2, help="JSON indent for the written graph (default 2)")
    p.add_argument("--report", default=None, help=f"report path (default: data/build/{REPORT_FILENAME} when --out is under data/build)")
    return p


def main(argv: list[str] | None = None) -> int:
    from pydantic import ValidationError

    from pipeline.config import BUILD_DIR

    args = _parser().parse_args(argv)
    in_path, out_path = Path(args.path), Path(args.out)
    graph = Graph.load(in_path)
    try:
        result = run_graph_logic(graph, today=args.today, penalties_enabled=not args.no_penalties)
    except ValidationError as exc:
        print(f"[logic] graph failed validation after graph logic:\n{exc}", file=sys.stderr)
        return 2
    result.graph.dump(out_path, indent=args.indent)
    try:
        Graph.load(out_path)
    except ValidationError as exc:
        print(f"[logic] written graph does not load back through the strict contract:\n{exc}", file=sys.stderr)
        return 2

    output: dict[str, Any] = {
        "in": str(in_path),
        "out": str(out_path),
        "today": result.report["today"],
        "ground_truth": evaluate_ground_truth(result.graph),
        "report": result.report,
    }
    if args.ablation:
        output["ablation"] = ablation(graph, today=args.today)

    report_path = Path(args.report) if args.report else (BUILD_DIR / REPORT_FILENAME if _under(out_path, BUILD_DIR) else None)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        output["report_written_to"] = str(report_path)
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
