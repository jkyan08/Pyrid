import { describe, expect, it } from "vitest";
import { displayLabel, humanizeVariantMentions, shortVariantLabel } from "../display";

describe("shortVariantLabel", () => {
  it("renders protein change in one-letter form", () => {
    expect(shortVariantLabel("NM_007315.4(STAT1):c.820C>T (p.Arg274Trp)")).toBe("STAT1 R274W");
  });

  it("keeps frameshift notation", () => {
    expect(shortVariantLabel("NM_007315.4(STAT1):c.1011_1012del (p.Val339fs)")).toBe(
      "STAT1 V339fs"
    );
  });

  it("falls back to the coding change when no protein change exists", () => {
    expect(shortVariantLabel("NM_007315.4(STAT1):c.1632+1G>A")).toBe("STAT1 c.1632+1G>A");
  });

  it("passes through labels it cannot parse", () => {
    expect(shortVariantLabel("NC_000002.11:g.(?_191835429)_(191878936_?)del")).toBe(
      "NC_000002.11:g.(?_191835429)_(191878936_?)del"
    );
    expect(shortVariantLabel("JAK2 V617F (somatic)")).toBe("JAK2 V617F (somatic)");
  });
});

describe("humanizeVariantMentions", () => {
  it("shortens HGVS mentions inside prose", () => {
    expect(
      humanizeVariantMentions(
        "What is broken: NM_007315.4(STAT1):c.820C>T (p.Arg274Trp) has mechanism STAT1 gain of function [e_000001]."
      )
    ).toBe("What is broken: STAT1 R274W has mechanism STAT1 gain of function [e_000001].");
  });
});

describe("displayLabel", () => {
  it("shortens variants and capitalizes diseases", () => {
    expect(displayLabel("NM_007315.4(STAT1):c.820C>T (p.Arg274Trp)", "Variant")).toBe(
      "STAT1 R274W"
    );
    expect(displayLabel("immunodeficiency 31B", "Disease")).toBe("Immunodeficiency 31B");
  });

  it("leaves other types untouched", () => {
    expect(displayLabel("ruxolitinib", "Drug")).toBe("ruxolitinib");
  });
});
