# Lane C internal contract: API shapes (api/ ↔ web/)

The graph data contract is README §4 (frozen at M0, owned by `pipeline/models.py`).
This file defines only **lane C's** seam: what the FastAPI app returns and what the
Next.js app consumes. Both sides must tolerate extra/missing optional fields —
M0 may settle open contract questions differently.

## Graph loading (API and web static-fallback share this rule)

Resolve the graph file as the first existing of:

1. `$ATLAS_GRAPH_PATH` (env override)
2. `<repo>/data/build/graph.json` (real, M1+)
3. `<repo>/data/build/graph.mock.json` (M0 mock)
4. `<repo>/api/fixtures/graph.mock.json` (lane C dev fixture — placeholder only)

Precomputed explanations: `$ATLAS_EXPLANATIONS_PATH` → `data/build/explanations.json`
→ `api/fixtures/explanations.mock.json`.

The API exposes which file it loaded via `GET /health` → `{"status": "ok",
"graph_path": "...", "graph_built_at": "...", "nodes": N, "edges": N, "mode": "real" | "mock"}`
(`mode: "mock"` when the resolved path contains `mock`).

## LLM seam (lane B's code; never implemented here)

`/explain` and `/draft` try, in order:
1. `pipeline.llm.explain` / `pipeline.llm.draft` (import at call time; lane B owns these,
   signature `explain(ids: list[str], graph: dict) -> dict` returning `{text, citations}`),
2. the precomputed explanations file,
3. a templated fallback built only from edge/claim fields already in the graph.

Response always includes `source: "live" | "precomputed" | "fallback"` so the UI can
label degraded mode ("cached only"). Never error because OpenAI/lane B is unavailable.

## Endpoints

All responses JSON; CORS enabled for all origins (demo). Prefix: none (e.g. `/search`).
404 for unknown ids: `{"detail": "..."}`.

### GET /graph
Entire loaded graph verbatim (for Cytoscape map + static-fallback parity).

### GET /search?q=<free text>
```json
{
  "query": "...",
  "results": [
    {
      "id": "mock:disease:gof",
      "label": "…", "type": "Disease",
      "matched_on": "chronic mock candidiasis",
      "match_kind": "synonym",        // "label" | "synonym" | "free_text"
      "resolution": "matched synonym 'chronic mock candidiasis' → Mock immune dysregulation syndrome",
      "score": 0.93
    }
  ]
}
```
Searches labels + synonyms of every node type (disease, gene, variant, symptom/phenotype,
patient group, mechanism). Case-insensitive token overlap is enough until lane B's
reconcile exists.

### GET /entity/{id}
```json
{
  "node": { …node… },
  "summary": ["plain line 1 [e_000003]", "line 2 [e_000011]", "line 3 [e_000014]"],
  "neighbors": [ { "edge": { …edge… }, "node": { …neighbor node… }, "direction": "out" | "in" } ],
  "cluster_ids": ["cl_mock_gof"],
  "assets": [ { "edge": {…}, "node": {…} } ],          // Registry/Study/PatientGroup neighbors
  "investigators": [ { "edge": {…}, "node": {…} } ],
  "no_supported_route": false,
  "coverage": { …meta.coverage[id] or null… },
  "next_question": null
}
```
**Ground-truth test 6 behavior:** when the entity has **no edges with
`status == "supported"`** (or no edges at all), return `no_supported_route: true`,
`coverage` from `meta.coverage[id]` (searched sources/queries/dates/hits), and a
`next_question` string built from the entity label (templated, no LLM required).
HTTP 200, not an error.

### GET /cluster/{id}
`{ "cluster": {…}, "members": [ …nodes… ], "why": {…} }`

### GET /edge/{id}
```json
{
  "edge": { …edge… },
  "source_node": {…}, "target_node": {…},
  "supporting_claims": [ …full claim objects… ],
  "contradicting_claims": [ …full claim objects… ],
  "labels": ["promising", "contested", "small_n"]   // edge.attributes.labels or [status]
}
```

### POST /explain   body: `{"ids": ["mock:disease:gof", …]}`
`{ "text": "…", "citations": ["e_000003", …], "source": "live|precomputed|fallback" }`
Lookup order per LLM seam above; precomputed is keyed by `ids[0]` (single-entity key).

### POST /draft   body: `{"entity_id": "...", "focus": "registry_pooling" (optional)}`
```json
{ "text": "…sourced outreach draft…", "citations": [...],
  "source": "live|fallback",
  "disclaimer": "Draft only — never sent. Research navigation, not medical advice." }
```

### GET /gaps/{id}
```json
{ "entity_id": "...", "coverage": {…}, "missing_evidence": ["…"],
  "what_would_change": ["…"], "honest_gap": true|false }
```
Built from `meta.coverage` + edges: zero-hit searches, `contested`/`small_n` edges,
`inferred_unsupported` edges are each a "missing evidence" line.

### GET /mechanism-search?direction=dampen
```json
{
  "direction": "dampen",
  "results": [ { "node": {…Disease…}, "mechanism": {…Mechanism node…},
                 "evidence_level": "case_report|case_series|…|observed",
                 "communities": [ …PatientGroup/Registry nodes… ],
                 "ranked_by": "max supporting-claim study_type, then confidence" } ],
  "excluded": [ { "node": {…}, "reason": "loss-of-function mechanism: dampening is the wrong direction" } ]
}
```
`direction=dampen` → diseases caused by a `GoF` mechanism rank; `LoF`-caused diseases
are listed under `excluded` with the reason. (`direction=boost` mirrors it; anything
else → 422.)

## Frontend expectations (web/)

- `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`). If the API is unreachable,
  web falls back to a bundled static copy of the graph (same loader order, file copied
  into `web/public/` at build/dev time) and renders explanations as "cached only".
- Footer on every page: “Research navigation, not medical advice.” No dosing anywhere.
- Draft button output is always labeled draft/never sent.

## Dev commands (until M0's Makefile lands)

- API: `uvicorn api.main:app --reload --port 8000` (from repo root)
- Web: `cd web && npm run dev` (port 3000)
