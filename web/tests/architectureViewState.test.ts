import assert from "node:assert/strict";
import test from "node:test";
import { parseArchitectureViewState } from "../src/features/architecture/viewState.ts";

test("architecture view keeps reading state while layout epoch clears position overrides", () => {
  const raw = JSON.stringify({
    version: 1,
    schemaVersion: 1,
    layoutEpoch: 2,
    viewport: { x: 40, y: 50, zoom: 1.2 },
    expandedNodeIds: ["encoder"],
    positionOverrides: { encoder: { x: 200, y: 300 } },
    sizeOverrides: { encoder: { width: 640, height: 420 } },
    pinnedAnnotationIds: ["note-1"],
    pinnedAnnotationPositions: { "note-1": { x: 10, y: 20 } },
  });

  const compatible = parseArchitectureViewState(raw, 2);
  assert.deepEqual(compatible.positionOverrides.encoder, { x: 200, y: 300 });
  assert.deepEqual(compatible.sizeOverrides.encoder, { width: 640, height: 420 });
  assert.deepEqual(compatible.expandedNodeIds, ["encoder"]);

  const relayout = parseArchitectureViewState(raw, 3);
  assert.deepEqual(relayout.positionOverrides, {});
  assert.deepEqual(relayout.sizeOverrides, {});
  assert.deepEqual(relayout.expandedNodeIds, ["encoder"]);
  assert.deepEqual(relayout.pinnedAnnotationIds, ["note-1"]);
});

test("architecture view rejects corrupt coordinates and incompatible schema", () => {
  const corrupt = parseArchitectureViewState(JSON.stringify({
    version: 1,
    schemaVersion: 1,
    layoutEpoch: 1,
    viewport: { x: "bad", y: 0, zoom: 1 },
    positionOverrides: { good: { x: 1, y: 2 }, bad: { x: null, y: 4 } },
    sizeOverrides: { good: { width: 200, height: 100 }, bad: { width: 0, height: 4 } },
  }), 1);
  assert.deepEqual(corrupt.positionOverrides, { good: { x: 1, y: 2 } });
  assert.deepEqual(corrupt.sizeOverrides, { good: { width: 200, height: 100 } });
  assert.deepEqual(corrupt.viewport, { x: 0, y: 0, zoom: 1 });

  const incompatible = parseArchitectureViewState(JSON.stringify({ version: 1, schemaVersion: 2 }), 1);
  assert.deepEqual(incompatible.expandedNodeIds, []);
});
