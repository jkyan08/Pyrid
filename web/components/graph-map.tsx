"use client";

/**
 * Obsidian-like map: D3 force owns one persistent simulation, while Canvas
 * paints each tick. Dragging fixes only the grabbed node; springs move its
 * neighbors, distant velocity is damped, and release lets the map relax.
 */

import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { useRouter } from "next/navigation";
import { useReducedMotion } from "motion/react";
import type { Graph } from "@/lib/types";
import { neighborhoodElements } from "@/lib/graph";
import { displayLabel } from "@/lib/display";
import { themeOf } from "@/lib/type-theme";
import {
  beginNodeDrag,
  createForcePhysics,
  endNodeDrag,
  moveNodeDrag,
  updateRepulsion,
} from "@/lib/force-physics";
import type { ForceEdge, ForceNode, ForcePhysics } from "@/lib/force-physics";
import {
  canvasPoint,
  fitGraph,
  hitEdge,
  hitNode,
  requestGraphDraw,
  worldPoint,
  zoomGraphAt,
} from "./graph-canvas";
import type { GraphCanvasState } from "./graph-canvas";
import ThinkingDots from "@/components/smoothui/thinking-dots";
import { TypeGlyph } from "./badges";
import { useEvidence } from "./evidence-drawer";

// Cluster fills harmonized with the atlas palette: sage, teal-green, ochre,
// clay, stone-blue and olive — earth tones that stay mutually distinguishable
// on the dark pine canvas.
const CLUSTER_COLORS = ["#a3b18a", "#74a589", "#c2a36b", "#b98a6e", "#7e99ad", "#9a9b6d"];
const NO_CLUSTER_COLOR = "#8a9589";

interface Runtime {
  canvas: GraphCanvasState;
  physics: ForcePhysics;
  destroyed: boolean;
}

interface PointerAction {
  pointerId: number;
  kind: "node" | "edge" | "pan";
  node?: ForceNode;
  edge?: ForceEdge;
  startX: number;
  startY: number;
  lastX: number;
  lastY: number;
  offsetX: number;
  offsetY: number;
  moved: boolean;
}

interface HoverInfo {
  kind: "node" | "edge";
  id: string;
  title: string;
  detail: string;
  x: number;
  y: number;
}

