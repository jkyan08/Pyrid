"""Map free-text mentions to graph nodes (README Section 7, use 2).

Each claim role is resolved only to the node types that role allows (a `treats`
object must be a Disease, a `has_mechanism` object a Mechanism, ...):

1. Exact match on a normalized label or synonym → accepted without a model call.
2. Otherwise: candidates (a lexical prefilter over large vocabularies, reranked by
   embeddings to a top-5, plus role-specific extras: backbone diseases linked to a
   cluster gene the mention names, ClinVar records for a variant), and the model
   picks one node or "none".
3. Only high-confidence picks are accepted. Everything else goes to
   `data/review_queue.csv`, never into the graph.

The same function maps free-text symptoms to Phenotype (HPO) nodes for Devon's
journey: pass `types={"Phenotype"}`.

Run: `python -m pipeline.extract.reconcile` links claim subjects, objects and
variants in `data/build/claims.raw.json` to the backbone node index
(`pipeline/extract/backbone.py`: the same OptimusKG files and contract ID formats
lane A uses), so it does not wait for graph v0. If `data/build/graph.json` exists,
every resolved id must also exist there; ids it lacks go to the review queue. Writes:

- `data/build/claims.json`: the `ClaimsBundle` handoff (pipeline/models.py): every
  quote-verified claim, with resolved node ids or `text:<mention>` where the
  reconciler was not confident, the Paper nodes (abstracts + author dicts), the
  unresolved rows, and PubMed coverage;
- `data/build/claims.meta.json`: run stats and every node the claims reference
  (ClinVar variants and mech: nodes included) for lane A to add if missing;
- `data/review_queue.csv`: every mention that did not resolve with high confidence.
"""
from __future__ import annotations

import csv
import json
import math
import re
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

from pipeline.extract._compat import REPO_ROOT
from pipeline.llm import client

PROMPT_VERSION = "reconcile-v3"
TOP_K = 8

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
- The graph merges several ontologies, so one disease can appear as near-duplicate nodes (e.g. MONDO, Orphanet
  and EFO entries for the same condition). Pick the most specific node that denotes the mention. Such duplicates
  are not ambiguity: if the mention clearly denotes the entity, confidence is 'high'.
