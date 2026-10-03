# Splitting the build across 3 parallel agents

This file splits the [README](README.md) build plan into three parallel lanes. Lanes are split by what each one hands to the others, not by layer: every agent writes only its own files and its own build output.

## Phase 0: one agent, about an hour (M0)

Don't parallelize M0. Until the contract, cache and mock exist, three agents would each invent their own versions and then conflict.

One agent builds:

- `pipeline/models.py` (the contract) and `docs/graph.schema.json`
- `pipeline/cache.py`: hash-keyed disk cache and a rate-limited HTTP client
- `Makefile`, `.env.example`, `.gitignore`
- the mock `data/build/graph.mock.json`
- `tests/test_contract.py`, and `tests/test_ground_truth.py` with every test marked `xfail`
- `pyproject.toml` with **all** the dependencies we expect, so three agents don't fight over `uv.lock` later

After M0, **`pipeline/models.py`, `pyproject.toml` and `uv.lock` are locked.** Changing them needs integrator sign-off.

## Phase 1: three lanes

| | **Agent A: Graph** | **Agent B: Evidence + AI** | **Agent C: Product** |
|---|---|---|---|
| **Owns** | `pipeline/sources/{optimuskg,clinvar,ctgov,reporter}.py`, `pipeline/graph/*`, `pipeline/export.py`, `tests/test_ground_truth.py`, `docs/optimuskg-spike.md` | `pipeline/sources/pubmed.py`, `pipeline/extract/*`, `pipeline/llm/{explain,draft,validate}.py`, `eval/` | `web/`, `api/` (FastAPI routes), `pipeline/seeds/patient_groups.yaml`, deploy |
| **Produces** | `data/build/graph.json` | `data/build/claims.json`, `data/build/explanations.json` | the deployed site |
| **Consumes** | B's `claims.json`, C's patient-group seeds | node labels and synonyms from A's graph v0 (reconcile needs them) | `graph.json`: the mock until M1, the real one after |
| **Key tests** | 1, 2, 3, 5, 7 | 4, plus the quote-match checks in the contract test | 6 (`no_supported_route` comes from the API), plus checking the deployed site while logged out |

Why this split:

- **B owns PubMed end to end, from fetching to verified claims.** That removes the worst cross-lane dependency, where extraction would sit waiting on another agent's fetch.
- **B's LLM code is pure functions.** For example, `explain(ids, graph) -> cited text`. C's API routes just call them, so C never touches prompts and B never touches HTTP routing.
- **C stays busy from minute one.** C builds the whole UI against the mock and verifies the patient groups, which is web-fetch work independent of the rest of the pipeline.
- **The load is roughly even.** A does the heavy graph and clustering work, B does the heavy LLM work, and C does the frontend, API and deploy.

## Mechanics

- **One git worktree and branch per agent:** `lane/graph`, `lane/evidence`, `lane/product`. Each agent merges to `main` only at sync points, and `main` must always pass `make test`.
- **Shared cache directory, not shared files.** Cache keys are request hashes, so all three agents can safely write to the same `data/cache/`. Point each worktree at one absolute cache path through an environment variable, e.g. `ATLAS_CACHE_DIR`.
- **One integrator: a human, or a coordinating session.** The integrator merges the lanes, runs `make data && make test`, and decides cuts. Agents ask the integrator, not each other.

## Sync points

1. **End of M1 (~5 PM):** A merges graph v0. B starts reconciling against real nodes, and C switches the site from the mock to real data.
2. **Mid-M2:** B merges a first `claims.json` covering the Section 11 papers. A wires the treatment edges and the `contested` and `small_n` labels.
3. **Gate (10 PM):** all three lanes stop and spend about 30 minutes getting Maria's journey working end to end on the deployed site. Nobody starts new work until it passes.
4. **M4 → freeze (3 AM):**
   - B precomputes the demo explanations.
   - C wires `/explain`, `/draft` and `/gaps` and handles the polish.
   - A runs the ablation and writes the results into the README.

## Cuts by lane

If a lane falls behind, cuts come from that lane:

- **A:** the robustness re-runs first, then RePORTER grants.
- **B:** the Dr. Osei view.
- **C:** the phone layout polish. **Never** the evidence drawer or the Gaps tab.

The hero journey is never cut. The humans own the "Story" work: the team video, the 1-minute walkthrough and the demo script.
