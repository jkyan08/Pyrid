import type { ForceEdge, ForceNode } from "@/lib/force-physics";

export interface GraphCanvasState {
  canvas: HTMLCanvasElement;
  context: CanvasRenderingContext2D;
  nodes: ForceNode[];
  edges: ForceEdge[];
  centerId: string;
  width: number;
  height: number;
  dpr: number;
  view: { x: number; y: number; k: number };
  hoveredNodeId: string | null;
  hoveredEdgeId: string | null;
  userMoved: boolean;
  frame: number | null;
}

export function edgeNodes(edge: ForceEdge): [ForceNode, ForceNode] {
  // d3-force replaces string endpoints with node references when the link
  // force is initialized. Rendering begins after that initialization.
  return [edge.source as ForceNode, edge.target as ForceNode];
}

export function canvasPoint(state: GraphCanvasState, clientX: number, clientY: number) {
  const rect = state.canvas.getBoundingClientRect();
  return { x: clientX - rect.left, y: clientY - rect.top };
}

export function worldPoint(state: GraphCanvasState, x: number, y: number) {
  return {
    x: (x - state.width / 2 - state.view.x) / state.view.k,
    y: (y - state.height / 2 - state.view.y) / state.view.k,
  };
}

function screenPoint(state: GraphCanvasState, node: ForceNode) {
  return {
    x: state.width / 2 + state.view.x + (node.x ?? 0) * state.view.k,
    y: state.height / 2 + state.view.y + (node.y ?? 0) * state.view.k,
  };
}

export function fitGraph(state: GraphCanvasState): void {
  if (!state.width || !state.height || state.nodes.length === 0) return;
  const minX = Math.min(...state.nodes.map((node) => (node.x ?? 0) - node.radius));
  const maxX = Math.max(...state.nodes.map((node) => (node.x ?? 0) + node.radius));
  const minY = Math.min(...state.nodes.map((node) => (node.y ?? 0) - node.radius));
  const maxY = Math.max(...state.nodes.map((node) => (node.y ?? 0) + node.radius));
  const roomX = Math.max(120, state.width - 120);
  const roomY = Math.max(120, state.height - 125);
  state.view.k = Math.max(0.32, Math.min(1.45, roomX / Math.max(1, maxX - minX), roomY / Math.max(1, maxY - minY)));
  state.view.x = -((minX + maxX) / 2) * state.view.k;
  state.view.y = -((minY + maxY) / 2) * state.view.k;
  requestGraphDraw(state);
}

export function zoomGraphAt(state: GraphCanvasState, factor: number, x: number, y: number): void {
  const before = worldPoint(state, x, y);
  state.view.k = Math.max(0.28, Math.min(3.2, state.view.k * factor));
  state.view.x = x - state.width / 2 - before.x * state.view.k;
  state.view.y = y - state.height / 2 - before.y * state.view.k;
  state.userMoved = true;
  requestGraphDraw(state);
}

export function hitNode(state: GraphCanvasState, x: number, y: number): ForceNode | null {
  let nearest: ForceNode | null = null;
  let nearestDistance = Infinity;
  for (const node of state.nodes) {
    const screen = screenPoint(state, node);
    const distance = Math.hypot(x - screen.x, y - screen.y);
    const hitRadius = Math.max(12, (node.radius + 5) * state.view.k);
    if (distance <= hitRadius && distance < nearestDistance) {
      nearest = node;
      nearestDistance = distance;
    }
  }
  return nearest;
}

export function hitEdge(state: GraphCanvasState, x: number, y: number): ForceEdge | null {
  let nearest: ForceEdge | null = null;
  let nearestDistance = 9;
  for (const edge of state.edges) {
    const [source, target] = edgeNodes(edge);
    const a = screenPoint(state, source);
    const b = screenPoint(state, target);
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSquared = dx * dx + dy * dy;
    if (lengthSquared === 0) continue;
    const t = Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / lengthSquared));
    const distance = Math.hypot(x - (a.x + t * dx), y - (a.y + t * dy));
    if (distance < nearestDistance) {
      nearest = edge;
      nearestDistance = distance;
    }
  }
  return nearest;
}

