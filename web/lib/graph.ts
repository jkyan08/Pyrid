/**
 * Pure functions over a loaded graph.json, mirroring the API semantics in
 * api/CONTRACT.md. Used by the static-fallback mode (and unit-tested with
 * vitest). No fetch, no DOM — everything here is deterministic and derived
 * only from fields already present in the graph. Nothing is invented.
 */

import type {
  Claim,
  Cluster,
  CoverageEntry,
  EdgeView,
  EntityView,
  GapsView,
  Graph,
  GraphEdge,
  GraphNode,
  MechanismExcluded,
  MechanismResult,
  MechanismSearchResponse,
  Neighbor,
  SearchResponse,
  SearchResult,
  ClusterView,
} from "./types";

/* ----------------------------- lookups ----------------------------- */

export function nodeById(graph: Graph, id: string): GraphNode | null {
  return graph.nodes.find((n) => n.id === id) ?? null;
}

export function edgeById(graph: Graph, id: string): GraphEdge | null {
  return graph.edges.find((e) => e.id === id) ?? null;
}

export function claimById(graph: Graph, id: string): Claim | null {
  return (graph.claims ?? []).find((c) => c.id === id) ?? null;
}

export function edgesTouching(graph: Graph, id: string): GraphEdge[] {
  return graph.edges.filter((e) => e.source === id || e.target === id);
}

export function clustersOf(graph: Graph, id: string): Cluster[] {
  return (graph.clusters ?? []).filter((c) => c.members.includes(id));
}

/** Extract [e_… / c_…] citation ids from bracketed spans in a text line. */
export function extractCitations(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(/\[([^\]]+)\]/g)) {
    for (const part of m[1].split(",")) {
      const id = part.trim();
      if (/^[ec]_[A-Za-z0-9_-]+$/.test(id)) out.push(id);
    }
  }
  return out;
}

/* ------------------------------ search ----------------------------- */

function tokenize(s: string): string[] {
  return s
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((t) => t.length > 0);
}

const STOPWORDS = new Set(["and", "or", "the", "a", "an", "of", "with", "in", "my", "i", "have"]);

/**
 * Case-insensitive search over labels + synonyms of every node type.
 * Exact/substring hits on a label or synonym win; otherwise token overlap
 * counts as a free-text match (per CONTRACT: token overlap is enough until
 * lane B's reconcile exists).
 */
export function searchGraph(graph: Graph, q: string): SearchResponse {
  const query = q.trim();
  const qLower = query.toLowerCase();
  const qTokens = tokenize(query).filter((t) => !STOPWORDS.has(t));
  const results: SearchResult[] = [];
  if (query.length === 0) return { query, results };

  for (const node of graph.nodes) {
    const candidates: { text: string; kind: "label" | "synonym" }[] = [
      { text: node.label, kind: "label" },
      ...(node.synonyms ?? []).map((s) => ({ text: s, kind: "synonym" as const })),
    ];

    let best: SearchResult | null = null;
    for (const cand of candidates) {
      const candLower = cand.text.toLowerCase();
      let score = 0;
      let kind: SearchResult["match_kind"] = cand.kind;
      let matchedOn = cand.text;

      if (candLower === qLower) {
        score = 1;
      } else if (candLower.includes(qLower) && qLower.length >= 3) {
        score = 0.9;
      } else if (qTokens.length > 0) {
        const candTokens = new Set(tokenize(cand.text));
        const hits = qTokens.filter((t) => t.length >= 3 && candTokens.has(t));
        const overlap = hits.length / qTokens.length;
        if (hits.length > 0 && overlap >= 0.3) {
          score = 0.3 + 0.5 * overlap;
          kind = "free_text";
          matchedOn = cand.text;
        }
      }

      if (score > 0 && (best === null || score > best.score)) {
        const resolution =
          kind === "synonym"
            ? `matched synonym '${matchedOn}' → ${node.label}`
            : kind === "free_text"
              ? `matched free text '${query}' against '${matchedOn}' → ${node.label}`
              : `matched '${matchedOn}' → ${node.label}`;
        best = {
          id: node.id,
          label: node.label,
          type: node.type,
          matched_on: matchedOn,
          match_kind: kind,
          resolution,
          score: Math.round(score * 100) / 100,
        };
      }
    }
    if (best) results.push(best);
  }

  results.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label));
  return { query, results: results.slice(0, 20) };
}

