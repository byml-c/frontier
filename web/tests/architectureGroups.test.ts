import assert from "node:assert/strict";
import test from "node:test";

import { architectureCardSize } from "../src/features/architecture/architectureCardSize.ts";
import { architectureGroupBounds } from "../src/features/architecture/architectureGroups.ts";
import { organizeArchitectureLayout } from "../src/features/architecture/architectureLayout.ts";
import type { ArchitectureGroup, ArchitectureNode } from "../src/features/architecture/types.ts";

function node(id: string, groupId: string, x: number, y: number): ArchitectureNode {
  return { id, groupId, type: "module", title: id, position: { x, y } };
}

const group = (id: string, y: number): ArchitectureGroup => ({ id, title: id, position: { x: 0, y }, size: { width: 100, height: 100 } });

test("group bounds follow the current member positions instead of stale scene bounds", () => {
  const member = node("member", "group", 0, 0);
  const bounds = architectureGroupBounds(group("group", 0), [member], new Set([member.id]), { member: { x: 420, y: 180 } }, { member: { width: 200, height: 90 } });

  assert.deepEqual(bounds.position, { x: 392, y: 132 });
  assert.deepEqual(bounds.size, { width: 256, height: 166 });
});

test("global layout keeps groups as separate containers", () => {
  const nodes = [node("a", "first", 0, 0), node("b", "first", 300, 0), node("c", "second", 0, 500), node("d", "second", 300, 500)];
  const layout = organizeArchitectureLayout(nodes, [{ id: "a-b", source: "a", target: "b" }, { id: "c-d", source: "c", target: "d" }], []);
  const sizes = Object.fromEntries(nodes.map((item) => [item.id, architectureCardSize(item)]));
  const first = architectureGroupBounds(group("first", 0), nodes, new Set(nodes.map((item) => item.id)), layout.positions, sizes);
  const second = architectureGroupBounds(group("second", 500), nodes, new Set(nodes.map((item) => item.id)), layout.positions, sizes);

  const separated = first.position.y + first.size.height <= second.position.y || second.position.y + second.size.height <= first.position.y;
  assert.equal(separated, true);
});
