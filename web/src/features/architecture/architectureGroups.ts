import type { ArchitectureGroup, ArchitectureNode, ArchitectureSize, XYPosition } from "./types";

const GROUP_SIDE_PADDING = 28;
const GROUP_TOP_PADDING = 48;
const GROUP_BOTTOM_PADDING = 28;

export function architectureGroupBounds(group: ArchitectureGroup, nodes: ArchitectureNode[], visible: Set<string>, positions: Record<string, XYPosition>, sizes: Record<string, ArchitectureSize>): { position: XYPosition; size: ArchitectureSize } {
  const members = nodes.filter((node) => node.groupId === group.id && visible.has(node.id) && positions[node.id] && sizes[node.id]);
  if (!members.length) return { position: group.position, size: group.size };
  const left = Math.min(...members.map((node) => positions[node.id].x));
  const top = Math.min(...members.map((node) => positions[node.id].y));
  const right = Math.max(...members.map((node) => positions[node.id].x + sizes[node.id].width));
  const bottom = Math.max(...members.map((node) => positions[node.id].y + sizes[node.id].height));
  return {
    position: { x: left - GROUP_SIDE_PADDING, y: top - GROUP_TOP_PADDING },
    size: { width: right - left + GROUP_SIDE_PADDING * 2, height: bottom - top + GROUP_TOP_PADDING + GROUP_BOTTOM_PADDING },
  };
}
