import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Graph } from "../types";
import {
  searchGraph,
  getEntityView,
  getEdgeView,
  getGaps,
  mechanismSearch,
  neighborhoodElements,
  discoveryRoutes,
  extractCitations,
  hasSupportedRoute,
  STUDY_RANK,
} from "../graph";

describe("discoveryRoutes", () => {
  it("returns a real, source-carrying multi-hop route and no invented destinations", () => {
    const small: Graph = {
      nodes: [
        { id: "d", type: "Disease", label: "Disease" },
        { id: "g", type: "Gene", label: "Gene" },
        { id: "g2", type: "Gene", label: "Another Gene" },
        { id: "p", type: "Pathway", label: "Pathway" },
        { id: "r", type: "Registry", label: "Registry" },
      ],
      edges: [
        { id: "e1", source: "d", target: "g", relation: "associated_with", evidence_class: "observed", status: "supported", sources: [{ kind: "db", id: "source-1" }] },
        { id: "e2", source: "g", target: "p", relation: "in_pathway", evidence_class: "observed", status: "supported", sources: [{ kind: "db", id: "source-2" }] },
        { id: "e3", source: "p", target: "g2", relation: "in_pathway", evidence_class: "observed", status: "supported", sources: [{ kind: "db", id: "source-3" }] },
      ],
    };
    const routes = discoveryRoutes(small, "d");
    expect(routes.map((route) => route.type)).toEqual(["Pathway", "Gene", "AnotherGene"]);
    expect(routes[0].steps.map((step) => step.edge.id)).toEqual(["e1", "e2"]);
    expect(routes[2].steps.map((step) => step.edge.id)).toEqual(["e1", "e2", "e3"]);
    expect(routes[0].steps.every((step) => step.edge.sources?.length)).toBe(true);
    expect(discoveryRoutes(small, "r")).toEqual([]);
  });
});

// The dev fixture is the only graph we can rely on existing pre-M1.
const graph: Graph = JSON.parse(
  readFileSync(join(__dirname, "../../../api/fixtures/graph.mock.json"), "utf-8")
);

describe("searchGraph", () => {
  it("matches on label", () => {
    const { results } = searchGraph(graph, "Gene Alpha");
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].id).toBe("mock:gene:alpha");
    expect(results[0].match_kind).toBe("label");
    expect(results[0].resolution).toContain("Gene Alpha (mock)");
  });

  it("matches on synonym with a resolution line", () => {
    const { results } = searchGraph(graph, "chronic mock candidiasis");
    expect(results.length).toBeGreaterThan(0);
    const top = results[0];
    expect(top.id).toBe("mock:disease:gof");
    expect(top.match_kind).toBe("synonym");
    expect(top.matched_on).toBe("chronic mock candidiasis");
    expect(top.resolution).toContain("chronic mock candidiasis");
    expect(top.resolution).toContain("Mock immune dysregulation syndrome");
  });

  it("matches free-text symptoms via token overlap", () => {
    const { results } = searchGraph(graph, "recurring candida infections and thyroid problems");
    const ids = results.map((r) => r.id);
    expect(ids).toContain("mock:phenotype:1"); // synonym: recurring candida infections (mock)
    expect(ids).toContain("mock:phenotype:2"); // synonym: thyroid problems (mock)
  });

  it("is case-insensitive", () => {
    const { results } = searchGraph(graph, "MOCKINIB");
    expect(results.map((r) => r.id)).toContain("mock:drug:x");
  });

  it("returns empty results for garbage", () => {
    const { results } = searchGraph(graph, "zzzzqqqq xylophone");
    expect(results).toHaveLength(0);
  });

  it("ranks results by score descending", () => {
    const { results } = searchGraph(graph, "alpha");
    for (let i = 1; i < results.length; i++) {
      expect(results[i - 1].score).toBeGreaterThanOrEqual(results[i].score);
    }
  });
});

describe("getEntityView / neighbor assembly", () => {
  it("assembles neighbors, assets and investigators for the GoF disease", () => {
    const view = getEntityView(graph, "mock:disease:gof");
    expect(view).not.toBeNull();
    const v = view!;
    expect(v.node.id).toBe("mock:disease:gof");

    const neighborEdgeIds = v.neighbors.map((n) => n.edge.id);
    // incoming: mechanism causes, drug treats, registry covers, group supports...
    expect(neighborEdgeIds).toContain("e_000003");
    expect(neighborEdgeIds).toContain("e_000011");
    // outgoing: phenotype edges
    expect(neighborEdgeIds).toContain("e_000005");

    // direction bookkeeping
    const mech = v.neighbors.find((n) => n.edge.id === "e_000003")!;
    expect(mech.direction).toBe("in");
    expect(mech.node.id).toBe("mech:ALPHA:GoF");
    const phen = v.neighbors.find((n) => n.edge.id === "e_000005")!;
    expect(phen.direction).toBe("out");

    // assets = Registry/Study/PatientGroup neighbors
    const assetNodeIds = v.assets.map((a) => a.node.id);
    expect(assetNodeIds).toContain("mock:registry:1");
    expect(assetNodeIds).toContain("mock:study:1");
    expect(assetNodeIds).toContain("mock:group:umbrella");

    // investigators
    expect(v.investigators.map((i) => i.node.id)).toContain("mock:investigator:1");

    // clusters
    expect(v.cluster_ids).toContain("cl_mock_gof");

    // connected entity: supported route exists
    expect(v.no_supported_route).toBe(false);
    expect(v.next_question).toBeNull();

    // three summary lines, each citing at least one id (unless an honest absence)
    expect(v.summary.length).toBe(3);
    const allCitations = v.summary.flatMap(extractCitations);
    expect(allCitations.length).toBeGreaterThan(0);
    for (const c of allCitations) {
      expect(
        graph.edges.some((e) => e.id === c) || (graph.claims ?? []).some((cl) => cl.id === c)
      ).toBe(true);
    }
  });

  it("returns null for unknown ids", () => {
    expect(getEntityView(graph, "mock:does-not-exist")).toBeNull();
  });
});

