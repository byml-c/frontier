import type { ArchitectureEdge } from "./types";

export type ResolvedArchitectureEdge = {
  edge: ArchitectureEdge;
  source: string;
  target: string;
};

export type ArchitectureEdgeBundle = {
  id: string;
  source: string;
  target: string;
  kind: "flow" | "conditioning";
  edges: ArchitectureEdge[];
};

export type ArchitectureEdgeAnchors = {
  source: "left" | "right" | "top" | "bottom";
  target: "left" | "right" | "top" | "bottom";
};

/** Collapse duplicate semantic edges so processing labels and Tensor metadata share one route. */
export function bundleArchitectureEdges(items: ResolvedArchitectureEdge[]): ArchitectureEdgeBundle[] {
  const bundles = new Map<string, ArchitectureEdgeBundle>();
  items.forEach(({ edge, source, target }) => {
    const kind = edge.kind ?? "flow";
    const key = JSON.stringify([source, target, kind]);
    const existing = bundles.get(key);
    if (existing) {
      existing.edges.push(edge);
      return;
    }
    bundles.set(key, { id: `bundle:${kind}:${edge.id}`, source, target, kind, edges: [edge] });
  });
  return [...bundles.values()];
}

/** Give the remaining distinct relations between a node pair separate physical curves. */
export function parallelArchitectureEdgeOffsets(bundles: ArchitectureEdgeBundle[], gap = 44): Map<string, number> {
  const pairs = new Map<string, ArchitectureEdgeBundle[]>();
  bundles.forEach((bundle) => {
    const key = [bundle.source, bundle.target].sort().join("\u0000");
    pairs.set(key, [...(pairs.get(key) ?? []), bundle]);
  });
  const offsets = new Map<string, number>();
  pairs.forEach((pair) => {
    pair.sort((first, second) => first.kind.localeCompare(second.kind) || first.id.localeCompare(second.id));
    pair.forEach((bundle, index) => {
      const canonicalOffset = (index - (pair.length - 1) / 2) * gap;
      const direction = bundle.source <= bundle.target ? 1 : -1;
      offsets.set(bundle.id, canonicalOffset * direction);
    });
  });
  return offsets;
}

/** Route reciprocal edges through opposite node sides instead of sharing the same handles. */
export function reciprocalArchitectureEdgeAnchors(sourceCenter: { x: number; y: number }, targetCenter: { x: number; y: number }): ArchitectureEdgeAnchors {
  const dx = targetCenter.x - sourceCenter.x;
  const dy = targetCenter.y - sourceCenter.y;
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? { source: "top", target: "top" } : { source: "bottom", target: "bottom" };
  return dy >= 0 ? { source: "left", target: "left" } : { source: "right", target: "right" };
}
