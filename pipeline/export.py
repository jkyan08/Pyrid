"""Assemble data/build/graph.json from the retrieved artifacts that exist today.

Integrator stop-gap (lane C acting on user direction): lane A's backbone /
similarity / clustering pipeline has not landed, so this export builds the
graph from what was actually retrieved and verified:

* lane B's claims bundle   (data/build/claims.json — quote-verified, reconciled
  against the OptimusKG backbone; only FULLY-RESOLVED claims become edges),
* lane B's papers          (data/build/papers.json — abstracts included, so the
  contract's quote check runs for real),
* lane B's investigators   (data/build/investigators.json — ORCID-resolved,
  public contact routes only),
* lane C's verified seeds  (pipeline/seeds/patient_groups.yaml — every URL
  fetched; groups are linked to diseases only on conservative label matches).

Honesty rules: nothing is invented to bridge unresolved mentions; clusters are
empty until lane A lands; every edge cites pmid sources or a seed-verified URL.

Run: `python -m pipeline.export` (or `make data`).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from pipeline.models import Graph

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD = REPO_ROOT / "data" / "build"
SEEDS = REPO_ROOT / "pipeline" / "seeds" / "patient_groups.yaml"

# confidence by the best supporting study type (README §6 rubric, extracted rows)
_STUDY_CONFIDENCE = {
    "trial": 0.8,
    "cohort": 0.7,
    "case_series": 0.6,
    "case_report": 0.5,
    "in_vitro": 0.4,
    "review": 0.35,
}
_MIN_INVESTIGATOR_PAPERS = 2
_MAX_EDGE_SOURCES = 8


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(name: str) -> Any:
    return json.loads((BUILD / name).read_text())


def _edge_id(n: int) -> str:
    return f"e_{n:06d}"


def _aggregate_claim_edges(
    claims: list[dict[str, Any]], node_ids: set[str], extracted_date: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group fully-resolved claims by (subject, predicate, object) into edges."""
    kept: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for c in claims:
        if c["subject"] in node_ids and c["object"] in node_ids and c["subject"] != c["object"]:
            if c.get("variant", "").startswith("text:"):
                # unresolved variant mention — drop the detail rather than invent
                # a Variant node (ClinVar variant layer is lane A's work)
                c = {**c, "variant": ""}
            kept.append(c)
            grouped[(c["subject"], c["predicate"], c["object"])].append(c)

    edges: list[dict[str, Any]] = []
    for idx, ((subject, predicate, obj), group) in enumerate(sorted(grouped.items()), start=1):
        supporting = [c["id"] for c in group if c["polarity"] == "supports"]
        contradicting = [c["id"] for c in group if c["polarity"] == "contradicts"]
        sup_claims = [c for c in group if c["polarity"] == "supports"]

        best_type = max(
            (c["study_type"] for c in sup_claims),
            key=lambda s: _STUDY_CONFIDENCE.get(s, 0.0),
            default=None,
        )
        confidence = _STUDY_CONFIDENCE.get(best_type, 0.2) if best_type else 0.2

        ns = [c["n_patients"] for c in sup_claims]
        known_ns = [n for n in ns if isinstance(n, int)]
        small_n = bool(known_ns) and all(n < 10 for n in known_ns) and len(known_ns) == len(ns)

        if supporting and contradicting:
            status = "contested"
        elif small_n and supporting:
            status = "small_n"
        else:
            status = "supported"

        labels: list[str] = []
        if predicate == "treats" and supporting:
            labels.append("promising")
        if supporting and contradicting:
            labels.append("contested")
        if small_n:
            labels.append("small_n")

        pmids: list[str] = []
        for c in group:
            if c["pmid"] and c["pmid"] not in pmids:
                pmids.append(c["pmid"])
        sources = [
            {"kind": "pmid", "id": pmid, "date": extracted_date}
            for pmid in pmids[:_MAX_EDGE_SOURCES]
        ]

        edges.append(
            {
                "id": _edge_id(idx),
                "source": subject,
                "target": obj,
                "relation": predicate,
                "evidence_class": "extracted",
                "confidence": round(confidence, 3),
                "confidence_basis": {
                    "rule": "best supporting study_type (trial>cohort>case_series>case_report>in_vitro>review)",
                    "best_study_type": best_type,
                    "supporting": len(supporting),
                    "contradicting": len(contradicting),
                    "n_patients": ns,
                },
                "status": status,
                "sources": sources,
                "supporting": supporting,
                "contradicting": contradicting,
                "attributes": {"labels": labels} if labels else {},
            }
        )
    return edges, kept


