/**
 * API client for the Atlas FastAPI app (api/CONTRACT.md), with a static-file
 * fallback: when the API is unreachable, the raw graph bundled into
 * web/public/ (by scripts/copy-fixtures.mjs) is loaded once and all views are
 * computed client-side by the pure functions in lib/graph.ts. Explain/draft
 * degrade to the bundled precomputed-explanations fixture / templated text,
 * labeled "cached only" by the UI (source != "live").
 */

import type {
  DraftResponse,
  EdgeView,
  EntityView,
  ExplainResponse,
  GapsView,
  Graph,
  MechanismSearchResponse,
  PrecomputedExplanations,
  SearchResponse,
} from "./types";
import {
  getEdgeView,
  getEntityView,
  getGaps,
  mechanismSearch as mechanismSearchPure,
  searchGraph,
  templatedDraft,
  templatedExplain,
  DISCLAIMER,
} from "./graph";

/**
 * API base URL. Three forms are supported:
 *   - unset/empty          → http://localhost:8000 (local dev default)
 *   - absolute URL         → used as-is (trailing slashes stripped)
 *   - relative path "/api" → same-origin fetches (e.g. the Replit/Next
 *     rewrite in next.config.ts proxying /api/* → 127.0.0.1:8000/*)
 */
function normalizeApiBase(raw: string | undefined): string {
  const trimmed = (raw ?? "").trim().replace(/\/+$/, "");
  if (!trimmed) return "http://localhost:8000";
  // Absolute URL → as-is; otherwise treat as a same-origin path ("/api").
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)) return trimmed;
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

export const API_BASE = normalizeApiBase(process.env.NEXT_PUBLIC_API_URL);

export type ApiMode = "unknown" | "live" | "fallback";

let mode: ApiMode = "unknown";
const listeners = new Set<(m: ApiMode) => void>();

function setMode(m: ApiMode) {
  if (mode === m) return;
  mode = m;
  for (const fn of listeners) fn(m);
}

export function getApiMode(): ApiMode {
  return mode;
}

export function subscribeApiMode(fn: (m: ApiMode) => void): () => void {
  listeners.add(fn);
  fn(mode);
  return () => listeners.delete(fn);
}

/** Thrown when the entity/edge does not exist in either the API or the graph. */
export class NotFoundError extends Error {
  constructor(what: string) {
    super(`${what} not found`);
    this.name = "NotFoundError";
  }
}

/* ----------------------- static fallback data ----------------------- */

let staticGraphPromise: Promise<Graph> | null = null;
let staticExplanationsPromise: Promise<PrecomputedExplanations> | null = null;

function loadStaticGraph(): Promise<Graph> {
  if (!staticGraphPromise) {
    staticGraphPromise = fetch("/graph.mock.json").then((r) => {
      if (!r.ok) throw new Error("static graph missing from public/");
      return r.json();
    });
    staticGraphPromise.catch(() => {
      staticGraphPromise = null; // allow retry
    });
  }
  return staticGraphPromise;
}

function loadStaticExplanations(): Promise<PrecomputedExplanations> {
  if (!staticExplanationsPromise) {
    staticExplanationsPromise = fetch("/explanations.mock.json")
      .then((r) => (r.ok ? r.json() : {}))
      .catch(() => ({}));
  }
  return staticExplanationsPromise;
}

/* ------------------------------ fetch ------------------------------ */

/**
 * Try the live API; on network failure or a 5xx, run the fallback.
 * A 404 from the live API is a real "not found" and does not fall back.
 */
async function withFallback<T>(
  path: string,
  init: RequestInit | undefined,
  fallback: () => Promise<T>
): Promise<T> {
  try {
    const res = await fetch(`${API_BASE}${path}`, init);
    if (res.status === 404) {
      setMode("live");
      throw new NotFoundError(path);
    }
    if (!res.ok) throw new Error(`API ${res.status}`);
    setMode("live");
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof NotFoundError) throw err;
    const result = await fallback();
    setMode("fallback");
    return result;
  }
}

/* ----------------------------- endpoints ---------------------------- */

export async function apiSearch(q: string): Promise<SearchResponse> {
  return withFallback(`/search?q=${encodeURIComponent(q)}`, undefined, async () => {
    const graph = await loadStaticGraph();
    return searchGraph(graph, q);
  });
}

export async function apiEntity(id: string): Promise<EntityView> {
  return withFallback(`/entity/${encodeURIComponent(id)}`, undefined, async () => {
    const graph = await loadStaticGraph();
    const view = getEntityView(graph, id);
    if (!view) throw new NotFoundError(`entity ${id}`);
    return view;
  });
}

export async function apiEdge(id: string): Promise<EdgeView> {
  return withFallback(`/edge/${encodeURIComponent(id)}`, undefined, async () => {
    const graph = await loadStaticGraph();
    const view = getEdgeView(graph, id);
    if (!view) throw new NotFoundError(`edge ${id}`);
    return view;
  });
}

export async function apiGaps(id: string): Promise<GapsView> {
  return withFallback(`/gaps/${encodeURIComponent(id)}`, undefined, async () => {
    const graph = await loadStaticGraph();
    const view = getGaps(graph, id);
    if (!view) throw new NotFoundError(`entity ${id}`);
    return view;
  });
}

export async function apiMechanismSearch(
  direction: "dampen" | "boost"
): Promise<MechanismSearchResponse> {
  return withFallback(`/mechanism-search?direction=${direction}`, undefined, async () => {
    const graph = await loadStaticGraph();
    return mechanismSearchPure(graph, direction);
  });
}

/** Whole graph (map rendering + claim lookups); served verbatim by GET /graph. */
export async function apiGraph(): Promise<Graph> {
  return withFallback(`/graph`, undefined, () => loadStaticGraph());
}

let graphPromise: Promise<Graph> | null = null;

/** Memoized whole-graph load, shared by lightweight consumers (citation chips). */
export function apiGraphCached(): Promise<Graph> {
  if (!graphPromise) {
    graphPromise = apiGraph();
    graphPromise.catch(() => {
      graphPromise = null; // allow retry after a transient failure
    });
  }
  return graphPromise;
}

export async function apiExplain(ids: string[]): Promise<ExplainResponse> {
  return withFallback(
    `/explain`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    },
    async () => {
      const explanations = await loadStaticExplanations();
      const hit = ids.length > 0 ? explanations[ids[0]] : undefined;
      if (hit) {
        return { text: hit.text, citations: hit.citations, source: "precomputed" };
      }
      const graph = await loadStaticGraph();
      const t = templatedExplain(graph, ids);
      return { ...t, source: "fallback" };
    }
  );
}

export async function apiDraft(entityId: string, focus?: string): Promise<DraftResponse> {
  return withFallback(
    `/draft`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entity_id: entityId, ...(focus ? { focus } : {}) }),
    },
    async () => {
      const graph = await loadStaticGraph();
      const t = templatedDraft(graph, entityId, focus);
      return { ...t, source: "fallback", disclaimer: DISCLAIMER };
    }
  );
}
