import { architectureCardSize } from "./architectureCardSize.ts";
import type { ArchitectureNode, ArchitectureSize, XYPosition } from "./types";

const SIDE_PADDING = 24;
const CONTENT_TOP_GAP = 28;
const BOTTOM_PADDING = 24;

export function fitExpandedArchitectureBounds(nodes: ArchitectureNode[], expandedNodeIds: Iterable<string>, inputPositions: Record<string, XYPosition>): { positions: Record<string, XYPosition>; sizes: Record<string, ArchitectureSize> } {
  const expanded = new Set(expandedNodeIds);
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const children = new Map<string, ArchitectureNode[]>();
  nodes.forEach((node) => { if (node.parentModuleId) children.set(node.parentModuleId, [...(children.get(node.parentModuleId) ?? []), node]); });
  const positions = Object.fromEntries(nodes.map((node) => [node.id, inputPositions[node.id] ?? node.position]));
  const sizes = Object.fromEntries(nodes.map((node) => [node.id, architectureCardSize(node)]));
  const depth = (node: ArchitectureNode): number => node.parentModuleId && byId.has(node.parentModuleId) ? depth(byId.get(node.parentModuleId)!) + 1 : 0;

  [...nodes].sort((first, second) => depth(second) - depth(first)).forEach((node) => {
    const members = children.get(node.id) ?? [];
    if (node.type !== "module" || !expanded.has(node.id) || !members.length) return;
    const base = architectureCardSize(node);
    const current = positions[node.id];
    const memberLeft = Math.min(...members.map((member) => positions[member.id].x));
    const memberTop = Math.min(...members.map((member) => positions[member.id].y));
    const memberRight = Math.max(...members.map((member) => positions[member.id].x + sizes[member.id].width));
    const memberBottom = Math.max(...members.map((member) => positions[member.id].y + sizes[member.id].height));
    const left = Math.min(current.x, memberLeft - SIDE_PADDING);
    const top = Math.min(current.y, memberTop - base.height - CONTENT_TOP_GAP);
    positions[node.id] = { x: left, y: top };
    sizes[node.id] = {
      width: Math.max(base.width, memberRight + SIDE_PADDING - left),
      height: Math.max(base.height, memberBottom + BOTTOM_PADDING - top),
    };
  });

  return { positions, sizes };
}
