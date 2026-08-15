import assert from "node:assert/strict";
import test from "node:test";

import { placeArchitectureEdgeLabels } from "../src/features/architecture/architectureLabelLayout.ts";

function overlaps(first: { x: number; y: number }, second: { x: number; y: number }, width = 100, height = 30) {
  return Math.abs(first.x - second.x) < width && Math.abs(first.y - second.y) < height;
}

test("edge-label repulsion separates labels with the same preferred position", () => {
  const positions = placeArchitectureEdgeLabels([
    { id: "first", x: 100, y: 100, preferredX: 100, preferredY: 100, width: 100, height: 30 },
    { id: "second", x: 100, y: 100, preferredX: 100, preferredY: 100, width: 100, height: 30 },
  ], []);

  assert.equal(overlaps(positions.first, positions.second), false);
});

test("edge-label repulsion pushes a label outside node obstacles", () => {
  const positions = placeArchitectureEdgeLabels([
    { id: "label", x: 100, y: 100, preferredX: 100, preferredY: 100, width: 80, height: 24 },
  ], [{ x: 50, y: 50, width: 100, height: 100 }]);
  const label = positions.label;
  const left = label.x - 40; const right = label.x + 40;
  const top = label.y - 12; const bottom = label.y + 12;

  assert.ok(right <= 42 || left >= 158 || bottom <= 42 || top >= 158);
});
