# Technical walkthrough — 60-second recording script

## Pre-recording
Pre-run `make test` + `cd web && npm test` (show green tails only) and have ready:
`curl -s https://pyridrare-disease-atlasrare-disease-atlas.replit.app/api/health | python3 -m json.tool`

## Scene 1 (0:00–0:12) — How it's built
**Screen:** architecture slide (scene1.html)

> This whole project is built around one file: a graph of evidence with a
> locked format. A Python pipeline builds it, a web server serves it, and the
> website displays it. Three people built those parts in parallel — the locked
> format kept everything fitting together.

## Scene 2 (0:12–0:30) — Never makes things up
**Screen:** honesty slide (scene2.html)

> Rule one: never invent facts. Every claim stores the exact sentence it came
> from and the paper it's in. The pipeline re-checks that sentence against the
> paper's real abstract — if the words aren't there, the claim is thrown out.
> Searches that find nothing get saved too, because for rare diseases, the
> gaps are information.

## Scene 3 (0:30–0:45) — The numbers
**Screen:** stats slide (scene3.html), then live server JSON

> The graph holds about 2,900 entities, 8,500 connections, and 900 quoted
> claims — checked by over 800 automated tests, including seven that walk a
> real patient journey end to end. And if the server ever falls back to demo
> data, the site shows a warning banner. It can't pass fake data off as real.

## Scene 4 (0:45–1:00) — Safe AI, live site
**Screen:** Research action tab ("Draft only — never sent" chip), end on the live map

> The AI features can only cite evidence already in the graph — with no key
> they fall back to plain templates, so they never invent. Outreach emails get
> drafted but never sent. And everything you just saw is running live right
> now.

---

# Extended version (~3 min) — for reference

## Pre-recording checklist
- [ ] API running on :8000, web on :3200, one browser tab on the live URL
- [ ] Terminal font enlarged; `.env` / secrets never on screen
- [ ] Pre-run both test suites so output is instant:
  - `make test 2>&1 | tail -5` (repo root)
  - `cd web && npm test 2>&1 | tail -4`
- [ ] Have this curl ready:
  - `curl -s https://pyridrare-disease-atlasrare-disease-atlas.replit.app/api/health | python3 -m json.tool`

## Scene 1 (0:00–0:25) — Problem + architecture
**Screen:** README.md architecture/plan section, then AGENT-SPLIT.md

> Rare disease evidence is scattered across papers, ClinVar, trial registries,
> and patient groups — and tools in this space tend to hallucinate exactly where
> accuracy matters most. Rare Disease Atlas is built around one artifact: a
> single evidence graph with a frozen schema. A Python pipeline builds it, a
> FastAPI layer serves it, and a Next.js frontend renders it. Three of us built
> those lanes in parallel against the frozen contract, so nothing downstream
> ever guessed at data shapes.

## Scene 2 (0:25–1:00) — Pipeline + honesty rules
**Screen:** `pipeline/` tree; scroll `pipeline/graph/evidence.py`, `pipeline/seeds/patient_groups.yaml`

> The pipeline pulls from real sources — PubMed, ClinVar, ClinicalTrials.gov,
> and hand-verified patient-group seeds. The hard rule: no invented facts.
> Every claim carries its quote, its PMID, and a study type, and the validator
> re-checks each quote against the paper's actual abstract — if the text isn't
> there, the claim doesn't ship. Lookups that come back empty aren't discarded
> either; they're recorded as coverage gaps, because for rare diseases, what we
> couldn't find is data too. The current build: about twenty-nine hundred
> nodes, eighty-five hundred edges, and nine hundred sourced claims across the
> JAK-STAT disease family.

## Scene 3 (1:00–1:25) — Tests as the contract
**Screen:** terminal with both test tails; flash `docs/graph.schema.json`

> The schema is enforced by JSON Schema validation plus a full pytest suite —
> including seven ground-truth journey tests that assert real biological paths
> exist end-to-end, like a disease reaching its mechanism and a repurposable
> drug. The frontend has its own vitest suite mirroring the API's ranking
> logic, so client and server can never drift apart.

## Scene 4 (1:25–1:55) — API + graceful degradation
**Screen:** `api/graph_store.py` loader chain; run the health curl

> The API is FastAPI over an in-memory graph store. One detail I care about:
> the loader falls back through a chain — real build, then demo fixtures — and
> if it ever serves non-sourced data, the response says mode: mock and the UI
> shows a permanent ribbon. The app literally cannot present demo data as real.
> Search ranks entities above papers with per-type caps, so a gene with a
> hundred ClinVar variants can't flood out the diseases.

## Scene 5 (1:55–2:25) — Frontend display layer
**Screen:** live STAT1 entity page; open evidence drawer; flash `web/lib/display.ts`

> The frontend is Next.js with a d3-force evidence map. Canonical data is never
> mutated — readability is a display layer: HGVS strings like NM_007315.4
> render as "STAT1 R274W", but the drawer always shows the canonical ID, the
> confidence basis, and supporting claims sorted strongest-evidence-first with
> live PubMed links. Contradicting evidence gets equal billing — it's a column,
> not a footnote.

## Scene 6 (2:25–2:50) — LLM seam + safety rails
**Screen:** Research action tab ("Draft only — never sent" chip); flash `api/llm_seam.py`

> LLM use is deliberately narrow: explain and draft endpoints that can only
> cite edges already in the graph. No key configured? It degrades to template
> mode silently — never an error, never a fabrication. Outreach drafts are
> generated but never sent, and the UI says so. No dosing, no medical advice,
> ever.

## Scene 7 (2:50–3:10) — Deploy + close
**Screen:** live home page; drag one map node

> The whole stack deploys on Replit autoscale — Next and uvicorn behind a
> same-origin proxy, so there's no CORS surface. Everything you just saw is
> live right now. Rare Disease Atlas: a research navigator that would rather
> show you a gap than invent an answer.
