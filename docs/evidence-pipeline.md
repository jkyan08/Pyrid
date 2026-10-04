# Evidence pipeline

The evidence pipeline turns PubMed abstracts into quote-verified claims linked to graph nodes, and
produces the cited explanations and outreach drafts the API serves. Code: `pipeline/sources/pubmed.py`,
`pipeline/extract/*`, `pipeline/llm/*`, `eval/`; tests in `tests/evidence/`.

## Stages

1. **PubMed fetch** (`pipeline.sources.pubmed`): resolves the Section 11 papers by title, runs capped topic
   searches over the cluster, fetches abstracts and author lists, and logs every search (query, date, hits,
   zero-hit searches included) as coverage data.
2. **Investigators** (`pipeline.extract.investigators`): deterministic, no LLM; groups paper authors by ORCID
   or by name + a shared affiliation token.
3. **Claim extraction with quote check** (`pipeline.extract.claims`): structured-output calls turn each
   abstract into claims in the `graph.json` contract shape. Every claim carries a verbatim quote that must
   string-match its abstract after whitespace normalization; claims that fail are dropped and counted, never
   repaired.
4. **Reconciliation** (`pipeline.extract.reconcile`): maps each claim's subject, object and variant mentions
   to node ids, restricted to the node types the role allows. Exact label/synonym matches are accepted
   directly; otherwise a lexical prefilter plus embedding rerank builds a shortlist and the model picks one
   node or "none". Only high-confidence picks are accepted.
5. **Review queue**: every mention that did not resolve with high confidence goes to
   `data/review_queue.csv`, never into the graph.
6. **Explain / draft** (`pipeline.llm.explain`, `pipeline.llm.draft`): cited plain-language explanations
   and outreach drafts; every sentence must cite an edge or claim id from the input, and the validator
   rejects anything else.
7. **Eval** (`eval.sample`, `eval.precision`): samples 40 raw extracted claims for human labelling and
   reports extraction precision from the labels.

## Run order

```bash
python -m pipeline.sources.pubmed      # → data/build/papers.json (no OpenAI needed)
python -m pipeline.extract.investigators  # → data/build/investigators.json (no OpenAI needed)
python -m pipeline.extract.claims      # → data/build/claims.raw.json (mentions as text)
python -m pipeline.extract.reconcile   # → data/build/claims.json (ClaimsBundle), claims.meta.json, data/review_queue.csv
python -m pipeline.llm.explain         # → data/build/explanations.json (demo journeys)
python -m eval.sample                  # → eval/claims_review.csv (40 claims to label)
python -m eval.precision
```

Needs `OPENAI_MODEL` and `OPENAI_EMBED_MODEL` (bare names, e.g. `gpt-6-astra`), plus `OPENAI_API_KEY` or `OPENROUTER_API_KEY`
(OpenRouter is used when its key is set; values in `.env` fill unset variables). `ATLAS_CACHE_DIR` points at the shared cache.
`ATLAS_OFFLINE=1` makes any uncached LLM call fail fast; `explain` and `draft` then fall back to the template.

### LLM provider

The provider switch lives in `pipeline/llm/client.py`. Structured-output calls (extract, reconcile
adjudication, explain, draft) use OpenAI by default; `ATLAS_LLM_PROVIDER=anthropic` switches them to
Anthropic (`ANTHROPIC_MODEL`, `ANTHROPIC_API_KEY`), with Anthropic's server-side refusal fallback
(`fallbacks: "default"`). The checked-in claims bundle was produced with `ATLAS_LLM_PROVIDER=anthropic` and
`ANTHROPIC_MODEL=claude-sonnet-5-5`; earlier `gpt-6-astra` results are still cached, so unsetting
`ATLAS_LLM_PROVIDER` goes back to OpenAI. Embeddings always use the OpenAI model in `OPENAI_EMBED_MODEL`
(Anthropic has no embeddings endpoint). The `anthropic` package is not in `pyproject.toml`, so run the
pipeline with that provider as `uv run --with anthropic ...`.

## Outputs consumed by the graph build

