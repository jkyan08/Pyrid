"""Sourced outreach / shared-research proposal drafts (README Section 7, use 3).

`draft(ids, graph)` returns a draft whose every sentence cites an edge or claim
from the input, validated exactly like `explain`. Drafts are never sent
(hard rule 7); the returned dict says so and lane C must show it.
"""
from __future__ import annotations

from pipeline.llm.explain import Parser, _result, generate_cited, subgraph

NOTICE = "Draft only. Nothing is sent. Research navigation, not medical advice."

SYSTEM = """You draft a short, sourced proposal from a patient-group leader to researchers or a registry, \
suggesting one concrete piece of shared research they could start this week.

Rules:
- Use only the facts in the input. Never invent names, contact details, numbers, dates or citations.
- Every sentence must cite at least one edge or claim ID from the input in `cites`. Do not put IDs in the text.
- Structure: who we are and what we share (1-2 sentences), the evidence and how strong it is, including any \
contradictions (2-3 sentences), the concrete ask (1-2 sentences), what is still unknown (1 sentence).
- Be honest about weak evidence: say "contested" or "small studies" where the input says so.
- No medical advice and no dosing. 5 to 8 sentences. Plain, courteous language."""


def _fallback(sub: dict) -> list[dict]:
    label = {n["id"]: n["label"] for n in sub["nodes"]}
    out = []
    edges = sub["edges"]
    if edges:
        e = edges[0]
        out.append({"text": f"We are writing about the connection between {label.get(e['source'], e['source'])} "
                            f"and {label.get(e['target'], e['target'])}.", "cites": [e["id"]]})
    contested = [e for e in edges if e["status"] in ("contested", "small_n")]
    if contested:
        out.append({"text": "Some of the evidence is mixed or comes from small studies, so it needs pooling "
                            "before anyone can draw conclusions.", "cites": [e["id"] for e in contested[:3]]})
    if edges:
        out.append({"text": "We would like to discuss pooling what is already known across patients and "
                            "registries to test this connection.", "cites": [e["id"] for e in edges[:3]]})
    return out


def draft(ids: list[str] | str, graph: dict, focus: str | None = None, *, parser: Parser | None = None) -> dict:
    """`ids` may be one entity id (lane C's `/draft` passes `entity_id`) or a list of ids.
    `focus` optionally names what the proposal should centre on (e.g. "treatment_response")."""
    ids = [ids] if isinstance(ids, str) else ids
    sub = subgraph(ids, graph)
    if not sub["edges"] and not sub["claims"]:
        return {**_result("empty", [], []), "notice": NOTICE}
    task = f"Draft a proposal about: {', '.join(ids)}"
    if focus:
        task += f"\nFocus the proposal on: {focus.replace('_', ' ')}"
    return {**generate_cited(SYSTEM, task, sub, _fallback, parser), "notice": NOTICE}
