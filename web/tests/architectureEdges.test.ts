import assert from "node:assert/strict";
import test from "node:test";

import { bundleArchitectureEdges, parallelArchitectureEdgeOffsets, reciprocalArchitectureEdgeAnchors } from "../src/features/architecture/architectureEdges.ts";

test("same-kind edges between the same nodes share one rendered route", () => {
  const bundles = bundleArchitectureEdges([
    { edge: { id: "process", source: "a", target: "b", label: "投影" }, source: "a", target: "b" },
    { edge: { id: "tensor", source: "a", target: "b", tensorId: "hidden" }, source: "a", target: "b" },
  ]);

  assert.equal(bundles.length, 1);
  assert.deepEqual(bundles[0].edges.map((edge) => edge.id), ["process", "tensor"]);
});

test("flow and conditioning relations remain separate and receive distinct curves", () => {
  const bundles = bundleArchitectureEdges([
    { edge: { id: "flow", source: "a", target: "b" }, source: "a", target: "b" },
    { edge: { id: "condition", source: "a", target: "b", kind: "conditioning" }, source: "a", target: "b" },
  ]);
  const offsets = parallelArchitectureEdgeOffsets(bundles);

  assert.equal(bundles.length, 2);
  assert.notEqual(offsets.get(bundles[0].id), offsets.get(bundles[1].id));
  assert.ok([...offsets.values()].every((offset) => offset !== 0));
});

test("reciprocal edges use opposite handles around horizontally arranged nodes", () => {
  assert.deepEqual(reciprocalArchitectureEdgeAnchors({ x: 0, y: 0 }, { x: 300, y: 20 }), { source: "top", target: "top" });
  assert.deepEqual(reciprocalArchitectureEdgeAnchors({ x: 300, y: 20 }, { x: 0, y: 0 }), { source: "bottom", target: "bottom" });
});

test("reciprocal edges use opposite handles around vertically arranged nodes", () => {
  assert.deepEqual(reciprocalArchitectureEdgeAnchors({ x: 0, y: 0 }, { x: 20, y: 300 }), { source: "left", target: "left" });
  assert.deepEqual(reciprocalArchitectureEdgeAnchors({ x: 20, y: 300 }, { x: 0, y: 0 }), { source: "right", target: "right" });
});
