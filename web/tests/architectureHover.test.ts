import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { architectureFocusNeighborhood } from "../src/features/architecture/architectureFocus.ts";
import type { ArchitectureNode } from "../src/features/architecture/types.ts";

function node(id: string, parentModuleId?: string): ArchitectureNode {
  return { id, type: "module", title: id, position: { x: 0, y: 0 }, parentModuleId };
}

test("node focus keeps only the node and its one-hop predecessors and successors", () => {
  const nodes = [node("a"), node("b"), node("c"), node("d"), node("unrelated")];
  const focus = architectureFocusNeighborhood(nodes, [
    { id: "a-b", source: "a", target: "b" },
    { id: "b-c", source: "b", target: "c" },
    { id: "c-d", source: "c", target: "d" },
  ], new Set(nodes.map((item) => item.id)), "b");

  assert.deepEqual([...focus!].sort(), ["a", "b", "c"]);
});

test("focusing an expanded module keeps its visible subtree and external neighbor", () => {
  const nodes = [node("source"), node("parent"), node("child", "parent"), node("unrelated")];
  const focus = architectureFocusNeighborhood(nodes, [{ id: "source-child", source: "source", target: "child" }], new Set(nodes.map((item) => item.id)), "parent");

  assert.deepEqual([...focus!].sort(), ["child", "parent", "source"]);
});

test("single click focuses while double click opens the node editor", () => {
  const source = readFileSync(new URL("../src/features/architecture/ArchitectureReader.tsx", import.meta.url), "utf8");

  assert.match(source, /function handleNodeClick/);
  assert.match(source, /nodeClickTimer\.current = window\.setTimeout/);
  assert.match(source, /function handleNodeDoubleClick/);
  assert.match(source, /closest\("\.architecture-node-title"\)/);
  assert.match(source, /cancelPendingNodeClick\(\);/);
  assert.match(source, /onNodeOpen\(entity\)/);
  assert.match(source, /zoomOnDoubleClick=\{false\}/);
  assert.match(source, /nodeDragThreshold=\{8\}/);
  assert.match(source, /nodeClickDistance=\{8\}/);
  assert.match(source, /dragHandle: "\.architecture-node-title"/);
  assert.match(source, /else \{ setSelectedIds\(\[\]\); setFocusedNodeId\(null\); \}/);
  assert.doesNotMatch(source, /onNodeMouseEnter|onNodeMouseLeave/);
  assert.match(source, /direction="row" alignItems="center" spacing=\{\.5\}/);
});