/* --------------------------- entity view --------------------------- */

const ASSET_TYPES = new Set(["Registry", "Study", "PatientGroup"]);

export function neighborsOf(graph: Graph, id: string): Neighbor[] {
  const out: Neighbor[] = [];
  for (const edge of edgesTouching(graph, id)) {
    const otherId = edge.source === id ? edge.target : edge.source;
    const node = nodeById(graph, otherId);
    if (!node) continue;
    out.push({ edge, node, direction: edge.source === id ? "out" : "in" });
  }
  return out;
}

/** Ground-truth test 6: no edges at all, or no edge with status "supported". */
export function hasSupportedRoute(graph: Graph, id: string): boolean {
  const edges = edgesTouching(graph, id);
  return edges.some((e) => e.status === "supported");
}

function humanRelation(rel: string): string {
  return rel.replace(/_/g, " ");
}

function neighborPhrase(selfLabel: string, n: Neighbor): string {
  const rel = humanRelation(n.edge.relation);
  return n.direction === "out"
    ? `${selfLabel} ${rel} ${n.node.label}`
    : `${n.node.label} ${rel} ${selfLabel}`;
}

/**
 * Three templated plain-language lines (what is broken / who shares it /
 * what already exists), each citing the edge ids it is built from. Lines are
 * assembled only from edges present in the graph; when a theme has no edges
 * the line says so honestly, with no citation.
 */
export function summaryLines(graph: Graph, node: GraphNode): string[] {
  const neighbors = neighborsOf(graph, node.id);

  // 1. What is broken: mechanism/variant/pathway/gene/drug-facing edges.
  const mechanismOrder = ["Mechanism", "Variant", "Pathway", "Gene", "Disease", "Drug"];
  const broken = neighbors
    .filter((n) => mechanismOrder.includes(n.node.type) && n.edge.relation !== "similar_to")
    .sort((a, b) => mechanismOrder.indexOf(a.node.type) - mechanismOrder.indexOf(b.node.type))
    .slice(0, 2);
  const line1 =
    broken.length > 0
      ? broken.map((n) => `${neighborPhrase(node.label, n)} [${n.edge.id}]`).join("; ") + "."
      : `No mechanism or pathway connection found for ${node.label} in the current graph.`;

  // 2. Who shares it: similar_to edges first, then shared phenotypes.
  const similar = neighbors.filter((n) => n.edge.relation === "similar_to");
  const phenos = neighbors.filter((n) => n.node.type === "Phenotype");
  let line2: string;
  if (similar.length > 0) {
    line2 =
      similar
        .map((n) => `${node.label} is similar to ${n.node.label} [${n.edge.id}]`)
        .join("; ") + ".";
  } else if (phenos.length > 0) {
    line2 =
      `Shared features: ` +
      phenos.map((n) => `${n.node.label} [${n.edge.id}]`).join(", ") +
      ".";
  } else {
    line2 = `No related disease or shared phenotype found for ${node.label} in the current graph.`;
  }

  // 3. What already exists: Registry / Study / PatientGroup neighbors.
  const assets = neighbors.filter((n) => ASSET_TYPES.has(n.node.type));
  const line3 =
    assets.length > 0
      ? `Already exists: ` +
        assets.map((n) => `${n.node.label} [${n.edge.id}]`).join(", ") +
        "."
      : `No registry, study or patient group linked to ${node.label} in the current graph.`;

  return [line1, line2, line3];
}

export function nextQuestionFor(label: string): string {
  return (
    `No supported route was found for ${label}. ` +
    `Next question: which mechanism, phenotype or treatment evidence would connect ` +
    `${label} to the rest of the graph? The coverage log below shows exactly what was searched.`
  );
}

