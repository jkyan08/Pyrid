"""Rare Disease Atlas API (lane C) — FastAPI app per api/CONTRACT.md.

Research navigation, not medical advice. No dosing information anywhere.
Drafts are drafts; nothing is ever sent.

Run from the repo root: `uvicorn api.main:app --reload --port 8000`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api import llm_seam
from api.graph_store import (
    GraphStore,
    load_explanations,
    resolve_explanations_path,
    resolve_graph_path,
)

FOOTER = "Research navigation, not medical advice."
DRAFT_DISCLAIMER = "Draft only — never sent. Research navigation, not medical advice."

# summary buckets: (relations in priority order, line prefix)
_SUMMARY_BUCKETS: list[tuple[tuple[str, ...], str]] = [
    (("causes", "has_mechanism"), "What is broken"),
    (("similar_to", "has_phenotype", "participates_in"), "Who shares it"),
    (("covers", "supports_community", "studies", "treats", "works_on"), "What already exists"),
]

_EVIDENCE_RANK = {
    "observed": 6,
    "trial": 5,
    "cohort": 4,
    "case_series": 3,
    "case_report": 2,
    "extracted": 2,
    "in_vitro": 1,
    "review": 1,
    "inferred": 0,
}

_WRONG_DIRECTION_REASON = {
    ("dampen", "LoF"): "loss-of-function mechanism: dampening is the wrong direction",
    ("boost", "GoF"): "gain-of-function mechanism: boosting is the wrong direction",
}
_RIGHT_DIRECTION = {"dampen": "GoF", "boost": "LoF"}


class ExplainRequest(BaseModel):
    ids: list[str]


class DraftRequest(BaseModel):
    entity_id: str
    focus: str | None = None


def _edge_labels(edge: dict[str, Any]) -> list[str]:
    labels = (edge.get("attributes") or {}).get("labels")
    return list(labels) if labels else [edge.get("status", "")]


def _summary_lines(store: GraphStore, node: dict[str, Any]) -> list[str]:
    """Up to three plain-language lines, each citing one edge id in brackets."""
    neighbors = store.neighbors(node["id"])
    used: set[str] = set()
    lines: list[str] = []

    def sentence(item: dict[str, Any]) -> str:
        edge = item["edge"]
        source_label = store.nodes_by_id.get(edge["source"], {}).get("label", edge["source"])
        target_label = store.nodes_by_id.get(edge["target"], {}).get("label", edge["target"])
        relation = edge["relation"].replace("_", " ")
        return f"{source_label} {relation} {target_label} [{edge['id']}]."

    for relations, prefix in _SUMMARY_BUCKETS:
        for relation in relations:
            item = next(
                (
                    n
                    for n in neighbors
                    if n["edge"]["relation"] == relation and n["edge"]["id"] not in used
                ),
                None,
            )
            if item is not None:
                used.add(item["edge"]["id"])
                lines.append(f"{prefix}: {sentence(item)}")
                break

    # top up from any remaining edges so sparse nodes still get a card
    for item in neighbors:
        if len(lines) >= 3:
            break
        if item["edge"]["id"] not in used:
            used.add(item["edge"]["id"])
            lines.append(sentence(item))
    return lines[:3]


def _next_question(label: str) -> str:
    return (
        f"No supported route found for {label}. Next question to test: which "
        f"pathway or mechanism is {label} part of, and do any associated "
        "phenotypes overlap an existing disease cluster?"
    )


def _mechanism_direction(mechanism: dict[str, Any]) -> str | None:
    direction = (mechanism.get("props") or {}).get("direction")
    if direction:
        return direction
    parts = mechanism.get("id", "").split(":")
    return parts[2] if len(parts) >= 3 and parts[0] == "mech" else None


def create_app(
    graph_path: Path | str | None = None,
    explanations_path: Path | str | None = None,
    repo_root: Path | str | None = None,
) -> FastAPI:
    store = GraphStore.load(
        Path(graph_path) if graph_path else resolve_graph_path(repo_root)
    )
    explanations = load_explanations(
        Path(explanations_path)
        if explanations_path
        else resolve_explanations_path(repo_root)
    )

    app = FastAPI(
        title="Rare Disease Atlas API",
        description=f"JAK-STAT MVP (lane C). {FOOTER}",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # demo-wide CORS per CONTRACT
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = store

    @app.get("/")
    def root() -> dict[str, Any]:
        return {
            "service": "rare-disease-atlas-api",
            "docs": "/docs",
            "disclaimer": FOOTER,
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "graph_path": str(store.path),
            "graph_built_at": store.meta.get("built_at", ""),
            "nodes": len(store.nodes),
            "edges": len(store.edges),
            "mode": store.mode,
        }

    @app.get("/graph")
    def graph() -> dict[str, Any]:
        return store.data

    @app.get("/search")
    def search(q: str = Query(default="")) -> dict[str, Any]:
        # Empty q -> 200 with empty results (documented choice, api/README.md)
        return {"query": q, "results": store.search(q)}

    @app.get("/entity/{entity_id:path}")
    def entity(entity_id: str) -> dict[str, Any]:
        node = store.nodes_by_id.get(entity_id)
        if node is None:
            # Honest-gap response for ids known only to meta.coverage
            # (ground-truth test 6 shape; eng-review D4).
            coverage = store.coverage_for(entity_id)
            if coverage is None:
                raise HTTPException(404, detail=f"unknown entity id: {entity_id}")
            return {
                "node": {
                    "id": entity_id,
                    "type": "Unknown",
                    "label": entity_id,
                    "synonyms": [],
                    "props": {},
                },
                "summary": [],
                "neighbors": [],
                "cluster_ids": [],
                "assets": [],
                "investigators": [],
                "no_supported_route": True,
                "coverage": coverage,
                "next_question": _next_question(entity_id),
            }
        neighbors = store.neighbors(entity_id)
        no_supported_route = not store.has_supported_edge(entity_id)
        assets = [
            {"edge": n["edge"], "node": n["node"]}
            for n in neighbors
            if n["node"].get("type") in ("Registry", "Study", "PatientGroup")
        ]
        investigators = [
            {"edge": n["edge"], "node": n["node"]}
            for n in neighbors
            if n["node"].get("type") == "Investigator"
        ]
        return {
            "node": node,
            "summary": _summary_lines(store, node),
            "neighbors": neighbors,
            "cluster_ids": store.cluster_ids_of(entity_id),
            "assets": assets,
            "investigators": investigators,
            "no_supported_route": no_supported_route,
            "coverage": store.coverage_for(entity_id),
            "next_question": _next_question(node["label"]) if no_supported_route else None,
        }

    @app.get("/cluster/{cluster_id}")
    def cluster(cluster_id: str) -> dict[str, Any]:
        found = store.clusters_by_id.get(cluster_id)
        if found is None:
            raise HTTPException(404, detail=f"unknown cluster id: {cluster_id}")
        members = [
            store.nodes_by_id.get(member_id, {"id": member_id})
            for member_id in found.get("members", [])
        ]
        return {"cluster": found, "members": members, "why": found.get("why", {})}

    @app.get("/edge/{edge_id}")
    def edge(edge_id: str) -> dict[str, Any]:
        found = store.edges_by_id.get(edge_id)
        if found is None:
            raise HTTPException(404, detail=f"unknown edge id: {edge_id}")
        return {
            "edge": found,
            "source_node": store.nodes_by_id.get(found["source"]),
            "target_node": store.nodes_by_id.get(found["target"]),
            "supporting_claims": store.resolve_claims(found.get("supporting", [])),
            "contradicting_claims": store.resolve_claims(found.get("contradicting", [])),
            "labels": _edge_labels(found),
        }

    @app.post("/explain")
    def explain(request: ExplainRequest) -> dict[str, Any]:
        live = llm_seam.try_live_explain(request.ids, store.data)
        if live is not None:
            return {
                "text": live["text"],
                "citations": list(live.get("citations", [])),
                "source": "live",
            }
        if request.ids:
            entry = explanations.get(request.ids[0])
            if entry is not None:
                return {
                    "text": entry.get("text", ""),
                    "citations": list(entry.get("citations", [])),
                    "source": "precomputed",
                }
        return llm_seam.fallback_explain(request.ids, store)

    @app.post("/draft")
    def draft(request: DraftRequest) -> dict[str, Any]:
        if request.entity_id not in store.nodes_by_id:
            raise HTTPException(404, detail=f"unknown entity id: {request.entity_id}")
        live = llm_seam.try_live_draft(request.entity_id, store.data, request.focus)
        if live is not None:
            payload = {
                "text": live["text"],
                "citations": list(live.get("citations", [])),
                "source": "live",
            }
        else:
            payload = llm_seam.fallback_draft(request.entity_id, store, request.focus)
        payload["disclaimer"] = DRAFT_DISCLAIMER
        return payload

    @app.get("/gaps/{entity_id:path}")
    def gaps(entity_id: str) -> dict[str, Any]:
        coverage = store.coverage_for(entity_id)
        if entity_id not in store.nodes_by_id and coverage is None:
            raise HTTPException(404, detail=f"unknown entity id: {entity_id}")
        missing: list[str] = []
        would_change: list[str] = []

        for searched in (coverage or {}).get("searched", []):
            if searched.get("hits", 0) == 0:
                query_text = searched.get("query", "")
                source_name = searched.get("source", "?")
                missing.append(
                    f"Search returned 0 hits: {source_name} for '{query_text}' "
                    f"on {searched.get('date', '?')}."
                )
                would_change.append(
                    f"Any result for '{query_text}' in {source_name} would fill this gap."
                )

        for edge_item in store.edges_by_endpoint.get(entity_id, []):
            labels = _edge_labels(edge_item)
            if edge_item.get("status") == "contested" or "contested" in labels:
                missing.append(
                    f"The [{edge_item['id']}] ({edge_item['relation']}) evidence is contested: "
                    f"{len(edge_item.get('supporting', []))} supporting vs "
                    f"{len(edge_item.get('contradicting', []))} contradicting claims."
                )
                would_change.append(
                    f"A larger or independent study would resolve the contested "
                    f"evidence [{edge_item['id']}]."
                )
            if "small_n" in labels:
                missing.append(
                    f"The [{edge_item['id']}] evidence is small_n: its supporting "
                    "evidence comes only from small studies."
                )
                would_change.append(
                    f"Evidence from a larger cohort would remove the small_n "
                    f"label [{edge_item['id']}]."
                )
            if edge_item.get("status") == "inferred_unsupported":
                reason = (edge_item.get("confidence_basis") or {}).get(
                    "reason", "no direct evidence"
                )
                missing.append(
                    f"The [{edge_item['id']}] link is an unsupported inference ({reason})."
                )
                would_change.append(
                    f"Direct evidence would change the status of [{edge_item['id']}]."
                )

        honest_gap = not store.has_supported_edge(entity_id)
        if honest_gap:
            label = store.nodes_by_id.get(entity_id, {}).get("label", entity_id)
            missing.append(f"No supported edges exist for {label} in the loaded graph.")
            would_change.append(
                f"Any supported connection for {label} would remove this honest gap."
            )

        return {
            "entity_id": entity_id,
            "coverage": coverage,
            "missing_evidence": missing,
            "what_would_change": would_change,
            "honest_gap": honest_gap,
        }

    @app.get("/mechanism-search")
    def mechanism_search(direction: Literal["dampen", "boost"]) -> dict[str, Any]:
        wanted = _RIGHT_DIRECTION[direction]
        results: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []

        seen: set[str] = set()
        for edge_item in store.edges:
            # "causes" (lane-C fixture) or "has_mechanism" (M0 mock vocabulary)
            if edge_item.get("relation") not in ("causes", "has_mechanism"):
                continue
            # accept either edge orientation (lane A's convention is not frozen)
            src = store.nodes_by_id.get(edge_item["source"])
            tgt = store.nodes_by_id.get(edge_item["target"])
            if src is None or tgt is None:
                continue
            if src.get("type") == "Mechanism" and tgt.get("type") == "Disease":
                mechanism, disease = src, tgt
            elif tgt.get("type") == "Mechanism" and src.get("type") == "Disease":
                mechanism, disease = tgt, src
            else:
                continue
            if disease["id"] in seen:
                continue
            seen.add(disease["id"])
            mech_direction = _mechanism_direction(mechanism)

            if mech_direction == wanted:
                supporting = store.resolve_claims(edge_item.get("supporting", []))
                if supporting:
                    evidence_level = max(
                        (c.get("study_type", "") for c in supporting),
                        key=lambda s: _EVIDENCE_RANK.get(s, 0),
                    )
                else:
                    evidence_level = edge_item.get("evidence_class", "inferred")
                communities = [
                    n["node"]
                    for n in store.neighbors(disease["id"])
                    if n["node"].get("type") in ("PatientGroup", "Registry")
                ]
                results.append(
                    {
                        "node": disease,
                        "mechanism": mechanism,
                        "evidence_level": evidence_level,
                        "communities": communities,
                        "ranked_by": "max supporting-claim study_type, then confidence",
                        "_rank": (
                            _EVIDENCE_RANK.get(evidence_level, 0),
                            edge_item.get("confidence", 0.0),
                        ),
                    }
                )
            else:
                reason = _WRONG_DIRECTION_REASON.get(
                    (direction, mech_direction or ""),
                    f"{mech_direction or 'unknown'} mechanism: not a {direction} target",
                )
                excluded.append({"node": disease, "reason": reason})

        results.sort(key=lambda r: r.pop("_rank"), reverse=True)
        return {"direction": direction, "results": results, "excluded": excluded}

    return app


app = create_app()
