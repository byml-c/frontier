import type { Viewport } from "@xyflow/react";
import type { ArchitectureSize, XYPosition } from "./types";

export type ArchitectureViewState = {
  version: 1;
  schemaVersion: 1;
  layoutEpoch: number;
  viewport: Viewport;
  expandedNodeIds: string[];
  positionOverrides: Record<string, XYPosition>;
  sizeOverrides: Record<string, ArchitectureSize>;
  pinnedAnnotationIds: string[];
  pinnedAnnotationPositions: Record<string, XYPosition>;
};

const initial = (layoutEpoch: number): ArchitectureViewState => ({
  version: 1,
  schemaVersion: 1,
  layoutEpoch,
  viewport: { x: 0, y: 0, zoom: 1 },
  expandedNodeIds: [],
  positionOverrides: {},
  sizeOverrides: {},
  pinnedAnnotationIds: [],
  pinnedAnnotationPositions: {},
});

const point = (value: unknown): XYPosition | undefined => {
  if (!value || typeof value !== "object") return undefined;
  const candidate = value as Partial<XYPosition>;
  return Number.isFinite(candidate.x) && Number.isFinite(candidate.y)
    ? { x: Number(candidate.x), y: Number(candidate.y) }
    : undefined;
};

const size = (value: unknown): ArchitectureSize | undefined => {
  if (!value || typeof value !== "object") return undefined;
  const candidate = value as Partial<ArchitectureSize>;
  return Number.isFinite(candidate.width) && Number.isFinite(candidate.height) && Number(candidate.width) > 0 && Number(candidate.height) > 0
    ? { width: Math.min(5000, Number(candidate.width)), height: Math.min(5000, Number(candidate.height)) }
    : undefined;
};

export function parseArchitectureViewState(raw: string | null, layoutEpoch: number): ArchitectureViewState {
  try {
    if (!raw) return initial(layoutEpoch);
    const candidate = JSON.parse(raw) as Partial<ArchitectureViewState>;
    if (candidate.version !== 1 || candidate.schemaVersion !== 1) return initial(layoutEpoch);
    const viewport = candidate.viewport;
    const positions = Object.fromEntries(
      Object.entries(candidate.positionOverrides ?? {}).flatMap(([id, value]) => {
        const position = point(value);
        return id && position ? [[id, position]] : [];
      }).slice(-1000),
    );
    return {
      ...initial(layoutEpoch),
      viewport: viewport && Number.isFinite(viewport.x) && Number.isFinite(viewport.y) && Number.isFinite(viewport.zoom)
        ? { x: viewport.x, y: viewport.y, zoom: Math.max(.1, Math.min(4, viewport.zoom)) }
        : initial(layoutEpoch).viewport,
      expandedNodeIds: Array.isArray(candidate.expandedNodeIds) ? candidate.expandedNodeIds.filter((id): id is string => typeof id === "string") : [],
      positionOverrides: candidate.layoutEpoch === layoutEpoch ? positions : {},
      sizeOverrides: candidate.layoutEpoch === layoutEpoch ? Object.fromEntries(Object.entries(candidate.sizeOverrides ?? {}).flatMap(([id, value]) => {
        const dimensions = size(value);
        return id && dimensions ? [[id, dimensions]] : [];
      }).slice(-1000)) : {},
      pinnedAnnotationIds: Array.isArray(candidate.pinnedAnnotationIds) ? candidate.pinnedAnnotationIds.filter((id): id is string => typeof id === "string") : [],
      pinnedAnnotationPositions: Object.fromEntries(Object.entries(candidate.pinnedAnnotationPositions ?? {}).flatMap(([id, value]) => {
        const position = point(value);
        return id && position ? [[id, position]] : [];
      })),
    };
  } catch {
    return initial(layoutEpoch);
  }
}

export function readArchitectureViewState(paperId: string, layoutEpoch: number): ArchitectureViewState {
  if (typeof window === "undefined") return initial(layoutEpoch);
  return parseArchitectureViewState(
    window.localStorage.getItem(`frontier.architecture.${paperId}.view.v1`),
    layoutEpoch,
  );
}

export function writeArchitectureViewState(paperId: string, state: ArchitectureViewState): void {
  try {
    window.localStorage.setItem(`frontier.architecture.${paperId}.view.v1`, JSON.stringify(state));
  } catch { /* localStorage may be unavailable. */ }
}