export function getEntityView(graph: Graph, id: string): EntityView | null {
  const node = nodeById(graph, id);
  if (!node) return null;

  const neighbors = neighborsOf(graph, id);
  const assets = neighbors
    .filter((n) => ASSET_TYPES.has(n.node.type))
    .map((n) => ({ edge: n.edge, node: n.node }));
  const investigators = neighbors
    .filter((n) => n.node.type === "Investigator")
    .map((n) => ({ edge: n.edge, node: n.node }));
  const cluster_ids = clustersOf(graph, id).map((c) => c.id);
  const supported = hasSupportedRoute(graph, id);
  const coverage = graph.meta?.coverage?.[id] ?? null;

  return {
    node,
    summary: summaryLines(graph, node),
    neighbors,
    cluster_ids,
    assets,
    investigators,
    no_supported_route: !supported,
    coverage,
    next_question: supported ? null : nextQuestionFor(node.label),
  };
}

/* ---------------------------- edge view ---------------------------- */

export function getEdgeView(graph: Graph, edgeId: string): EdgeView | null {
  const edge = edgeById(graph, edgeId);
  if (!edge) return null;
  const resolve = (ids: string[] | undefined): Claim[] =>
    (ids ?? []).map((id) => claimById(graph, id)).filter((c): c is Claim => c !== null);
  const labels =
    edge.attributes?.labels && edge.attributes.labels.length > 0
      ? edge.attributes.labels
      : edge.status
        ? [edge.status]
        : [];
  return {
    edge,
    source_node: nodeById(graph, edge.source),
    target_node: nodeById(graph, edge.target),
    supporting_claims: resolve(edge.supporting),
    contradicting_claims: resolve(edge.contradicting),
    labels,
  };
}

/* ------------------------------ gaps ------------------------------- */

export function getGaps(graph: Graph, id: string): GapsView | null {
  const node = nodeById(graph, id);
  if (!node) return null;

  const coverage: CoverageEntry | null = graph.meta?.coverage?.[id] ?? null;
  const missing: string[] = [];
  const change: string[] = [];

  for (const s of coverage?.searched ?? []) {
    if (s.hits === 0) {
      missing.push(`Zero hits: ${s.source} — "${s.query}"${s.date ? ` (${s.date})` : ""}.`);
      change.push(`A non-empty result for "${s.query}" in ${s.source} would add evidence.`);
    }
  }

  for (const n of neighborsOf(graph, id)) {
    const e = n.edge;
    const labels = e.attributes?.labels ?? [];
    if (e.status === "contested" || labels.includes("contested")) {
      missing.push(
        `Contested: ${neighborPhrase(node.label, n)} has both supporting and contradicting claims [${e.id}].`
      );
      change.push(
        `A larger or controlled study could resolve the contested "${humanRelation(e.relation)}" evidence [${e.id}].`
      );
    }
    if (e.status === "small_n" || labels.includes("small_n")) {
      missing.push(
        `Small-n only: evidence for ${neighborPhrase(node.label, n)} comes from very small studies [${e.id}].`
      );
    }
    if (e.status === "inferred_unsupported") {
      missing.push(
        `Unsupported inference: ${neighborPhrase(node.label, n)} is inferred only, with no supporting evidence [${e.id}].`
      );
    }
  }

  const honest_gap = !hasSupportedRoute(graph, id);
  if (honest_gap) {
    missing.push(`No supported route: ${node.label} has no edge with supported evidence.`);
    change.push(`Any supported edge for ${node.label} would change the no-supported-route answer.`);
  }

  return {
    entity_id: id,
    coverage,
    missing_evidence: missing,
    what_would_change: change,
    honest_gap,
  };
}

/* ------------------------- mechanism search ------------------------ */

/** Mirrors the API's _EVIDENCE_RANK table (api/main.py) exactly. */
export const STUDY_RANK: Record<string, number> = {
  observed: 6,
  trial: 5,
  cohort: 4,
  case_series: 3,
  case_report: 2,
  extracted: 2,
  in_vitro: 1,
  review: 1,
  inferred: 0,
};

function evidenceLevelForEdge(graph: Graph, edge: GraphEdge): string {
  if (edge.evidence_class === "observed") return "observed";
  const types = (edge.supporting ?? [])
    .map((id) => claimById(graph, id)?.study_type)
    .filter((t): t is string => !!t);
  if (types.length === 0) return edge.evidence_class;
  return types.reduce((best, t) => ((STUDY_RANK[t] ?? 0) > (STUDY_RANK[best] ?? 0) ? t : best));
}

