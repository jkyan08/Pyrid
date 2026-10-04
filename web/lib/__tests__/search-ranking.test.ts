import { describe, expect, it } from "vitest";
import { searchGraph } from "../graph";
import type { Graph } from "../types";

function graphWith(nodes: Array<{ id: string; type: string; label: string }>): Graph {
  return {
    meta: { built_at: "", sources: [], coverage: {} },
    nodes: nodes.map((n) => ({ ...n, synonyms: [], props: {} })),
    edges: [],
    claims: [],
    clusters: [],
  } as unknown as Graph;
}

describe("search ranking (mirrors API)", () => {
  it("ranks entities before papers of equal match quality", () => {
    const g = graphWith([
      { id: "p1", type: "Paper", label: "STAT1 mutations in disease: a review" },
      { id: "p2", type: "Paper", label: "Another STAT1 paper" },
      { id: "g1", type: "Gene", label: "STAT1" },
      { id: "v1", type: "Variant", label: "NM_007315.4(STAT1):c.820C>T" },
      { id: "d1", type: "Disease", label: "STAT1 deficiency" },
    ]);
    const types = searchGraph(g, "STAT1").results.map((r) => r.type);
    expect(searchGraph(g, "STAT1").results[0].id).toBe("g1");
    expect(types.indexOf("Disease")).toBeLessThan(types.indexOf("Paper"));
    expect(types.indexOf("Variant")).toBeLessThan(types.indexOf("Paper"));
  });

  it("caps papers and investigators at 3 each, total at 24", () => {
    const nodes = Array.from({ length: 11 }, (_, i) => ({
      id: `p${i}`, type: "Paper", label: `STAT1 paper number ${i}`,
    }));
    for (let i = 0; i < 8; i++) nodes.push({ id: `i${i}`, type: "Investigator", label: `STAT1 Lab ${i}` });
    for (let i = 0; i < 30; i++) nodes.push({ id: `ph${i}`, type: "Phenotype", label: `STAT1 phenotype ${i}` });
    const results = searchGraph(graphWith(nodes), "STAT1").results;
    expect(results.filter((r) => r.type === "Paper").length).toBeLessThanOrEqual(3);
    expect(results.filter((r) => r.type === "Investigator").length).toBeLessThanOrEqual(3);
    expect(results.length).toBeLessThanOrEqual(24);
  });

  it("rejects single-generic-token free-text noise", () => {
    const g = graphWith([{ id: "d1", type: "Disease", label: "immune syndrome of the skin" }]);
    expect(searchGraph(g, "bone marrow syndrome").results).toEqual([]);
  });
});