describe("no_supported_route (ground-truth test 6 semantics)", () => {
  it("flags the orphan gene with coverage and a next question", () => {
    const view = getEntityView(graph, "mock:gene:orphan")!;
    expect(view.no_supported_route).toBe(true);
    expect(view.coverage).not.toBeNull();
    expect(view.coverage!.searched.length).toBeGreaterThan(0);
    expect(view.coverage!.searched.every((s) => s.hits === 0)).toBe(true);
    expect(view.next_question).toBeTruthy();
    expect(view.next_question).toContain("Gene Orphan");
  });

  it("hasSupportedRoute is true for connected entities", () => {
    expect(hasSupportedRoute(graph, "mock:disease:gof")).toBe(true);
    expect(hasSupportedRoute(graph, "mock:gene:orphan")).toBe(false);
  });
});

describe("getEdgeView", () => {
  it("resolves full claim objects on both sides", () => {
    const view = getEdgeView(graph, "e_000011")!;
    expect(view.edge.id).toBe("e_000011");
    expect(view.source_node!.id).toBe("mock:drug:x");
    expect(view.target_node!.id).toBe("mock:disease:gof");
    expect(view.supporting_claims.map((c) => c.id)).toEqual(["c_000001"]);
    expect(view.contradicting_claims.map((c) => c.id)).toEqual(["c_000002"]);
    expect(view.labels).toEqual(["promising", "contested", "small_n"]);
  });

  it("falls back to [status] when attributes.labels is absent", () => {
    const view = getEdgeView(graph, "e_000012")!;
    expect(view.labels).toEqual(["inferred_unsupported"]);
  });

  it("returns null for unknown edge ids", () => {
    expect(getEdgeView(graph, "e_999999")).toBeNull();
  });
});

describe("getGaps", () => {
  it("derives coverage, missing evidence and what-would-change for the GoF disease", () => {
    const gaps = getGaps(graph, "mock:disease:gof")!;
    expect(gaps.entity_id).toBe("mock:disease:gof");
    expect(gaps.honest_gap).toBe(false);

    // coverage retained verbatim, including the zero-hit row
    expect(gaps.coverage).not.toBeNull();
    const zero = gaps.coverage!.searched.find((s) => s.hits === 0);
    expect(zero).toBeTruthy();

    // zero-hit search and the contested treatment edge each produce a line
    expect(gaps.missing_evidence.some((l) => l.includes("MOCK-TRIALS"))).toBe(true);
    expect(gaps.missing_evidence.some((l) => l.includes("e_000011"))).toBe(true);
    expect(gaps.what_would_change.length).toBeGreaterThan(0);
  });

  it("marks the orphan gene as an honest gap", () => {
    const gaps = getGaps(graph, "mock:gene:orphan")!;
    expect(gaps.honest_gap).toBe(true);
    expect(gaps.coverage!.searched.length).toBeGreaterThan(0);
  });

  it("returns null for unknown ids", () => {
    expect(getGaps(graph, "nope")).toBeNull();
  });
});

describe("mechanismSearch", () => {
  it("dampen ranks GoF diseases and excludes LoF with a reason", () => {
    const res = mechanismSearch(graph, "dampen");
    expect(res.direction).toBe("dampen");

    const includedIds = res.results.map((r) => r.node.id);
    expect(includedIds).toContain("mock:disease:gof");
    expect(includedIds).not.toContain("mock:disease:lof");
    expect(includedIds).not.toContain("mock:disease:beta-lof");

    const excludedIds = res.excluded.map((e) => e.node.id);
    expect(excludedIds).toContain("mock:disease:lof");
    expect(excludedIds).toContain("mock:disease:beta-lof");
    for (const ex of res.excluded) {
      expect(ex.reason.length).toBeGreaterThan(0);
      expect(ex.reason.toLowerCase()).toContain("loss-of-function");
    }

    // result shape
    const gof = res.results.find((r) => r.node.id === "mock:disease:gof")!;
    expect(gof.mechanism.id).toBe("mech:ALPHA:GoF");
    expect(gof.evidence_level).toBe("observed");
    expect(gof.communities.map((c) => c.id)).toContain("mock:group:umbrella");
    expect(gof.communities.map((c) => c.id)).toContain("mock:registry:1");
  });

  it("boost mirrors dampen", () => {
    const res = mechanismSearch(graph, "boost");
    const includedIds = res.results.map((r) => r.node.id);
    expect(includedIds).toContain("mock:disease:lof");
    expect(includedIds).not.toContain("mock:disease:gof");
    const ex = res.excluded.find((e) => e.node.id === "mock:disease:gof")!;
    expect(ex.reason.toLowerCase()).toContain("gain-of-function");
  });

  it("results are ranked by evidence level then confidence", () => {
    const res = mechanismSearch(graph, "dampen");
    expect(res.results.length).toBeGreaterThan(0);
    expect(res.results[0].ranked_by).toContain("study_type");
  });
});