- confidence 'high' when the mention clearly denotes the chosen node; 'medium' or 'low' only when the mention
  could reasonably mean a different, non-equivalent candidate, or the best candidate is only an approximation
  (broader, narrower, or related)."""


def norm(s: str) -> str:
    """Case- and punctuation-insensitive key. Keeps non-ASCII letters, so "IFN-γ" stays distinct
    from "IFN" (an IFNA1 synonym)."""
    return re.sub(r"[\W_]+", " ", s.lower()).strip()


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


LEXICAL_THRESHOLD = 5000  # names; above this, prefilter lexically before embedding


class NodeIndex:
    """Candidate lookup over nodes. Small vocabularies are embedded whole; large ones
    (the backbone) are prefiltered lexically and only the shortlisted names are embedded
    (embeddings are cached per string)."""

    def __init__(self, nodes: list[dict], embed: Embedder | None = None, types: set[str] | None = None):
        self.nodes: dict[str, dict] = {}
        self.embed = embed or client.embed
        self.exact: dict[str, set[str]] = {}
        self.lexical = None
        for n in nodes:
            if types is None or n["type"] in types:
                self.add(n)
        n_names = sum(1 + len(n.get("synonyms", [])) for n in self.nodes.values())
        if n_names > LEXICAL_THRESHOLD:
            from pipeline.extract.backbone import LexicalIndex
            self.lexical = LexicalIndex(list(self.nodes.values()))

    def add(self, n: dict) -> None:
        if n["id"] in self.nodes:
            return
        self.nodes[n["id"]] = n
        for name in [n["label"], *n.get("synonyms", [])]:
            if name and norm(name):
                self.exact.setdefault(norm(name), set()).add(n["id"])

    def exact_hits(self, mention: str, types: set[str] | None = None) -> set[str]:
        return {i for i in self.exact.get(norm(mention), set()) if types is None or self.nodes[i]["type"] in types}

    def _pairs(self, mention: str, types: set[str] | None) -> list[tuple[str, str]]:
        if self.lexical is not None:
            pool = [i for i in self.lexical.search(mention, k=300) if types is None or self.nodes[i]["type"] in types][:60]
        else:
            pool = [i for i, n in self.nodes.items() if types is None or n["type"] in types]
        return [(nid, name) for nid in pool for name in [self.nodes[nid]["label"], *self.nodes[nid].get("synonyms", [])[:6]] if name]

    def prefetch(self, queries: list[tuple[str, set[str] | None]]) -> None:
        """Embed every string the given shortlists will need in one batched pass, so the
        per-mention shortlists that follow are cache hits."""
        texts = {m for m, _ in queries}
        for m, t in queries:
            texts.update(name for _, name in self._pairs(m, t))
        if texts:
            self.embed(sorted(texts))

    def shortlist(self, mention: str, k: int = TOP_K, types: set[str] | None = None) -> list[str]:
        pairs = self._pairs(mention, types)
        if not pairs:
            return []
        vecs = self.embed([mention] + [name for _, name in pairs])
        q, rest = vecs[0], vecs[1:]
        best: dict[str, float] = {}
        for (nid, _), v in zip(pairs, rest):
            s = _cos(q, v)
            if s > best.get(nid, -1):
                best[nid] = s
        return sorted(best, key=lambda i: (-best[i], i))[:k]

    def describe(self, nid: str) -> str:
        n = self.nodes[nid]
        syn = f"; synonyms: {', '.join(n.get('synonyms', [])[:8])}" if n.get("synonyms") else ""
        extra = "".join(f"; {k}: {v}" for k, v in n.get("props", {}).items()
                        if k in ("protein_change", "clinical_significance", "direction") and v)
        return f"{nid}: {n['label']} ({n['type']}{syn}{extra})"


MECHANISM_HINT = ("The candidates are mechanism classes, not specific molecular effects. Pick the class the "
                  "described effect belongs to: the direction the claim, quote or paper title states or directly implies for "
                  "this gene or variant (e.g. a finding about a gain-of-function variant belongs to that gene's "
                  "gain-of-function class). Use 'high' when the claim, quote or paper title names the direction.")
DISEASE_HINT = ("A mention naming a gene's mutation class (e.g. 'X GOF', 'X gain-of-function patients', "
                "'X LOF') refers to the disease that mutation class causes: pick the Disease candidate caused by "
                "that gene in that direction, if one is listed.")


def role_hint(types: set[str] | None) -> str:
    if types == {"Mechanism"}:
        return MECHANISM_HINT
    if types and "Disease" in types:
        return DISEASE_HINT
    return ""


def link(mention: str, index: NodeIndex, *, context: str = "", parser: Parser | None = None,
         types: set[str] | None = None, extra: list[str] = ()) -> Link:
    hits = index.exact_hits(mention, types)
    if len(hits) == 1:
        nid = next(iter(hits))
        return Link(mention, nid, "exact", "high", [nid])
    try:
        shortlist = sorted(hits) if hits else index.shortlist(mention, types=types)
    except client.CacheMiss:
        return Link(mention, None, "none", "low", [], "offline: embeddings not cached")
    shortlist += [i for i in extra if i in index.nodes and i not in shortlist]
    if not shortlist:
        return Link(mention, None, "none", "low", [], "no candidates")
    # Picking from a shortlist needs little reasoning: run Claude at low effort by default.
    parser = parser or (lambda system, user, schema: client.parse(system, user, schema, effort="low"))
    user = (f"[{PROMPT_VERSION}]\nMention: {mention}\n"
            + (f"Context: {context}\n" if context else "")
            + (f"Note: {role_hint(types)}\n" if role_hint(types) else "")
            + "Candidates:\n" + "\n".join(f"- {index.describe(i)}" for i in shortlist))
    try:
        out = parser(SYSTEM, user, Adjudication)
    except client.CacheMiss:
        return Link(mention, None, "none", "low", shortlist, "offline: not cached")
    except Exception as exc:  # provider failure: send to review, don't sink the run
        return Link(mention, None, "none", "low", shortlist, f"llm error: {type(exc).__name__}")
    if out is None:
        return Link(mention, None, "none", "low", shortlist, "model refused")
    if out.choice == "none" or out.choice not in shortlist:
        return Link(mention, None, "none", out.confidence, shortlist, out.reason)
    return Link(mention, out.choice, "model", out.confidence, shortlist, out.reason)


def accepted(l: Link) -> bool:
    return l.node_id is not None and l.confidence == "high"


ANY = {"Gene", "Variant", "Mechanism", "Disease", "Phenotype", "Drug", "Pathway"}
# predicate → (subject types, object types). Matches how lane A builds edges from claims
# (test 4: Drug → Disease treats edges; test 7: Variant → Mechanism has_mechanism edges).
ROLE_TYPES: dict[str, tuple[set[str], set[str]]] = {
    "treats": ({"Drug"}, {"Disease"}),
    "has_mechanism": ({"Variant", "Gene", "Disease"}, {"Mechanism"}),
    "has_phenotype": ({"Disease", "Variant", "Mechanism", "Gene"}, {"Phenotype"}),
    "causes": ({"Gene", "Variant", "Mechanism"}, {"Disease"}),
    "modulates": ({"Drug", "Gene", "Variant", "Mechanism"}, {"Gene", "Pathway", "Phenotype", "Mechanism"}),
    "associated_with": (ANY, ANY),
}

_PROTEIN_CHANGE = re.compile(r"\b(?:p\.)?([A-Z](?:[a-z]{2})?\d{1,4}(?:[A-Z](?:[a-z]{2})?|\*|fs\S*|del|dup))\b")


def _cluster_symbol(*texts: str) -> str | None:
    from pipeline.extract.backbone import CLUSTER_GENES
    for t in texts:
        for g in CLUSTER_GENES:
            if re.search(rf"\b{g}\b", t or "", re.I):
                return g
    return None


def _disease_extras(mention: str, claim: dict, gene_diseases: dict[str, list[str]]) -> list[str]:
    g = _cluster_symbol(mention, claim["subject"], claim["object"], claim["quote"])
    return gene_diseases.get(g, [])[:8] if g else []


def link_claims(claims: list[dict], nodes: list[dict], embed: Embedder | None = None,
                parser: Parser | None = None, *, gene_diseases: dict[str, list[str]] | None = None,
                titles: dict[str, str] | None = None,
                variant_lookup: Callable[[str, str], list[dict]] | None = None,
                index: NodeIndex | None = None, workers: int = 1) -> tuple[dict, list[dict]]:
    """Returns ({claim_id: {"subject": id|None, "object": id|None, "variant": id|None|""}}, review rows).

    `variant` is "" when the claim names no variant. `gene_diseases` (cluster gene →
    disease ids) and `variant_lookup(gene, protein_change) → Variant nodes` supply
    role-specific candidates; both default to none."""
    index = index or NodeIndex(nodes, embed=embed)
    gene_diseases = gene_diseases or {}
    # Pass 1: work out each (claim, role) request and its candidates (ClinVar lookups are cached).
    plan: list[tuple[str, str, str, tuple]] = []   # (claim id, role, mention, memo key)
    requests: dict[tuple, dict] = {}
    for c in claims:
        subj_types, obj_types = ROLE_TYPES.get(c["predicate"], (ANY, ANY))
        ctx = f"{c['subject']} {c['predicate']} {c['object']}; quote: {c['quote']}"
        for role, types in (("subject", subj_types), ("object", obj_types), ("variant", {"Variant"})):
            mention = c.get(role) or ""
            if not mention:
                continue
            extra: list[str] = []
            if "Disease" in types:
                extra = _disease_extras(mention, c, gene_diseases)
            role_ctx = ctx
            if "Mechanism" in types:
                g = _cluster_symbol(mention, c["subject"], c["object"], c["quote"])
                extra += [i for i in index.nodes if i.startswith(f"mech:{g}:")] if g else []
                # The source paper's title often states the direction the abstract sentence leaves implicit.
                if titles and titles.get(c["paper"]):
                    role_ctx = f"{ctx}; paper title: {titles[c['paper']]}"
            if "Variant" in types and variant_lookup is not None:
                m = _PROTEIN_CHANGE.search(c.get("variant") or mention)
                g = _cluster_symbol(mention, c["subject"], c["object"], c["quote"])
                if m and g:
                    for v in variant_lookup(g, m.group(1)):
                        index.add(v)
                        extra.append(v["id"])
            # Exact matches need no context, so they share one key across claims.
            key = (role, mention, tuple(sorted(types)), tuple(extra),
                   "" if len(index.exact_hits(mention, types)) == 1 else role_ctx)
            requests.setdefault(key, {"mention": mention, "context": role_ctx, "types": types, "extra": extra})
            plan.append((c["id"], role, mention, key))

    index.prefetch([(r["mention"], r["types"]) for r in requests.values()
                    if len(index.exact_hits(r["mention"], r["types"])) != 1])

    # Pass 2: adjudicate unique requests concurrently (model calls dominate the run time).
    def run(item):
        key, r = item
        return key, link(r["mention"], index, context=r["context"], parser=parser, types=r["types"], extra=r["extra"])

    with ThreadPoolExecutor(workers) as pool:
        memo = dict(pool.map(run, requests.items()))

    links: dict[str, dict] = {c["id"]: {"variant": ""} for c in claims}
    review = []
    for cid, role, mention, key in plan:
        l = memo[key]
        links[cid][role] = l.node_id if accepted(l) else None
        if not accepted(l):
            review.append({"claim_id": cid, "role": role, "mention": mention, "proposed": l.node_id or "",
                           "confidence": l.confidence, "shortlist": " | ".join(l.shortlist), "reason": l.reason})
    review += _gene_mechanism_mismatches(links, index)
    return links, review


def _node_gene(n: dict) -> str | None:
    if n["type"] == "Gene":
        return n["label"]
    return n.get("props", {}).get("gene_symbol")


def _gene_mechanism_mismatches(links: dict, index: NodeIndex) -> list[dict]:
    """A Gene/Variant/Mechanism linked to `mech:<GENE>:...` must be about that same gene."""
    review = []
    for cid, row in links.items():
        s, o = row.get("subject"), row.get("object")
        for a, b in ((s, o), (o, s)):
            if a and b and b.startswith("mech:") and a in index.nodes and b in index.nodes:
                ga, gb = _node_gene(index.nodes[a]), _node_gene(index.nodes[b])
                if ga and gb and ga != gb:
                    review.append({"claim_id": cid, "role": "subject" if a == s else "object", "mention": "",
                                   "proposed": a, "confidence": "high", "shortlist": "",
                                   "reason": f"gene mismatch: {a} ({ga}) linked to {b} ({gb})"})
                    row["subject"] = row["object"] = None
    return review


DECISIONS = REPO_ROOT / "data" / "review_decisions.csv"
PRIORITY = REPO_ROOT / "data" / "review_priority.csv"


def apply_review_decisions(links: dict, review: list[dict], decisions: list[dict], known: set[str]) -> tuple[list[dict], int]:
    """Apply human decisions: rows with decision `accept` set (claim_id, role) to node_id. Only
    ids the index knows are applied. Returns (remaining review rows, number applied)."""
    applied = set()
    for d in decisions:
        if d.get("decision", "").strip().lower() != "accept":
            continue
        cid, role, nid = d["claim_id"], d["role"], d["node_id"].strip()
        if cid in links and role in ("subject", "object", "variant") and nid in known:
            links[cid][role] = nid
            applied.add((cid, role))
    return [r for r in review if (r["claim_id"], r["role"]) not in applied], len(applied)


def review_priority(review: list[dict], raw: dict, index: NodeIndex) -> list[dict]:
    """Pending proposals worth a human look: a proposed node exists and it, or the claim, touches a
    cluster gene's mechanism or disease. Grouped by (mention, role, proposed node), ranked by how
    many claims accepting it would affect; one row per claim so decisions apply per claim."""
    from pipeline.extract.backbone import CLUSTER_GENES

    def relevant(r):
        n = index.nodes.get(r["proposed"])
        if not n:
            return False
        c = raw[r["claim_id"]]
        text = f"{n['label']} {' '.join(n.get('synonyms', [])[:6])} {c['subject']} {c['object']}"
        return r["proposed"].startswith("mech:") or any(g in text for g in CLUSTER_GENES)

    rows = [r for r in review if r["proposed"] and r["confidence"] in ("medium", "low") and relevant(r)]
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["mention"], r["role"], r["proposed"]), []).append(r)
    out = []
    for (mention, role, nid), rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        for r in rs:
            out.append({"decision": "", "claim_id": r["claim_id"], "role": role, "mention": mention, "node_id": nid,
                        "node_label": index.nodes[nid]["label"], "claims_in_group": len(rs),
                        "model_reason": r["reason"], "quote": raw[r["claim_id"]]["quote"]})
    return out


def check_against_graph(links: dict, graph_ids: set[str]) -> list[dict]:
    """Null out resolved ids that graph.json lacks (Variant ids excepted: lane A may not
    have loaded that ClinVar record yet). Returns review rows for each."""
    review = []
    for cid, row in links.items():
        for role, nid in row.items():
            if nid and nid not in graph_ids and not nid.startswith("clinvar:"):
                review.append({"claim_id": cid, "role": role, "mention": "", "proposed": nid,
                               "confidence": "high", "shortlist": "", "reason": "resolved id not in graph.json"})
                row[role] = None
    return review


def paper_node(p: dict) -> dict:
    ref = f"pmid:{p['pmid']}" if p["pmid"] else f"doi:{p['doi']}"
    authors = []
    for a in p["authors"]:
        d = {"name": f"{a['fore']} {a['last']}".strip()}
        if a.get("affiliations"):
            d["affiliation"] = a["affiliations"][0]
        if a.get("orcid"):
            d["orcid"] = a["orcid"]
        authors.append(d)
    props = {"title": p["title"], "abstract": p["abstract"], "journal": p["journal"], "authors": authors,
             "pub_types": p["pub_types"]}
    if p.get("year"):
        props["year"] = p["year"]
    if p.get("doi"):
        props["doi"] = p["doi"]
    if p.get("preprint"):
        props["preprint"] = True
    return {"id": ref, "type": "Paper", "label": p["title"], "synonyms": [], "props": props}


def contract_claims(raw: list[dict], links: dict, papers: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Every raw claim in contract form: resolved node ids where the reconciler was confident,
    `text:<mention>` otherwise (ClaimsBundle keeps unresolved mentions so nothing is silently
    dropped; the graph build links only claims whose ends are node ids). Returns (claims,
    Paper nodes they cite, counts). Each claim is validated with the frozen `Claim` model."""
    from pipeline.models import Claim

    by_ref = {(f"pmid:{p['pmid']}" if p["pmid"] else f"doi:{p['doi']}"): p for p in papers}
    out, cited = [], {}
    counts = {"claims": len(raw), "fully_resolved": 0, "unresolved_subject_or_object": 0, "unresolved_variant": 0}
    for c in raw:
        l = links.get(c["id"], {})
        ends = {r: l.get(r) or f"text:{c[r]}" for r in ("subject", "object")}
        variant = (l.get("variant") or f"text:{c['variant']}") if c.get("variant") else ""
        if not (l.get("subject") and l.get("object")):
            counts["unresolved_subject_or_object"] += 1
        elif variant.startswith("text:"):
            counts["unresolved_variant"] += 1
        else:
            counts["fully_resolved"] += 1
        claim = {"id": c["id"], "pmid": c["pmid"], "paper": c["paper"], "quote": c["quote"],
                 "polarity": c["polarity"], "subject": ends["subject"], "predicate": c["predicate"],
                 "object": ends["object"], "variant": variant, "study_type": c["study_type"],
                 "n_patients": c["n_patients"]}
        out.append(Claim.model_validate(claim).model_dump(mode="json"))
        cited[c["paper"]] = paper_node(by_ref[c["paper"]])
    return out, sorted(cited.values(), key=lambda n: n["id"]), counts


