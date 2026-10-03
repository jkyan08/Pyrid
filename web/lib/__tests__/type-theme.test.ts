import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Graph } from "../types";
import { STUDY_RANK } from "../graph";
import { evidenceLevelChipClass, themeOf, TYPE_THEMES } from "../type-theme";

/**
 * The theme map is the ONE source of truth for how a node type looks.
 * Completeness is enforced against the NodeType union in types.ts itself:
 * adding a type there without a theme entry fails this suite.
 */
function nodeTypeLiteralsFromSource(): string[] {
  const src = readFileSync(join(__dirname, "../types.ts"), "utf-8");
  const union = src.match(/export type NodeType =([\s\S]*?);/);
  if (!union) throw new Error("NodeType union not found in types.ts");
  return Array.from(union[1].matchAll(/"([^"]+)"/g), (m) => m[1]);
}

// Shapes cytoscape actually supports (subset we allow ourselves).
const CYTOSCAPE_SHAPES = new Set([
  "ellipse",
  "triangle",
  "round-triangle",
  "rectangle",
  "round-rectangle",
  "bottom-round-rectangle",
  "cut-rectangle",
  "barrel",
  "rhomboid",
  "right-rhomboid",
  "diamond",
  "round-diamond",
  "pentagon",
  "round-pentagon",
  "hexagon",
  "round-hexagon",
  "concave-hexagon",
  "heptagon",
  "round-heptagon",
  "octagon",
  "round-octagon",
  "star",
  "tag",
  "round-tag",
  "vee",
]);

describe("type-theme completeness", () => {
  const literals = nodeTypeLiteralsFromSource();

  it("extracts the NodeType literals from types.ts", () => {
    expect(literals.length).toBeGreaterThanOrEqual(13);
    expect(literals).toContain("Disease");
    expect(literals).toContain("Drug");
  });

  it("has a complete theme entry for every NodeType literal in types.ts", () => {
    for (const type of literals) {
      const theme = TYPE_THEMES[type];
      expect(theme, `missing theme for NodeType "${type}"`).toBeDefined();
      expect(theme.label.length).toBeGreaterThan(0);
      expect(theme.badge).toMatch(/bg-[^ ]+ text-[^ ]+ border-/);
      expect(theme.text).toMatch(/^text-/);
      expect(theme.accent).toMatch(/^border-l-/);
      expect(theme.band).toMatch(/bg-/);
      expect(theme.hex).toMatch(/^#[0-9a-f]{6}$/i);
      expect(theme.glyph).toMatch(/<(path|circle|rect|ellipse|g)\b/);
    }
  });

  it("has an Unknown fallback, used for unrecognized and missing types", () => {
    expect(TYPE_THEMES.Unknown).toBeDefined();
    expect(themeOf("SomethingNew")).toBe(TYPE_THEMES.Unknown);
    expect(themeOf(undefined)).toBe(TYPE_THEMES.Unknown);
    expect(themeOf(null)).toBe(TYPE_THEMES.Unknown);
    expect(themeOf("Drug")).toBe(TYPE_THEMES.Drug);
  });

  it("keeps hues, badges and glyphs distinct across types", () => {
    const entries = Object.values(TYPE_THEMES);
    const unique = (xs: string[]) => new Set(xs).size === xs.length;
    expect(unique(entries.map((t) => t.hex))).toBe(true);
    expect(unique(entries.map((t) => t.badge))).toBe(true);
    expect(unique(entries.map((t) => t.glyph))).toBe(true);
  });

  it("uses only valid cytoscape node shapes", () => {
    for (const [type, theme] of Object.entries(TYPE_THEMES)) {
      expect(CYTOSCAPE_SHAPES.has(theme.shape), `invalid shape "${theme.shape}" for ${type}`).toBe(
        true
      );
    }
  });

  it("covers every node type present in the demo fixture without falling back", () => {
    const graph: Graph = JSON.parse(
      readFileSync(join(__dirname, "../../../api/fixtures/graph.demo.mock.json"), "utf-8")
    );
    for (const type of new Set(graph.nodes.map((n) => n.type))) {
      expect(TYPE_THEMES[type], `demo fixture type "${type}" has no theme`).toBeDefined();
    }
  });
});

describe("evidenceLevelChipClass", () => {
  it("returns a chip class for every known study rank level", () => {
    for (const level of Object.keys(STUDY_RANK)) {
      expect(evidenceLevelChipClass(level)).toMatch(/bg-[^ ]+ text-[^ ]+ border-/);
    }
  });

  it("scales from observed/trial (strongest) down to inferred (weakest)", () => {
    expect(evidenceLevelChipClass("observed")).toBe(evidenceLevelChipClass("trial"));
    expect(evidenceLevelChipClass("observed")).toContain("emerald");
    expect(evidenceLevelChipClass("in_vitro")).not.toBe(evidenceLevelChipClass("observed"));
    expect(evidenceLevelChipClass("inferred")).toContain("gray");
    expect(evidenceLevelChipClass("not_a_level")).toBe(evidenceLevelChipClass("inferred"));
  });
});
