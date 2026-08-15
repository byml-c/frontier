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

export type OrganizedArchitectureLayout = {
  positions: Record<string, XYPosition>;
  sizeOverrides: Record<string, ArchitectureSize>;
};

type LayoutEdge = Pick<ArchitectureEdge, "id" | "source" | "target">;

const GLOBAL_COLUMN_GAP = 148;
const GLOBAL_ROW_GAP = 48;
const GLOBAL_PADDING = 40;
const MODULE_PADDING = 32;

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

function estimateLabelWidth(value: string | undefined): number {
  if (!value) return 0;
  return Math.min(240, Math.max(48, Array.from(value).reduce((width, character) => width + (/\p{Script=Han}/u.test(character) ? 13 : 7), 0)));
}

function normalizedRanks(nodes: ArchitectureNode[], ranks: Map<string, number>): Map<string, number> {
  const values = [...new Set(nodes.map((node) => ranks.get(node.id) ?? 0))].sort((first, second) => first - second);
  const compact = new Map(values.map((value, index) => [value, index]));
  return new Map(nodes.map((node) => [node.id, compact.get(ranks.get(node.id) ?? 0) ?? 0]));
}

function layersForRanks(nodes: ArchitectureNode[], ranks: Map<string, number>): Map<number, ArchitectureNode[]> {
  const layers = new Map<number, ArchitectureNode[]>();
  nodes.forEach((node) => { const rank = ranks.get(node.id) ?? 0; layers.set(rank, [...(layers.get(rank) ?? []), node]); });
  layers.forEach((layer) => layer.sort((first, second) => first.position.y - second.position.y || first.position.x - second.position.x || first.id.localeCompare(second.id)));
  return layers;
}

function countLayerCrossings(layers: Map<number, ArchitectureNode[]>, ranks: Map<string, number>, edges: LayoutEdge[]): number {
  const order = new Map<string, number>();
  layers.forEach((layer) => layer.forEach((node, index) => order.set(node.id, index)));
  const segments = new Map<string, Array<{ source: number; target: number }>>();
  edges.forEach((edge) => {
    const sourceRank = ranks.get(edge.source) ?? 0; const targetRank = ranks.get(edge.target) ?? 0;
    if (sourceRank === targetRank) return;
    const forward = sourceRank < targetRank;
    const lowRank = Math.min(sourceRank, targetRank); const highRank = Math.max(sourceRank, targetRank);
    const sourceOrder = order.get(forward ? edge.source : edge.target); const targetOrder = order.get(forward ? edge.target : edge.source);
    if (sourceOrder === undefined || targetOrder === undefined) return;
    const key = `${lowRank}:${highRank}`;
    segments.set(key, [...(segments.get(key) ?? []), { source: sourceOrder, target: targetOrder }]);
  });
  let crossings = 0;
  segments.forEach((items) => items.forEach((first, index) => items.slice(index + 1).forEach((second) => { if ((first.source - second.source) * (first.target - second.target) < 0) crossings += 1; })));
  return crossings;
}

function rankDimensions(nodes: ArchitectureNode[], ranks: Map<string, number>, sizes: Map<string, ArchitectureSize>, labels: Record<string, string>, edges: LayoutEdge[]) {
  const layers = layersForRanks(nodes, ranks);
  const maximumRank = Math.max(0, ...layers.keys());
  const widths = Array.from({ length: maximumRank + 1 }, (_, rank) => Math.max(0, ...(layers.get(rank) ?? []).map((node) => sizes.get(node.id)?.width ?? architectureCardSize(node).width)));
  const heights = Array.from({ length: maximumRank + 1 }, (_, rank) => (layers.get(rank) ?? []).reduce((height, node) => height + (sizes.get(node.id)?.height ?? architectureCardSize(node).height), 0) + Math.max(0, (layers.get(rank)?.length ?? 0) - 1) * GLOBAL_ROW_GAP);
  const gaps = Array.from({ length: maximumRank }, () => GLOBAL_COLUMN_GAP);
  let edgeSpan = 0;
  edges.forEach((edge) => {
    const sourceRank = ranks.get(edge.source) ?? 0; const targetRank = ranks.get(edge.target) ?? 0;
    edgeSpan += Math.abs(targetRank - sourceRank);
    const low = Math.min(sourceRank, targetRank); const high = Math.max(sourceRank, targetRank);
    if (low === high) return;
    const required = Math.min(240, estimateLabelWidth(labels[edge.id]) + 72);
    for (let boundary = low; boundary < high; boundary += 1) gaps[boundary] = Math.max(gaps[boundary], required);
  });
  return { width: widths.reduce((sum, width) => sum + width, 0) + gaps.reduce((sum, gap) => sum + gap, 0), height: Math.max(0, ...heights), edgeSpan, layers };
}