describe("STUDY_RANK parity with the API", () => {
  it("matches api/main.py _EVIDENCE_RANK exactly", () => {
    expect(STUDY_RANK).toEqual({
      observed: 6,
      trial: 5,
      cohort: 4,
      case_series: 3,
      case_report: 2,
      extracted: 2,
      in_vitro: 1,
      review: 1,
      inferred: 0,
    });
  });
});

describe("mechanismSearch relation filter (API parity)", () => {
  // Minimal graph exercising the M0 mock vocabulary: Disease
  // --has_mechanism--> Mechanism, plus a wrong-relation edge and a
  // duplicate mechanism link for the same disease.
  const mini: Graph = {
    nodes: [
      { id: "mech:G1:GoF", type: "Mechanism", label: "G1 gain of function" },
      { id: "mech:G1:LoF", type: "Mechanism", label: "G1 loss of function" },
      { id: "d1", type: "Disease", label: "Disease One" },
      { id: "d2", type: "Disease", label: "Disease Two" },
      { id: "d3", type: "Disease", label: "Disease Three" },
    ],
    edges: [
      // M0 vocabulary + orientation: Disease --has_mechanism--> Mechanism
      {
        id: "m1",
        source: "d1",
        target: "mech:G1:GoF",
        relation: "has_mechanism",
        evidence_class: "observed",
        status: "supported",
      },
      {
        id: "m2",
        source: "d2",
        target: "mech:G1:LoF",
        relation: "has_mechanism",
        evidence_class: "observed",
        status: "supported",
      },
      // Same disease linked again via the lane-C vocabulary → deduped
      {
        id: "m3",
        source: "mech:G1:GoF",
        target: "d1",
        relation: "causes",
        evidence_class: "observed",
        status: "supported",
      },
      // Mechanism–disease edge with a non-link relation → ignored entirely
      {
        id: "m4",
        source: "mech:G1:GoF",
        target: "d3",
        relation: "similar_to",
        evidence_class: "inferred",
        status: "supported",
      },
    ],
    claims: [],
  };

  it("ranks a Disease --has_mechanism--> Mechanism edge for dampen", () => {
    const res = mechanismSearch(mini, "dampen");
    const included = res.results.map((r) => r.node.id);
    expect(included).toContain("d1");
    const d1 = res.results.find((r) => r.node.id === "d1")!;
    expect(d1.mechanism.id).toBe("mech:G1:GoF");
    expect(d1.evidence_level).toBe("observed");
  });

  it("excludes the LoF disease with a reason", () => {
    const res = mechanismSearch(mini, "dampen");
    const ex = res.excluded.find((e) => e.node.id === "d2")!;
    expect(ex.reason.toLowerCase()).toContain("loss-of-function");
  });

  it("counts each disease once across duplicate mechanism links", () => {
    const res = mechanismSearch(mini, "dampen");
    expect(res.results.filter((r) => r.node.id === "d1")).toHaveLength(1);
  });

  it("ignores mechanism-disease edges whose relation is not causes/has_mechanism", () => {
    const res = mechanismSearch(mini, "dampen");
    expect(res.results.map((r) => r.node.id)).not.toContain("d3");
    expect(res.excluded.map((e) => e.node.id)).not.toContain("d3");
  });
});

describe("neighborhoodElements (map data)", () => {
  it("caps at maxNodes and keeps only internal edges", () => {
    const { nodes, edges } = neighborhoodElements(graph, "mock:disease:gof", 40);
    expect(nodes.length).toBeLessThanOrEqual(40);
    expect(nodes.map((n) => n.id)).toContain("mock:disease:gof");
    const ids = new Set(nodes.map((n) => n.id));
    for (const e of edges) {
      expect(ids.has(e.source)).toBe(true);
      expect(ids.has(e.target)).toBe(true);
    }
  });

  it("respects a tiny cap", () => {
    const { nodes } = neighborhoodElements(graph, "mock:disease:gof", 3);
    expect(nodes.length).toBeLessThanOrEqual(3);
    expect(nodes.map((n) => n.id)).toContain("mock:disease:gof");
  });
});

describe("extractCitations", () => {
  it("pulls edge and claim ids out of bracketed text", () => {
    expect(extractCitations("linked [e_000003] and [c_000001, e_000011]")).toEqual([
      "e_000003",
      "c_000001",
      "e_000011",
    ]);
    expect(extractCitations("no citations here")).toEqual([]);
  });
});
