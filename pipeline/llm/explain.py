"""Cited plain-language explanations of a node set or path (README Section 7, use 3).

`explain(ids, graph)` is a pure function of its inputs (plus the on-disk LLM
cache). Lane C's `/explain` route calls it; it never touches HTTP routing.

Flow: build a compact context from the subgraph → structured-output call →
validator → one retry with the errors → templated fallback. The fallback is
always valid, so callers always get cited text.

`python -m pipeline.llm.explain` precomputes the demo journeys into
`data/build/explanations.json`.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Callable

from pydantic import BaseModel, Field

from pipeline.extract._compat import REPO_ROOT
from pipeline.llm import client
from pipeline.llm.validate import DOSING, render, validate_sentences

log = logging.getLogger(__name__)

PROMPT_VERSION = "explain-v1"
MAX_EDGES = 40
MAX_CLAIMS = 30


class CitedSentence(BaseModel):
    text: str = Field(description="One plain-language sentence. No IDs inside the text.")
    cites: list[str] = Field(description="Edge or claim IDs from the input that support this sentence.")


class CitedText(BaseModel):
    sentences: list[CitedSentence]


Parser = Callable[[str, str, type], BaseModel | None]


def subgraph(ids: list[str], graph: dict) -> dict:
    """Nodes, edges and claims relevant to `ids` (node, edge or claim IDs).

    Node IDs pull in the edges among them (and, for a single node, its incident
    edges); edge IDs pull in their endpoints; edges pull in their claims.
    """
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = {e["id"]: e for e in graph.get("edges", [])}
    claims = {c["id"]: c for c in graph.get("claims", [])}

    node_ids = {i for i in ids if i in nodes}
    edge_ids = {i for i in ids if i in edges}
    claim_ids = {i for i in ids if i in claims}
    for e in edges.values():
        ends = {e["source"], e["target"]}
        if ends <= node_ids or (len(node_ids) == 1 and ends & node_ids):
            edge_ids.add(e["id"])
    picked = sorted(edge_ids, key=lambda i: (-edges[i].get("confidence", 0), i))[:MAX_EDGES]
    for i in picked:
        node_ids |= {edges[i]["source"], edges[i]["target"]}
        claim_ids |= set(edges[i].get("supporting", [])) | set(edges[i].get("contradicting", []))
    claim_ids &= claims.keys()
    return {
        "nodes": [nodes[i] for i in sorted(node_ids) if i in nodes],
        "edges": [edges[i] for i in picked],
        "claims": [claims[i] for i in sorted(claim_ids)][:MAX_CLAIMS],
    }


def context_text(sub: dict) -> str:
    label = {n["id"]: n["label"] for n in sub["nodes"]}
    lines = ["NODES"]
    lines += [f"- {n['id']}: {n['label']} ({n['type']})" for n in sub["nodes"]]
    lines.append("EDGES")
    for e in sub["edges"]:
        attrs = e.get("attributes") or {}
        extra = f", labels={attrs['labels']}" if attrs.get("labels") else ""
        extra += f", origin={attrs['origin']}" if attrs.get("origin") else ""
        lines.append(
            f"- {e['id']}: {label.get(e['source'], e['source'])} --{e['relation']}--> {label.get(e['target'], e['target'])} "
            f"[status={e['status']}, evidence={e['evidence_class']}, confidence={e['confidence']:.2f}, "
            f"supporting={e.get('supporting', [])}, contradicting={e.get('contradicting', [])}{extra}]")
    lines.append("CLAIMS")
    for c in sub["claims"]:
        n = f", n={c['n_patients']}" if c.get("n_patients") else ""
        lines.append(f"- {c['id']} ({c['polarity']}, {c['study_type']}{n}, PMID {c['pmid']}): \"{c['quote']}\"")
    return "\n".join(lines)


def allowed(sub: dict) -> tuple[set[str], set[str]]:
    ids = {e["id"] for e in sub["edges"]} | {c["id"] for c in sub["claims"]}
    pmids = {c["pmid"] for c in sub["claims"]}
    return ids, pmids


SYSTEM = """You explain a small piece of a rare-disease research graph to a patient-group leader with no biology training.