export function architectureLayoutObjective({ crossings, width, height, edgeSpan = 0 }: { crossings: number; width: number; height: number; edgeSpan?: number }): number {
  const longestSide = Math.max(width, height);
  const areaScale = Math.sqrt(Math.max(1, width * height));
  const aspectPenalty = Math.abs(Math.log(Math.max(1, width) / Math.max(1, height))) * 90;
  return crossings * 100_000 + longestSide * 1.5 + areaScale + aspectPenalty + edgeSpan * 6;
}

function layeredLayout(nodes: ArchitectureNode[], edges: LayoutEdge[], sizes: Map<string, ArchitectureSize>, labels: Record<string, string>, padding: { left: number; top: number; right: number; bottom: number }) {
  if (!nodes.length) return { positions: {} as Record<string, XYPosition>, size: { width: padding.left + padding.right, height: padding.top + padding.bottom } };
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const scopedEdges = edges.filter((edge) => edge.source !== edge.target && byId.has(edge.source) && byId.has(edge.target));
  const incoming = new Map(nodes.map((node) => [node.id, [] as string[]]));
  const outgoing = new Map(nodes.map((node) => [node.id, [] as string[]]));
  const indegree = new Map(nodes.map((node) => [node.id, 0]));
  scopedEdges.forEach((edge) => {
    outgoing.get(edge.source)!.push(edge.target);
    incoming.get(edge.target)!.push(edge.source);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
  });

  const stableNodeOrder = (first: ArchitectureNode, second: ArchitectureNode) => first.position.x - second.position.x || first.position.y - second.position.y || first.id.localeCompare(second.id);
  const queue = nodes.filter((node) => indegree.get(node.id) === 0).sort(stableNodeOrder);
  const ranks = new Map(nodes.map((node) => [node.id, 0]));
  const processed = new Set<string>();
  const topologicalOrder: ArchitectureNode[] = [];
  while (queue.length) {
    const node = queue.shift()!;
    processed.add(node.id);
    topologicalOrder.push(node);
    (outgoing.get(node.id) ?? []).forEach((target) => {
      ranks.set(target, Math.max(ranks.get(target) ?? 0, (ranks.get(node.id) ?? 0) + 1));
      indegree.set(target, (indegree.get(target) ?? 1) - 1);
      if (indegree.get(target) === 0) queue.push(byId.get(target)!);
    });
    queue.sort(stableNodeOrder);
  }

  // Cycles have no complete topological order. Preserve their existing left-to-right
  // intent while still placing them in deterministic layers.
  const remaining = nodes.filter((node) => !processed.has(node.id));
  if (remaining.length) {
    const fallbackColumns = columnsFromExistingPositions(remaining);
    fallbackColumns.forEach((column, index) => column.nodes.forEach((node) => ranks.set(node.id, Math.max(ranks.get(node.id) ?? 0, index))));
  }

  if (!remaining.length && nodes.length > 2) {
    const earliest = normalizedRanks(nodes, ranks);
    const maximumCandidateRank = Math.max(0, ...earliest.values());
    const latest = new Map(nodes.map((node) => [node.id, maximumCandidateRank]));
    [...topologicalOrder].reverse().forEach((node) => {
      const successors = outgoing.get(node.id) ?? [];
      if (!successors.length) return;
      const latestAllowed = Math.min(...successors.map((target) => (latest.get(target) ?? maximumCandidateRank) - 1));
      latest.set(node.id, Math.max(earliest.get(node.id) ?? 0, latestAllowed));
    });
    const midpoint = new Map(nodes.map((node) => [node.id, Math.round(((earliest.get(node.id) ?? 0) + (latest.get(node.id) ?? maximumCandidateRank)) / 2)]));
    const balanced = new Map(midpoint);
    const isolated = nodes.filter((node) => !(incoming.get(node.id)?.length) && !(outgoing.get(node.id)?.length)).sort(stableNodeOrder);
    const layerLoads = Array.from({ length: maximumCandidateRank + 1 }, () => 0);
    nodes.filter((node) => !isolated.includes(node)).forEach((node) => { const rank = balanced.get(node.id) ?? 0; layerLoads[rank] += sizes.get(node.id)?.height ?? architectureCardSize(node).height; });
    isolated.forEach((node) => {
      const candidates = Array.from({ length: maximumCandidateRank + 1 }, (_, rank) => rank);
      const rank = candidates.reduce((best, candidate) => layerLoads[candidate] < layerLoads[best] ? candidate : best, 0);
      balanced.set(node.id, rank);
      layerLoads[rank] += (sizes.get(node.id)?.height ?? architectureCardSize(node).height) + GLOBAL_ROW_GAP;
    });
    const candidates = [earliest, normalizedRanks(nodes, latest), normalizedRanks(nodes, midpoint), normalizedRanks(nodes, balanced)];
    const chosen = candidates.reduce((best, candidate) => {
      const metrics = rankDimensions(nodes, candidate, sizes, labels, scopedEdges);
      const score = architectureLayoutObjective({ crossings: countLayerCrossings(metrics.layers, candidate, scopedEdges), width: metrics.width, height: metrics.height, edgeSpan: metrics.edgeSpan });
      if (!best || score < best.score) return { ranks: candidate, score };
      return best;
    }, null as { ranks: Map<string, number>; score: number } | null)!.ranks;
    ranks.clear(); chosen.forEach((rank, id) => ranks.set(id, rank));
  }

  const layers = new Map<number, ArchitectureNode[]>();
  nodes.forEach((node) => {
    const rank = ranks.get(node.id) ?? 0;
    layers.set(rank, [...(layers.get(rank) ?? []), node]);
  });
  const maximumRank = Math.max(...layers.keys());
  for (let rank = 0; rank <= maximumRank; rank += 1) (layers.get(rank) ?? []).sort((first, second) => first.position.y - second.position.y || stableNodeOrder(first, second));

  // Repeated barycenter sweeps are the crossing-reduction phase of a compact
  // Sugiyama-style layout. Stable tie-breakers keep the result deterministic.
  for (let pass = 0; pass < 4; pass += 1) {
    const sweep = (rank: number, neighbors: Map<string, string[]>) => {
      const layer = layers.get(rank);
      if (!layer?.length) return;
      const adjacentOrder = new Map<string, number>();
      layers.forEach((items) => items.forEach((node, index) => adjacentOrder.set(node.id, index)));
      layer.sort((first, second) => {
        const center = (node: ArchitectureNode) => {
          const values = (neighbors.get(node.id) ?? []).map((id) => adjacentOrder.get(id)).filter((value): value is number => value !== undefined);
          return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : adjacentOrder.get(node.id) ?? 0;
        };
        return center(first) - center(second) || first.position.y - second.position.y || first.id.localeCompare(second.id);
      });
    };
    for (let rank = 1; rank <= maximumRank; rank += 1) sweep(rank, incoming);
    for (let rank = maximumRank - 1; rank >= 0; rank -= 1) sweep(rank, outgoing);
  }

  if (nodes.length <= 120 && scopedEdges.length <= 300) {
    for (let pass = 0; pass < 2; pass += 1) {
      for (let rank = 0; rank <= maximumRank; rank += 1) {
        const layer = layers.get(rank) ?? [];
        for (let index = 0; index < layer.length - 1; index += 1) {
          const before = countLayerCrossings(layers, ranks, scopedEdges);
          [layer[index], layer[index + 1]] = [layer[index + 1], layer[index]];
          const after = countLayerCrossings(layers, ranks, scopedEdges);
          if (after >= before) [layer[index], layer[index + 1]] = [layer[index + 1], layer[index]];
        }
      }
    }
  }

  const columnWidths = Array.from({ length: maximumRank + 1 }, (_, rank) => Math.max(0, ...(layers.get(rank) ?? []).map((node) => sizes.get(node.id)?.width ?? architectureCardSize(node).width)));
  const columnGaps = Array.from({ length: maximumRank }, () => GLOBAL_COLUMN_GAP);
  scopedEdges.forEach((edge) => {
    const sourceRank = ranks.get(edge.source) ?? 0;
    const targetRank = ranks.get(edge.target) ?? 0;
    const low = Math.min(sourceRank, targetRank);
    const high = Math.max(sourceRank, targetRank);
    if (low === high) return;
    const required = Math.min(240, estimateLabelWidth(labels[edge.id]) + 72);
    for (let boundary = low; boundary < high; boundary += 1) columnGaps[boundary] = Math.max(columnGaps[boundary], required);
  });
  const columnHeights = Array.from({ length: maximumRank + 1 }, (_, rank) => {
    const layer = layers.get(rank) ?? [];
    return layer.reduce((height, node) => height + (sizes.get(node.id)?.height ?? architectureCardSize(node).height), 0) + Math.max(0, layer.length - 1) * GLOBAL_ROW_GAP;
  });
  const contentHeight = Math.max(...columnHeights, 0);
  const positions: Record<string, XYPosition> = {};
  let x = padding.left;
  for (let rank = 0; rank <= maximumRank; rank += 1) {
    const layer = layers.get(rank) ?? [];
    let y = padding.top + (contentHeight - columnHeights[rank]) / 2;
    layer.forEach((node) => {
      positions[node.id] = { x, y };
      y += (sizes.get(node.id)?.height ?? architectureCardSize(node).height) + GLOBAL_ROW_GAP;
    });
    x += columnWidths[rank] + (columnGaps[rank] ?? 0);
  }
  return {
    positions,
    size: {
      width: x - (columnGaps[maximumRank] ?? 0) + padding.right,
      height: padding.top + contentHeight + padding.bottom,
    },
  };
}