def _investigator_edges(
    investigators: list[dict[str, Any]],
    paper_ids: set[str],
    kept_claims: list[dict[str, Any]],
    node_types: dict[str, str],
    start: int,
    extracted_date: str,
) -> list[dict[str, Any]]:
    """author_of edges to included papers; works_on edges to entities their
    papers produced kept claims about (README: 'author of N papers on X')."""
    claims_by_paper: dict[str, set[str]] = defaultdict(set)
    for c in kept_claims:
        for end in (c["subject"], c["object"]):
            if node_types.get(end) in ("Disease", "Gene", "Drug"):
                claims_by_paper[c["paper"]].add(end)

    edges: list[dict[str, Any]] = []
    n = start
    for inv in investigators:
        inv_papers = [f"pmid:{p}" for p in inv.get("papers", [])]
        included = [p for p in inv_papers if p in paper_ids]
        for paper_node in included:
            n += 1
            edges.append(
                {
                    "id": _edge_id(n),
                    "source": inv["id"],
                    "target": paper_node,
                    "relation": "author_of",
                    "evidence_class": "observed",
                    "confidence": 0.9,
                    "confidence_basis": {"rule": "PubMed author list", "orcid": inv.get("orcid", "")},
                    "status": "supported",
                    "sources": [
                        {"kind": "pmid", "id": paper_node.removeprefix("pmid:"), "date": extracted_date}
                    ],
                    "supporting": [],
                    "contradicting": [],
                    "attributes": {},
                }
            )
        # works_on: entity appears in kept claims from >=2 of their papers
        entity_papers: dict[str, list[str]] = defaultdict(list)
        for paper_node in included:
            for entity in claims_by_paper.get(paper_node, ()):
                entity_papers[entity].append(paper_node.removeprefix("pmid:"))
        for entity, pmids in sorted(entity_papers.items()):
            if len(pmids) < 2 or entity == inv["id"]:
                continue
            n += 1
            edges.append(
                {
                    "id": _edge_id(n),
                    "source": inv["id"],
                    "target": entity,
                    "relation": "works_on",
                    "evidence_class": "observed",
                    "confidence": 0.6,
                    "confidence_basis": {
                        "rule": "author of N papers whose kept claims mention this entity",
                        "n_papers": len(pmids),
                    },
                    "status": "supported",
                    "sources": [
                        {"kind": "pmid", "id": p, "date": extracted_date}
                        for p in pmids[:_MAX_EDGE_SOURCES]
                    ],
                    "supporting": [],
                    "contradicting": [],
                    "attributes": {},
                }
            )
    return edges


