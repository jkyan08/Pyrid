# Lane B: Evidence + AI

Owner files: `pipeline/sources/pubmed.py`, `pipeline/extract/*`, `pipeline/llm/*`, `eval/`, `tests/evidence/`.

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

### Provider (temporary: Claude, OpenAI later)

Structured-output calls (extract, reconcile adjudication, explain, draft) currently run on Claude:
`ATLAS_LLM_PROVIDER=anthropic`, `ANTHROPIC_MODEL=claude-sonnet-5-5`, `ANTHROPIC_API_KEY`, with Anthropic's
server-side refusal fallback (`fallbacks: "default"`). Unset `ATLAS_LLM_PROVIDER` to go back to OpenAI;
earlier `gpt-6-astra` results are still cached. Embeddings always use OpenAI models (Anthropic has none).
The `anthropic` package is not in M0's `pyproject.toml` yet (locked: integrator sign-off), so run lane B
commands with `uv run --with anthropic ...`.

## For lane A (graph)

- `claims.json` is M0's `ClaimsBundle` (`ClaimsBundle.load` passes): all 935 quote-verified claims;
  `subject`/`object`/`variant` are node ids where reconcile was confident, else `text:<mention>`. Link only
  claims whose ends are node ids. `papers` carry `props.abstract` and author dicts (`name`, `affiliation`,
  `orcid`) for Investigator nodes. `coverage` is keyed by cluster gene id (topic searches) or paper id.
- Reconcile does **not** wait for graph v0: it resolves against the OptimusKG backbone files you load
  (`data/raw/optimuskg/`, contract id formats), `mech:<GENE>:<dir>` nodes, and ClinVar (`clinvar:<id>`).
  Re-checking ids against `graph.json` is opt-in (`ATLAS_RECHECK_GRAPH=1`), since the graph is built from
  `claims.json`. Grouping diseases (2+ subtypes in the backbone hierarchy, e.g. CMC, SCID) can't carry one
  gene's mechanism in `causes`/`has_mechanism` claims; those links go to review. Role-aware: `treats` = Drug → Disease,
  `has_mechanism` = Variant/Disease/Gene → Mechanism, `causes` → Disease, `has_phenotype` → HPO.
- `claims.meta.json` → `nodes`: every node a claim references (incl. ClinVar variants, mech: nodes), in contract
  shape, to add if you have not loaded them. Drug ids follow lane A's `pipeline/ids.py` rule
  (`CHEMBL:CHEMBL1789941`; `DRUGBANK:` ids kept).
- Evidence for the tests: ruxolitinib (`CHEMBL1789941`) → STAT1 GoF disease `MONDO:0013599`: 11 supporting,
  4 contradicting (test 4). Six STAT1 ClinVar variants → `mech:STAT1:GoF` (test 7).
  Disease → mechanism claims for STAT1 GoF, STAT3 GoF/DN, TYK2 LoF, JAK3 LoF.
- `claims.meta.json` → `stats` has the quote-check drop rate and reconcile counts for README Section 14.
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

Matches `api/llm_seam.py` (`text` + `citations`). Called as lane C calls them (no `fallback`), `explain`/`draft`
return a result only when the model produced validated cited text (`mode` `llm` / `llm_retry`), and **None**
otherwise (provider error, offline cache miss, or nothing to explain), so `/explain` falls through to the
precomputed `explanations.json` and then lane C's own fallback. `fallback=True` returns lane B's validated
template instead (used by `precompute`). `python -m pipeline.llm.explain` precomputes every disease, mechanism, drug, patient group and registry with an
evidence edge, plus variants/phenotypes/genes on claim-backed edges (291 on lane A's graph). Rerun it after every
`make data`: explanations cite edge ids. Precomputed results are keyed by entity id (a path is its ids joined
with `|`; drafts are `draft:<key>`), each with `text` and `citations`.

**Requested change for lane C** (lane C's files, so not edited here). With lane B merged, 1 of lane C's 68 API
tests fails:
- `api/llm_seam.py::_live_call` logs "returned a malformed result" for `None`. `None` now means "no live answer",
  so it should return quietly: `if result is None: return None` before the malformed-result check.
- `api/tests/test_review_fixes.py::test_llm_seam_silent_when_module_absent` can't hide `pipeline.llm` once it
  exists in the repo; monkeypatch `importlib.import_module` to raise `ModuleNotFoundError` instead.
- Lane C's API tests don't set `ATLAS_OFFLINE=1`; with lane B present and `anthropic` installed, `/explain` on an
  uncached id makes a live model call. Set it in the API test fixtures.

Demo journeys are read from `data/build/journeys.json` (`{name: [[ids...], ...]}`).

Symptom → HPO for Devon: `NodeIndex(graph["nodes"], types={"Phenotype"})` and `link(text, index)`.

## Status / open items

- On M0: `pipeline/extract/_compat.py` is a thin wrapper over `pipeline.cache`.
- OpenRouter limits new accounts to 20 requests/min per model; `ATLAS_LLM_RPM` (default 18) paces live calls
  and `ATLAS_LLM_WORKERS` (default 16) sets concurrency.
- All 7 Section 11 papers resolve to PMIDs by title. The NK-cell bioRxiv preprint has a published JACI version,
  so it resolves via PubMed; the bioRxiv path remains as fallback.

## Human review (low-confidence links)

`reconcile` never puts a medium/low-confidence link in the graph. It writes `data/review_priority.csv`: pending
proposals that touch cluster mechanisms or cluster genes' diseases, grouped and ranked by how many claims each
would unlock, with the model's reason and the quote. To approve: copy rows into `data/review_decisions.csv`
(same columns) with `decision` = `accept`, then rerun `reconcile`, `make data`, and `python -m pipeline.llm.explain`.
Only `accept` rows for ids the index knows are applied; the count is in `claims.meta.json` → `stats`.