function traceNode(context: CanvasRenderingContext2D, node: ForceNode): void {
  const x = node.x ?? 0;
  const y = node.y ?? 0;
  const r = node.radius;
  context.beginPath();
  if (node.shape === "diamond" || node.shape === "rhomboid") {
    const skew = node.shape === "rhomboid" ? r * 0.45 : 0;
    context.moveTo(x + skew, y - r);
    context.lineTo(x + r, y);
    context.lineTo(x - skew, y + r);
    context.lineTo(x - r, y);
    context.closePath();
  } else if (node.shape === "hexagon" || node.shape === "round-pentagon" || node.shape === "tag") {
    const sides = node.shape === "hexagon" ? 6 : 5;
    for (let i = 0; i < sides; i++) {
      const angle = -Math.PI / 2 + (i * Math.PI * 2) / sides;
      const px = x + Math.cos(angle) * r;
      const py = y + Math.sin(angle) * r;
      if (i === 0) context.moveTo(px, py);
      else context.lineTo(px, py);
    }
    context.closePath();
  } else if (node.shape === "round-triangle") {
    context.moveTo(x, y - r);
    context.lineTo(x + r, y + r * 0.8);
    context.lineTo(x - r, y + r * 0.8);
    context.closePath();
  } else if (node.shape === "star") {
    for (let i = 0; i < 10; i++) {
      const angle = -Math.PI / 2 + (i * Math.PI) / 5;
      const radius = i % 2 === 0 ? r : r * 0.52;
      const px = x + Math.cos(angle) * radius;
      const py = y + Math.sin(angle) * radius;
      if (i === 0) context.moveTo(px, py);
      else context.lineTo(px, py);
    }
    context.closePath();
  } else if (["rectangle", "round-rectangle", "cut-rectangle", "barrel"].includes(node.shape)) {
    context.roundRect(x - r, y - r * 0.82, r * 2, r * 1.64, node.shape === "rectangle" ? 1 : r * 0.38);
  } else {
    context.arc(x, y, r, 0, Math.PI * 2);
  }
}

function drawGraph(state: GraphCanvasState): void {
  const { context: ctx, width, height, dpr } = state;
  if (!width || !height) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.translate(width / 2 + state.view.x, height / 2 + state.view.y);
  ctx.scale(state.view.k, state.view.k);

  for (const edge of state.edges) {
    const [source, target] = edgeNodes(edge);
    const emphasized = state.hoveredEdgeId === edge.id ||
      state.hoveredNodeId === source.id || state.hoveredNodeId === target.id;
    ctx.beginPath();
    ctx.moveTo(source.x ?? 0, source.y ?? 0);
    ctx.lineTo(target.x ?? 0, target.y ?? 0);
    ctx.strokeStyle = emphasized ? "#8edcff" : edge.muted ? "#8193a8" : "#8faac0";
    ctx.globalAlpha = emphasized ? 0.95 : edge.muted ? 0.28 : 0.42;
    ctx.lineWidth = emphasized ? 2.4 : 0.9 + edge.confidence * 0.75;
    ctx.setLineDash(edge.dashed ? [5, 5] : []);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;

  for (const node of state.nodes) {
    const focused = node.id === state.centerId;
    const hovered = node.id === state.hoveredNodeId;
    const adjacent = state.hoveredNodeId && state.edges.some((edge) => {
      const [source, target] = edgeNodes(edge);
      return (source.id === state.hoveredNodeId && target.id === node.id) ||
        (target.id === state.hoveredNodeId && source.id === node.id);
    });
    ctx.save();
    if (focused || hovered) {
      ctx.shadowColor = focused ? "#38bdf8" : node.typeColor;
      ctx.shadowBlur = focused ? 22 : 15;
    }
    traceNode(ctx, node);
    ctx.fillStyle = node.clusterColor;
    ctx.globalAlpha = focused || hovered || adjacent ? 1 : 0.86;
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = focused ? "#e0f2fe" : node.typeColor;
    ctx.lineWidth = focused ? 3.2 : hovered ? 3 : 2;
    ctx.stroke();
    if (focused) {
      ctx.beginPath();
      ctx.arc(node.x ?? 0, node.y ?? 0, node.radius + 8, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(125,211,252,0.45)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    ctx.restore();
  }

  // Labels use screen-space pixels so zooming out never turns the key labels
  // into unreadably small canvas text. Other labels appear on hover/zoom.
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  for (const node of state.nodes) {
    const focused = node.id === state.centerId;
    const hovered = node.id === state.hoveredNodeId;
    // detail nodes (variants/papers/investigators) never label from zoom or
    // degree — their serial-number names flood the canvas
    if (node.detail && !focused && !hovered) continue;
    if (!focused && !hovered && !(state.view.k >= 0.88 && node.degree >= 4) && state.view.k < 1.45) continue;
    const point = screenPoint(state, node);
    const label = node.label.length > 29 ? `${node.label.slice(0, 28)}…` : node.label;
    ctx.font = `${focused || hovered ? "600 12" : "500 11"}px Arial, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.lineWidth = 4;
    ctx.strokeStyle = "#152235";
    ctx.fillStyle = focused || hovered ? "#ffffff" : "#cbd5e1";
    const y = point.y + node.radius * state.view.k + 8;
    ctx.strokeText(label, point.x, y);
    ctx.fillText(label, point.x, y);
  }
}

export function requestGraphDraw(state: GraphCanvasState): void {
  if (state.frame !== null) return;
  state.frame = requestAnimationFrame(() => {
    state.frame = null;
    drawGraph(state);
  });
}