function mechanismDirection(mech: GraphNode): string | null {
  const d = mech.props?.direction;
  if (typeof d === "string") return d;
  // Fall back to the id convention mech:<GENE>:<DIRECTION>[:somatic]
  const m = mech.id.match(/^mech:[^:]+:(GoF|LoF|DN)/);
  return m ? m[1] : null;
}

const COMMUNITY_TYPES = new Set(["PatientGroup", "Registry"]);

/** Mechanism↔disease link relations (mirrors api/main.py): "causes" is the
 * lane-C fixture vocabulary, "has_mechanism" the M0 mock vocabulary. */
const MECHANISM_LINK_RELATIONS = new Set(["causes", "has_mechanism"]);

/**
 * direction=dampen → diseases caused by a GoF mechanism rank; LoF-caused
 * diseases are excluded with the reason. boost mirrors it. DN mechanisms are
 * excluded in both directions as needing expert review. Only "causes" /
 * "has_mechanism" edges are considered (either orientation, one entry per
 * disease), mirroring the API.
 */
export function mechanismSearch(
  graph: Graph,
  direction: "dampen" | "boost"
): MechanismSearchResponse {
  const include = direction === "dampen" ? "GoF" : "LoF";
  const excludeReasons: Record<string, string> = {
    GoF: "gain-of-function mechanism: boosting is the wrong direction",
    LoF: "loss-of-function mechanism: dampening is the wrong direction",
    DN: "dominant-negative mechanism: direction-based intervention needs expert review",
  };

  const results: MechanismResult[] = [];
  const excluded: MechanismExcluded[] = [];
  const seen = new Set<string>();

  for (const edge of graph.edges) {
    if (!MECHANISM_LINK_RELATIONS.has(edge.relation)) continue;
    const src = nodeById(graph, edge.source);
    const tgt = nodeById(graph, edge.target);
    if (!src || !tgt) continue;
    let mech: GraphNode | null = null;
    let disease: GraphNode | null = null;
    if (src.type === "Mechanism" && tgt.type === "Disease") {
      mech = src;
      disease = tgt;
    } else if (tgt.type === "Mechanism" && src.type === "Disease") {
      mech = tgt;
      disease = src;
    }
    if (!mech || !disease || seen.has(disease.id)) continue;
    const dir = mechanismDirection(mech);
    if (!dir) continue;
    seen.add(disease.id);

    if (dir === include) {
      const communities = neighborsOf(graph, disease.id)
        .filter((n) => COMMUNITY_TYPES.has(n.node.type))
        .map((n) => n.node);
      results.push({
        node: disease,
        mechanism: mech,
        evidence_level: evidenceLevelForEdge(graph, edge),
        communities,
        ranked_by: "max supporting-claim study_type, then confidence",
      });
    } else {
      excluded.push({
        node: disease,
        reason: excludeReasons[dir] ?? `mechanism direction ${dir}: not a ${direction} target`,
      });
    }
  }

  results.sort((a, b) => {
    const ra = STUDY_RANK[a.evidence_level] ?? 0;
    const rb = STUDY_RANK[b.evidence_level] ?? 0;
    if (rb !== ra) return rb - ra;
    const ca = edgesBetween(graph, a.mechanism.id, a.node.id)[0]?.confidence ?? 0;
    const cb = edgesBetween(graph, b.mechanism.id, b.node.id)[0]?.confidence ?? 0;
    return cb - ca;
  });

  return { direction, results, excluded };
}

function edgesBetween(graph: Graph, a: string, b: string): GraphEdge[] {
  return graph.edges.filter(
    (e) => (e.source === a && e.target === b) || (e.source === b && e.target === a)
  );
}

/* --------------------------- cluster view -------------------------- */

export function getClusterView(graph: Graph, id: string): ClusterView | null {
  const cluster = (graph.clusters ?? []).find((c) => c.id === id);
  if (!cluster) return null;
  const members = cluster.members
    .map((m) => nodeById(graph, m))
    .filter((n): n is GraphNode => n !== null);
  return { cluster, members, why: cluster.why ?? {} };
}

/* ------------------------- map neighborhood ------------------------ */

export interface NeighborhoodData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** node id → cluster id (first cluster containing it), for coloring */
  clusterOf: Record<string, string>;
  /** node id → degree in the FULL graph (size proxy) */
  degree: Record<string, number>;
}