export function GraphMap({ graph, centerId }: { graph: Graph; centerId: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const runtimeRef = useRef<Runtime | null>(null);
  const pointerRef = useRef<PointerAction | null>(null);
  const hoverRef = useRef<HoverInfo | null>(null);
  const router = useRouter();
  const { openEvidence } = useEvidence();
  const reducedMotion = Boolean(useReducedMotion());
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [repulsion, setRepulsion] = useState(280);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [legend, setLegend] = useState<{ clusters: { id: string; color: string }[]; types: string[]; count: number }>({
    clusters: [], types: [], count: 0,
  });

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) {
      setError("The graph canvas is unavailable in this browser.");
      return;
    }

    const { nodes, edges, clusterOf, degree } = neighborhoodElements(graph, centerId, 40);
    const clusterIds = Array.from(new Set(Object.values(clusterOf)));
    const colorOf = (clusterId: string | undefined) =>
      clusterId === undefined ? NO_CLUSTER_COLOR : CLUSTER_COLORS[clusterIds.indexOf(clusterId) % CLUSTER_COLORS.length];
    setLegend({
      clusters: clusterIds.map((id) => ({ id, color: colorOf(id) })),
      types: Array.from(new Set(nodes.map((node) => node.type))).sort(),
      count: nodes.length,
    });

    const maxDegree = Math.max(1, ...Object.values(degree));
    const forceNodes: ForceNode[] = nodes.map((node, index) => {
      const angle = index * 2.39996;
      const distance = 54 * Math.sqrt(index);
      const theme = themeOf(node.type);
      return {
        id: node.id,
        label: displayLabel(node.label, node.type),
        type: node.type,
        detail: ["Variant", "Paper", "Investigator"].includes(node.type),
        clusterColor: colorOf(clusterOf[node.id]),
        typeColor: theme.hex,
        shape: theme.shape,
        degree: degree[node.id] ?? 0,
        radius: node.id === centerId ? 22 : 8 + 10 * Math.sqrt((degree[node.id] ?? 0) / maxDegree),
        x: Math.cos(angle) * distance,
        y: Math.sin(angle) * distance,
      };
    });
    const forceEdges: ForceEdge[] = edges.map((edge) => {
      const bridge = clusterOf[edge.source] !== undefined && clusterOf[edge.target] !== undefined &&
        clusterOf[edge.source] !== clusterOf[edge.target];
      const muted = edge.evidence_class === "inferred" || edge.status === "inferred_unsupported";
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        relation: edge.relation,
        sourceCount: edge.sources?.length ?? 0,
        status: edge.status ?? "unknown",
        dashed: bridge || muted,
        muted,
        confidence: typeof edge.confidence === "number" ? edge.confidence : 0.5,
      };
    });

    const physics = createForcePhysics(forceNodes, forceEdges, 230);
    // Warm up before first paint so the graph appears coherent rather than
    // bursting from its seed points. Tick does not recreate the simulation.
    physics.simulation.tick(reducedMotion ? 190 : 90);
    const state: GraphCanvasState = {
      canvas, context, nodes: forceNodes, edges: forceEdges, centerId,
      width: 0, height: 0, dpr: 1,
      view: { x: 0, y: 0, k: 1 },
      hoveredNodeId: null, hoveredEdgeId: null, userMoved: false, frame: null,
    };
    const runtime: Runtime = { canvas: state, physics, destroyed: false };
    runtimeRef.current = runtime;

    const resize = () => {
      if (runtime.destroyed) return;
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      state.width = rect.width;
      state.height = rect.height;
      state.dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(rect.width * state.dpr);
      canvas.height = Math.round(rect.height * state.dpr);
      if (!state.userMoved) fitGraph(state);
      requestGraphDraw(state);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const point = canvasPoint(state, event.clientX, event.clientY);
      zoomGraphAt(state, Math.exp(-event.deltaY * 0.0012), point.x, point.y);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });

    physics.simulation.on("tick", () => requestGraphDraw(state));
    if (!reducedMotion) physics.simulation.alpha(0.2).restart();
    else requestGraphDraw(state);
    setReady(true);

    return () => {
      runtime.destroyed = true;
      observer.disconnect();
      canvas.removeEventListener("wheel", onWheel);
      physics.simulation.stop().on("tick", null);
      if (state.frame !== null) cancelAnimationFrame(state.frame);
      if (runtimeRef.current === runtime) runtimeRef.current = null;
    };
  }, [graph, centerId, reducedMotion]);

  const clearHover = () => {
    const runtime = runtimeRef.current;
    if (runtime) {
      runtime.canvas.hoveredNodeId = null;
      runtime.canvas.hoveredEdgeId = null;
      runtime.canvas.canvas.style.cursor = "grab";
      requestGraphDraw(runtime.canvas);
    }
    if (hoverRef.current) {
      hoverRef.current = null;
      setHover(null);
    }
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (event.button !== 0 || !runtimeRef.current) return;
    const state = runtimeRef.current.canvas;
    const point = canvasPoint(state, event.clientX, event.clientY);
    const node = hitNode(state, point.x, point.y);
    const edge = node ? null : hitEdge(state, point.x, point.y);
    const world = worldPoint(state, point.x, point.y);
    pointerRef.current = {
      pointerId: event.pointerId,
      kind: node ? "node" : edge ? "edge" : "pan",
      node: node ?? undefined,
      edge: edge ?? undefined,
      startX: point.x, startY: point.y, lastX: point.x, lastY: point.y,
      offsetX: node ? (node.x ?? 0) - world.x : 0,
      offsetY: node ? (node.y ?? 0) - world.y : 0,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.style.cursor = node || !edge ? "grabbing" : "pointer";
    event.preventDefault();
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const state = runtime.canvas;
    const point = canvasPoint(state, event.clientX, event.clientY);
    const action = pointerRef.current;
    if (action && action.pointerId === event.pointerId) {
      const distance = Math.hypot(point.x - action.startX, point.y - action.startY);
      if (action.kind === "node" && action.node) {
        if (!action.moved && distance >= 4) {
          action.moved = true;
          beginNodeDrag(runtime.physics, action.node, state.edges);
          if (reducedMotion) runtime.physics.simulation.stop();
          clearHover();
        }
        if (action.moved) {
          const world = worldPoint(state, point.x, point.y);
          moveNodeDrag(action.node, world.x + action.offsetX, world.y + action.offsetY);
          if (reducedMotion) runtime.physics.simulation.tick(3);
          requestGraphDraw(state);
        }
      } else if (action.kind === "pan" || action.kind === "edge") {
        if (distance >= 4) action.moved = true;
        if (action.moved) {
          action.kind = "pan";
          state.view.x += point.x - action.lastX;
          state.view.y += point.y - action.lastY;
          state.userMoved = true;
          requestGraphDraw(state);
        }
      }
      action.lastX = point.x;
      action.lastY = point.y;
      return;
    }

    const node = hitNode(state, point.x, point.y);
    const edge = node ? null : hitEdge(state, point.x, point.y);
    state.hoveredNodeId = node?.id ?? null;
    state.hoveredEdgeId = edge?.id ?? null;
    event.currentTarget.style.cursor = node || edge ? "pointer" : "grab";
    const next: HoverInfo | null = node ? {
      kind: "node", id: node.id, title: node.label,
      detail: `${themeOf(node.type).label} · ${node.degree} connection${node.degree === 1 ? "" : "s"}`,
      x: Math.min(point.x + 14, state.width - 220), y: Math.max(12, point.y - 64),
    } : edge ? {
      kind: "edge", id: edge.id,
      title: edge.relation.replaceAll("_", " "),
      detail: `${edge.sourceCount} source${edge.sourceCount === 1 ? "" : "s"} · ${edge.status}`,
      x: Math.min(point.x + 14, state.width - 220), y: Math.max(12, point.y - 64),
    } : null;
    if (next?.id !== hoverRef.current?.id || next?.kind !== hoverRef.current?.kind) {
      hoverRef.current = next;
      setHover(next);
    }
    requestGraphDraw(state);
  };

  const finishPointer = (event: ReactPointerEvent<HTMLCanvasElement>, cancelled = false) => {
    const action = pointerRef.current;
    const runtime = runtimeRef.current;
    if (!action || !runtime || action.pointerId !== event.pointerId) return;
    pointerRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (action.kind === "node" && action.node) {
      if (action.moved) {
        endNodeDrag(runtime.physics, action.node);
        if (reducedMotion) {
          runtime.physics.simulation.stop().tick(20);
          requestGraphDraw(runtime.canvas);
        }
      } else if (!cancelled && action.node.id !== centerId) {
        router.push(`/entity/${encodeURIComponent(action.node.id)}`);
      }
    } else if (action.kind === "edge" && action.edge && !action.moved && !cancelled) {
      openEvidence(action.edge.id);
    }
    event.currentTarget.style.cursor = "grab";
  };

  const zoom = (factor: number) => {
    const state = runtimeRef.current?.canvas;
    if (state) zoomGraphAt(state, factor, state.width / 2, state.height / 2);
  };

  const reheat = () => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    runtime.physics.simulation.alpha(Math.max(runtime.physics.simulation.alpha(), 0.36)).restart();
    if (reducedMotion) {
      runtime.physics.simulation.stop().tick(70);
      requestGraphDraw(runtime.canvas);
    }
  };

  const changeRepulsion = (value: number) => {
    setRepulsion(value);
    const runtime = runtimeRef.current;
    if (!runtime) return;
    updateRepulsion(runtime.physics, value);
    if (reducedMotion) {
      runtime.physics.simulation.stop().tick(70);
      requestGraphDraw(runtime.canvas);
    }
  };

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.15em] text-hunter">Living evidence map</p>
          <p className="mt-1 text-sm text-muted-foreground">Drag a node to feel its links pull nearby evidence into place.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="force-strength" className="text-xs font-medium text-muted-foreground">Repulsion</label>
          <select id="force-strength" value={repulsion} onChange={(event) => changeRepulsion(Number(event.target.value))} className="rounded-lg border border-dust bg-white px-2.5 py-2 text-xs font-medium text-foreground focus:outline-none focus:ring-2 focus:ring-fern">
            <option value={140}>Compact</option>
            <option value={280}>Balanced</option>
            <option value={460}>Spacious</option>
          </select>
          <button onClick={reheat} disabled={!ready} className="rounded-lg border border-dust bg-white px-3 py-2 text-xs font-medium text-foreground transition-colors hover:bg-dust/25 disabled:opacity-50">Reheat graph</button>
          <button onClick={() => { const state = runtimeRef.current?.canvas; if (state) fitGraph(state); }} disabled={!ready} className="rounded-lg border border-dust bg-white px-3 py-2 text-xs font-medium text-foreground transition-colors hover:bg-dust/25 disabled:opacity-50">Fit to view</button>
          <div className="flex overflow-hidden rounded-lg border border-dust bg-white">
            <button onClick={() => zoom(1.25)} disabled={!ready} aria-label="Zoom in" className="px-3 py-2 text-sm font-medium text-foreground hover:bg-dust/25 disabled:opacity-50">+</button>
            <button onClick={() => zoom(0.8)} disabled={!ready} aria-label="Zoom out" className="border-l border-dust px-3 py-2 text-sm font-medium text-foreground hover:bg-dust/25 disabled:opacity-50">−</button>
          </div>
        </div>
      </div>
      <div className="relative overflow-hidden rounded-[1.5rem] border border-pine/60 bg-[#1d2b24]">
        <canvas
          ref={canvasRef}
          className="h-[64vh] min-h-[520px] w-full touch-none lg:h-[76vh] lg:max-h-[900px]"
          role="img"
          aria-label="Interactive force-directed evidence graph. Drag nodes, pan the background, zoom, or select an edge for evidence. A text-based evidence trail follows below."
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={(event) => finishPointer(event)}
          onPointerCancel={(event) => finishPointer(event, true)}
          onPointerLeave={() => { if (!pointerRef.current) clearHover(); }}
        />
        {!ready && !error && <p className="pointer-events-none absolute inset-0 flex items-center justify-center gap-2 text-sm text-dust"><ThinkingDots label="Arranging the evidence network" className="text-sage" />Arranging the evidence network…</p>}
        {error && <p className="absolute inset-0 flex items-center justify-center text-sm text-[#e3b5a6]">{error}</p>}
        {ready && legend.count === 0 && <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-dust">No graph nodes are available for this entity.</p>}
        {hover && <div className="pointer-events-none absolute z-10 max-w-[220px] rounded-xl border border-sage/30 bg-[#142019]/95 px-3 py-2 text-xs text-white" style={{ left: Math.max(8, hover.x), top: hover.y }}>
          <p className="font-semibold leading-5">{hover.title}</p>
          <p className="mt-0.5 text-dust">{hover.detail}</p>
        </div>}
        <div className="pointer-events-none absolute bottom-4 left-4 rounded-full border border-sage/25 bg-[#142019]/75 px-3 py-1.5 text-[11px] font-medium text-dust backdrop-blur-sm">Drag nodes · Pan the canvas · Scroll to zoom · Click edges for evidence</div>
      </div>
      {/* Cluster-id swatch list removed: cluster ids are serial numbers
          (cl_001…) that mean nothing to a reader. Fill color still encodes
          cluster membership; the line below explains the encoding. */}
      <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-dust bg-white px-4 py-3 text-xs text-muted-foreground">
        <span className="text-muted-foreground/80">Fill = cluster · Shape + outline = type · Dashed = inferred or bridge</span>
        <span className="flex flex-wrap items-center gap-3">
          {legend.types.map((type) => {
            const theme = themeOf(type);
            return <span key={type} className="inline-flex items-center gap-1"><TypeGlyph type={type} size={13} className={theme.text} />{theme.label}</span>;
          })}
        </span>
      </div>
    </div>
  );
}
