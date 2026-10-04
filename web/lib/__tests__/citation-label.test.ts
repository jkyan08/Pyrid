import { describe, expect, it } from "vitest";
import { citationLabel } from "../graph";
import type { Graph } from "../types";

const graph = {
  meta: { built_at: "", sources: [], coverage: {} },
  nodes: [
    { id: "d:1", type: "Disease", label: "STAT1 gain of function", synonyms: [], props: {} },
    { id: "drug:1", type: "Drug", label: "ruxolitinib", synonyms: [], props: {} },
  ],
  edges: [
    {
      id: "e_000001", source: "drug:1", target: "d:1", relation: "treats",
      evidence_class: "extracted", confidence: 0.5, confidence_basis: { n: 1 },
      status: "contested", sources: [], supporting: ["c_000001"], contradicting: [],
      attributes: {},
    },
  ],
  claims: [
    {
      id: "c_000001", pmid: "28139313", paper: "pmid:28139313", quote: "q",
      polarity: "supports", subject: "drug:1", predicate: "treats", object: "d:1",
      variant: "", study_type: "case_report", n_patients: 1,
    },
    {
      id: "c_000002", pmid: "DEMO-1", paper: "pmid:x", quote: "q",
      polarity: "supports", subject: "drug:1", predicate: "treats", object: "d:1",
      variant: "", study_type: "in_vitro", n_patients: null,
    },
  ],
  clusters: [],
} as unknown as Graph;

describe("citationLabel", () => {
  it("labels edges as relation → target label", () => {
    expect(citationLabel(graph, "e_000001")).toBe("treats → STAT1 gain of function");
  });

  it("labels claims by numeric PMID", () => {
    expect(citationLabel(graph, "c_000001")).toBe("PMID 28139313");
  });

  it("labels non-numeric-pmid claims by study type", () => {
    expect(citationLabel(graph, "c_000002")).toBe("in vitro claim");
  });

  it("falls back to the raw id for unknown ids", () => {
    expect(citationLabel(graph, "e_999999")).toBe("e_999999");
  });

  it("truncates very long target labels", () => {
    const g = {
      ...graph,
      nodes: [
        ...graph.nodes.filter((n) => n.id !== "d:1"),
        { id: "d:1", type: "Disease", label: "x".repeat(60), synonyms: [], props: {} },
      ],
    } as Graph;
    const label = citationLabel(g, "e_000001");
    expect(label.length).toBeLessThanOrEqual("treats → ".length + 33);
    expect(label.endsWith("…")).toBe(true);
  });
});
