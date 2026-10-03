"""Map free-text mentions to graph nodes (README Section 7, use 2).

1. Exact match on a normalized label or synonym → accepted without a model call.
2. Otherwise: embeddings over node labels + synonyms, top-5 shortlist, and the
   model picks one node or "none".
3. Only high-confidence picks are accepted. Everything else goes to
   `data/review_queue.csv`, never into the graph.

The same function maps free-text symptoms to Phenotype (HPO) nodes for Devon's
journey: pass `types={"Phenotype"}`.

Run: `python -m pipeline.extract.reconcile` links claim subjects, objects and
variants in `data/build/claims.raw.json` to nodes in graph.json (or the mock until
graph v0) and writes:

- `data/build/claims.json`: contract `Claim`s whose ends all resolved, plus the
  `Paper` nodes they cite (with `props.abstract`, so the quote check runs from
  graph.json alone) and run stats;
- `data/review_queue.csv`: every mention that did not resolve with high confidence.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

from pipeline.extract._compat import REPO_ROOT
from pipeline.llm import client

PROMPT_VERSION = "reconcile-v1"
TOP_K = 5

Embedder = Callable[[list[str]], list[list[float]]]
Parser = Callable[[str, str, type], Optional[BaseModel]]


@dataclass
class Link:
    mention: str
    node_id: str | None
    method: Literal["exact", "model", "none"]
    confidence: Literal["high", "medium", "low"]
    shortlist: list[str]
    reason: str = ""


class Adjudication(BaseModel):
    choice: str = Field(description="The ID of the matching candidate, or 'none'.")
    confidence: Literal["high", "medium", "low"]
    reason: str = Field(description="One short sentence.")


SYSTEM = """You link an entity mention from a biomedical abstract to one node in a knowledge graph.