TOPIC_GENE = {"stat1": "STAT1", "stat3": "STAT3", "jak1": "JAK1", "jak2": "JAK2", "jak3": "JAK3",
              "tyk2": "TYK2", "il2rg": "IL2RG"}


def coverage_by_entity(search_log: list[dict], papers: list[dict], gene_ids: dict[str, str]) -> dict:
    """PubMed search log → contract `coverage`, keyed by entity: topic searches by the cluster
    gene they are about, Section 11 title searches by the paper found (or `text:<key>`)."""
    found = {}
    for p in papers:
        for f in p["found_by"]:
            if f.startswith("section11:"):
                found[f.split(":", 1)[1]] = f"pmid:{p['pmid']}" if p["pmid"] else f"doi:{p['doi']}"
    out: dict[str, dict] = {}
    for s in search_log:
        tag = s["for"]
        if tag.startswith("topic:"):
            sym = TOPIC_GENE.get(tag.split(":", 1)[1].split("_")[0])
            key = gene_ids.get(sym) if sym else None
            key = key or f"text:{tag}"
        else:
            key = found.get(tag, f"text:{tag}")
        out.setdefault(key, {"searched": []})["searched"].append(
            {"source": s["source"], "query": s["query"], "date": s["date"], "hits": s["hits"]})
    return out


