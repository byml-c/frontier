import { architectureCardSize } from "./architectureCardSize.ts";
import type { ArchitectureEdge, ArchitectureNode, ArchitectureSize, XYPosition } from "./types";

const COLUMN_TOLERANCE = 96;
const HORIZONTAL_GAP = 180;
const VERTICAL_GAP = 40;
const SIDE_PADDING = 28;
const BOTTOM_PADDING = 28;

export type OrganizedModuleLayout = {
  positions: Record<string, XYPosition>;
  size: ArchitectureSize;
};

function columnsFromExistingPositions(children: ArchitectureNode[]) {
  const ordered = [...children].sort((first, second) => first.position.x - second.position.x || first.position.y - second.position.y || first.id.localeCompare(second.id));
  const columns: Array<{ anchor: number; nodes: ArchitectureNode[] }> = [];
  ordered.forEach((node) => {
    const column = columns.find((candidate) => Math.abs(candidate.anchor - node.position.x) <= COLUMN_TOLERANCE);
    if (column) column.nodes.push(node);
    else columns.push({ anchor: node.position.x, nodes: [node] });
  });
  columns.sort((first, second) => first.anchor - second.anchor);
  return columns;
}

function columnsFromDataflow(children: ArchitectureNode[], edges: ArchitectureEdge[]) {
  const byId = new Map(children.map((node) => [node.id, node]));
  const scopedEdges = edges.filter((edge) => byId.has(edge.source) && byId.has(edge.target));
  if (!scopedEdges.length) return columnsFromExistingPositions(children);

  const outgoing = new Map(children.map((node) => [node.id, [] as string[]]));
  const indegree = new Map(children.map((node) => [node.id, 0]));
  const connected = new Set<string>();
  scopedEdges.forEach((edge) => {
    outgoing.get(edge.source)?.push(edge.target);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
    connected.add(edge.source);
    connected.add(edge.target);
  });

  const rank = new Map<string, number>();
  const candidateRank = new Map(children.map((node) => [node.id, 0]));
  const queue = children.filter((node) => connected.has(node.id) && indegree.get(node.id) === 0)
    .sort((first, second) => first.position.x - second.position.x || first.position.y - second.position.y);
  while (queue.length) {
    const node = queue.shift()!;
    const currentRank = candidateRank.get(node.id) ?? 0;
    rank.set(node.id, currentRank);
    (outgoing.get(node.id) ?? []).forEach((target) => {
      candidateRank.set(target, Math.max(candidateRank.get(target) ?? 0, currentRank + 1));
      const remaining = (indegree.get(target) ?? 1) - 1;
      indegree.set(target, remaining);
      if (remaining === 0) queue.push(byId.get(target)!);
    });
    queue.sort((first, second) => first.position.x - second.position.x || first.position.y - second.position.y);
  }

  const maximumRank = Math.max(0, ...rank.values());
  const minimumX = Math.min(...children.map((node) => node.position.x));
  const maximumX = Math.max(...children.map((node) => node.position.x));
  const fallbackRank = (node: ArchitectureNode) => maximumRank > 0 && maximumX > minimumX
    ? Math.round(((node.position.x - minimumX) / (maximumX - minimumX)) * maximumRank)
    : 0;
  children.forEach((node) => { if (!rank.has(node.id)) rank.set(node.id, fallbackRank(node)); });

  const byRank = new Map<number, ArchitectureNode[]>();
  children.forEach((node) => byRank.set(rank.get(node.id) ?? 0, [...(byRank.get(rank.get(node.id) ?? 0) ?? []), node]));
  return [...byRank.entries()].sort(([first], [second]) => first - second).map(([anchor, nodes]) => ({ anchor, nodes }));
}

export function organizeModuleLayout(parent: ArchitectureNode, children: ArchitectureNode[], edges: ArchitectureEdge[]): OrganizedModuleLayout {
  const columns = columnsFromDataflow(children, edges);

  const connected = new Set(edges.flatMap((edge) => [edge.source, edge.target]));
  columns.forEach((column) => column.nodes.sort((first, second) => {
    const connectivity = Number(connected.has(second.id)) - Number(connected.has(first.id));
    return connectivity || first.position.y - second.position.y || first.id.localeCompare(second.id);
  }));

  const positions: Record<string, XYPosition> = {};
  const headerHeight = architectureCardSize(parent).height;
  const top = headerHeight + 28;
  let x = SIDE_PADDING;
  let maximumBottom = top;
  columns.forEach((column) => {
    const sizes = column.nodes.map(architectureCardSize);
    const columnWidth = Math.max(...sizes.map((size) => size.width), 0);
    let y = top;
    column.nodes.forEach((node, index) => {
      positions[node.id] = { x, y };
      y += sizes[index].height + VERTICAL_GAP;
    });
    maximumBottom = Math.max(maximumBottom, y - VERTICAL_GAP);
    x += columnWidth + HORIZONTAL_GAP;
  });

  return {
    positions,
    size: {
      width: Math.max(architectureCardSize(parent).width, x - HORIZONTAL_GAP + SIDE_PADDING),
      height: Math.max(headerHeight, maximumBottom + BOTTOM_PADDING),
    },
  };
}
