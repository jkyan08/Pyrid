import { describe, it, expect } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ThinkingDots from "../../components/smoothui/thinking-dots";

/** The app's one loading indicator: a label for screen readers + 3 dots. */
describe("ThinkingDots", () => {
  it("renders the accessible label", () => {
    const html = renderToStaticMarkup(
      createElement(ThinkingDots, { label: "Searching" })
    );
    expect(html).toContain('role="status"');
    expect(html).toContain('aria-label="Searching"');
    expect(html).toContain("Searching");
  });

  it("renders exactly three pulsing dots", () => {
    const html = renderToStaticMarkup(createElement(ThinkingDots));
    expect(html.match(/thinking-dot/g)?.length).toBe(3);
  });

  it("applies the size prop to every dot", () => {
    const html = renderToStaticMarkup(createElement(ThinkingDots, { size: 8 }));
    expect(html.match(/width:8px/g)?.length).toBe(3);
  });

  it("defaults to a generic loading label", () => {
    const html = renderToStaticMarkup(createElement(ThinkingDots));
    expect(html).toContain('aria-label="Loading"');
  });
});
