import { describe, expect, it } from "vitest";
import {
  beginNodeDrag,
  connectedWithin,
  createForcePhysics,
  endNodeDrag,
  moveNodeDrag,
} from "../force-physics";
import type { ForceEdge, ForceNode } from "../force-physics";

function node(id: string, x: number): ForceNode {
  return {
    id, x, y: 0, label: id, type: "Gene", clusterColor: "#888",
    typeColor: "#fff", shape: "ellipse", radius: 10, degree: 1,
  };
}

function edge(id: string, source: string, target: string): ForceEdge {
  return {
    id, source, target, relation: "associated_with", sourceCount: 1,
    status: "supported", dashed: false, muted: false, confidence: 0.8,
  };
}

describe("persistent force drag", () => {
  it("pins only the grabbed node, moves its linked neighbor, then releases without rebuilding", () => {
    const nodes = [node("a", 0), node("b", 120), node("c", 240), node("far", 700)];
    const edges = [edge("ab", "a", "b"), edge("bc", "b", "c")];
    const physics = createForcePhysics(nodes, edges, 230);
    const simulation = physics.simulation;
    simulation.tick(50);
    nodes[1].vx = 3;
    nodes[1].vy = -1;
    const beforeNeighbor = nodes[1].x!;

    expect(connectedWithin(edges, "a", 2)).toEqual(new Set(["a", "b", "c"]));
    beginNodeDrag(physics, nodes[0], edges);
    expect(physics.simulation).toBe(simulation);
    expect(nodes[0].fx).toBe(nodes[0].x);
    expect(nodes[1].vx).toBe(3);
    expect(nodes[1].vy).toBe(-1);
    expect(physics.locality.active?.has("far")).toBe(false);

    moveNodeDrag(nodes[0], nodes[0].x! + 100, 40);
    simulation.stop().tick(6);
    expect(nodes[0].x).toBeCloseTo(nodes[0].fx!);
    expect(nodes[1].x).not.toBeCloseTo(beforeNeighbor);

    endNodeDrag(physics, nodes[0]);
    simulation.stop();
    expect(nodes[0].fx).toBeNull();
    expect(nodes[0].fy).toBeNull();
    expect(physics.locality.active).toBeNull();
    expect(simulation.alphaTarget()).toBe(0);
  });
});
