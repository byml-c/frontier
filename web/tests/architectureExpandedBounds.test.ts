import assert from "node:assert/strict";
import test from "node:test";

import { architectureCardSize } from "../src/features/architecture/architectureCardSize.ts";
import { fitExpandedArchitectureBounds } from "../src/features/architecture/architectureExpandedBounds.ts";
import type { ArchitectureNode, XYPosition } from "../src/features/architecture/types.ts";

function moduleNode(id: string, position: XYPosition, parentModuleId?: string): ArchitectureNode {
  return { id, type: "module", title: id, position, parentModuleId };
}

test("expanded module wraps moved members while retaining its own minimum card size", () => {
  const parent = moduleNode("parent", { x: 100, y: 100 });
  const child = moduleNode("child", { x: 0, y: 0 }, "parent");
  const positions = { parent: { x: 100, y: 100 }, child: { x: 430, y: 260 } };
  const result = fitExpandedArchitectureBounds([parent, child], ["parent"], positions);
  const childSize = architectureCardSize(child);
  const parentBase = architectureCardSize(parent);

  assert.deepEqual(result.positions.parent, positions.parent);
  assert.ok(result.sizes.parent.width >= positions.child.x + childSize.width + 24 - positions.parent.x);
  assert.ok(result.sizes.parent.height >= positions.child.y + childSize.height + 24 - positions.parent.y);
  assert.ok(result.sizes.parent.width >= parentBase.width);
  assert.ok(result.sizes.parent.height >= parentBase.height);
});

test("expanded module grows left and upward when a member is dragged beyond its boundary", () => {
  const parent = moduleNode("parent", { x: 100, y: 100 });
  const child = moduleNode("child", { x: 0, y: 0 }, "parent");
  const result = fitExpandedArchitectureBounds([parent, child], ["parent"], {
    parent: { x: 100, y: 100 },
    child: { x: 10, y: 20 },
  });

  assert.equal(result.positions.parent.x, -14);
  assert.ok(result.positions.parent.y < 20);
  assert.ok(result.positions.parent.x + result.sizes.parent.width >= 10 + architectureCardSize(child).width + 24);
});

test("nested expanded bounds are calculated from the inside out", () => {
  const parent = moduleNode("parent", { x: 0, y: 0 });
  const section = moduleNode("section", { x: 120, y: 120 }, "parent");
  const leaf = moduleNode("leaf", { x: 500, y: 260 }, "section");
  const result = fitExpandedArchitectureBounds([parent, section, leaf], ["parent", "section"], {
    parent: { x: 0, y: 0 },
    section: { x: 120, y: 120 },
    leaf: { x: 500, y: 260 },
  });

  assert.ok(result.sizes.section.width > architectureCardSize(section).width);
  assert.ok(result.sizes.parent.width >= result.positions.section.x + result.sizes.section.width + 24 - result.positions.parent.x);
  assert.ok(result.sizes.parent.height >= result.positions.section.y + result.sizes.section.height + 24 - result.positions.parent.y);
});
