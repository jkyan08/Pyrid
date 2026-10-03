"""LLM seam (api/CONTRACT.md "LLM seam").

Lane B owns pipeline/llm/*; this module only *tries* to import it at call
time and otherwise builds templated fallbacks from fields already in the
loaded graph. A missing or broken pipeline.llm must never cause a 500.

No invented facts: every fallback sentence is assembled from node labels,
edge relations/statuses and claim ids that exist in the graph, and cites
those ids in brackets.
"""

from __future__ import annotations

import importlib
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from api.graph_store import GraphStore


def _live_call(func_name: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
    """Call pipeline.llm.<func_name> if importable; None on any failure."""
    try:
        module = importlib.import_module("pipeline.llm")
    except Exception:
        return None
    func = getattr(module, func_name, None)
    if func is None:
        return None
    try:
        result = func(*args, **kwargs)
    except Exception:
        return None
    if not isinstance(result, dict) or "text" not in result:
        return None
    return result


def try_live_explain(ids: list[str], graph: dict[str, Any]) -> dict[str, Any] | None:
    return _live_call("explain", ids, graph)


def try_live_draft(
    entity_id: str, graph: dict[str, Any], focus: str | None = None
) -> dict[str, Any] | None:
    # Lane B's draft signature is not frozen yet: try with focus, then without.
    result = _live_call("draft", entity_id, graph, focus=focus)
    if result is None:
        result = _live_call("draft", entity_id, graph)
    return result


# ---- templated fallbacks (graph fields only) -------------------------------


def _humanize(relation: str) -> str:
    return relation.replace("_", " ")


def fallback_explain(ids: list[str], store: "GraphStore") -> dict[str, Any]:
    """Templated explanation: one sentence per edge, each citing real ids."""
    lines: list[str] = []
    citations: list[str] = []
    seen_edges: set[str] = set()

    for node_id in ids:
        node = store.nodes_by_id.get(node_id)
        if node is None:
            continue
        for item in store.neighbors(node_id):
            edge = item["edge"]
            if edge["id"] in seen_edges:
                continue
            seen_edges.add(edge["id"])
            source_label = store.nodes_by_id.get(edge["source"], {}).get(
                "label", edge["source"]
            )
            target_label = store.nodes_by_id.get(edge["target"], {}).get(
                "label", edge["target"]
            )
            claim_ids = store.resolve_claims(
                list(edge.get("supporting", [])) + list(edge.get("contradicting", []))
            )
            cited = [edge["id"]] + [c["id"] for c in claim_ids]
            lines.append(
                f"{source_label} {_humanize(edge['relation'])} {target_label} "
                f"({edge['evidence_class']}, status: {edge['status']}) "
                f"[{', '.join(cited)}]."
            )
            citations.extend(cited)

    if not lines:
        text = (
            "No connections are recorded in the loaded graph for the requested "
            "ids, so no cited explanation is available."
        )
    else:
        text = " ".join(lines)
    return {
        "text": text,
        "citations": list(dict.fromkeys(citations)),
        "source": "fallback",
    }


def fallback_draft(
    entity_id: str, store: "GraphStore", focus: str | None = None
) -> dict[str, Any]:
    """Templated outreach draft citing edge/claim ids. Never sent, no dosing."""
    node = store.nodes_by_id[entity_id]
    label = node["label"]
    neighbors = store.neighbors(entity_id)
    citations: list[str] = []

    subject = f"Subject: exploring shared research on {label}"
    if focus:
        subject += f" (focus: {_humanize(focus)})"
    lines = [subject, ""]

    cause = next(
        (
            item
            for item in neighbors
            if item["edge"]["relation"] in ("causes", "has_mechanism")
        ),
        None,
    )
    if cause is not None:
        edge = cause["edge"]
        lines.append(
            f"Our evidence graph links {label} to {cause['node']['label']} "
            f"(relation: {_humanize(edge['relation'])}) [{edge['id']}]."
        )
        citations.append(edge["id"])

    for item in neighbors:
        if item["node"].get("type") in ("Registry", "PatientGroup", "Study"):
            edge = item["edge"]
            lines.append(
                f"Existing asset: {item['node']['label']} "
                f"({item['node']['type']}) [{edge['id']}]."
            )
            citations.append(edge["id"])

    for item in neighbors:
        edge = item["edge"]
        if edge["relation"] != "treats":
            continue
        claims = store.resolve_claims(
            list(edge.get("supporting", [])) + list(edge.get("contradicting", []))
        )
        cited = [edge["id"]] + [c["id"] for c in claims]
        lines.append(
            f"Recorded treatment evidence involving {item['node']['label']} has "
            f"status '{edge['status']}' with {len(edge.get('supporting', []))} "
            f"supporting and {len(edge.get('contradicting', []))} contradicting "
            f"claims [{', '.join(cited)}]."
        )
        citations.extend(cited)

    citations = list(dict.fromkeys(citations))
    if citations:
        lines.append(
            "Proposed next step: review the cited edges together and assess "
            f"whether these assets can be pooled or adapted [{citations[0]}]."
        )
    else:
        lines.append(
            f"No supported connections are recorded for {label}; the only "
            "honest next step is to gather primary evidence."
        )

    return {"text": "\n".join(lines), "citations": citations, "source": "fallback"}
