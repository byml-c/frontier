export const GRAPH_VIEWPORT_STORAGE_KEY = "frontier.graph.viewport.v2";
const LEGACY_GRAPH_VIEWPORT_STORAGE_KEY = "frontier.graph.viewport.v1";

export type GraphPoint = { x: number; y: number };
export type GraphViewportState = {
  version: 2;
  center: GraphPoint;
  nodes: Record<string, GraphPoint>;
};

const DEFAULT_VIEWPORT: GraphViewportState = { version: 2, center: { x: 0, y: 0 }, nodes: {} };
const MAX_STORED_NODES = 500;

function point(value: unknown): GraphPoint | undefined {
  if (!value || typeof value !== "object") return undefined;
  const candidate = value as Partial<GraphPoint>;
  if (!Number.isFinite(candidate.x) || !Number.isFinite(candidate.y)) return undefined;
  return { x: Number(candidate.x), y: Number(candidate.y) };
}

export function parseGraphViewportState(raw: string | null): GraphViewportState {
  if (!raw) return DEFAULT_VIEWPORT;
  try {
    const candidate = JSON.parse(raw) as { version?: number; center?: unknown; nodes?: Record<string, unknown> };
    if (candidate.version !== 1 && candidate.version !== 2) return DEFAULT_VIEWPORT;
    const nodes: Record<string, GraphPoint> = {};
    Object.entries(candidate.nodes ?? {}).slice(-MAX_STORED_NODES).forEach(([id, value]) => {
      const position = point(value);
      if (id && position) nodes[id] = position;
    });
    return {
      version: 2,
      center: point(candidate.center) ?? DEFAULT_VIEWPORT.center,
      nodes,
    };
  } catch {
    return DEFAULT_VIEWPORT;
  }
}

export function mergeGraphViewportState(current: GraphViewportState, center: GraphPoint, positions: Record<string, GraphPoint>): GraphViewportState {
  const entries = Object.entries({ ...current.nodes, ...positions }).slice(-MAX_STORED_NODES);
  return { version: 2, center, nodes: Object.fromEntries(entries) };
}

export function readGraphViewportState(): GraphViewportState {
  if (typeof window === "undefined") return DEFAULT_VIEWPORT;
  try {
    return parseGraphViewportState(window.localStorage.getItem(GRAPH_VIEWPORT_STORAGE_KEY) ?? window.localStorage.getItem(LEGACY_GRAPH_VIEWPORT_STORAGE_KEY));
  } catch { return DEFAULT_VIEWPORT; }
}

export function writeGraphViewportState(state: GraphViewportState): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(GRAPH_VIEWPORT_STORAGE_KEY, JSON.stringify(state));
    window.localStorage.removeItem(LEGACY_GRAPH_VIEWPORT_STORAGE_KEY);
  } catch { /* localStorage may be unavailable in private mode. */ }
}