- `claims.json` is a `ClaimsBundle` (`ClaimsBundle.load` passes): all 935 quote-verified claims;
  `subject`/`object`/`variant` are node ids where reconcile was confident, else `text:<mention>`. Only
  claims whose ends are node ids are linked. `papers` carry `props.abstract` and author dicts (`name`,
  `affiliation`, `orcid`) for Investigator nodes. `coverage` is keyed by cluster gene id (topic searches) or
  paper id.
- Reconcile does not wait for a built graph: it resolves against the same OptimusKG backbone files the
  graph build loads (`data/raw/optimuskg/`, contract id formats), `mech:<GENE>:<dir>` nodes, and ClinVar
  (`clinvar:<id>`). When `data/build/graph.json` exists it re-checks every id against it. Role-aware:
  `treats` = Drug → Disease, `has_mechanism` = Variant/Disease/Gene → Mechanism, `causes` → Disease,
  `has_phenotype` → HPO.
- `claims.meta.json` → `nodes`: every node a claim references (incl. ClinVar variants, mech: nodes), in
  contract shape, so the graph build can add any it has not loaded. Drug ids are the backbone's bare ChEMBL
  ids (`CHEMBL1789941`); both sides must normalise them the same way.
- Evidence for the tests: ruxolitinib (`CHEMBL1789941`) → STAT1 GoF disease `MONDO:0013599`: 11 supporting,
  4 contradicting (test 4). Six STAT1 ClinVar variants → `mech:STAT1:GoF` (test 7).
  Disease → mechanism claims for STAT1 GoF, STAT3 GoF/DN, TYK2 LoF, JAK3 LoF.
- `claims.meta.json` → `stats` has the quote-check drop rate and reconcile counts for README Section 14.
- `investigators.json`: grouped by ORCID (`high`) or name + affiliation (`low`); `description` is
  "author of N papers on X"; `contact` only when the paper prints the email (with the source PMID).
- `papers.json`: per paper, authors with affiliations and ORCID (for Investigator nodes) and `coverage`
  (every search with query, date and hits, for `meta.coverage`). `unresolved` lists Section 11 papers that
  didn't resolve.

## Functions used by the API

Pure functions; routes just call them and render the result.

```python
import pipeline.llm as llm                  # explain/draft are re-exported on the package
llm.explain(ids: list[str], graph: dict)    -> {"text", "citations", "mode", "sentences": [{"text", "cites"}], "errors"}
llm.draft(entity_id_or_ids, graph, focus=None) -> same, plus "notice" (show it: drafts are never sent)
```

Matches `api/llm_seam.py` (`text` + `citations`). Called the way the API calls them (no `fallback`),
`explain`/`draft` return a result only when the model produced validated cited text (`mode` `llm` /
`llm_retry`), and **None** otherwise (provider error, offline cache miss, or nothing to explain), so
`/explain` falls through to the precomputed `explanations.json` and then the API's own templated fallback.
The seam treats `None` as the quiet "no live answer" case. `fallback=True` returns the evidence pipeline's
validated template instead (used by `precompute`). Precomputed results are keyed by entity id (a path is its
ids joined with `|`; drafts are `draft:<key>`), each with `text` and `citations`.

Run the API tests with `ATLAS_OFFLINE=1`: with `anthropic` installed, `/explain` on an uncached id would
otherwise make a live model call.

Demo journeys are read from `data/build/journeys.json` (`{name: [[ids...], ...]}`).

Symptom → HPO for Devon: `NodeIndex(graph["nodes"], types={"Phenotype"})` and `link(text, index)`.

## Notes

- `pipeline/extract/_compat.py` is a thin wrapper over `pipeline.cache`.
- OpenRouter limits new accounts to 20 requests/min per model; `ATLAS_LLM_RPM` (default 18) paces live calls
  and `ATLAS_LLM_WORKERS` sets concurrency (default 6 for extraction, 8 for reconciliation).
- All 7 Section 11 papers resolve to PMIDs by title. The NK-cell bioRxiv preprint has a published JACI version,
  so it resolves via PubMed; the bioRxiv path remains as fallback.
