# Atlas API (lane C)

FastAPI app serving the knowledge graph per [`api/CONTRACT.md`](CONTRACT.md)
(the spec for every endpoint shape). The graph data contract itself is README
§4, owned by `pipeline/models.py` (M0).

> Research navigation, not medical advice. No dosing information anywhere.
> `/draft` output is a draft; nothing is ever sent.

## Run locally

From the **repo root** (the `api` package is imported as `api.main`, nothing
is pip-installed):

```bash
uvicorn api.main:app --reload --port 8000
```

Dev deps come from the root `pyproject.toml` (M0-owned, canonical). The
`api/requirements.txt` here lists deploy-image deps only (fastapi, uvicorn).

## Graph loading (fallback chain)

First existing file wins:

1. `$ATLAS_GRAPH_PATH` (env override; empty or missing paths are skipped)
2. `data/build/graph.json` (real, M1+)
3. `data/build/graph.mock.json` (M0 mock)
4. `api/fixtures/graph.mock.json` (lane C dev fixture — placeholder only)

Explanations: `$ATLAS_EXPLANATIONS_PATH` → `data/build/explanations.json` →
`api/fixtures/explanations.mock.json`. `GET /health` reports which file was
loaded and `mode: "mock" | "real"` (mock when the resolved path contains
`mock`). Indexes (nodes/edges/claims by id, edges by endpoint, cluster
membership, search tokens) are built once at load.

## Endpoints

See `api/CONTRACT.md` for full shapes. Implemented: `/health`, `/graph`,
`/search`, `/entity/{id}`, `/cluster/{id}`, `/edge/{id}`, `POST /explain`,
`POST /draft`, `/gaps/{id}`, `/mechanism-search?direction=dampen|boost`.
CORS is enabled for all origins (demo). Unknown ids → 404 `{"detail": ...}`.

**Documented choice (CONTRACT leaves it open):** `GET /search?q=` with an
empty/blank `q` returns `200 {"query": "", "results": []}`, not 422.

### Natural-language search

`/search` is hybrid. Every query gets lexical matching (label, synonym, shared words). On top:

- **Semantic** (`match_kind: "semantic"`): the query is embedded and compared with
  `data/build/search_index.json`, so "my son keeps getting thrush and has thyroid problems" or
  "medicines that block JAK" find candidiasis, hypothyroidism or JAK inhibitors without sharing
  words with them. Needs one embeddings call per query (cached in memory):
  `OPENAI_API_KEY` and/or `OPENROUTER_API_KEY` in the API's environment (tried in turn; the
  working one is remembered). The index must have been built from the served `graph.json`
  (`meta.built_at`); otherwise it is ignored.
- **Related** (`match_kind: "related"`, no API): diseases that have the symptoms the query
  matched, including more specific HPO terms (`is_a`), weighted by information content and
  discounted for grouping terms and catch-all annotations. The `resolution` line says which
  symptoms matched ("has 2 of the features you searched for: …").

No key, `ATLAS_OFFLINE=1`, or a failed call: lexical + related only, never an error.
Rebuild the index after `make data`: `make search-index`.

## LLM seam

`/explain` and `/draft` try, in order: `pipeline.llm.explain/draft` (lane B,
imported at call time — a missing or broken module never 500s), then the
precomputed explanations file (keyed by `ids[0]`), then a templated fallback
built only from edge/claim fields already in the graph. The response's
`source` field (`live | precomputed | fallback`) tells the UI when it is in
degraded/"cached only" mode. `/draft` always returns the exact disclaimer:
`Draft only — never sent. Research navigation, not medical advice.`

## Tests

```bash
python -m pytest api/tests -q   # from the repo root
```

Tests run against the committed dev fixture plus tmp-dir graphs generated
inside the tests; they never read or write `data/` (M0-owned).

## Deploy (Render-ready)

`render.yaml` at the repo root points Render at `api/Dockerfile` with the
repo root as build context (`healthCheckPath: /health`). The image:

- is `python:3.12-slim` + `api/requirements.txt` only (no numpy/polars);
- copies `api/` including `api/fixtures/`, so it **boots and serves mock mode
  even without `data/build`**;
- honors `$PORT` (Render sets it; defaults to 8000).

To serve the real graph: bake `data/build/` into the image (uncomment the
`COPY data/build/` line in the Dockerfile once it exists in the context) or
set `ATLAS_GRAPH_PATH`/`ATLAS_EXPLANATIONS_PATH` to a mounted path.

Build and run locally:

```bash
docker build -f api/Dockerfile -t atlas-api .
docker run --rm -p 8000:8000 atlas-api
curl localhost:8000/health
```