def _group_nodes_and_edges(
    backbone: list[dict[str, Any]], start: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Seed-verified patient groups/registries; edges only on conservative
    label matches between seed disease terms and backbone labels/synonyms."""
    seeds = yaml.safe_load(SEEDS.read_text())
    diseases = [n for n in backbone if n["type"] == "Disease"]

    def matches(term: str) -> list[str]:
        t = term.lower().split("(")[0].strip()
        if len(t) < 6:
            return []
        out = []
        for d in diseases:
            texts = [d["label"], *d.get("synonyms", [])]
            if any(t in x.lower() or x.lower() in t for x in texts if len(x) >= 6):
                out.append(d["id"])
        return out

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    n = start
    for i, g in enumerate(seeds["groups"], start=1):
        node_type = "Registry" if g["kind"] == "registry" else "PatientGroup"
        gid = f"group:{i:02d}"
        nodes.append(
            {
                "id": gid,
                "type": node_type,
                "label": g["name"],
                "synonyms": [],
                "props": {
                    "url": g["url"],
                    "scope": g.get("scope", ""),
                    "verified_at": g["verified"]["retrieved_at"],
                },
            }
        )
        linked: set[str] = set()
        for term in g.get("diseases", []):
            for did in matches(term):
                if did in linked:
                    continue
                linked.add(did)
                n += 1
                edges.append(
                    {
                        "id": _edge_id(n),
                        "source": gid,
                        "target": did,
                        "relation": "covers" if node_type == "Registry" else "supports_community",
                        "evidence_class": "observed",
                        "confidence": 0.7,
                        "confidence_basis": {
                            "rule": "seed-verified group; disease linked by label match",
                            "matched_term": term,
                        },
                        "status": "supported",
                        "sources": [
                            {
                                "kind": "url",
                                "id": g["url"],
                                "date": g["verified"]["retrieved_at"],
                            }
                        ],
                        "supporting": [],
                        "contradicting": [],
                        "attributes": {},
                    }
                )
    coverage = {
        # the honest gap: directory searches for a dedicated STAT1 GoF group
        "searches": seeds.get("searches", []),
    }
    return nodes, edges, coverage


def build_graph() -> Graph:
    bundle = _load_json("claims.json")
    papers = _load_json("papers.json")["papers"]
    investigators_all = _load_json("investigators.json")
    investigators_all = (
        investigators_all["investigators"]
        if isinstance(investigators_all, dict)
        else investigators_all
    )
    meta_nodes = _load_json("claims.meta.json")["nodes"]
    extracted_date = bundle["extracted_at"][:10]

    backbone = meta_nodes
    paper_nodes = [
        {
            "id": f"pmid:{p['pmid']}",
            "type": "Paper",
            "label": p["title"],
            "synonyms": [],
            "props": {
                "pmid": p["pmid"],
                "doi": p.get("doi", ""),
                "journal": p.get("journal", ""),
                "year": p.get("year", ""),
                "preprint": p.get("preprint", False),
                "abstract": p.get("abstract", ""),
            },
        }
        for p in papers
    ]
    investigators = [
        inv for inv in investigators_all if inv.get("n_papers", 0) >= _MIN_INVESTIGATOR_PAPERS
    ]
    investigator_nodes = [
        {
            "id": inv["id"],
            "type": "Investigator",
            "label": inv["name"],
            "synonyms": [],
            "props": {
                k: v
                for k, v in {
                    "orcid": inv.get("orcid", ""),
                    "affiliation": (inv.get("affiliations") or [""])[0],
                    "description": inv.get("description", ""),
                    "papers_on_topic": inv.get("n_papers", 0),
                    "contact": (inv.get("contact") or {}).get("email", ""),
                }.items()
                if v
            },
        }
        for inv in investigators
    ]

    node_ids = {n["id"] for n in backbone}
    node_types = {n["id"]: n["type"] for n in backbone}
    claim_edges, kept_claims = _aggregate_claim_edges(
        bundle["claims"], node_ids, extracted_date
    )
    paper_ids = {n["id"] for n in paper_nodes}
    inv_edges = _investigator_edges(
        investigators, paper_ids, kept_claims, node_types, len(claim_edges), extracted_date
    )
    group_nodes, group_edges, seed_coverage = _group_nodes_and_edges(
        backbone, len(claim_edges) + len(inv_edges)
    )

    # coverage: lane B's per-paper searches + the seed directory searches,
    # attached to any disease the STAT1-GoF-related searches were about
    coverage: dict[str, Any] = dict(bundle.get("coverage", {}))
    gof_ids = [
        n["id"]
        for n in backbone
        if n["type"] == "Disease"
        and any("stat1" in t.lower() for t in [n["label"], *n.get("synonyms", [])])
    ]
    seed_searches = [
        {
            "source": s["directory"],
            "query": s["query"],
            "date": s["date"],
            "hits": s["hits"] if isinstance(s.get("hits"), int) else 0,
        }
        for s in seed_coverage["searches"]
    ]
    for did in gof_ids:
        coverage.setdefault(did, {"searched": []})
        coverage[did]["searched"] = coverage[did].get("searched", []) + seed_searches

    graph = {
        "meta": {
            "built_at": _now(),
            "sources": [
                {
                    "name": (
                        "PubMed abstracts via lane B extraction "
                        f"(model {bundle.get('model', '?')}; {len(bundle['claims'])} kept claims, "
                        f"{len(kept_claims)} fully resolved against the backbone and exported; "
                        "unresolved mentions are NOT bridged)"
                    ),
                    "version": bundle.get("extracted_at", ""),
                    "retrieved_at": extracted_date,
                    "license": "NCBI/NLM terms",
                },
                {
                    "name": "OptimusKG backbone node table (labels/synonyms via lane B reconcile)",
                    "version": "stratified slice",
                    "retrieved_at": extracted_date,
                    "license": "CC BY-NC-SA 4.0 (verify upstream per-source)",
                },
                {
                    "name": "patient_groups.yaml (lane C; every URL fetched and verified)",
                    "version": "",
                    "retrieved_at": "2026-10-03",
                    "license": "n/a (hand-curated seed)",
                },
                {
                    "name": (
                        "NOTE: lane A (ClinVar variants, mechanism nodes, HPO phenotype layer, "
                        "similarity + Leiden clustering) has not landed; clusters are empty and "
                        "no inferred similarity edges exist in this build"
                    ),
                    "version": "",
                    "retrieved_at": extracted_date,
                    "license": "n/a",
                },
            ],
            "coverage": coverage,
        },
        "nodes": backbone + paper_nodes + investigator_nodes + group_nodes,
        "edges": claim_edges + inv_edges + group_edges,
        "claims": kept_claims,
        "clusters": [],
    }
    return Graph.model_validate(graph)


def main() -> None:
    graph = build_graph()
    out = BUILD / "graph.json"
    out.write_text(graph.model_dump_json(indent=2))
    print(
        f"wrote {out} — {len(graph.nodes)} nodes, {len(graph.edges)} edges, "
        f"{len(graph.claims)} claims, {len(graph.clusters)} clusters"
    )


if __name__ == "__main__":
    main()
