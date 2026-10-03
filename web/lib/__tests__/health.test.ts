import { describe, it, expect } from "vitest";
import { shouldShowMockRibbon } from "../health";

describe("shouldShowMockRibbon", () => {
  it("shows the ribbon when the API reports mock mode", () => {
    expect(shouldShowMockRibbon({ status: "ok", mode: "mock" })).toBe(true);
  });

  it("stays hidden for real mode", () => {
    expect(shouldShowMockRibbon({ status: "ok", mode: "real" })).toBe(false);
  });

  it("stays hidden when mode is absent", () => {
    expect(shouldShowMockRibbon({ status: "ok" })).toBe(false);
  });

  it("stays hidden for unreachable/garbage responses (silent no-op)", () => {
    expect(shouldShowMockRibbon(null)).toBe(false);
    expect(shouldShowMockRibbon(undefined)).toBe(false);
    expect(shouldShowMockRibbon("mock")).toBe(false);
    expect(shouldShowMockRibbon({ mode: "MOCK" })).toBe(false);
    expect(shouldShowMockRibbon(42)).toBe(false);
  });
});
