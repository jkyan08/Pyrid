"""Build ``data/build/search_index.json``: embeddings for natural-language search.

Each searchable node (diseases, phenotypes, genes, drugs, mechanisms, pathways,
studies, patient groups, registries) is embedded once from a short text built
from the graph itself: label, synonyms, description, and for diseases their most
distinctive phenotypes. The API embeds the user's query with the same model and
ranks nodes by cosine similarity (api/semantic.py), so "my son keeps getting
thrush and has thyroid problems" reaches chronic mucocutaneous candidiasis and
autoimmune thyroiditis without sharing any words with them.

Vectors are truncated to ``DIMS`` and renormalised (valid for OpenAI's v3
embedding models), then stored as base64 int8 to keep the file small. The index
records the graph's ``meta.built_at`` so the API only uses it with the graph it
was built from.

Run after ``make data``: ``uv run python -m pipeline.search_index``
(needs ``OPENAI_EMBED_MODEL`` and ``OPENAI_API_KEY`` or ``OPENROUTER_API_KEY``;
embeddings are cached under ATLAS_CACHE_DIR, so re-runs only embed new text).
"""
from __future__ import annotations

import base64
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from pipeline.llm import client

REPO_ROOT = Path(__file__).resolve().parents[1]
GRAPH = REPO_ROOT / "data" / "build" / "graph.json"
OUT = REPO_ROOT / "data" / "build" / "search_index.json"

DIMS = 256
SEARCHABLE = {"Disease", "Phenotype", "Gene", "Drug", "Mechanism", "Pathway", "Study", "PatientGroup", "Registry"}
TYPE_WORDS = {"Disease": "disease", "Phenotype": "symptom or clinical feature", "Gene": "gene", "Drug": "drug",
              "Mechanism": "disease mechanism", "Pathway": "biological pathway", "Study": "clinical study",
              "PatientGroup": "patient organization", "Registry": "patient registry"}


# Context lines added from the graph, so a node is findable by what it does or relates to:
# (relation, direction, phrase). "out" = this node is the edge source.
CONTEXT = {
    "Drug": [("inhibits", "out", "Inhibits"), ("targets", "out", "Targets"), ("modulates", "out", "Modulates"),
             ("treats", "out", "Reported to treat"), ("indicated_for", "out", "Approved or studied for")],
    "Mechanism": [("has_mechanism", "in", "Mechanism of"), ("has_phenotype", "out", "Features")],
    "Gene": [("inhibits", "in", "Inhibited by"), ("targets", "in", "Targeted by")],
    "Disease": [("has_mechanism", "out", "Mechanism"), ("treats", "in", "Treated with")],
}


def node_text(node: dict, phenotypes_of: dict[str, list[str]], context: dict[str, list[str]] | None = None) -> str:
    """Label first, then what the node does or relates to (graph context), then synonyms and a
    short description. Context goes early: for a drug, "Inhibits: JAK2, JAK1" matters more to a
    query like "medicines that block JAK" than its regulatory boilerplate."""
    props = node.get("props") or {}
    parts = [f"{node['label']} ({TYPE_WORDS.get(node['type'], node['type'])})."]
    parts.extend((context or {}).get(node["id"], []))
    syn = [s for s in node.get("synonyms") or [] if s and s.lower() != node["label"].lower()][:8]
    if syn:
        parts.append("Also called: " + "; ".join(syn) + ".")
    limit = 120 if node["type"] == "Drug" else 300
    for key in ("name", "symbol", "description", "scope", "brief_title", "title"):
        v = props.get(key)
        if isinstance(v, str) and v and v != node["label"]:
            parts.append(v[:limit])
    if phenotypes_of.get(node["id"]):
        parts.append("Features: " + ", ".join(phenotypes_of[node["id"]][:10]) + ".")
    return " ".join(parts)


def context_lines(graph: dict, nodes: dict[str, dict]) -> dict[str, list[str]]:
    """Per node, short "<phrase>: a, b, c." lines from the CONTEXT relations (max 8 names each)."""
    found: dict[tuple[str, str], list[str]] = defaultdict(list)
    for e in graph["edges"]:
        for nid, other, direction in ((e["source"], e["target"], "out"), (e["target"], e["source"], "in")):
            n, o = nodes.get(nid), nodes.get(other)
            if not n or not o:
                continue
            for rel, want_dir, phrase in CONTEXT.get(n["type"], []):
                if e["relation"] == rel and direction == want_dir and len(found[(nid, phrase)]) < 8:
                    if o["label"] not in found[(nid, phrase)]:
                        found[(nid, phrase)].append(o["label"])
    order = {phrase: i for rels in CONTEXT.values() for i, (_, _, phrase) in enumerate(rels)}
    out: dict[str, list[str]] = defaultdict(list)
    for (nid, phrase), names in sorted(found.items(), key=lambda kv: (kv[0][0], order.get(kv[0][1], 99))):
        out[nid].append(f"{phrase}: {', '.join(names)}.")
    return out


def truncate_normalise(v: list[float], dims: int = DIMS) -> list[float]:
    v = v[:dims]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def pack_int8(vectors: list[list[float]]) -> str:
    raw = bytearray()
    for v in vectors:
        raw.extend((max(-127, min(127, round(x * 127))) & 0xFF) for x in v)
    return base64.b64encode(bytes(raw)).decode("ascii")


def build(graph: dict, embed=None) -> dict:
    embed = embed or client.embed
    nodes = {n["id"]: n for n in graph["nodes"]}
    # Disease -> its most distinctive phenotypes (highest information content first).
    feats: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for e in graph["edges"]:
        if e["relation"] == "has_phenotype" and e["target"] in nodes:
            ph = nodes[e["target"]]
            feats[e["source"]].append(((ph.get("props") or {}).get("ic") or 0.0, ph["label"]))
    phenotypes_of = {k: [lab for _, lab in sorted(v, reverse=True)] for k, v in feats.items()}

    chosen = [n for n in graph["nodes"] if n["type"] in SEARCHABLE]
    context = context_lines(graph, nodes)
    texts = [node_text(n, phenotypes_of, context) for n in chosen]
    vectors = [truncate_normalise(v) for v in embed(texts)]
    return {
        "graph_built_at": graph["meta"]["built_at"],
        "model": client.embed_model(),
        "dims": DIMS,
        "ids": [n["id"] for n in chosen],
        "vectors_int8_b64": pack_int8(vectors),
    }


def main() -> None:
    graph = json.loads(GRAPH.read_text())
    index = build(graph)
    OUT.write_text(json.dumps(index))
    print(f"search index: {len(index['ids'])} nodes, {index['dims']} dims, model {index['model']}, "
          f"{OUT.stat().st_size // 1024} KB", file=sys.stderr)


if __name__ == "__main__":
    main()
