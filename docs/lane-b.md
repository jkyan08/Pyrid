# Lane B: Evidence + AI

Owner files: `pipeline/sources/pubmed.py`, `pipeline/extract/*`, `pipeline/llm/*`, `eval/`, `tests/evidence/`.

## Run order

```bash
python -m pipeline.sources.pubmed      # → data/build/papers.json (no OpenAI needed)
python -m pipeline.extract.claims      # → data/build/claims.json
python -m pipeline.extract.reconcile   # → data/build/claim_links.json, data/review_queue.csv
python -m pipeline.llm.explain         # → data/build/explanations.json (demo journeys)
python -m eval.sample                  # → eval/claims_review.csv (40 claims to label)
python -m eval.precision
```

Needs `OPENAI_MODEL` and `OPENAI_EMBED_MODEL`. `ATLAS_CACHE_DIR` points at the shared cache.
`ATLAS_OFFLINE=1` makes any uncached LLM call fail fast; `explain` and `draft` then fall back to the template.

## For lane A (graph)

- `claims.json`: `claims` in the contract shape. `subject` and `object` are mention text; resolved node IDs
  are in `claim_links.json` as `{claim_id: {subject, object}}` (null = unresolved, see review queue).
  Build treatment edges from claims where both ends resolve. `polarity` gives supporting vs contradicting.
  `study_type` and `n_patients` drive `contested` / `small_n`.
- `claims.json` → `stats` has the quote-check drop rate for README Section 14.
- `papers.json`: per paper, authors with affiliations and ORCID (for Investigator nodes) and `coverage`
  (every search with query, date and hits, for `meta.coverage`). `unresolved` lists Section 11 papers that
  didn't resolve.

## For lane C (API)

Pure functions; routes just call them and render the result.

```python
from pipeline.llm.explain import explain, cache_key
from pipeline.llm.draft import draft
explain(ids: list[str], graph: dict) -> {"mode", "sentences": [{"text", "cites"}], "text", "errors"}
draft(ids, graph)                    -> same, plus "notice" (show it: drafts are never sent)
```

`mode` is `llm`, `llm_retry`, `template` (validated fallback) or `empty` (nothing to explain: show as a gap).
Precomputed results live in `explanations.json` under `cache_key(ids)` / `cache_key(ids, "draft")`; check
there first. Demo journeys are read from `data/build/journeys.json` (`{name: [[ids...], ...]}`).

Symptom → HPO for Devon: `NodeIndex(graph["nodes"], types={"Phenotype"})` and `link(text, index)`.

## Status / open items

- M0 not landed yet: `pipeline/extract/_compat.py` stands in for `pipeline/cache.py`. Swap its bodies for
  `pipeline.cache` calls when M0 merges. Deps used: `httpx`, `openai`, `pydantic`, `pytest`.
- All 7 Section 11 papers resolve to PMIDs by title. The NK-cell bioRxiv preprint has a published JACI version,
  so it resolves via PubMed; the bioRxiv path remains as fallback.