Rules:
- Use only the facts in the input. Never add outside knowledge, numbers, names or citations.
- Every sentence must cite at least one edge or claim ID from the input in `cites`. Do not put IDs in the text.
- Say how strong the evidence is in plain words: "observed in a database", "reported in one patient", "contested: \
some reports support it and some contradict it", "inferred, not yet supported".
- If an edge is inferred_unsupported, say plainly that the link is not supported and why, if the input says why.
- Never give medical advice or dosing. This is research navigation.
- 3 to 7 short sentences."""


def _fallback(sub: dict) -> list[dict]:
    label = {n["id"]: n["label"] for n in sub["nodes"]}
    phrase = {
        "supported": "is supported by the evidence collected",
        "contested": "is contested: some reports support it and others contradict it",
        "small_n": "rests on studies of fewer than ten patients",
        "inferred_unsupported": "is inferred but not supported by the evidence collected",
    }
    out = []
    for e in sub["edges"][:7]:
        rel = e["relation"].replace("_", " ")
        out.append({
            "text": f"{label.get(e['source'], e['source'])} {rel} {label.get(e['target'], e['target'])}; "
                    f"this link {phrase.get(e['status'], 'has status ' + e['status'])} "
                    f"({e['evidence_class']}, confidence {e['confidence']:.2f}).",
            "cites": [e["id"]],
        })
    if not out:
        for c in [c for c in sub["claims"] if not DOSING.search(c["quote"])][:5]:
            out.append({"text": f"A {c['study_type'].replace('_', ' ')} reports: \"{c['quote']}\"", "cites": [c["id"]]})
    return out


def _result(mode: str, sentences: list[dict], errors: list) -> dict:
    """`text` and `citations` are what lane C's API reads; `sentences` keeps the per-sentence cites."""
    citations = list(dict.fromkeys(c for s in sentences for c in s["cites"]))
    return {"mode": mode, "text": render(sentences), "citations": citations, "sentences": sentences, "errors": errors}


def generate_cited(system: str, task: str, sub: dict, fallback: Callable[[dict], list[dict]],
                   parser: Parser | None = None) -> dict:
    """Shared LLM → validate → retry → fallback loop for explain and draft."""
    parser = parser or client.parse
    ids, pmids = allowed(sub)
    ctx = context_text(sub)
    errors_seen: list[list[str]] = []
    if ids:
        user = f"[{PROMPT_VERSION}]\n{task}\n\n{ctx}"
        for attempt in range(2):
            if attempt == 1:
                user += "\n\nYour previous answer was rejected:\n- " + "\n- ".join(errors_seen[-1]) + "\nFix every problem."
            try:
                out = parser(system, user, CitedText)
            except client.CacheMiss:
                break
            except Exception as exc:  # provider/auth/network failure: never fail the caller
                log.warning("LLM call failed; using template: %s", exc)
                errors_seen.append([f"llm error: {type(exc).__name__}"])
                break
            if out is None:
                errors_seen.append(["model refused"])
                continue
            sentences = [s.model_dump() for s in out.sentences]
            v = validate_sentences(sentences, ids, pmids)
            if v.ok:
                return _result("llm" if attempt == 0 else "llm_retry", sentences, errors_seen)
            errors_seen.append(v.errors)
    return _result("template", fallback(sub), errors_seen)


def explain(ids: list[str], graph: dict, *, parser: Parser | None = None) -> dict:
    """Cited plain-language explanation of `ids`. Always returns valid cited text (or no sentences
    when the subgraph is empty, which callers should treat as an honest gap)."""
    sub = subgraph(ids, graph)
    if not sub["edges"] and not sub["claims"]:
        return _result("empty", [], [])
    task = f"Explain how these connect: {', '.join(ids)}"
    return generate_cited(SYSTEM, task, sub, _fallback, parser)


def cache_key(ids: list[str] | str, kind: str = "explain") -> str:
    """Key in explanations.json. Lane C looks explanations up by the first requested id, so a
    single-entity explanation is keyed by that id; a path is its ids joined with '|'.
    Drafts are keyed 'draft:<key>'."""
    key = ids if isinstance(ids, str) else "|".join(ids)
    return key if kind == "explain" else f"{kind}:{key}"


# Demo journeys to precompute. Filled with node/edge IDs once graph v0 lands
# (sync point 1); `journeys.json` beside graph.json overrides this.
JOURNEYS: dict[str, list[list[str]]] = {}


def precompute(graph: dict, journeys: dict[str, list[list[str]]]) -> dict:
    from pipeline.llm.draft import draft
    out = {}
    for name, steps in journeys.items():
        for ids in steps:
            out[cache_key(ids)] = {"journey": name, "ids": ids, **explain(ids, graph)}
            out[cache_key(ids, "draft")] = {"journey": name, "ids": ids, **draft(ids, graph)}
    return out


def main() -> None:
    build = REPO_ROOT / "data" / "build"
    graph_path = build / "graph.json"
    if not graph_path.exists():
        graph_path = build / "graph.mock.json"
    graph = json.loads(graph_path.read_text())
    jpath = build / "journeys.json"
    journeys = json.loads(jpath.read_text()) if jpath.exists() else JOURNEYS
    result = precompute(graph, journeys)
    (build / "explanations.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    modes = {}
    for r in result.values():
        modes[r["mode"]] = modes.get(r["mode"], 0) + 1
    print(f"explanations: {len(result)} from {graph_path.name}; modes {modes}", file=sys.stderr)


if __name__ == "__main__":
    main()