export function organizeArchitectureLayout(nodes: ArchitectureNode[], edges: ArchitectureEdge[], expandedNodeIds: Iterable<string>, edgeLabels: Record<string, string> = {}): OrganizedArchitectureLayout {
  const expanded = new Set(expandedNodeIds);
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const children = new Map<string | null, ArchitectureNode[]>();
  nodes.forEach((node) => {
    const parent = node.parentModuleId ?? null;
    children.set(parent, [...(children.get(parent) ?? []), node]);
  });
  const relativePositions: Record<string, XYPosition> = {};
  const sizeOverrides: Record<string, ArchitectureSize> = {};
  const sizes = new Map(nodes.map((node) => [node.id, architectureCardSize(node)]));

  const directBranch = (scopeId: string | null, endpoint: string): string | undefined => {
    let current = byId.get(endpoint);
    while (current) {
      if ((current.parentModuleId ?? null) === scopeId) return current.id;
      current = current.parentModuleId ? byId.get(current.parentModuleId) : undefined;
    }
    return undefined;
  };

  const arrangeScope = (scopeId: string): ArchitectureSize => {
    const directNodes = children.get(scopeId) ?? [];
    directNodes.forEach((node) => {
      if (node.type !== "module" || !expanded.has(node.id) || !(children.get(node.id)?.length)) return;
      const nestedSize = arrangeScope(node.id);
      sizes.set(node.id, nestedSize);
      sizeOverrides[node.id] = nestedSize;
    });
    const scopeEdges = edges.flatMap((edge) => {
      const source = directBranch(scopeId, edge.source);
      const target = directBranch(scopeId, edge.target);
      return source && target && source !== target ? [{ id: edge.id, source, target }] : [];
    });
    const parent = byId.get(scopeId);
    const headerHeight = parent ? architectureCardSize(parent).height + 36 : GLOBAL_PADDING;
    const layout = layeredLayout(directNodes, scopeEdges, sizes, edgeLabels, {
      left: parent ? MODULE_PADDING : GLOBAL_PADDING,
      top: headerHeight,
      right: parent ? MODULE_PADDING : GLOBAL_PADDING,
      bottom: parent ? MODULE_PADDING : GLOBAL_PADDING,
    });
    Object.assign(relativePositions, layout.positions);
    if (!parent) return layout.size;
    const card = architectureCardSize(parent);
    return { width: Math.max(card.width, layout.size.width), height: Math.max(card.height, layout.size.height) };
  };

  const roots = children.get(null) ?? [];
  roots.forEach((node) => {
    if (node.type !== "module" || !expanded.has(node.id) || !(children.get(node.id)?.length)) return;
    const nestedSize = arrangeScope(node.id);
    sizes.set(node.id, nestedSize);
    sizeOverrides[node.id] = nestedSize;
  });
  const rootEdges = edges.flatMap((edge) => {
    const source = directBranch(null, edge.source);
    const target = directBranch(null, edge.target);
    return source && target && source !== target ? [{ id: edge.id, source, target }] : [];
  });
  const groupedRoots = new Map<string, ArchitectureNode[]>();
  roots.forEach((node) => { if (node.groupId) groupedRoots.set(node.groupId, [...(groupedRoots.get(node.groupId) ?? []), node]); });
  const groupLayouts = new Map<string, ReturnType<typeof layeredLayout>>();
  const syntheticGroups: ArchitectureNode[] = [];
  groupedRoots.forEach((members, groupId) => {
    const memberIds = new Set(members.map((node) => node.id));
    const layout = layeredLayout(members, rootEdges.filter((edge) => memberIds.has(edge.source) && memberIds.has(edge.target)), sizes, edgeLabels, { left: 28, top: 48, right: 28, bottom: 28 });
    groupLayouts.set(groupId, layout);
    const minimumX = Math.min(...members.map((node) => node.position.x));
    const minimumY = Math.min(...members.map((node) => node.position.y));
    const synthetic: ArchitectureNode = { id: `group:${groupId}`, type: "module", title: groupId, position: { x: minimumX, y: minimumY } };
    syntheticGroups.push(synthetic);
    sizes.set(synthetic.id, layout.size);
  });
  const representative = (nodeId: string) => {
    const node = byId.get(nodeId);
    return node?.groupId && groupedRoots.has(node.groupId) ? `group:${node.groupId}` : nodeId;
  };
  const topNodes = [...syntheticGroups, ...roots.filter((node) => !node.groupId)];
  const topEdges = rootEdges.flatMap((edge) => {
    const source = representative(edge.source);
    const target = representative(edge.target);
    return source !== target ? [{ id: edge.id, source, target }] : [];
  });
  const topLayout = layeredLayout(topNodes, topEdges, sizes, edgeLabels, { left: GLOBAL_PADDING, top: GLOBAL_PADDING, right: GLOBAL_PADDING, bottom: GLOBAL_PADDING });
  roots.forEach((node) => {
    if (!node.groupId || !groupLayouts.has(node.groupId)) {
      relativePositions[node.id] = topLayout.positions[node.id] ?? node.position;
      return;
    }
    const groupPosition = topLayout.positions[`group:${node.groupId}`] ?? { x: 0, y: 0 };
    const memberPosition = groupLayouts.get(node.groupId)!.positions[node.id] ?? node.position;
    relativePositions[node.id] = { x: groupPosition.x + memberPosition.x, y: groupPosition.y + memberPosition.y };
  });
  const positions: Record<string, XYPosition> = {};
  const resolveAbsolute = (scopeId: string | null, origin: XYPosition) => {
    (children.get(scopeId) ?? []).forEach((node) => {
      const relative = relativePositions[node.id] ?? node.position;
      const absolute = { x: origin.x + relative.x, y: origin.y + relative.y };
      positions[node.id] = absolute;
      if (node.type === "module" && expanded.has(node.id)) resolveAbsolute(node.id, absolute);
    });
  };
  resolveAbsolute(null, { x: 0, y: 0 });
  return { positions, sizeOverrides };
}