def main() -> None:
    import datetime as dt

    from pipeline.extract import backbone
    from pipeline.models import ClaimsBundle

    build = REPO_ROOT / "data" / "build"
    raw = json.loads((build / "claims.raw.json").read_text())
    pubmed = json.loads((build / "papers.json").read_text())
    papers = pubmed["papers"]
    nodes = list(backbone.load_nodes())
    index = NodeIndex(nodes)
    titles = {(f"pmid:{p['pmid']}" if p["pmid"] else f"doi:{p['doi']}"): p["title"] for p in papers}
    links, review = link_claims(raw["claims"], nodes, index=index, gene_diseases=backbone.gene_linked_diseases(),
                                titles=titles,
                                variant_lookup=backbone.clinvar_variants,
                                workers=int(os.environ.get("ATLAS_LLM_WORKERS", "8")))
    n_applied = 0
    if DECISIONS.exists():
        review, n_applied = apply_review_decisions(links, review, list(csv.DictReader(DECISIONS.open())),
                                                   set(index.nodes))
    graph_path = build / "graph.json"
    if graph_path.exists() and os.environ.get("ATLAS_RECHECK_GRAPH") == "1":
        # Opt-in: graph.json is now built *from* claims.json (pipeline/export.py), so re-checking against
        # it would drop every newly resolved id. Use when a lane-A graph built independently exists.
        review += check_against_graph(links, {n["id"] for n in json.loads(graph_path.read_text())["nodes"]})
    claims, paper_nodes, counts = contract_claims(raw["claims"], links, papers)
    gene_ids = {n["label"]: n["id"] for n in nodes if n["type"] == "Gene" and n["label"] in backbone.CLUSTER_GENES}
    bundle = ClaimsBundle.model_validate({
        "extracted_at": raw["built_at"],
        "model": raw["model"],
        "papers": paper_nodes,
        "claims": claims,
        "unresolved": review,
        "coverage": coverage_by_entity(pubmed["coverage"], papers, gene_ids),
    })
    bundle.dump(build / "claims.json")
    referenced = sorted({c[k] for c in claims for k in ("subject", "object", "variant")
                         if c[k] and not c[k].startswith("text:")})
    (build / "claims.meta.json").write_text(json.dumps({
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "resolved_against": "optimuskg backbone" + (" + graph.json" if graph_path.exists() else ""),
        "stats": {"extraction": raw["stats"], "reconcile": counts, "review_rows": len(review),
                  "human_decisions_applied": n_applied},
        "nodes": [index.nodes[i] for i in referenced],
    }, indent=1, ensure_ascii=False))
    raw_by_id = {c["id"]: c for c in raw["claims"]}
    prio = review_priority(review, raw_by_id, index)
    with PRIORITY.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["decision", "claim_id", "role", "mention", "node_id", "node_label",
                                          "claims_in_group", "model_reason", "quote"])
        w.writeheader()
        w.writerows(prio)
    with open(REPO_ROOT / "data" / "review_queue.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["claim_id", "role", "mention", "proposed", "confidence", "shortlist", "reason"])
        w.writeheader()
        w.writerows(review)
    print(f"reconcile: {counts}; review rows: {len(review)}; human decisions applied: {n_applied}; "
          f"priority review rows: {len(prio)}", file=sys.stderr)


if __name__ == "__main__":
    main()
