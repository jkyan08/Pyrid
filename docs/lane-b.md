# Lane B: Evidence + AI

Owner files: `pipeline/sources/pubmed.py`, `pipeline/extract/*`, `pipeline/llm/*`, `eval/`, `tests/evidence/`.

## Run order

```bash
python -m pipeline.sources.pubmed      # → data/build/papers.json (no OpenAI needed)
python -m pipeline.extract.investigators  # → data/build/investigators.json (no OpenAI needed)
python -m pipeline.extract.claims      # → data/build/claims.raw.json (mentions as text)
python -m pipeline.extract.reconcile   # → data/build/claims.json (contract), data/review_queue.csv
python -m pipeline.llm.explain         # → data/build/explanations.json (demo journeys)
python -m eval.sample                  # → eval/claims_review.csv (40 claims to label)
python -m eval.precision
```

Needs `OPENAI_MODEL` and `OPENAI_EMBED_MODEL` (bare names, e.g. `gpt-6-astra`), plus `OPENAI_API_KEY` or `OPENROUTER_API_KEY`
(OpenRouter is used when its key is set; values in `.env` fill unset variables). `ATLAS_CACHE_DIR` points at the shared cache.
`ATLAS_OFFLINE=1` makes any uncached LLM call fail fast; `explain` and `draft` then fall back to the template.

### Provider (temporary: Claude, OpenAI later)

Structured-output calls (extract, reconcile adjudication, explain, draft) currently run on Claude:
`ATLAS_LLM_PROVIDER=anthropic`, `ANTHROPIC_MODEL=claude-sonnet-5-5`, `ANTHROPIC_API_KEY`, with Anthropic's
server-side refusal fallback (`fallbacks: "default"`). Unset `ATLAS_LLM_PROVIDER` to go back to OpenAI;
earlier `gpt-6-astra` results are still cached. Embeddings always use OpenAI models (Anthropic has none).
The `anthropic` package is not in M0's `pyproject.toml` yet (locked: integrator sign-off), so run lane B
commands with `uv run --with anthropic ...`.

## For lane A (graph)

- `claims.json`: `claims` validated against `pipeline.models.Claim`; `subject`/`object`/`variant` are node IDs,
  `paper` is `pmid:<n>`. Only claims whose ends all resolved are included; the rest are in the review queue.
  `papers` holds the Paper nodes those claims cite, with `props.abstract` (the contract test checks quotes
  against it). Build treatment edges from `treats` claims; `polarity` gives supporting vs contradicting,
  `study_type` and `n_patients` drive `contested` / `small_n`.
- `claims.json` → `stats` has the quote-check drop rate for README Section 14.
- `investigators.json`: grouped by ORCID (`high`) or name + affiliation (`low`); `description` is
  "author of N papers on X"; `contact` only when the paper prints the email (with the source PMID).
- `papers.json`: per paper, authors with affiliations and ORCID (for Investigator nodes) and `coverage`
  (every search with query, date and hits, for `meta.coverage`). `unresolved` lists Section 11 papers that
  didn't resolve.

## For lane C (API)

Pure functions; routes just call them and render the result.

```python
import pipeline.llm as llm                  # explain/draft are re-exported on the package
llm.explain(ids: list[str], graph: dict)    -> {"text", "citations", "mode", "sentences": [{"text", "cites"}], "errors"}
llm.draft(entity_id_or_ids, graph, focus=None) -> same, plus "notice" (show it: drafts are never sent)
```

Matches `api/llm_seam.py` on lane C's branch (`text` + `citations`). `mode` is `llm`, `llm_retry`,
`template` (validated fallback, also used if the provider errors) or `empty` (nothing to explain: show as a gap).
Precomputed results live in `explanations.json` keyed by entity id (a path is its ids joined with `|`;
drafts are `draft:<key>`), each with `text` and `citations`, which is the lookup `/explain` already does.
Demo journeys are read from `data/build/journeys.json` (`{name: [[ids...], ...]}`).

Symptom → HPO for Devon: `NodeIndex(graph["nodes"], types={"Phenotype"})` and `link(text, index)`.

## Status / open items

- On M0: `pipeline/extract/_compat.py` is a thin wrapper over `pipeline.cache`.
- OpenRouter limits new accounts to 20 requests/min per model; `ATLAS_LLM_RPM` (default 18) paces live calls
  and `ATLAS_LLM_WORKERS` (default 16) sets concurrency.
- All 7 Section 11 papers resolve to PMIDs by title. The NK-cell bioRxiv preprint has a published JACI version,
  so it resolves via PubMed; the bioRxiv path remains as fallback.