Pick the candidate that denotes the same entity, or 'none' if no candidate does.
- A gene and a disease caused by that gene are different entities.
- Gain-of-function and loss-of-function forms of a gene's disease are different entities; never link one to the other.
- Germline disease and somatic disease are different entities.
- A drug class (e.g. "JAK inhibitors") is not the same as one drug unless only that drug fits the context.
- confidence 'high' only if the match is unambiguous."""


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class NodeIndex:
    def __init__(self, nodes: list[dict], embed: Embedder | None = None, types: set[str] | None = None):
        self.nodes = {n["id"]: n for n in nodes if types is None or n["type"] in types}
        self.embed = embed or client.embed
        self.exact: dict[str, set[str]] = {}
        self.names: list[tuple[str, str]] = []  # (node id, name)
        for n in self.nodes.values():
            for name in [n["label"], *n.get("synonyms", [])]:
                if name and norm(name):
                    self.exact.setdefault(norm(name), set()).add(n["id"])
                    self.names.append((n["id"], name))
        self._vecs: list[list[float]] | None = None

    def vecs(self) -> list[list[float]]:
        if self._vecs is None:
            self._vecs = self.embed([name for _, name in self.names]) if self.names else []
        return self._vecs

    def shortlist(self, mention: str, k: int = TOP_K) -> list[str]:
        if not self.names:
            return []
        q = self.embed([mention])[0]
        best: dict[str, float] = {}
        for (nid, _), v in zip(self.names, self.vecs()):
            s = _cos(q, v)
            if s > best.get(nid, -1):
                best[nid] = s
        return sorted(best, key=lambda i: -best[i])[:k]

    def describe(self, nid: str) -> str:
        n = self.nodes[nid]
        syn = f"; synonyms: {', '.join(n.get('synonyms', [])[:8])}" if n.get("synonyms") else ""
        return f"{nid}: {n['label']} ({n['type']}{syn})"


def link(mention: str, index: NodeIndex, *, context: str = "", parser: Parser | None = None) -> Link:
    hits = index.exact.get(norm(mention), set())
    if len(hits) == 1:
        nid = next(iter(hits))
        return Link(mention, nid, "exact", "high", [nid])
    shortlist = sorted(hits) if hits else index.shortlist(mention)
    if not shortlist:
        return Link(mention, None, "none", "low", [], "no candidates")
    parser = parser or client.parse
    user = (f"[{PROMPT_VERSION}]\nMention: {mention}\n"
            + (f"Context: {context}\n" if context else "")
            + "Candidates:\n" + "\n".join(f"- {index.describe(i)}" for i in shortlist))
    try:
        out = parser(SYSTEM, user, Adjudication)
    except client.CacheMiss:
        return Link(mention, None, "none", "low", shortlist, "offline: not cached")
    if out is None:
        return Link(mention, None, "none", "low", shortlist, "model refused")
    if out.choice == "none" or out.choice not in shortlist:
        return Link(mention, None, "none", out.confidence, shortlist, out.reason)
    return Link(mention, out.choice, "model", out.confidence, shortlist, out.reason)


def accepted(l: Link) -> bool:
    return l.node_id is not None and l.confidence == "high"


ROLE_TYPES = {
    "subject": {"Gene", "Variant", "Mechanism", "Disease", "Phenotype", "Drug", "Pathway"},
    "object": {"Gene", "Variant", "Mechanism", "Disease", "Phenotype", "Drug", "Pathway"},
    "variant": {"Variant"},
}


def link_claims(claims: list[dict], nodes: list[dict], embed: Embedder | None = None,
                parser: Parser | None = None) -> tuple[dict, list[dict]]:
    """Returns ({claim_id: {"subject": id|None, "object": id|None, "variant": id|None|""}}, review rows).

    `variant` is "" when the claim names no variant."""
    indexes = {role: NodeIndex(nodes, embed=embed, types=types) for role, types in ROLE_TYPES.items()}
    memo: dict[tuple[str, str, str], Link] = {}
    links, review = {}, []
    for c in claims:
        row: dict[str, str | None] = {"variant": ""}
        for role in ("subject", "object", "variant"):
            mention = c.get(role) or ""
            if not mention:
                continue
            ctx = f"{c['subject']} {c['predicate']} {c['object']}; quote: {c['quote']}"
            key = (role, mention, norm(c["subject"] + c["predicate"] + c["object"]))
            if key not in memo:
                memo[key] = link(mention, indexes[role], context=ctx, parser=parser)
            l = memo[key]
            row[role] = l.node_id if accepted(l) else None
            if not accepted(l):
                review.append({"claim_id": c["id"], "role": role, "mention": mention, "proposed": l.node_id or "",
                               "confidence": l.confidence, "shortlist": " | ".join(l.shortlist), "reason": l.reason})
        links[c["id"]] = row
    return links, review


def paper_node(p: dict) -> dict:
    ref = f"pmid:{p['pmid']}" if p["pmid"] else f"doi:{p['doi']}"
    props = {"title": p["title"], "abstract": p["abstract"], "journal": p["journal"],
             "authors": [f"{a['fore']} {a['last']}".strip() for a in p["authors"]],
             "pub_types": p["pub_types"]}
    if p.get("year"):
        props["year"] = p["year"]
    if p.get("doi"):
        props["doi"] = p["doi"]
    if p.get("preprint"):
        props["preprint"] = True
    return {"id": ref, "type": "Paper", "label": p["title"], "synonyms": [], "props": props}


def contract_claims(raw: list[dict], links: dict, papers: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Keep claims whose subject, object and (if named) variant all resolved; renumber them.

    Returns (claims, paper nodes they cite, counts). Each claim is validated with the
    frozen `pipeline.models.Claim`."""
    from pipeline.models import Claim

    by_ref = {(f"pmid:{p['pmid']}" if p["pmid"] else f"doi:{p['doi']}"): p for p in papers}
    out, cited = [], {}
    counts = {"raw": len(raw), "unresolved_subject_or_object": 0, "unresolved_variant": 0}
    for c in raw:
        l = links.get(c["id"], {})
        if not (l.get("subject") and l.get("object")):
            counts["unresolved_subject_or_object"] += 1
            continue
        if c.get("variant") and not l.get("variant"):
            counts["unresolved_variant"] += 1
            continue
        claim = {"id": f"c_{len(out) + 1:06d}", "pmid": c["pmid"], "paper": c["paper"], "quote": c["quote"],
                 "polarity": c["polarity"], "subject": l["subject"], "predicate": c["predicate"],
                 "object": l["object"], "variant": l.get("variant") or "", "study_type": c["study_type"],
                 "n_patients": c["n_patients"]}
        out.append(Claim.model_validate(claim).model_dump(mode="json"))
        cited[c["paper"]] = paper_node(by_ref[c["paper"]])
    counts["kept"] = len(out)
    return out, sorted(cited.values(), key=lambda n: n["id"]), counts


def main() -> None:
    import datetime as dt

    build = REPO_ROOT / "data" / "build"
    graph_path = build / "graph.json" if (build / "graph.json").exists() else build / "graph.mock.json"
    graph = json.loads(graph_path.read_text())
    raw = json.loads((build / "claims.raw.json").read_text())
    papers = json.loads((build / "papers.json").read_text())["papers"]
    links, review = link_claims(raw["claims"], graph["nodes"])
    claims, paper_nodes, counts = contract_claims(raw["claims"], links, papers)
    (build / "claims.json").write_text(json.dumps({
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "graph": graph_path.name,
        "claims": claims,
        "papers": paper_nodes,
        "stats": {"extraction": raw["stats"], "reconcile": counts, "review_rows": len(review)},
    }, indent=1, ensure_ascii=False))
    with open(REPO_ROOT / "data" / "review_queue.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["claim_id", "role", "mention", "proposed", "confidence", "shortlist", "reason"])
        w.writeheader()
        w.writerows(review)
    print(f"reconcile against {graph_path.name}: {counts}; review rows: {len(review)}", file=sys.stderr)


if __name__ == "__main__":
    main()
