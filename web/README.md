# web/ — Rare Disease Atlas frontend

Next.js (App Router, TypeScript, Tailwind) frontend for the atlas. Consumes the
lane-C API (see `../api/CONTRACT.md`) with a static-file fallback. UI components
are styled with [SmoothUI](https://smoothui.dev) (motion-animated, shadcn
registry `@smoothui/*`, vendored under `components/smoothui/`); all animations
respect `prefers-reduced-motion`.

> Research navigation, not medical advice. Drafts are never sent.

## Run

```bash
npm install
npm run dev        # predev copies the graph fixture into public/, then starts :3000
```

- `NEXT_PUBLIC_API_URL` — API base URL (default `http://localhost:8000`).
  - **Absolute URL** (`https://api.example.com`): used as-is.
  - **Relative path** (`/api`): same-origin fetches. `next.config.ts` rewrites
    `/api/:path*` → `http://127.0.0.1:8000/:path*`, so on single-port hosts
    (e.g. Replit) set `NEXT_PUBLIC_API_URL=/api` and run the FastAPI app on
    port 8000 next to the Next server — the browser only ever talks to one
    origin.
  - **Unset/empty**: keeps the `http://localhost:8000` default.
- On load the app fetches `GET /health` once; when the API reports
  `mode: "mock"` a slim amber ribbon ("Placeholder (mock) data — not sourced")
  is shown. An unreachable `/health` is a silent no-op (the fallback banner
  below covers that case).
- If the API is unreachable, the app falls back to the bundled static graph
  (`public/graph.mock.json`, copied at predev/prebuild from
  `data/build/graph.json` → `data/build/graph.mock.json` →
  `api/fixtures/graph.mock.json`, first match wins) and computes all views
  client-side (`lib/graph.ts`). A thin banner marks static-fallback mode;
  explain/draft degrade to "cached only".

## Pages

- `/` — one global search box (disease, gene, variant, free-text symptom,
  patient group, mechanism) with resolution lines.
- `/entity/[id]` — opens on Map (D3-force + Canvas neighborhood with link
  springs, many-body repulsion, collision and centering; drag temporarily pins
  one node and moves nearby nodes without rebuilding the simulation; pan,
  zoom, reheat and fit controls). Fill = cluster only, size = connectivity,
  dashed = bridge or inferred. Evidence brief has cited plain-language lines;
  Research action has assets with verdicts / what differs / expert-review flags,
  investigators with public contact routes only, Draft button — output labeled
  "Draft only — never sent". Open questions shows coverage incl. zero-hit rows, missing
  evidence and what would change. Entities with no supported route show the
  honest-gap state prominently.
- `/mechanism` — dampen/boost mechanism search with an Excluded-with-reason
  section.
- Evidence drawer — opens from any edge/claim reference anywhere; supporting
  vs contradicting claims side by side; PubMed links only for numeric PMIDs.

## Test / build

```bash
npm test           # vitest over the pure graph logic (lib/graph.ts)
npm run build
```
