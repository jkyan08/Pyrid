import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
} from "d3-force";
import type {
  ForceManyBody,
  Simulation,
  SimulationLinkDatum,
  SimulationNodeDatum,
} from "d3-force";

export interface ForceNode extends SimulationNodeDatum {
  id: string;
  label: string;
  type: string;
  clusterColor: string;
  typeColor: string;
  shape: string;
  radius: number;
  degree: number;
  /** Detail nodes (variants, papers, investigators) only label on hover/focus
   *  — their canonical names are serial numbers that flood the canvas. */
  detail?: boolean;
}

export interface ForceEdge extends SimulationLinkDatum<ForceNode> {
  id: string;
  source: string | ForceNode;
  target: string | ForceNode;
  relation: string;
  sourceCount: number;
  status: string;
  dashed: boolean;
  muted: boolean;
  confidence: number;
}

export interface ForcePhysics {
  simulation: Simulation<ForceNode, ForceEdge>;
  charge: ForceManyBody<ForceNode>;
  locality: { active: Set<string> | null };
}

export function endpointId(endpoint: string | ForceNode): string {
  return typeof endpoint === "string" ? endpoint : endpoint.id;
}

export function connectedWithin(links: ForceEdge[], startId: string, hops: number): Set<string> {
  const adjacent = new Map<string, Set<string>>();
  for (const link of links) {
    const source = endpointId(link.source);
    const target = endpointId(link.target);
    if (!adjacent.has(source)) adjacent.set(source, new Set());
    if (!adjacent.has(target)) adjacent.set(target, new Set());
    adjacent.get(source)!.add(target);
    adjacent.get(target)!.add(source);
  }
  const reached = new Set([startId]);
  let frontier = [startId];
  for (let step = 0; step < hops; step++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const other of adjacent.get(id) ?? []) {
        if (reached.has(other)) continue;
        reached.add(other);
        next.push(other);
      }
    }
    frontier = next;
  }
  return reached;
}

/** One persistent simulation. D3 mutates node positions and link endpoints. */
export function createForcePhysics(nodes: ForceNode[], links: ForceEdge[], repulsion: number): ForcePhysics {
  const locality: ForcePhysics["locality"] = { active: null };
  const charge = forceManyBody<ForceNode>().strength(-repulsion).distanceMax(750);
  const springs = forceLink<ForceNode, ForceEdge>(links)
    .id((node) => node.id)
    .distance((link) => link.dashed ? 150 : 120)
    .strength((link) => link.muted ? 0.12 : 0.24 + 0.12 * link.confidence);

  const simulation = forceSimulation<ForceNode, ForceEdge>(nodes)
    .force("links", springs)
    .force("charge", charge)
    .force("center", forceCenter<ForceNode>(0, 0).strength(0.035))
    .force("collision", forceCollide<ForceNode>().radius((node) => node.radius + 17).strength(0.9))
    // While a node is dragged, distant nodes keep most of their current
    // configuration. Immediate and two-hop neighbors retain full response.
    .force("drag-locality", () => {
      if (!locality.active) return;
      for (const node of nodes) {
        if (!locality.active.has(node.id)) {
          node.vx = (node.vx ?? 0) * 0.68;
          node.vy = (node.vy ?? 0) * 0.68;
        }
      }
    })
    .alphaDecay(0.045)
    .velocityDecay(0.52)
    .stop();

  return { simulation, charge, locality };
}

export function beginNodeDrag(physics: ForcePhysics, node: ForceNode, links: ForceEdge[]): void {
  node.fx = node.x;
  node.fy = node.y;
  physics.locality.active = connectedWithin(links, node.id, 2);
  // Reheat gently; positions, velocity and the simulation instance survive.
  physics.simulation.alpha(Math.max(physics.simulation.alpha(), 0.12)).alphaTarget(0.08).restart();
}

export function moveNodeDrag(node: ForceNode, x: number, y: number): void {
  node.fx = x;
  node.fy = y;
}

export function endNodeDrag(physics: ForcePhysics, node: ForceNode): void {
  node.fx = null;
  node.fy = null;
  physics.locality.active = null;
  physics.simulation.alphaTarget(0);
}

export function updateRepulsion(physics: ForcePhysics, strength: number): void {
  physics.charge.strength(-strength);
  physics.simulation.alpha(Math.max(physics.simulation.alpha(), 0.16)).restart();
}
