import type { ArchitectureEdge, ArchitectureNode } from "./types";

export function architectureFocusNeighborhood(nodes: ArchitectureNode[], edges: ArchitectureEdge[], visible: Set<string>, focusedNodeId: string | null): Set<string> | null {
  if (!focusedNodeId || !visible.has(focusedNodeId)) return null;
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const focus = new Set<string>();
  const isInside = (candidateId: string, ancestorId: string) => {
    let candidate = byId.get(candidateId);
    while (candidate) {
      if (candidate.id === ancestorId) return true;
      candidate = candidate.parentModuleId ? byId.get(candidate.parentModuleId) : undefined;
    }
    return false;
  };
  const nearestVisible = (id: string) => {
    let current = byId.get(id);
    while (current) {
      if (visible.has(current.id)) return current.id;
      current = current.parentModuleId ? byId.get(current.parentModuleId) : undefined;
    }
    return undefined;
  };
  const addWithAncestors = (id: string | undefined) => {
    let current = id ? byId.get(id) : undefined;
    while (current) {
      if (visible.has(current.id)) focus.add(current.id);
      current = current.parentModuleId ? byId.get(current.parentModuleId) : undefined;
    }
  };

  visible.forEach((id) => { if (isInside(id, focusedNodeId)) focus.add(id); });
  addWithAncestors(focusedNodeId);
  edges.forEach((edge) => {
    const sourceInside = isInside(edge.source, focusedNodeId);
    const targetInside = isInside(edge.target, focusedNodeId);
    if (sourceInside) addWithAncestors(nearestVisible(edge.target));
    if (targetInside) addWithAncestors(nearestVisible(edge.source));
  });
  return focus;
}