/**
 * BFS neighborhood around `id`, capped at `maxNodes` nodes; only edges whose
 * two endpoints are both inside the neighborhood are returned.
 */
export function neighborhoodElements(graph: Graph, id: string, maxNodes = 40): NeighborhoodData {
  const start = nodeById(graph, id);
  const picked: GraphNode[] = [];
  const pickedIds = new Set<string>();
  if (start) {
    const queue: string[] = [id];
    while (queue.length > 0 && picked.length < maxNodes) {
      const cur = queue.shift()!;
      if (pickedIds.has(cur)) continue;
      const node = nodeById(graph, cur);
      if (!node) continue;
      pickedIds.add(cur);
      picked.push(node);
      for (const n of neighborsOf(graph, cur)) {
        if (!pickedIds.has(n.node.id)) queue.push(n.node.id);
      }
    }
  }

  const edges = graph.edges.filter((e) => pickedIds.has(e.source) && pickedIds.has(e.target));

  const clusterOf: Record<string, string> = {};
  for (const c of graph.clusters ?? []) {
    for (const m of c.members) {
      if (pickedIds.has(m) && !(m in clusterOf)) clusterOf[m] = c.id;
    }
  }

  const degree: Record<string, number> = {};
  for (const n of picked) degree[n.id] = 0;
  for (const e of graph.edges) {
    if (pickedIds.has(e.source)) degree[e.source] += 1;
    if (pickedIds.has(e.target)) degree[e.target] += 1;
  }

  return { nodes: picked, edges, clusterOf, degree };
}

/* --------------------- fallback explain / draft --------------------- */

export const DISCLAIMER =
  "Draft only — never sent. Research navigation, not medical advice.";

/**
 * Templated explanation built only from edge fields already in the graph
 * (the CONTRACT's level-3 fallback). Every sentence cites an edge id.
 */
export function templatedExplain(
  graph: Graph,
  ids: string[]
): { text: string; citations: string[] } {
  const sentences: string[] = [];
  const citations: string[] = [];
  for (const id of ids) {
    const node = nodeById(graph, id);
    if (!node) continue;
    const neighbors = neighborsOf(graph, id).slice(0, 4);
    if (neighbors.length === 0) {
      sentences.push(`${node.label} has no connections in the current graph.`);
      continue;
    }
    for (const n of neighbors) {
      sentences.push(`${neighborPhrase(node.label, n)} [${n.edge.id}].`);
      citations.push(n.edge.id);
    }
  }
  return {
    text:
      sentences.length > 0
        ? sentences.join(" ")
        : "None of the requested ids were found in the current graph.",
    citations,
  };
}

/**
 * Templated outreach draft built only from graph content: the entity, its
 * assets and investigators. Never sent; clearly labeled by the caller.
 */
export function templatedDraft(
  graph: Graph,
  entityId: string,
  focus?: string
): { text: string; citations: string[] } {
  const view = getEntityView(graph, entityId);
  if (!view) {
    return { text: "Entity not found in the current graph.", citations: [] };
  }
  const citations: string[] = [];
  const lines: string[] = [];
  lines.push(
    `Subject: exploring shared research on ${view.node.label}` +
      (focus ? ` (${humanRelation(focus)})` : "")
  );
  lines.push("");
  lines.push(
    `Hello — I am researching ${view.node.label} and mapping the existing evidence and assets around it.`
  );
  for (const a of view.assets) {
    lines.push(`- Existing asset: ${a.node.label} (${a.node.type}) [${a.edge.id}]`);
    citations.push(a.edge.id);
  }
  for (const i of view.investigators) {
    lines.push(`- Relevant investigator: ${i.node.label} [${i.edge.id}]`);
    citations.push(i.edge.id);
  }
  if (view.assets.length === 0 && view.investigators.length === 0) {
    lines.push(
      `- The current graph lists no registry, study, patient group or investigator for ${view.node.label}; the first step is to identify one.`
    );
  }
  lines.push("");
  lines.push(
    "Would you be open to a short conversation about whether existing data or study designs could be shared or adapted? Every connection above links to its evidence."
  );
  return { text: lines.join("\n"), citations };
}
