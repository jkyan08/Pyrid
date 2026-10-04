import { describe, expect, it } from "vitest";
import { sortClaimsByEvidence } from "../graph";
import type { Claim } from "../types";

const claim = (id: string, study_type?: string, n_patients?: number | null): Claim =>
  ({ id, study_type, n_patients } as Claim);

describe("sortClaimsByEvidence", () => {
  it("orders claims strongest study type first", () => {
    const sorted = sortClaimsByEvidence([
      claim("c_weak", "in_vitro"),
      claim("c_strong", "cohort"),
      claim("c_mid", "case_series"),
    ]);
    expect(sorted.map((c) => c.id)).toEqual(["c_strong", "c_mid", "c_weak"]);
  });

  it("breaks ties by larger patient count, unknown n last", () => {
    const sorted = sortClaimsByEvidence([
      claim("c_no_n", "case_series", null),
      claim("c_big", "case_series", 40),
      claim("c_small", "case_series", 3),
    ]);
    expect(sorted.map((c) => c.id)).toEqual(["c_big", "c_small", "c_no_n"]);
  });

  it("puts unknown study types last and does not mutate the input", () => {
    const input = [claim("c_mystery", "vibes"), claim("c_report", "case_report")];
    const sorted = sortClaimsByEvidence(input);
    expect(sorted.map((c) => c.id)).toEqual(["c_report", "c_mystery"]);
    expect(input.map((c) => c.id)).toEqual(["c_mystery", "c_report"]);
  });
});
