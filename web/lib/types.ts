/**
 * TypeScript mirror of the graph.json contract (README §4) and the Atlas
 * API shapes (api/CONTRACT.md). Both sides tolerate extra/missing optional
 * fields, so almost everything beyond ids is optional here.
 */

export type NodeType =
  | "Gene"
  | "Variant"
  | "Mechanism"
  | "Disease"
  | "Phenotype"
  | "Pathway"
  | "Drug"
  | "Claim"
  | "Paper"
  | "Investigator"
  | "PatientGroup"
  | "Registry"
  | "Study"
  | string;

export type EvidenceClass = "observed" | "extracted" | "inferred" | string;

export type EdgeStatus =
  | "supported"
  | "contested"
  | "small_n"
  | "inferred_unsupported"
  | string;

export interface GraphNode {
  id: string;
  type: NodeType;
  label: string;
  synonyms?: string[];
  props?: Record<string, unknown>;
}

export interface EdgeSource {
  kind: "pmid" | "db" | "url" | "nct" | "doi" | string;
  id: string;
  date?: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  evidence_class: EvidenceClass;
  confidence?: number;
  confidence_basis?: Record<string, unknown>;
  status?: EdgeStatus;
  sources?: EdgeSource[];
  supporting?: string[];
  contradicting?: string[];
  attributes?: {
    origin?: "germline" | "somatic" | string;
    labels?: string[];
    verdict?: string;
    differs?: string[];
    needs_review?: string[];
    [k: string]: unknown;
  };
}

export interface Claim {
  id: string;
  pmid?: string;
  quote?: string;
  polarity?: "supports" | "contradicts" | string;
  subject?: string;
  predicate?: string;
  object?: string;
  variant?: string;
  study_type?: string;
  n_patients?: number | null;
}

export interface Cluster {
  id: string;
  members: string[];
  robust?: boolean;
  why?: Record<string, unknown>;
}

export interface CoverageSearch {
  source: string;
  query: string;
  date?: string;
  hits: number;
}

export interface CoverageEntry {
  searched: CoverageSearch[];
}

export interface Graph {
  meta?: {
    built_at?: string;
    sources?: { name: string; version?: string; retrieved_at?: string; license?: string }[];
    coverage?: Record<string, CoverageEntry>;
  };
  nodes: GraphNode[];
  edges: GraphEdge[];
  claims?: Claim[];
  clusters?: Cluster[];
}

/* ------------------------- API view shapes ------------------------- */

export interface SearchResult {
  id: string;
  label: string;
  type: NodeType;
  matched_on: string;
  match_kind: "label" | "synonym" | "free_text";
  resolution: string;
  score: number;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
}

export interface Neighbor {
  edge: GraphEdge;
  node: GraphNode;
  direction: "out" | "in";
}

export interface EntityView {
  node: GraphNode;
  summary: string[];
  neighbors: Neighbor[];
  cluster_ids: string[];
  assets: { edge: GraphEdge; node: GraphNode }[];
  investigators: { edge: GraphEdge; node: GraphNode }[];
  no_supported_route: boolean;
  coverage: CoverageEntry | null;
  next_question: string | null;
}

export interface EdgeView {
  edge: GraphEdge;
  source_node: GraphNode | null;
  target_node: GraphNode | null;
  supporting_claims: Claim[];
  contradicting_claims: Claim[];
  labels: string[];
}

export interface ExplainResponse {
  text: string;
  citations: string[];
  source: "live" | "precomputed" | "fallback" | string;
}

export interface DraftResponse {
  text: string;
  citations: string[];
  source: "live" | "fallback" | string;
  disclaimer: string;
}

export interface GapsView {
  entity_id: string;
  coverage: CoverageEntry | null;
  missing_evidence: string[];
  what_would_change: string[];
  honest_gap: boolean;
}

export interface MechanismResult {
  node: GraphNode;
  mechanism: GraphNode;
  evidence_level: string;
  communities: GraphNode[];
  ranked_by: string;
}

export interface MechanismExcluded {
  node: GraphNode;
  reason: string;
}

export interface MechanismSearchResponse {
  direction: "dampen" | "boost";
  results: MechanismResult[];
  excluded: MechanismExcluded[];
}

export interface ClusterView {
  cluster: Cluster;
  members: GraphNode[];
  why: Record<string, unknown>;
}

export type PrecomputedExplanations = Record<
  string,
  { text: string; citations: string[]; source?: string }
>;
