"use client";

/**
 * Cytoscape map of an entity's neighborhood (≤40 nodes), client-only.
 * - Color encodes CLUSTER MEMBERSHIP only (nodes outside any cluster are gray).
 * - Node size is a degree/centrality proxy.
 * - Dashed edges mark inter-cluster bridges.
 * - Inferred / inferred_unsupported edges are visually muted AND dashed.
 * - Clicking an edge opens the evidence drawer; clicking a node navigates.
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { Graph } from "@/lib/types";
import { neighborhoodElements } from "@/lib/graph";
import { useEvidence } from "./evidence-drawer";
import type { Core } from "cytoscape";

// Muted categorical palette; color = cluster membership only.
const CLUSTER_COLORS = ["#7c93c4", "#6fb3a0", "#d99a6c", "#c290bd", "#a3b86c", "#d4bd68"];
const NO_CLUSTER_COLOR = "#d6d6dc";

export function GraphMap({ graph, centerId }: { graph: Graph; centerId: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const router = useRouter();
  const { openEvidence } = useEvidence();
  const [legend, setLegend] = useState<{ id: string; color: string }[]>([]);

  useEffect(() => {
    let cy: Core | null = null;
    let cancelled = false;

    (async () => {
      const cytoscape = (await import("cytoscape")).default;
      if (cancelled || !containerRef.current) return;

      const { nodes, edges, clusterOf, degree } = neighborhoodElements(graph, centerId, 40);

      const clusterIds = Array.from(new Set(Object.values(clusterOf)));
      const colorOf = (clusterId: string | undefined) =>
        clusterId === undefined
          ? NO_CLUSTER_COLOR
          : CLUSTER_COLORS[clusterIds.indexOf(clusterId) % CLUSTER_COLORS.length];
      setLegend(clusterIds.map((id) => ({ id, color: colorOf(id) })));

      const maxDegree = Math.max(1, ...Object.values(degree));

      const elements = [
        ...nodes.map((n) => ({
          data: {
            id: n.id,
            label: n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label,
            color: colorOf(clusterOf[n.id]),
            size: 18 + 26 * Math.sqrt((degree[n.id] ?? 0) / maxDegree),
            borderWidth: n.id === centerId ? 3 : 1,
          },
        })),
        ...edges.map((e) => {
          const bridge =
            clusterOf[e.source] !== undefined &&
            clusterOf[e.target] !== undefined &&
            clusterOf[e.source] !== clusterOf[e.target];
          const muted = e.evidence_class === "inferred" || e.status === "inferred_unsupported";
          return {
            data: {
              id: e.id,
              source: e.source,
              target: e.target,
              lineStyle: bridge || muted ? "dashed" : "solid",
              lineColor: muted ? "#d9d9e0" : "#b4b4bd",
              opacity: muted ? 0.55 : 1,
              width: muted ? 1.25 : 2,
            },
          };
        }),
      ];

      cy = cytoscape({
        container: containerRef.current,
        elements,
        style: [
          {
            selector: "node",
            style: {
              "background-color": "data(color)",
              width: "data(size)",
              height: "data(size)",
              label: "data(label)",
              "font-size": "9px",
              color: "#4b4b55",
              "text-valign": "bottom",
              "text-margin-y": 4,
              "text-wrap": "ellipsis",
              "text-max-width": "110px",
              "border-width": "data(borderWidth)",
              "border-color": "#6b7280",
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
        ],
        layout: { name: "cose", animate: false, padding: 24 },
        wheelSensitivity: 0.2,
      });

      cy.on("tap", "node", (ev) => {
        const id: string = ev.target.id();
        if (id !== centerId) router.push(`/entity/${encodeURIComponent(id)}`);
      });
      cy.on("tap", "edge", (ev) => {
        openEvidence(ev.target.id());
      });
    })();

    return () => {
      cancelled = true;
      cy?.destroy();
    };
  }, [graph, centerId, router, openEvidence]);

  return (
    <div>
      <div
        ref={containerRef}
        className="h-[420px] w-full rounded-lg border border-gray-200 bg-gray-50/40"
        aria-label="Entity neighborhood map"
      />
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
        {legend.map((l) => (
          <span key={l.id} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: l.color }} />
            {l.id}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: NO_CLUSTER_COLOR }} />
          no cluster
        </span>
        <span>· size = connectivity · dashed = bridge or inferred (muted)</span>
      </div>
      <p className="mt-1 text-xs text-gray-400">
        Click a node to navigate; click an edge to open its evidence.
      </p>
    </div>
  );
}
