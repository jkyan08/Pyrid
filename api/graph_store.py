"""Graph loading + in-memory indexes for the Atlas API.

Loader fallback chain (api/CONTRACT.md "Graph loading"), first existing wins:

1. $ATLAS_GRAPH_PATH (env override; skipped when empty or missing)
2. <repo>/data/build/graph.json          (real, M1+)
3. <repo>/data/build/graph.mock.json     (M0 mock)
4. <repo>/api/fixtures/graph.mock.json   (API dev fixture)

Explanations: $ATLAS_EXPLANATIONS_PATH -> data/build/explanations.json
-> api/fixtures/explanations.mock.json (or None — explanations are optional).

Indexes are built once at load: nodes/edges/claims/clusters by id, edges by
endpoint, cluster membership, and a token index for /search. No numpy/polars.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent

_STOPWORDS = {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "with"}

# search ordering among equal-quality matches: core biological entities first,
# then communities/assets, then people, literature last
_SEARCH_TYPE_RANK = {
    "Disease": 0,
    "Gene": 0,
    "Drug": 0,
    "Variant": 1,
    "Mechanism": 1,
    "Phenotype": 1,
    "Pathway": 2,
    "PatientGroup": 2,
    "Registry": 2,
    "Study": 2,
    "Investigator": 3,
    "Paper": 4,
    "Claim": 4,
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS}


def _resolve(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _graph_candidates(root: Path, env: Mapping[str, str]) -> list[Path]:
    candidates: list[Path] = []
    override = (env.get("ATLAS_GRAPH_PATH") or "").strip()
    if override:
        candidates.append(Path(override))
    candidates += [
        root / "data" / "build" / "graph.json",
        # demo dataset models the finished product; beats the skeleton mocks,
        # always loses to real build output above ("mock" in name keeps the
        # not-sourced ribbon on)
        root / "api" / "fixtures" / "graph.demo.mock.json",
        root / "data" / "build" / "graph.mock.json",
        root / "api" / "fixtures" / "graph.mock.json",
    ]
    return candidates


def resolve_graph_path(
    repo_root: Path | str | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """Return the first existing graph file in the fallback chain."""
    env = os.environ if env is None else env
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    candidates = _graph_candidates(root, env)
    resolved = _resolve(candidates)
    if resolved is None:
        raise FileNotFoundError(
            "no graph file found; tried: " + ", ".join(str(c) for c in candidates)
        )
    return resolved


def resolve_explanations_path(
    repo_root: Path | str | None = None, env: Mapping[str, str] | None = None
) -> Path | None:
    """Same chain for precomputed explanations; None when nothing exists."""
    env = os.environ if env is None else env
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    candidates: list[Path] = []
    override = (env.get("ATLAS_EXPLANATIONS_PATH") or "").strip()
    if override:
        candidates.append(Path(override))
    candidates += [
        root / "data" / "build" / "explanations.json",
        root / "api" / "fixtures" / "explanations.demo.mock.json",
        root / "api" / "fixtures" / "explanations.mock.json",
    ]
    return _resolve(candidates)


def load_explanations(path: Path | str | None) -> dict[str, dict[str, Any]]:
    """Load precomputed explanations, dropping comment keys. Never raises on None."""
    if path is None:
        return {}
    data = json.loads(Path(path).read_text())
    return {
        key: value
        for key, value in data.items()
        if not key.startswith("_") and isinstance(value, dict)
    }


class GraphStore:
    """The loaded graph plus indexes, all built once at load time."""

    def __init__(self, data: dict[str, Any], path: Path | str):
        self.data = data
        self.path = Path(path)
        self.mode = "mock" if "mock" in str(path) else "real"

        self.nodes: list[dict[str, Any]] = data.get("nodes", [])
        self.edges: list[dict[str, Any]] = data.get("edges", [])
        self.claims: list[dict[str, Any]] = data.get("claims", [])
        self.clusters: list[dict[str, Any]] = data.get("clusters", [])
        self.meta: dict[str, Any] = data.get("meta", {})

        self.nodes_by_id = {n["id"]: n for n in self.nodes}
        self.edges_by_id = {e["id"]: e for e in self.edges}
        self.claims_by_id = {c["id"]: c for c in self.claims}
        self.clusters_by_id = {c["id"]: c for c in self.clusters}

        self.edges_by_endpoint: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in self.edges:
            self.edges_by_endpoint[edge["source"]].append(edge)
            if edge["target"] != edge["source"]:
                self.edges_by_endpoint[edge["target"]].append(edge)

        self._clusters_by_member: dict[str, list[str]] = defaultdict(list)
        for cluster in self.clusters:
            for member in cluster.get("members", []):
                self._clusters_by_member[member].append(cluster["id"])

        # search rows: (node, lowered label, lowered synonyms, token set per text)
        self._search_rows = []
        for node in self.nodes:
            label = node.get("label", "")
            synonyms = node.get("synonyms", []) or []
            texts = [label, *synonyms]
            self._search_rows.append(
                {
                    "node": node,
                    "label": label,
                    "synonyms": synonyms,
                    "token_sets": [(text, _tokens(text)) for text in texts],
                }
            )

    @classmethod
    def load(cls, path: Path | str) -> "GraphStore":
        return cls(json.loads(Path(path).read_text()), path)

    # ---- lookups -----------------------------------------------------------

    def cluster_ids_of(self, node_id: str) -> list[str]:
        return list(self._clusters_by_member.get(node_id, []))

    def coverage_for(self, node_id: str) -> dict[str, Any] | None:
        return (self.meta.get("coverage") or {}).get(node_id)

    def has_supported_edge(self, node_id: str) -> bool:
        return any(
            edge.get("status") == "supported"
            for edge in self.edges_by_endpoint.get(node_id, [])
        )

    def neighbors(self, node_id: str) -> list[dict[str, Any]]:
        """[{edge, node, direction}] for every edge touching node_id."""
        items: list[dict[str, Any]] = []
        for edge in self.edges_by_endpoint.get(node_id, []):
            if edge["source"] == node_id:
                other_id, direction = edge["target"], "out"
            else:
                other_id, direction = edge["source"], "in"
            other = self.nodes_by_id.get(
                other_id,
                {"id": other_id, "type": "Unknown", "label": other_id, "synonyms": [], "props": {}},
            )
            items.append({"edge": edge, "node": other, "direction": direction})
        return items

    def resolve_claims(self, claim_ids: list[str]) -> list[dict[str, Any]]:
        return [self.claims_by_id[cid] for cid in claim_ids if cid in self.claims_by_id]

    # ---- search ------------------------------------------------------------

    def search(self, query: str) -> list[dict[str, Any]]:
        """Label > synonym > free-text token overlap, case-insensitive."""
        q = query.strip().lower()
        if not q:
            return []
        q_tokens = _tokens(q)
        results: list[dict[str, Any]] = []

        for row in self._search_rows:
            node = row["node"]
            label = row["label"]
            best: tuple[float, str, str, str] | None = None  # score, kind, matched_on, resolution

            lowered_label = label.lower()
            # substring matches require len(q) >= 3, mirroring the web fallback
            if q == lowered_label:
                best = (1.0, "label", label, f"matched label '{label}'")
            elif len(q) >= 3 and q in lowered_label:
                best = (0.9, "label", label, f"matched label '{label}'")
            else:
                for synonym in row["synonyms"]:
                    lowered = synonym.lower()
                    if q == lowered:
                        best = (
                            0.95,
                            "synonym",
                            synonym,
                            f"matched synonym '{synonym}' → {label}",
                        )
                        break
                    if len(q) >= 3 and q in lowered and (best is None or best[0] < 0.85):
                        best = (
                            0.85,
                            "synonym",
                            synonym,
                            f"matched synonym '{synonym}' → {label}",
                        )

            if best is None and q_tokens:
                overlap_best: tuple[int, str, set[str]] | None = None
                for text, tokens in row["token_sets"]:
                    overlap = q_tokens & tokens
                    # >=2 token hits, or >=50% of the query, must land — one
                    # shared generic token ("syndrome") is noise, but a
                    # multi-symptom query (Devon) may hit each phenotype with
                    # only part of its tokens
                    if len(overlap) < 2 and len(overlap) / len(q_tokens) < 0.5:
                        continue
                    if overlap_best is None or len(overlap) > overlap_best[0]:
                        overlap_best = (len(overlap), text, overlap)
                if overlap_best is not None:
                    count, text, overlap = overlap_best
                    score = round(0.4 + 0.4 * count / max(len(q_tokens), 1), 4)
                    shared = ", ".join(sorted(overlap))
                    best = (
                        score,
                        "free_text",
                        text,
                        f"free-text match on '{text}' → {label} (shared terms: {shared})",
                    )

            if best is not None:
                score, kind, matched_on, resolution = best
                results.append(
                    {
                        "id": node["id"],
                        "label": label,
                        "type": node.get("type", ""),
                        "matched_on": matched_on,
                        "match_kind": kind,
                        "resolution": resolution,
                        "score": float(score),
                    }
                )

        # Entities outrank literature of equal string quality (user feedback:
        # "papers first instead of the actual proteins"), and the list stays
        # siftable: <=3 papers, <=3 investigators, <=24 results overall.
        results.sort(
            key=lambda r: (-r["score"], _SEARCH_TYPE_RANK.get(r["type"], 2), r["label"])
        )
        # default 5 per type keeps one entity class (e.g. 600 ClinVar variants)
        # from flooding out every other kind of match
        per_type_caps = {"Paper": 3, "Investigator": 3}
        seen_per_type: dict[str, int] = {}
        capped: list[dict[str, Any]] = []
        for r in results:
            count = seen_per_type.get(r["type"], 0)
            if count >= per_type_caps.get(r["type"], 5):
                continue
            seen_per_type[r["type"]] = count + 1
            capped.append(r)
            if len(capped) >= 24:
                break
        return capped
