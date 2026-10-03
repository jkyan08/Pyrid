"use client";

/**
 * Cytoscape map of an entity's neighborhood (≤40 nodes), client-only.
 * Visual grammar (also spelled out in the legend):
 * - FILL color encodes CLUSTER MEMBERSHIP only (README §10: colour =
 *   cluster only; nodes outside any cluster are gray).
 * - SHAPE + BORDER color encode the node TYPE (from lib/type-theme).
 * - Size = degree/centrality proxy (sqrt scale).
 * - Dashed edges mark inter-cluster bridges and inferred evidence
 *   (inferred is also muted); edge width scales with confidence.
 * - fcose force-directed layout: animated physical settle, draggable
 *   nodes, re-run on container resize, animation off under
 *   prefers-reduced-motion.
 * - Hover highlights a node and its connected edges; the selected node
 *   gets a brand ring. Clicking an edge opens the evidence drawer;
 *   clicking a node navigates.
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useReducedMotion } from "motion/react";
import type { Graph } from "@/lib/types";
import { neighborhoodElements } from "@/lib/graph";
import { themeOf } from "@/lib/type-theme";
import { TypeGlyph } from "./badges";
import { useEvidence } from "./evidence-drawer";
import type { Core, EdgeSingular, NodeSingular } from "cytoscape";

// Muted categorical palette; FILL color = cluster membership only.
const CLUSTER_COLORS = ["#7c93c4", "#6fb3a0", "#d99a6c", "#c290bd", "#a3b86c", "#d4bd68"];
const NO_CLUSTER_COLOR = "#d6d6dc";
// Brand ring for the selected node (~sky-600, the --brand token).
const BRAND_HEX = "#0284c7";

// Register the fcose extension once per module (re-registration warns).
let fcoseRegistered = false;

export function GraphMap({ graph, centerId }: { graph: Graph; centerId: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const router = useRouter();
  const { openEvidence } = useEvidence();
  const reducedMotion = useReducedMotion();
  const [legend, setLegend] = useState<{ clusters: { id: string; color: string }[]; types: string[] }>(
    { clusters: [], types: [] }
  );
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    let cy: Core | null = null;
    let cancelled = false;
    let resizeObserver: ResizeObserver | null = null;
    let resizeTimer: ReturnType<typeof setTimeout> | null = null;

    (async () => {
      const [{ default: cytoscape }, { default: fcose }] = await Promise.all([
        import("cytoscape"),
        import("cytoscape-fcose"),
      ]);
      if (!fcoseRegistered) {
        cytoscape.use(fcose);
        fcoseRegistered = true;
      }
      if (cancelled || !containerRef.current) return;

      const { nodes, edges, clusterOf, degree } = neighborhoodElements(graph, centerId, 40);

      const clusterIds = Array.from(new Set(Object.values(clusterOf)));
      const colorOf = (clusterId: string | undefined) =>
        clusterId === undefined
          ? NO_CLUSTER_COLOR
          : CLUSTER_COLORS[clusterIds.indexOf(clusterId) % CLUSTER_COLORS.length];
      setLegend({
        clusters: clusterIds.map((id) => ({ id, color: colorOf(id) })),
        types: Array.from(new Set(nodes.map((n) => n.type))).sort(),
      });

      const maxDegree = Math.max(1, ...Object.values(degree));

      const elements = [
        ...nodes.map((n, i) => {
          const theme = themeOf(n.type);
          // Deterministic sunflower-spiral seed positions: with
          // randomize:false fcose refines from current positions, and an
          // all-at-origin start would collapse into a degenerate line.
          const r = 26 * Math.sqrt(i);
          const a = i * 2.39996; // golden angle
          return {
            position: { x: Math.cos(a) * r, y: Math.sin(a) * r },
            data: {
              id: n.id,
              label: n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label,
              color: colorOf(clusterOf[n.id]),
              // sqrt scale over a wider range than before: 22 → 60 px
              size: 22 + 38 * Math.sqrt((degree[n.id] ?? 0) / maxDegree),
              shape: theme.shape,
              borderColor: theme.hex,
              borderWidth: n.id === centerId ? 3 : 2,
            },
          };
        }),
        ...edges.map((e) => {
          const bridge =
            clusterOf[e.source] !== undefined &&
            clusterOf[e.target] !== undefined &&
            clusterOf[e.source] !== clusterOf[e.target];
          const muted = e.evidence_class === "inferred" || e.status === "inferred_unsupported";
          // Edge width scales with confidence (default 0.5 when unstated).
          const confidence = typeof e.confidence === "number" ? e.confidence : 0.5;
          return {
            data: {
              id: e.id,
              source: e.source,
              target: e.target,
              lineStyle: bridge || muted ? "dashed" : "solid",
              lineColor: muted ? "#d9d9e0" : "#b4b4bd",
              opacity: muted ? 0.55 : 1,
              width: muted ? 1.25 : 1.25 + 2.25 * confidence,
            },
          };
        }),
      ];

      const layoutOptions = {
        name: "fcose",
        quality: "default",
        animate: !reducedMotion,
        animationDuration: 850,
        randomize: false,
        fit: true,
        padding: 32,
        nodeRepulsion: 7500,
        // Ideal edge length scaled to how crowded the neighborhood is.
        idealEdgeLength: Math.max(55, Math.min(130, Math.round(420 / Math.sqrt(Math.max(1, nodes.length))))),
        edgeElasticity: 0.45,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any;

      cy = cytoscape({
        container: containerRef.current,
        elements,
        style: [
          {
            selector: "node",
            style: {
              "background-color": "data(color)",
              shape: "data(shape)" as "ellipse",
              width: "data(size)",
              height: "data(size)",
              label: "data(label)",
              "font-size": "10px",
              color: "#374151",
              "text-valign": "bottom",
              "text-margin-y": 5,
              "text-wrap": "wrap",
              "text-max-width": "110px",
              // Outline keeps labels readable over edges and fills.
              "text-outline-width": 2,
              "text-outline-color": "#ffffff",
              "text-outline-opacity": 0.9,
              "border-width": "data(borderWidth)",
              "border-color": "data(borderColor)",
            },
          },
          {
            selector: "edge",
            style: {
              "curve-style": "bezier",
              "line-style": "data(lineStyle)" as "solid",
              "line-color": "data(lineColor)",
              opacity: "data(opacity)" as unknown as number,
              width: "data(width)",
            },
          },
          // Hover: node highlight + connected-edge emphasis.
          {
            selector: "node.hovered",
            style: {
              "border-width": 4,
              "overlay-color": "#0284c7",
              "overlay-opacity": 0.08,
              "overlay-padding": 6,
              "font-size": "11px",
            },
          },
          {
            selector: "edge.adjacent",
            style: {
              "line-color": "#64748b",
              opacity: 1,
              width: (ele: EdgeSingular) => (ele.data("width") as number) + 1,
              "z-index": 9,
            },
          },
          // Selected node: brand ring.
          {
            selector: "node:selected",
            style: {
              "border-width": 4,
              "border-color": BRAND_HEX,
              "overlay-color": BRAND_HEX,
              "overlay-opacity": 0.12,
              "overlay-padding": 7,
            },
          },
        ],
        layout: layoutOptions,
        wheelSensitivity: 0.2,
      });

      // Smooth fade-in once the force layout settles.
      cy.one("layoutstop", () => setSettled(true));
      // Safety: if layoutstop never fires (zero nodes), still reveal.
      if (nodes.length === 0) setSettled(true);

      cy.getElementById(centerId).select();

      cy.on("tap", "node", (ev) => {
        const id: string = ev.target.id();
        if (id !== centerId) router.push(`/entity/${encodeURIComponent(id)}`);
      });
      cy.on("tap", "edge", (ev) => {
        openEvidence(ev.target.id());
      });
      cy.on("mouseover", "node", (ev) => {
        const node = ev.target as NodeSingular;
        node.addClass("hovered");
        node.connectedEdges().addClass("adjacent");
      });
      cy.on("mouseout", "node", (ev) => {
        const node = ev.target as NodeSingular;
        node.removeClass("hovered");
        node.connectedEdges().removeClass("adjacent");
      });

      // Re-run the force layout when the container resizes (debounced).
      let lastW = containerRef.current.clientWidth;
      let lastH = containerRef.current.clientHeight;
      resizeObserver = new ResizeObserver((entries) => {
        const rect = entries[0]?.contentRect;
        if (!rect || !cy) return;
        if (Math.abs(rect.width - lastW) < 8 && Math.abs(rect.height - lastH) < 8) return;
        lastW = rect.width;
        lastH = rect.height;
        if (resizeTimer) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
          if (!cy) return;
          cy.resize();
          cy.layout(layoutOptions).run();
        }, 250);
      });
      resizeObserver.observe(containerRef.current);
    })();

    return () => {
      cancelled = true;
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeObserver?.disconnect();
      cy?.destroy();
    };
  }, [graph, centerId, router, openEvidence, reducedMotion]);

  return (
    <div>
      <div className="relative overflow-hidden rounded-xl border border-gray-200 bg-gradient-to-br from-sky-50/50 via-white to-gray-50 shadow-[0_1px_8px_rgba(2,132,199,0.06)]">
        <div
          ref={containerRef}
          className={`h-[50vh] min-h-[340px] w-full transition-opacity duration-500 motion-reduce:transition-none md:h-[68vh] ${
            settled ? "opacity-100" : "opacity-0"
          }`}
          aria-label="Entity neighborhood map"
        />
        {!settled && (
          <p className="absolute inset-0 flex items-center justify-center text-sm text-gray-400">
            Settling the force layout…
          </p>
        )}
        {/* Compact legend overlaying a corner: fill = cluster, shape/border = type. */}
        <div className="absolute right-2 top-2 max-h-[55%] max-w-[46%] overflow-y-auto rounded-lg border border-gray-200/80 bg-white/85 px-2.5 py-2 text-[10px] leading-4 text-gray-600 shadow-sm backdrop-blur-sm sm:max-w-[38%]">
          <p className="mb-1 font-semibold uppercase tracking-wide text-gray-400">Fill = cluster</p>
          {legend.clusters.map((l) => (
            <p key={l.id} className="flex items-center gap-1.5">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: l.color }} />
              <span className="truncate">{l.id}</span>
            </p>
          ))}
          <p className="flex items-center gap-1.5">
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: NO_CLUSTER_COLOR }} />
            no cluster
          </p>
          <p className="mb-1 mt-2 font-semibold uppercase tracking-wide text-gray-400">
            Shape/border = type
          </p>
          {legend.types.map((t) => {
            const theme = themeOf(t);
            return (
              <p key={t} className="flex items-center gap-1.5">
                <TypeGlyph type={t} size={11} className={`shrink-0 ${theme.text}`} />
                <span className="truncate">{theme.label}</span>
              </p>
            );
          })}
          <p className="mt-2 border-t border-gray-100 pt-1.5 text-gray-400">
            dashed = inferred/bridge
          </p>
        </div>
      </div>
      <p className="mt-2 text-xs text-gray-500">
        Fill = cluster · shape &amp; border = type · size = connectivity · edge width = confidence ·
        dashed = inferred or inter-cluster bridge (inferred is muted).
      </p>
      <p className="mt-1 text-xs text-gray-400">
        Drag nodes to rearrange; click a node to navigate; click an edge to open its evidence.
      </p>
    </div>
  );
}
