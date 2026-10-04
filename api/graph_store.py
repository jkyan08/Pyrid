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

/search is hybrid: lexical label/synonym/token matching, plus (when
data/build/search_index.json matches this graph and an embeddings key is set)
semantic matching of natural-language queries (api/semantic.py), plus "related"
diseases reached from the symptoms a query matches (has_phenotype, expanded to
more specific HPO terms through is_a, weighted by information content).
"""

from __future__ import annotations

import json
import math
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


# semantic matching: cosine floor, and how far below the best hit a result may fall
_SEM_MIN_SIM = 0.38
_SEM_BAND = 0.12
# query words that say which kind of entity the user is after (a small nudge, not a filter)
_INTENT = {
    ("drug", "drugs", "medicine", "medicines", "medication", "medications", "treatment", "treatments",
     "therapy", "therapies", "inhibitor", "inhibitors", "treat"): {"Drug": 0.08, "Mechanism": 0.04},
    ("group", "groups", "foundation", "organization", "organisation", "support", "families", "family",
     "community", "charity", "advocacy", "registry"): {"PatientGroup": 0.08, "Registry": 0.08},
    ("gene", "genes", "mutation", "mutations", "variant", "variants"): {"Gene": 0.05, "Variant": 0.04},
    ("trial", "trials", "study", "studies"): {"Study": 0.08},
    ("symptom", "symptoms", "sign", "signs", "keeps", "getting", "has", "having"): {"Phenotype": 0.03},
}


_ID_LIKE_LABEL = re.compile(r"^(CHEMBL|DB)\d+$|[\[\]{}]|\(\d")


def _prominence(node: Mapping[str, Any]) -> float:
    """Small semantic-ranking adjustment so a lay query surfaces approved drugs before
    unnamed research compounds (labels like 'CHEMBL480411' or IUPAC strings)."""
    if node.get("type") != "Drug":
        return 0.0
    props = node.get("props") or {}
    if props.get("is_approved"):
        return 0.06
    if (props.get("max_phase") or 0) >= 2:
        return 0.02
    label = node.get("label", "")
    return -0.1 if _ID_LIKE_LABEL.search(label) or len(label) > 40 else -0.03


def _semantic_score(sim: float) -> float:
    """Map cosine similarity to the search score scale (exact label = 1.0, free text 0.4-0.8)."""
    return round(max(0.3, min(0.88, 0.3 + (sim - 0.35) * 2.0)), 4)


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

        # related-disease expansion: phenotype -> diseases annotated with it; phenotype -> child terms
        self._diseases_by_phenotype: dict[str, set[str]] = defaultdict(set)
        self._phenotype_children: dict[str, set[str]] = defaultdict(set)
        self._disease_children: dict[str, int] = defaultdict(int)
        self._phenotype_count: dict[str, int] = defaultdict(int)
        for edge in self.edges:
            src = self.nodes_by_id.get(edge["source"], {})
            dst = self.nodes_by_id.get(edge["target"], {})
            if edge["relation"] == "has_phenotype" and src.get("type") == "Disease":
                self._diseases_by_phenotype[edge["target"]].add(edge["source"])
                self._phenotype_count[edge["source"]] += 1
            elif edge["relation"] == "is_a" and src.get("type") == dst.get("type") == "Phenotype":
                self._phenotype_children[edge["target"]].add(edge["source"])
            elif edge["relation"] == "is_a" and src.get("type") == dst.get("type") == "Disease":
                self._disease_children[edge["target"]] += 1

        # semantic search (attached by create_app; see api/semantic.py)
        self.semantic = None
        self.embedder = None

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

        by_id = {r["id"]: r for r in results}
        self._merge_semantic(query, by_id)
        self._merge_related_diseases(by_id)
        results = list(by_id.values())

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

    # ---- semantic + related ------------------------------------------------

    def semantic_ready(self) -> bool:
        idx = self.semantic
        return bool(idx is not None and self.embedder is not None
                    and idx.graph_built_at == (self.data.get("meta") or {}).get("built_at"))

    def _merge_semantic(self, query: str, by_id: dict[str, dict[str, Any]]) -> None:
        """Add nodes that match the query's meaning. A lexical hit keeps its kind unless the
        semantic score beats it."""
        q = query.strip()
        if len(q) < 3 or not self.semantic_ready():
            return
        vector = self.embedder(q, self.semantic.model)
        if vector is None:
            return
        hits = self.semantic.query(vector, k=40, min_sim=_SEM_MIN_SIM)
        if not hits:
            return
        top = hits[0][1]
        # When the query names something exactly ("ruxolitinib"), meaning-alikes stay below the
        # name matches instead of tying with them.
        ceiling = 0.84 if any(r["score"] >= 0.95 for r in by_id.values()) else 0.9
        words = _tokens(q)
        boosts: dict[str, float] = {}
        for keys, bump in _INTENT.items():
            if words & set(keys):
                for t, b in bump.items():
                    boosts[t] = max(boosts.get(t, 0.0), b)
        for nid, sim in hits:
            node = self.nodes_by_id.get(nid)
            if node is None or sim < top - _SEM_BAND:
                continue
            score = min(ceiling, _semantic_score(sim) + boosts.get(node.get("type", ""), 0.0) + _prominence(node))
            prev = by_id.get(nid)
            if prev is not None and prev["score"] >= score:
                continue
            by_id[nid] = {
                "id": nid,
                "label": node.get("label", nid),
                "type": node.get("type", ""),
                "matched_on": q,
                "match_kind": "semantic",
                "resolution": f"related in meaning to your search → {node.get('label', nid)}",
                "score": float(score),
            }

    def _merge_related_diseases(self, by_id: dict[str, dict[str, Any]]) -> None:
        """Diseases that have the symptoms the query matched (Devon's journey: symptoms in
        plain words -> HPO terms -> the diseases that show them).

        Each matched phenotype counts for diseases annotated with it or with a more specific
        term (is_a, two levels). Its weight is match strength x information content, so a
        distinctive, well-matched symptom outweighs a common or loosely matched one. A disease's
        score reflects how much of the query's symptom weight it covers, discounted when it
        covers only one of several symptoms, when it is a grouping term (2+ subtypes), or when
        it is annotated with so many phenotypes that it matches almost anything."""
        matched = [r for r in by_id.values() if r["type"] == "Phenotype" and r["score"] >= 0.42]
        matched = sorted(matched, key=lambda r: -r["score"])[:10]
        if not matched:
            return
        weight = {r["id"]: r["score"] * max(float((self.nodes_by_id[r["id"]].get("props") or {}).get("ic") or 0.0), 0.5)
                  for r in matched}
        total = sum(weight.values())
        best_phen = matched[0]["score"]
        hits: dict[str, set[str]] = defaultdict(set)  # disease -> matched phenotype ids
        for r in matched:
            terms, frontier = {r["id"]}, {r["id"]}
            for _ in range(2):
                frontier = {c for p in frontier for c in self._phenotype_children.get(p, ())}
                terms |= frontier
            for t in terms:
                for d in self._diseases_by_phenotype.get(t, ()):
                    hits[d].add(r["id"])
        labels = {r["id"]: r["label"] for r in matched}
        for d, phens in hits.items():
            coverage = sum(weight[p] for p in phens) / total
            breadth = min(1.0, len(phens) / min(2, len(matched)))
            score = 0.3 + 0.5 * coverage * breadth + 0.04 * (len(phens) - 1)
            score *= self._disease_specificity(d)
            score = round(min(0.86, score, best_phen + 0.15), 4)
            if score < 0.45:
                continue
            prev = by_id.get(d)
            if prev is not None and prev["score"] >= score:
                continue
            node = self.nodes_by_id[d]
            names = ", ".join(sorted(labels[p] for p in phens))
            by_id[d] = {
                "id": d,
                "label": node.get("label", d),
                "type": node.get("type", "Disease"),
                "matched_on": names,
                "match_kind": "related",
                "resolution": f"has {len(phens)} of the features you searched for: {names}",
                "score": float(score),
            }

    def _disease_specificity(self, disease_id: str) -> float:
        """1.0 for a specific disease; less for grouping terms and catch-all annotations."""
        factor = 1.0
        if self._disease_children.get(disease_id, 0) >= 2:
            factor *= 0.8
        n_phen = self._phenotype_count.get(disease_id, 0)
        if n_phen > 60:
            factor *= 1.0 / (1.0 + math.log10(n_phen / 60))
        return factor
